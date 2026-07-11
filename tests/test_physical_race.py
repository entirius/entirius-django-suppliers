# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Unit tests for physical race detection.

Hits `_apply_physical_update_to_real_product` directly, bypassing the delta sync harness.
Covers the four outcome branches: applied (preferred), skipped_non_preferred, overwritten
(opt-in), noop (no link → still skipped per spec). Integration coverage of the counts
dispatcher lives in `test_delta_sync_race.py`.
"""

from decimal import Decimal

import pytest

from django_suppliers.enums import ChangeLogSource, EventSeverity, EventType, ProductStatus
from django_suppliers.models import IntegrationEvent, ProductSupplierLink, SupplierProductChangeLog
from django_suppliers.schemas.contract import PriceStockUpdate
from django_suppliers.services import import_service
from tests.factories import FeedFactory, SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db


def _make_sp_with_real_product(*, supplier, sku: str, initial_weight: Decimal):
    """Helper — creates RealProduct + SupplierProduct + wires the FK."""
    from django_pim.models.real_product import RealProduct

    feed = FeedFactory(supplier=supplier, sync_mode="delta")
    rp = RealProduct.objects.create(sku=sku, weight=initial_weight)
    sp = SupplierProductFactory(
        supplier=supplier, feed=feed, external_id=f"EXT-{sku}", status=ProductStatus.PUSHED.value
    )
    sp.real_product = rp
    sp.save(update_fields=["real_product"])
    return sp, rp


def test_preferred_link_applies_physical_change():
    """Baseline — preferred supplier writes through to RealProduct + emits applied event."""
    supplier = SupplierFactory()
    sp, rp = _make_sp_with_real_product(supplier=supplier, sku="race-pref-001", initial_weight=Decimal("0.15"))
    ProductSupplierLink.objects.create(real_product_sku=rp.sku, supplier=supplier, is_preferred=True, is_active=True)

    upd = PriceStockUpdate(external_id=sp.external_id, physical={"weight": "0.25"})
    outcome = import_service._apply_physical_update_to_real_product(sp, upd, started_at=None)

    assert outcome == import_service.PHYSICAL_OUTCOME_APPLIED
    rp.refresh_from_db()
    assert rp.weight == Decimal("0.25")
    audit = SupplierProductChangeLog.objects.get(field_path="physical.weight", supplier_product=sp)
    assert audit.source == ChangeLogSource.DELTA_SYNC.value
    assert audit.applied_to_pim is True
    event = IntegrationEvent.objects.get(event_type=EventType.PHYSICAL_UPDATE_APPLIED.value, supplier_product=sp)
    assert event.severity == EventSeverity.INFO.value


def test_non_preferred_link_default_skips_write():
    """Non-preferred supplier → RealProduct unchanged + info skip event + physical_skipped audit."""
    preferred_supplier = SupplierFactory(idx="race-preferred")
    non_pref_supplier = SupplierFactory(idx="race-non-preferred")
    sp, rp = _make_sp_with_real_product(supplier=non_pref_supplier, sku="race-skip-001", initial_weight=Decimal("0.15"))
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=preferred_supplier, is_preferred=True, is_active=True
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=non_pref_supplier, is_preferred=False, is_active=True
    )

    upd = PriceStockUpdate(external_id=sp.external_id, physical={"weight": "2.50"})
    outcome = import_service._apply_physical_update_to_real_product(sp, upd, started_at=None)

    assert outcome == import_service.PHYSICAL_OUTCOME_SKIPPED_NON_PREFERRED
    rp.refresh_from_db()
    assert rp.weight == Decimal("0.15"), "RealProduct.weight MUST NOT change on race skip"
    event = IntegrationEvent.objects.get(
        event_type=EventType.PHYSICAL_UPDATE_SKIPPED_NON_PREFERRED.value, supplier_product=sp
    )
    assert event.severity == EventSeverity.INFO.value
    assert event.details["preferred_supplier_idx"] == "race-preferred"
    assert event.details["supplier_idx"] == "race-non-preferred"
    assert event.details["attempted_fields"] == ["weight"]
    audit = SupplierProductChangeLog.objects.get(source=ChangeLogSource.PHYSICAL_SKIPPED.value, supplier_product=sp)
    assert audit.applied_to_pim is False
    assert audit.field_path == "physical_skipped"
    assert audit.after == {"weight": "2.50"}
    # No physical.weight audit row should exist for this SP.
    assert not SupplierProductChangeLog.objects.filter(supplier_product=sp, field_path="physical.weight").exists()


def test_non_preferred_link_with_opt_in_overwrites():
    """allow_physical_writes_from_non_preferred=True → write lands + warning event + physical_overwrite audit."""
    preferred_supplier = SupplierFactory(idx="race-pref-opt")
    non_pref_supplier = SupplierFactory(idx="race-opt-in", allow_physical_writes_from_non_preferred=True)
    sp, rp = _make_sp_with_real_product(
        supplier=non_pref_supplier, sku="race-overwrite-001", initial_weight=Decimal("0.15")
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=preferred_supplier, is_preferred=True, is_active=True
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=non_pref_supplier, is_preferred=False, is_active=True
    )

    upd = PriceStockUpdate(external_id=sp.external_id, physical={"weight": "3.00"})
    outcome = import_service._apply_physical_update_to_real_product(sp, upd, started_at=None)

    assert outcome == import_service.PHYSICAL_OUTCOME_OVERWRITTEN
    rp.refresh_from_db()
    assert rp.weight == Decimal("3.00")
    audit = SupplierProductChangeLog.objects.get(source=ChangeLogSource.PHYSICAL_OVERWRITE.value, supplier_product=sp)
    assert audit.applied_to_pim is True
    assert audit.field_path == "physical.weight"
    event = IntegrationEvent.objects.get(event_type=EventType.PHYSICAL_UPDATE_OVERWRITE.value, supplier_product=sp)
    assert event.severity == EventSeverity.WARNING.value
    assert event.details["preferred_supplier_idx"] == "race-pref-opt"
    assert event.details["non_preferred_supplier_idx"] == "race-opt-in"


def test_no_link_existing_treated_as_non_preferred_skip():
    """Edge case — SP has a RealProduct but no ProductSupplierLink row exists yet.

    In a healthy push flow the link is created by `upsert_for_push`, but legacy data /
    race conditions / cleanup scripts can produce orphan SPs. Default behaviour MUST
    be skip (preferred is the source of truth — and there is no preferred here).
    """
    supplier = SupplierFactory(idx="race-orphan")
    sp, rp = _make_sp_with_real_product(supplier=supplier, sku="race-orphan-001", initial_weight=Decimal("0.15"))

    upd = PriceStockUpdate(external_id=sp.external_id, physical={"weight": "2.50"})
    outcome = import_service._apply_physical_update_to_real_product(sp, upd, started_at=None)

    assert outcome == import_service.PHYSICAL_OUTCOME_SKIPPED_NON_PREFERRED
    rp.refresh_from_db()
    assert rp.weight == Decimal("0.15")
    assert IntegrationEvent.objects.filter(
        event_type=EventType.PHYSICAL_UPDATE_SKIPPED_NON_PREFERRED.value, supplier_product=sp
    ).exists()


def test_skip_path_details_payload_shape():
    """Lock the IntegrationEvent.details schema on the skip path — CMS will key off this."""
    preferred_supplier = SupplierFactory(idx="race-details-pref")
    non_pref_supplier = SupplierFactory(idx="race-details-non")
    sp, rp = _make_sp_with_real_product(
        supplier=non_pref_supplier, sku="race-details-001", initial_weight=Decimal("0.15")
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=preferred_supplier, is_preferred=True, is_active=True
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=non_pref_supplier, is_preferred=False, is_active=True
    )

    upd = PriceStockUpdate(
        external_id=sp.external_id, physical={"weight": "2.50", "ean": "5901234567890", "width": None}
    )
    import_service._apply_physical_update_to_real_product(sp, upd, started_at=None)

    event = IntegrationEvent.objects.get(
        event_type=EventType.PHYSICAL_UPDATE_SKIPPED_NON_PREFERRED.value, supplier_product=sp
    )
    details = event.details
    assert set(details.keys()) >= {"sku", "supplier_idx", "preferred_supplier_idx", "attempted_fields"}
    assert details["attempted_fields"] == ["ean", "weight"], "Only non-None fields, sorted"
    assert details["sku"] == "race-details-001"
