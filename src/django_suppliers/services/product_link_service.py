# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""ProductSupplierLink CRUD + auto-upsert from push.

Frozen decisions:
  D14 — multi-supplier per produkt PIM via ProductSupplierLink.
  D15 — manual scenariusz 2.1: brak SupplierProduct, link dodawany ręcznie po SKU PIM.
  D25 — `is_preferred` operator-only. `upsert_for_push` MUSI nigdy nie nadpisywać
        is_preferred / priority / notes / is_active. Ratio: re-import nie resetuje
        flag operatora.
"""

import logging
from typing import Any

from django.contrib.auth.models import AbstractBaseUser
from django.db import transaction
from django.db.models import QuerySet

from django_suppliers.models import ProductSupplierLink, Supplier

logger = logging.getLogger(__name__)

_EDITABLE_FIELDS = frozenset({"external_id", "priority", "is_preferred", "is_active", "notes"})


def list_links(
    *, real_product_sku: str | None = None, supplier_id: int | None = None, is_active: bool | None = None
) -> QuerySet[ProductSupplierLink]:
    qs = ProductSupplierLink.objects.all()
    if real_product_sku is not None:
        qs = qs.filter(real_product_sku__iexact=real_product_sku)
    if supplier_id is not None:
        qs = qs.filter(supplier_id=supplier_id)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    return qs


def get_link(pk: int) -> ProductSupplierLink:
    try:
        return ProductSupplierLink.objects.get(pk=pk)
    except ProductSupplierLink.DoesNotExist as exc:
        raise ValueError(f"ProductSupplierLink with pk={pk} not found") from exc


def create_link(
    real_product_sku: str,
    supplier_idx: str,
    *,
    external_id: str = "",
    priority: int = 0,
    is_preferred: bool = False,
    notes: str = "",
) -> ProductSupplierLink:
    """Create a manual link. Validates SKU exists in PIM and (sku, supplier) is unique."""
    from django_pim.models.real_product import RealProduct

    if not RealProduct.objects.filter(sku__iexact=real_product_sku).exists():
        raise ValueError(f"PIM RealProduct with SKU '{real_product_sku}' not found")

    try:
        supplier = Supplier.objects.get(idx=supplier_idx)
    except Supplier.DoesNotExist as exc:
        raise ValueError(f"Supplier '{supplier_idx}' not found") from exc

    if ProductSupplierLink.objects.filter(real_product_sku=real_product_sku, supplier=supplier).exists():
        raise ValueError(f"ProductSupplierLink already exists for sku='{real_product_sku}' supplier='{supplier_idx}'")

    if is_preferred:
        from django_suppliers.services import role_guard

        role_guard.assert_not_monitoring(supplier, "creating a preferred link")

    return ProductSupplierLink.objects.create(
        real_product_sku=real_product_sku,
        supplier=supplier,
        external_id=external_id,
        priority=priority,
        is_preferred=is_preferred,
        notes=notes,
    )


def update_link(pk: int, **fields: Any) -> ProductSupplierLink:
    """Update a link. Only whitelisted fields editable; `real_product_sku`/`supplier` immutable."""
    invalid = set(fields) - _EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_link: {sorted(invalid)}")
    try:
        link = ProductSupplierLink.objects.select_related("supplier").get(pk=pk)
    except ProductSupplierLink.DoesNotExist as exc:
        raise ValueError(f"ProductSupplierLink with pk={pk} not found") from exc
    if fields.get("is_preferred"):
        from django_suppliers.services import role_guard

        role_guard.assert_not_monitoring(link.supplier, "setting a link as preferred")
    for field, value in fields.items():
        setattr(link, field, value)
    link.save()
    return link


def delete_link(pk: int) -> None:
    ProductSupplierLink.objects.filter(pk=pk).delete()


def upsert_for_push(
    real_product_sku: str, supplier: Supplier, external_id: str | None = None, *, set_preferred_if_first: bool = False
) -> ProductSupplierLink:
    """Push-side upsert: create if missing, update only `external_id`.

    D25 — defaults MUST contain ONLY external_id on the upsert. Never reset is_preferred /
    priority / notes / is_active on UPDATE (operator-controlled fields). Re-imports must
    not erase operator state.

    `set_preferred_if_first` — when True AND the link was just created AND no
    other link exists for this SKU, set is_preferred=True. D25 is preserved because we
    only touch is_preferred at creation time (nothing to overwrite) and only when this
    is the only link for the SKU. Subsequent re-imports never re-flag because the link
    already exists. The legacy single-supplier flow finally gets a preferred link by
    default — required so the pricemanager cost subscriber stops ignoring it.
    """
    defaults = {"external_id": external_id or ""}
    link, created = ProductSupplierLink.objects.update_or_create(
        real_product_sku=real_product_sku, supplier=supplier, defaults=defaults
    )
    if created and set_preferred_if_first:
        other_links_exist = (
            ProductSupplierLink.objects.filter(real_product_sku=real_product_sku).exclude(pk=link.pk).exists()
        )
        if not other_links_exist:
            link.is_preferred = True
            link.save(update_fields=["is_preferred", "modified_at"])
    return link


def set_preferred(pk: int) -> ProductSupplierLink:
    """Mark a link as the preferred supplier for its SKU. Unsets all other links for the same SKU."""
    try:
        link = ProductSupplierLink.objects.select_related("supplier").get(pk=pk)
    except ProductSupplierLink.DoesNotExist as exc:
        raise ValueError(f"ProductSupplierLink with pk={pk} not found") from exc
    from django_suppliers.services import role_guard

    role_guard.assert_not_monitoring(link.supplier, "setting a link as preferred")
    ProductSupplierLink.objects.filter(real_product_sku=link.real_product_sku).exclude(pk=pk).update(is_preferred=False)
    link.is_preferred = True
    link.save(update_fields=["is_preferred", "modified_at"])
    return link


def unset_preferred(pk: int) -> ProductSupplierLink:
    """Clear is_preferred flag. Post-state may be 'no preferred for SKU' (operator decides)."""
    try:
        link = ProductSupplierLink.objects.get(pk=pk)
    except ProductSupplierLink.DoesNotExist as exc:
        raise ValueError(f"ProductSupplierLink with pk={pk} not found") from exc
    link.is_preferred = False
    link.save(update_fields=["is_preferred", "modified_at"])
    return link


def unlink_sp_from_realproduct(
    sp_pk: int, user: AbstractBaseUser | None, *, event_sink: list[dict[str, Any]] | None = None
) -> dict[str, str]:
    """Operator force-unlink: detach an auto-linked SP from its RealProduct.

    Creates a fresh RealProduct (per-supplier-prefixed SKU via pim_writer.generate_sku),
    moves the SP to it, deletes the old ProductSupplierLink, and creates a new one
    flagged is_preferred=True (the SP is now the only supplier on the new RP).

    The original RealProduct is left intact — other suppliers' ProductSupplierLinks
    may still reference it.

    Side effects:
      - audit row source=manual_unlink with before/after sku
      - IntegrationEvent manual_unlink_from_realproduct (info) appended to event_sink
      - all DB writes wrapped in transaction.atomic()

    Raises ValueError when sp is missing, or when sp has no real_product / no link.
    """
    from django_pim.models.real_product import RealProduct

    from django_suppliers.enums import ChangeLogSource, EventSeverity, EventType
    from django_suppliers.models import SupplierProduct
    from django_suppliers.services import audit_service, event_service, pim_writer

    try:
        sp = SupplierProduct.objects.select_related("supplier", "real_product").get(pk=sp_pk)
    except SupplierProduct.DoesNotExist as exc:
        raise ValueError(f"SupplierProduct with pk={sp_pk} not found") from exc

    if sp.real_product_id is None:
        raise ValueError(f"SupplierProduct {sp_pk} is not linked to any RealProduct")

    supplier = sp.supplier
    from django_suppliers.services import role_guard

    # Unlink creates a FRESH RealProduct for the SP — a PIM write monitoring may not do.
    role_guard.assert_not_monitoring(supplier, "unlink (creates a new RealProduct)")
    old_rp = sp.real_product
    old_sku = old_rp.sku

    try:
        link = ProductSupplierLink.objects.get(real_product_sku=old_sku, supplier=supplier)
    except ProductSupplierLink.DoesNotExist as exc:
        raise ValueError(
            f"SupplierProduct {sp_pk} is attached to RealProduct '{old_sku}' but no "
            f"ProductSupplierLink exists for (sku='{old_sku}', supplier='{supplier.idx}')"
        ) from exc

    new_sku = pim_writer.generate_sku(supplier, sp.external_id)
    if new_sku == old_sku or RealProduct.objects.filter(sku=new_sku).exists():
        raise ValueError(
            f"Cannot unlink: generated SKU '{new_sku}' already exists. "
            "Pick a different external_id or manually adjust the SP before unlinking."
        )

    with transaction.atomic():
        new_rp = RealProduct.objects.create(
            sku=new_sku,
            ean=sp.ean or None,
            weight=old_rp.weight,
            width=old_rp.width,
            height=old_rp.height,
            deep=old_rp.deep,
            kind_of_product=old_rp.kind_of_product,
        )
        link.delete()
        new_link = ProductSupplierLink.objects.create(
            real_product_sku=new_sku, supplier=supplier, external_id=sp.external_id, is_preferred=True
        )
        sp.real_product = new_rp
        sp.save(update_fields=["real_product", "modified_at"])

    message = f"SP {sp.id} unlinked from RealProduct '{old_sku}' and reattached to fresh RealProduct '{new_sku}'."
    details = {
        "supplier_product_id": sp.id,
        "supplier_idx": supplier.idx,
        "previous_real_product_sku": old_sku,
        "new_real_product_sku": new_sku,
        "ean": sp.ean or None,
    }
    try:
        event_service.record(
            event_type=EventType.MANUAL_UNLINK_FROM_REALPRODUCT.value,
            severity=EventSeverity.INFO.value,
            supplier=supplier,
            supplier_product=sp,
            message=message,
            details=details,
        )
    except Exception:  # noqa: BLE001 — event log must be best-effort
        logger.warning("event_service.record failed for manual_unlink_from_realproduct", exc_info=True)

    try:
        audit_service.log_change(
            supplier_product=sp,
            source=ChangeLogSource.MANUAL_UNLINK.value,
            field_path="real_product.unlink",
            before={"sku": old_sku},
            after={"sku": new_sku, "ean": sp.ean or None},
            triggered_by=user,
            applied_to_pim=True,
            real_product_sku=new_sku,
        )
    except Exception:  # noqa: BLE001 — audit must be best-effort
        logger.warning("audit_service.log_change failed for manual_unlink", exc_info=True)

    if event_sink is not None:
        event_sink.append(
            {
                "event_type": EventType.MANUAL_UNLINK_FROM_REALPRODUCT.value,
                "severity": EventSeverity.INFO.value,
                "message": message,
                "details": details,
            }
        )

    return {"previous_real_product_sku": old_sku, "new_real_product_sku": new_sku, "new_link_pk": new_link.pk}
