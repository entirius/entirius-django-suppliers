# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from django_suppliers.enums import EventSeverity, EventType
from django_suppliers.models import IntegrationEvent, Supplier, SupplierSettings
from tests.factories import CurrencyFactory, LanguageFactory, SupplierFactory


@pytest.mark.django_db
def test_supplier_can_be_created_and_fetched():
    supplier = SupplierFactory(idx="test-1", name="Test One")
    assert Supplier.objects.get(idx="test-1") == supplier


@pytest.mark.django_db
def test_supplier_duplicate_idx_raises_integrity_error():
    SupplierFactory(idx="dup")
    with pytest.raises(IntegrityError), transaction.atomic():
        Supplier.objects.create(
            idx="dup", name="Other", default_language=LanguageFactory(), default_currency=CurrencyFactory()
        )


@pytest.mark.django_db
def test_supplier_settings_load_returns_pk_one():
    settings = SupplierSettings.load()
    assert settings.pk == 1


@pytest.mark.django_db
def test_supplier_settings_load_is_idempotent():
    a = SupplierSettings.load()
    b = SupplierSettings.load()
    assert a.pk == b.pk == 1
    assert SupplierSettings.objects.count() == 1


@pytest.mark.django_db
def test_supplier_settings_save_forces_pk_one():
    SupplierSettings.load()
    instance = SupplierSettings(pk=2, auto_push_enabled=False)
    instance.save()
    assert instance.pk == 1
    assert SupplierSettings.objects.count() == 1


@pytest.mark.django_db
def test_supplier_settings_defaults():
    settings = SupplierSettings.load()
    assert settings.auto_push_enabled is True
    assert settings.scraper_dispatch_enabled is True
    assert settings.delta_sync_enabled is True
    assert settings.integration_event_retention_days == 90


@pytest.mark.django_db
def test_supplier_qty_subtract_negative_rejected():
    supplier = SupplierFactory(idx="neg-sub", qty_subtract=-1)
    with pytest.raises(ValidationError):
        supplier.full_clean()


@pytest.mark.django_db
def test_supplier_qty_minimum_negative_rejected():
    supplier = SupplierFactory(idx="neg-min", qty_minimum=-1)
    with pytest.raises(ValidationError):
        supplier.full_clean()


@pytest.mark.django_db
def test_supplier_defaults():
    supplier = SupplierFactory(idx="defaults")
    assert supplier.supplier_role == "trade"
    assert supplier.supplier_type == "feed"
    assert supplier.review_mode == "manual"
    assert supplier.is_active is True


@pytest.mark.django_db
def test_integration_event_can_be_created_with_minimal_args():
    event = IntegrationEvent.objects.create(
        event_type=EventType.IMPORT_COMPLETED.value, severity=EventSeverity.INFO.value, message="Import completed"
    )
    assert event.pk is not None


@pytest.mark.django_db
def test_integration_event_details_default_is_empty_dict():
    event = IntegrationEvent.objects.create(
        event_type=EventType.IMPORT_COMPLETED.value, severity=EventSeverity.INFO.value, message="x"
    )
    assert event.details == {}


@pytest.mark.django_db
def test_integration_event_str_contains_type_and_severity():
    event = IntegrationEvent.objects.create(
        event_type=EventType.IMPORT_COMPLETED.value, severity=EventSeverity.INFO.value, message="x"
    )
    rendered = str(event)
    assert "import_completed" in rendered
    assert "info" in rendered
