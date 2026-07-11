# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Unit tests for change_log_service."""

from datetime import timedelta

import pytest
from django.contrib.auth.models import User
from django.utils import timezone

from django_suppliers.enums import ChangeLogSource
from django_suppliers.models import ProductSupplierLink, SupplierProductChangeLog
from django_suppliers.services import audit_service, change_log_service
from tests.factories import SupplierProductFactory

pytestmark = pytest.mark.django_db


def _make_log(sp, *, sku, source=ChangeLogSource.DELTA_SYNC, field_path="cost", applied=False, days_ago=0):
    entry = audit_service.log_change(
        supplier_product=sp,
        source=source.value,
        field_path=field_path,
        before=1,
        after=2,
        applied_to_pim=applied,
        real_product_sku=sku,
    )
    if days_ago:
        SupplierProductChangeLog.objects.filter(pk=entry.pk).update(
            created_at=timezone.now() - timedelta(days=days_ago)
        )
    return entry


def _link_sku(supplier, sku, *, is_preferred=False):
    return ProductSupplierLink.objects.create(
        real_product_sku=sku, supplier=supplier, is_preferred=is_preferred, is_active=True
    )


# ---------------------------------------------------------------------------
# list_for_sku
# ---------------------------------------------------------------------------


def test_list_for_sku_not_found_when_no_link_and_no_logs():
    with pytest.raises(ValueError, match="not found"):
        change_log_service.list_for_sku("UNKNOWN-SKU")


def test_list_for_sku_returns_has_supplier_with_supplier_payload():
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-1", is_preferred=True)
    _make_log(sp, sku="FT-1")
    payload = change_log_service.list_for_sku("FT-1")
    assert payload["has_supplier"] is True
    assert payload["supplier"] == {"idx": sp.supplier.idx, "name": sp.supplier.name}
    assert payload["unseen_count"] == 1
    assert len(payload["changes"]) == 1


def test_list_for_sku_filters_unseen_only():
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-2")
    _make_log(sp, sku="FT-2", applied=False)
    _make_log(sp, sku="FT-2", applied=True, field_path="stock")
    payload = change_log_service.list_for_sku("FT-2", unseen_only=True)
    assert payload["unseen_count"] == 1
    assert len(payload["changes"]) == 1
    assert payload["changes"][0]["applied_to_pim"] is False


def test_list_for_sku_filters_by_source():
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-3")
    _make_log(sp, sku="FT-3", source=ChangeLogSource.FULL_SYNC, field_path="name")
    _make_log(sp, sku="FT-3", source=ChangeLogSource.DELTA_SYNC, field_path="cost")
    payload = change_log_service.list_for_sku("FT-3", sources=["delta_sync"])
    assert len(payload["changes"]) == 1
    assert payload["changes"][0]["source"] == "delta_sync"


def test_list_for_sku_rejects_unknown_source_value():
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-4")
    _make_log(sp, sku="FT-4")
    with pytest.raises(ValueError, match="Unknown source"):
        change_log_service.list_for_sku("FT-4", sources=["bogus"])


def test_list_for_sku_filters_by_since():
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-5")
    _make_log(sp, sku="FT-5", days_ago=10)
    _make_log(sp, sku="FT-5", field_path="stock")  # today
    since = timezone.now() - timedelta(days=1)
    payload = change_log_service.list_for_sku("FT-5", since=since)
    assert len(payload["changes"]) == 1
    assert payload["changes"][0]["field_path"] == "stock"


def test_list_for_sku_returns_200_when_link_exists_but_no_changes():
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-6")
    payload = change_log_service.list_for_sku("FT-6")
    assert payload["has_supplier"] is True
    assert payload["unseen_count"] == 0
    assert payload["changes"] == []
    assert payload["last_change_at"] is None


# ---------------------------------------------------------------------------
# bulk_has_changes
# ---------------------------------------------------------------------------


def test_bulk_has_changes_empty_list_returns_empty():
    assert change_log_service.bulk_has_changes([]) == {}


def test_bulk_has_changes_unknown_sku_returns_no_supplier_entry():
    result = change_log_service.bulk_has_changes(["UNKNOWN-A", "UNKNOWN-B"])
    assert set(result) == {"UNKNOWN-A", "UNKNOWN-B"}
    assert result["UNKNOWN-A"]["has_supplier"] is False
    assert result["UNKNOWN-A"]["unseen_count"] == 0


def test_bulk_has_changes_combines_link_and_audit_aggregate():
    sp1 = SupplierProductFactory()
    sp2 = SupplierProductFactory()
    _link_sku(sp1.supplier, "FT-A", is_preferred=True)
    _link_sku(sp2.supplier, "FT-B")
    _make_log(sp1, sku="FT-A", applied=False)
    _make_log(sp1, sku="FT-A", applied=False, field_path="stock")
    _make_log(sp1, sku="FT-A", applied=True, field_path="weight")
    _make_log(sp2, sku="FT-B", applied=False)
    result = change_log_service.bulk_has_changes(["FT-A", "FT-B", "UNKNOWN"])
    assert result["FT-A"]["has_supplier"] is True
    assert result["FT-A"]["supplier_idx"] == sp1.supplier.idx
    assert result["FT-A"]["unseen_count"] == 2
    assert result["FT-B"]["has_supplier"] is True
    assert result["FT-B"]["unseen_count"] == 1
    assert result["UNKNOWN"]["has_supplier"] is False
    assert result["UNKNOWN"]["unseen_count"] == 0


