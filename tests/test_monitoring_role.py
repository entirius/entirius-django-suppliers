# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Monitoring supplier role — read-only observer semantics.

Role unblock + every PIM-bound gate: push preflight, review approve/queue,
delta QMS/PM writers, preferred selection, link preferred flags, unlink.
Manual link to an EXISTING RealProduct stays allowed (price-alert path).
"""

from decimal import Decimal
from unittest import mock

import pytest
from django_pim.models.real_product import RealProduct

from django_suppliers.enums import ProductStatus, SupplierRole
from django_suppliers.models import ProductSupplierLink
from django_suppliers.services import (
    import_service,
    preferred_strategy_service,
    product_link_service,
    push_service,
    review_service,
    role_guard,
    supplier_service,
)
from tests.factories import SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def monitoring_supplier(db):
    return SupplierFactory(idx="mon-1", supplier_role=SupplierRole.MONITORING.value)


@pytest.fixture
def monitoring_sp(monitoring_supplier):
    return SupplierProductFactory(supplier=monitoring_supplier, external_id="MON-001")


# ---------------------------------------------------------------------------
# Role unblock
# ---------------------------------------------------------------------------


def test_create_supplier_with_monitoring_role_succeeds(language, currency):
    supplier = supplier_service.create_supplier(
        idx="mon-create",
        name="Monitoring Supplier",
        default_language=language,
        default_currency=currency,
        supplier_role=SupplierRole.MONITORING.value,
        supplier_type="feed",
    )
    assert supplier.supplier_role == SupplierRole.MONITORING.value


def test_create_supplier_with_data_role_still_blocked(language, currency):
    with pytest.raises(NotImplementedError):
        supplier_service.create_supplier(
            idx="data-create",
            name="Data Supplier",
            default_language=language,
            default_currency=currency,
            supplier_role=SupplierRole.DATA.value,
        )


def test_update_supplier_to_monitoring_succeeds(language, currency):
    SupplierFactory(idx="trade-to-mon")
    updated = supplier_service.update_supplier("trade-to-mon", supplier_role=SupplierRole.MONITORING.value)
    assert updated.supplier_role == SupplierRole.MONITORING.value


# ---------------------------------------------------------------------------
# Push gates (preflight chokepoint)
# ---------------------------------------------------------------------------


def test_preflight_blocks_monitoring(monitoring_supplier):
    result = push_service.preflight_check(monitoring_supplier)
    assert result["ok"] is False
    assert role_guard.MONITORING_PUSH_BLOCKED in result["errors"]


def test_push_supplier_product_refuses_monitoring(monitoring_sp):
    monitoring_sp.status = ProductStatus.APPROVED.value
    monitoring_sp.save(update_fields=["status"])
    from django.db import transaction

    with pytest.raises(ValueError, match=role_guard.MONITORING_PUSH_BLOCKED), transaction.atomic():
        push_service.push_supplier_product(monitoring_sp.id, None)


def test_push_approved_for_supplier_refuses_monitoring(monitoring_supplier):
    result = push_service.push_approved_for_supplier(monitoring_supplier.id, None)
    assert result["preflight_failed"] is True
    assert role_guard.MONITORING_PUSH_BLOCKED in result["errors"]


def test_force_repush_refuses_monitoring(monitoring_sp):
    monitoring_sp.status = ProductStatus.PUSHED.value
    monitoring_sp.save(update_fields=["status"])
    with pytest.raises(ValueError, match=role_guard.MONITORING_PUSH_BLOCKED):
        push_service.force_repush_supplier_product(monitoring_sp.id, None)


# ---------------------------------------------------------------------------
# Review gates — approve/queue blocked, reject/skip allowed
# ---------------------------------------------------------------------------


def test_approve_blocked_for_monitoring(monitoring_sp):
    with pytest.raises(ValueError, match="monitoring"):
        review_service.approve(monitoring_sp.id, None)


def test_queue_blocked_for_monitoring(monitoring_sp):
    with pytest.raises(ValueError, match="monitoring"):
        review_service.queue(monitoring_sp.id, None)


def test_reject_allowed_for_monitoring(monitoring_sp):
    sp = review_service.reject(monitoring_sp.id, None)
    assert sp.status == ProductStatus.REJECTED.value


def test_bulk_approve_skips_monitoring(monitoring_sp):
    trade_sp = SupplierProductFactory(external_id="TRD-001")
    result = review_service.bulk_approve([monitoring_sp.id, trade_sp.id], None)
    assert result["success"] == 1
    assert monitoring_sp.id in result["ids_failed"]


def test_bulk_requeue_skips_monitoring(monitoring_sp):
    monitoring_sp.status = ProductStatus.REJECTED.value
    monitoring_sp.save(update_fields=["status"])
    result = review_service.bulk_requeue([monitoring_sp.id], None)
    assert result["success"] == 0
    assert monitoring_sp.id in result["ids_failed"]


# ---------------------------------------------------------------------------
# Delta writers — no QMS/PM writes for monitoring
# ---------------------------------------------------------------------------


def test_delta_qms_cost_writers_skipped_for_monitoring(monitoring_sp):
    monitoring_sp.status = ProductStatus.PUSHED.value
    monitoring_sp.pushed_to_channel_idxs = ["default"]
    monitoring_sp.save(update_fields=["status", "pushed_to_channel_idxs"])
    with (
        mock.patch("django_suppliers.services.qms_writer.write_stock") as qms_mock,
        mock.patch("django_suppliers.services.pricemanager_writer.log_cost") as pm_mock,
    ):
        import_service._write_qms_cost_for_pushed_sp(monitoring_sp, monitoring_sp.supplier)
    qms_mock.assert_not_called()
    pm_mock.assert_not_called()


def test_delta_physical_update_noop_for_monitoring(monitoring_sp):
    from django_suppliers.schemas.contract import PriceStockUpdate

    monitoring_sp.status = ProductStatus.PUSHED.value
    monitoring_sp.save(update_fields=["status"])
    upd = PriceStockUpdate(external_id="MON-001", physical={"weight": "1.20"})
    outcome = import_service._apply_physical_update_to_real_product(monitoring_sp, upd, None)
    assert outcome == import_service.PHYSICAL_OUTCOME_NOOP


# ---------------------------------------------------------------------------
# Links — manual link to existing RP allowed; preferred blocked
# ---------------------------------------------------------------------------


def test_manual_link_to_existing_realproduct_allowed(monitoring_supplier):
    RealProduct.objects.create(sku="RP-EXIST-1", weight=Decimal("0.10"))
    link = product_link_service.create_link("RP-EXIST-1", monitoring_supplier.idx, external_id="MON-001")
    assert link.pk is not None
    assert link.is_preferred is False


def test_create_link_preferred_blocked_for_monitoring(monitoring_supplier):
    RealProduct.objects.create(sku="RP-EXIST-2", weight=Decimal("0.10"))
    with pytest.raises(ValueError, match="monitoring"):
        product_link_service.create_link(
            "RP-EXIST-2", monitoring_supplier.idx, external_id="MON-001", is_preferred=True
        )


def test_set_preferred_blocked_for_monitoring(monitoring_supplier):
    RealProduct.objects.create(sku="RP-EXIST-3", weight=Decimal("0.10"))
    link = product_link_service.create_link("RP-EXIST-3", monitoring_supplier.idx)
    with pytest.raises(ValueError, match="monitoring"):
        product_link_service.set_preferred(link.pk)


def test_update_link_preferred_blocked_for_monitoring(monitoring_supplier):
    RealProduct.objects.create(sku="RP-EXIST-4", weight=Decimal("0.10"))
    link = product_link_service.create_link("RP-EXIST-4", monitoring_supplier.idx)
    with pytest.raises(ValueError, match="monitoring"):
        product_link_service.update_link(link.pk, is_preferred=True)


def test_force_set_preferred_blocked_for_monitoring(monitoring_supplier):
    RealProduct.objects.create(sku="RP-EXIST-5", weight=Decimal("0.10"))
    product_link_service.create_link("RP-EXIST-5", monitoring_supplier.idx)
    with pytest.raises(ValueError, match="monitoring"):
        preferred_strategy_service.force_set_preferred(
            real_product_sku="RP-EXIST-5", supplier_idx=monitoring_supplier.idx, reason="manual test"
        )


def test_preferred_eval_excludes_monitoring_candidates(monitoring_supplier, monitoring_sp):
    RealProduct.objects.create(sku="RP-EXIST-6", weight=Decimal("0.10"))
    ProductSupplierLink.objects.create(
        real_product_sku="RP-EXIST-6", supplier=monitoring_supplier, external_id="MON-001"
    )
    result = preferred_strategy_service.evaluate_preferred_supplier("RP-EXIST-6")
    assert result.should_switch is False
    assert result.decision_audit["candidates_count"] == 0


def test_unlink_blocked_for_monitoring(monitoring_supplier, monitoring_sp):
    rp = RealProduct.objects.create(sku="RP-EXIST-7", weight=Decimal("0.10"))
    monitoring_sp.real_product = rp
    monitoring_sp.save(update_fields=["real_product"])
    with pytest.raises(ValueError, match="monitoring"):
        product_link_service.unlink_sp_from_realproduct(monitoring_sp.pk, None)


# ---------------------------------------------------------------------------
# Row exposure for CMS
# ---------------------------------------------------------------------------


def test_list_for_review_annotates_supplier_role(monitoring_sp):
    rows = list(review_service.list_for_review(status=ProductStatus.NEW.value))
    assert rows
    assert any(getattr(r, "supplier_role", None) == SupplierRole.MONITORING.value for r in rows)
