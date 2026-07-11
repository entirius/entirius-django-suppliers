# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Operator-driven merge of two RealProducts sharing an EAN.

The auto-EAN-match path skips re-merging RealProducts when a
physical-tolerance check fails -- the operator must reconcile the resulting
duplicates manually. This service is the write end of that reconciliation:
take a winner SKU + loser SKU, redirect every `ProductSupplierLink` from loser
to winner, delete the loser, and audit the action.

Cascade is trivial because `ProductSupplierLink.real_product_sku` is a string
(not an FK) -- a single `.update()` redirects all links. `SupplierProduct.real_product`
is an FK with `on_delete=SET_NULL`, so deleting the loser sets `real_product=NULL`
on any SPs that pointed at it; those SPs are then re-linked to the winner.

Side effects (all inside one `transaction.atomic()`):
  1. Move ProductSupplierLink (by real_product_sku) loser -> winner
  2. Re-point SupplierProduct.real_product loser -> winner (and back-fill the FK
     that on_delete=SET_NULL would otherwise null out)
  3. Delete the loser RealProduct
  4. Write SupplierProductChangeLog source=manual_merge against the preferred SP
     (or any SP if no preferred), field_path=realproduct.merge, before=loser_sku,
     after=winner_sku
  5. Emit IntegrationEvent realproduct_manually_merged (severity=info)

Failures raise; nothing is committed.

`verified_separate` is intentionally NOT implemented in this service -- it would
require a RealProduct field, which lives in django-pim (release/2.1.0 production).
Operator workaround for "do not auto-merge again": `Supplier.disable_ean_auto_link=True`.
"""

from dataclasses import dataclass

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction

from django_suppliers.enums import ChangeLogSource, EventSeverity, EventType
from django_suppliers.services import audit_service, event_service


@dataclass(frozen=True)
class MergeResult:
    winner_sku: str
    loser_sku: str
    links_redirected: int
    supplier_products_repointed: int
    audit_id: int | None  # None when no SupplierProduct exists for either side (orphan RPs)


def merge_realproducts(
    *, winner_sku: str, loser_sku: str, reason: str, actor: AbstractBaseUser | None = None
) -> MergeResult:
    """Merge `loser_sku` INTO `winner_sku`. Atomic. Re-validates EAN equality.

    Raises:
        ValueError -- winner_sku == loser_sku, EAN mismatch, reason too short.
        RealProduct.DoesNotExist -- either SKU not found.
    """
    if not reason or len(reason.strip()) < 3:
        raise ValueError("reason must be at least 3 characters.")
    if winner_sku == loser_sku:
        raise ValueError("winner_sku and loser_sku must differ.")

    from django_pim.models.real_product import RealProduct

    from django_suppliers.models import ProductSupplierLink, SupplierProduct

    with transaction.atomic():
        winner = RealProduct.objects.select_for_update().get(sku=winner_sku)
        loser = RealProduct.objects.select_for_update().get(sku=loser_sku)

        if not winner.ean or not loser.ean:
            raise ValueError("Both RealProducts must have an EAN to merge.")
        if winner.ean != loser.ean:
            raise ValueError(
                f"EAN mismatch: winner '{winner.ean}' != loser '{loser.ean}'. Merge only allowed within same EAN group."
            )

        links_qs = ProductSupplierLink.objects.filter(real_product_sku=loser_sku)
        links_redirected = links_qs.update(real_product_sku=winner_sku)

        sps_qs = SupplierProduct.objects.filter(real_product=loser)
        sps_repointed = sps_qs.update(real_product=winner)

        # ProductSupplierLink keys on real_product_sku (string), not on SupplierProduct,
        # so there is no reverse FK to traverse for "preferred SP". Pick any SP on the
        # winner deterministically (lowest id) as the audit anchor.
        audit_sp = SupplierProduct.objects.filter(real_product=winner).order_by("id").first()

        audit_id: int | None = None
        if audit_sp is not None:
            entry = audit_service.log_change(
                supplier_product=audit_sp,
                source=ChangeLogSource.MANUAL_MERGE.value,
                field_path="realproduct.merge",
                before={"sku": loser_sku, "ean": loser.ean},
                after={"sku": winner_sku, "ean": winner.ean, "reason": reason},
                triggered_by=actor,
                real_product_sku=winner_sku,
            )
            audit_id = entry.id

        event_service.record(
            event_type=EventType.REALPRODUCT_MANUALLY_MERGED.value,
            severity=EventSeverity.INFO.value,
            message=f"Merged RealProduct '{loser_sku}' into '{winner_sku}' (reason: {reason})",
            supplier_product=audit_sp,
            details={
                "winner_sku": winner_sku,
                "loser_sku": loser_sku,
                "ean": winner.ean,
                "links_redirected": links_redirected,
                "supplier_products_repointed": sps_repointed,
                "reason": reason,
            },
        )

        loser.delete()

    return MergeResult(
        winner_sku=winner_sku,
        loser_sku=loser_sku,
        links_redirected=links_redirected,
        supplier_products_repointed=sps_repointed,
        audit_id=audit_id,
    )