def test_bulk_has_changes_uses_few_queries(django_assert_max_num_queries):
    sp = SupplierProductFactory()
    for i in range(20):
        sku = f"FT-{i:03d}"
        _link_sku(sp.supplier, sku)
        _make_log(sp, sku=sku, applied=False)
    skus = [f"FT-{i:03d}" for i in range(20)]
    with django_assert_max_num_queries(3):
        result = change_log_service.bulk_has_changes(skus)
    assert len(result) == 20


# ---------------------------------------------------------------------------
# acknowledge
# ---------------------------------------------------------------------------


def test_acknowledge_rejects_both_modes_set():
    with pytest.raises(ValueError, match="not both"):
        change_log_service.acknowledge("FT-1", change_ids=[1], all_unseen=True)


def test_acknowledge_rejects_neither_mode_set():
    with pytest.raises(ValueError, match="Specify exactly one"):
        change_log_service.acknowledge("FT-1", change_ids=None, all_unseen=False)


def test_acknowledge_all_unseen_flips_only_unseen_rows():
    sp = SupplierProductFactory()
    _make_log(sp, sku="FT-Z", applied=False)
    _make_log(sp, sku="FT-Z", applied=False, field_path="stock")
    _make_log(sp, sku="FT-Z", applied=True, field_path="weight")
    count = change_log_service.acknowledge("FT-Z", all_unseen=True)
    assert count == 2
    assert SupplierProductChangeLog.objects.filter(real_product_sku="FT-Z", applied_to_pim=False).count() == 0
    # audit-of-audit row created
    assert SupplierProductChangeLog.objects.filter(source=ChangeLogSource.OPERATOR_ACKNOWLEDGE.value).count() == 1


def test_acknowledge_change_ids_validates_membership():
    sp = SupplierProductFactory()
    other_sp = SupplierProductFactory()
    own = _make_log(sp, sku="FT-OWN", applied=False)
    foreign = _make_log(other_sp, sku="FT-OTHER", applied=False)
    with pytest.raises(ValueError, match="do not belong"):
        change_log_service.acknowledge("FT-OWN", change_ids=[own.pk, foreign.pk])


def test_acknowledge_zero_rows_does_not_emit_audit_entry():
    sp = SupplierProductFactory()
    _make_log(sp, sku="FT-NONE", applied=True)
    count = change_log_service.acknowledge("FT-NONE", all_unseen=True)
    assert count == 0
    assert SupplierProductChangeLog.objects.filter(source=ChangeLogSource.OPERATOR_ACKNOWLEDGE.value).count() == 0


def test_acknowledge_records_user_in_audit_entry():
    sp = SupplierProductFactory()
    user = User.objects.create_user(username="ack-op", password="p")
    _make_log(sp, sku="FT-U", applied=False)
    change_log_service.acknowledge("FT-U", all_unseen=True, user=user)
    audit_row = SupplierProductChangeLog.objects.get(source=ChangeLogSource.OPERATOR_ACKNOWLEDGE.value)
    assert audit_row.triggered_by == user
    assert audit_row.applied_to_pim is True
    assert audit_row.after["count"] == 1


# ---------------------------------------------------------------------------
# force_repush_by_sku
# ---------------------------------------------------------------------------


def test_force_repush_by_sku_no_links_raises_not_found():
    with pytest.raises(ValueError, match="not found"):
        change_log_service.force_repush_by_sku("UNKNOWN", user=None)


def test_force_repush_by_sku_delegates_per_link(monkeypatch):
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-FR")
    calls: list[int] = []

    def fake_force(sp_id: int, user):
        calls.append(sp_id)
        return ["chan1", "chan2"]

    monkeypatch.setattr(
        "django_suppliers.services.change_log_service.push_service.force_repush_supplier_product", fake_force
    )
    # Need RealProduct.sku = FT-FR linked to sp — simulate by attaching a stub
    sp.real_product = None  # ensure lookup via sp linkage by supplier; service falls back

    # Force the lookup to find sp_id via SupplierProduct query — we monkeypatch that too
    def fake_values_list(self, *fields, **kw):  # noqa: ARG001
        from django_suppliers.models import SupplierProduct as SP

        return SP.objects.filter(pk=sp.pk).values_list(*fields, **kw)

    # Simpler approach: link the real_product on the SP via PIM
    from django_pim.models.real_product import RealProduct

    rp = RealProduct.objects.create(sku="FT-FR")
    sp.real_product = rp
    sp.save(update_fields=["real_product"])

    result = change_log_service.force_repush_by_sku("FT-FR", user=None)
    assert calls == [sp.pk]
    assert result["processed_sp_ids"] == [sp.pk]
    assert result["pushed_channels_count"] == 2
    assert result["failed"] == []


def test_force_repush_by_sku_captures_failures(monkeypatch):
    sp = SupplierProductFactory()
    _link_sku(sp.supplier, "FT-FAIL")
    from django_pim.models.real_product import RealProduct

    rp = RealProduct.objects.create(sku="FT-FAIL")
    sp.real_product = rp
    sp.save(update_fields=["real_product"])

    def fake_force(sp_id: int, user):
        raise ValueError("not in pushed status")

    monkeypatch.setattr(
        "django_suppliers.services.change_log_service.push_service.force_repush_supplier_product", fake_force
    )
    result = change_log_service.force_repush_by_sku("FT-FAIL", user=None)
    assert result["processed_sp_ids"] == []
    assert len(result["failed"]) == 1
    assert "not in pushed status" in result["failed"][0]["reason"]
