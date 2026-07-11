# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_suppliers.models import IntegrationEvent
from django_suppliers.services import supplier_service
from tests.factories import CurrencyFactory, LanguageFactory, SupplierFactory


@pytest.mark.django_db
def test_list_suppliers_empty():
    assert list(supplier_service.list_suppliers()) == []


@pytest.mark.django_db
def test_list_suppliers_returns_all():
    SupplierFactory.create_batch(3)
    assert supplier_service.list_suppliers().count() == 3


@pytest.mark.django_db
def test_list_suppliers_filters_by_role():
    SupplierFactory(idx="trade-1")
    qs = supplier_service.list_suppliers(role="trade")
    assert qs.count() == 1
    assert qs.first().supplier_role == "trade"


@pytest.mark.django_db
def test_list_suppliers_filters_by_is_active():
    SupplierFactory(idx="active-1", is_active=True)
    SupplierFactory(idx="inactive-1", is_active=False)
    qs = supplier_service.list_suppliers(is_active=False)
    assert qs.count() == 1
    assert qs.first().idx == "inactive-1"


@pytest.mark.django_db
def test_list_suppliers_search_matches_idx_or_name():
    SupplierFactory(idx="amazon-de", name="Amazon Deutschland")
    SupplierFactory(idx="ikea-pl", name="IKEA Polska")
    matches = supplier_service.list_suppliers(search="amazon")
    assert matches.count() == 1
    assert matches.first().idx == "amazon-de"


@pytest.mark.django_db
def test_get_supplier_returns_existing():
    SupplierFactory(idx="exists")
    assert supplier_service.get_supplier("exists").idx == "exists"


@pytest.mark.django_db
def test_get_supplier_missing_raises():
    """C4: get_supplier raises ValueError (translated from DoesNotExist) for view layer."""
    with pytest.raises(ValueError, match="not found"):
        supplier_service.get_supplier("missing")


@pytest.mark.django_db
def test_create_supplier_creates_new():
    language = LanguageFactory()
    currency = CurrencyFactory()
    supplier = supplier_service.create_supplier(
        idx="new-supplier", name="New Supplier", default_language=language, default_currency=currency
    )
    assert supplier.idx == "new-supplier"


@pytest.mark.django_db
def test_create_supplier_duplicate_raises_value_error():
    SupplierFactory(idx="dup")
    with pytest.raises(ValueError):
        supplier_service.create_supplier(
            idx="dup", name="x", default_language=LanguageFactory(), default_currency=CurrencyFactory()
        )


@pytest.mark.django_db
def test_create_supplier_unsupported_role_raises_not_implemented():
    with pytest.raises(NotImplementedError):
        supplier_service.create_supplier(
            idx="data-role",
            name="x",
            supplier_role="data",
            default_language=LanguageFactory(),
            default_currency=CurrencyFactory(),
        )


@pytest.mark.django_db
def test_update_supplier_changes_name():
    SupplierFactory(idx="upd")
    updated = supplier_service.update_supplier("upd", name="New Name")
    assert updated.name == "New Name"


@pytest.mark.django_db
def test_delete_supplier_soft_is_idempotent():
    SupplierFactory(idx="soft")
    first = supplier_service.delete_supplier("soft", force=False)
    assert first == {"mode": "soft", "supplier_idx": "soft"}
    assert supplier_service.get_supplier("soft").is_active is False
    second = supplier_service.delete_supplier("soft", force=False)
    assert second == {"mode": "soft", "supplier_idx": "soft"}


@pytest.mark.django_db
def test_delete_supplier_force_emits_event_and_removes_supplier():
    SupplierFactory(idx="hard-1")
    result = supplier_service.delete_supplier("hard-1", force=True)
    assert result["mode"] == "hard"
    assert result["affected_links_count"] == 0
    assert result["affected_pushed_skus_count"] == 0
    with pytest.raises(ValueError, match="not found"):
        supplier_service.get_supplier("hard-1")
    events = IntegrationEvent.objects.filter(event_type="supplier_deleted")
    assert events.count() == 1
    event = events.first()
    assert event.severity == "warning"
    assert event.details["supplier_idx"] == "hard-1"
    assert event.details["affected_links_count"] == 0
    assert event.details["affected_pushed_skus"] == []


@pytest.mark.django_db
def test_delete_supplier_force_missing_raises():
    with pytest.raises(ValueError, match="not found"):
        supplier_service.delete_supplier("missing", force=True)


@pytest.mark.django_db
def test_update_supplier_rejects_unknown_field():
    """Mass-assignment defense: only whitelisted fields are editable."""
    SupplierFactory(idx="ms-1")
    with pytest.raises(ValueError, match="not editable"):
        supplier_service.update_supplier("ms-1", created_at="2020-01-01")
    with pytest.raises(ValueError, match="not editable"):
        supplier_service.update_supplier("ms-1", id=999)
