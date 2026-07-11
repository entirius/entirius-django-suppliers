# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stage 6: prune_supplier_events_task — retention coverage."""

from datetime import timedelta

import pytest
from django.utils import timezone

from django_suppliers.models import IntegrationEvent, Supplier, SupplierSettings
from django_suppliers.tasks.retention import prune_supplier_events_task


@pytest.fixture
def supplier(db, language, currency):
    return Supplier.objects.create(idx="sup-r", name="R", default_language=language, default_currency=currency)


def _create_old_event(supplier, *, days_old: int, severity: str = "info") -> IntegrationEvent:
    ev = IntegrationEvent.objects.create(
        event_type="push_succeeded", severity=severity, supplier=supplier, message="test"
    )
    cutoff = timezone.now() - timedelta(days=days_old)
    IntegrationEvent.objects.filter(pk=ev.pk).update(created_at=cutoff)
    return ev


@pytest.mark.django_db
def test_prune_default_retention_90_keeps_recent(supplier):
    _create_old_event(supplier, days_old=10)
    result = prune_supplier_events_task()
    assert result["retention_days"] == 90
    assert result["deleted"] == 0


@pytest.mark.django_db
def test_prune_with_custom_retention(supplier):
    settings = SupplierSettings.load()
    settings.integration_event_retention_days = 30
    settings.save()
    _create_old_event(supplier, days_old=45)
    _create_old_event(supplier, days_old=10)
    result = prune_supplier_events_task()
    assert result["retention_days"] == 30
    assert result["deleted"] == 1


@pytest.mark.django_db
def test_prune_preserves_critical_events(supplier):
    settings = SupplierSettings.load()
    settings.integration_event_retention_days = 1
    settings.save()
    _create_old_event(supplier, days_old=30, severity="critical")
    _create_old_event(supplier, days_old=30, severity="warning")
    result = prune_supplier_events_task()
    assert result["deleted"] == 1  # only warning, critical preserved
    assert IntegrationEvent.objects.filter(severity="critical").exists()


@pytest.mark.django_db
def test_prune_returns_keys(supplier):
    result = prune_supplier_events_task()
    assert set(result.keys()) == {"deleted", "retention_days"}


@pytest.mark.django_db
def test_prune_zero_retention_deletes_non_critical(supplier):
    settings = SupplierSettings.load()
    settings.integration_event_retention_days = 0
    settings.save()
    _create_old_event(supplier, days_old=1, severity="info")
    _create_old_event(supplier, days_old=1, severity="critical")
    result = prune_supplier_events_task()
    assert result["deleted"] == 1


@pytest.mark.django_db
def test_prune_uses_load_get_or_create(db):
    SupplierSettings.objects.all().delete()
    result = prune_supplier_events_task()
    assert result["retention_days"] == 90  # default after get_or_create
