# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Retention coverage for prune_supplier_change_logs_task + management command."""

from datetime import timedelta

import pytest
from django.core.management import call_command
from django.utils import timezone

from django_suppliers.enums import ChangeLogSource
from django_suppliers.models import SupplierProductChangeLog, SupplierSettings
from django_suppliers.services import audit_service
from django_suppliers.tasks.audit_retention import prune_supplier_change_logs_task
from tests.factories import SupplierProductFactory

pytestmark = pytest.mark.django_db


def _make_old_entry(sp, days_old: int) -> SupplierProductChangeLog:
    entry = audit_service.log_change(
        supplier_product=sp, source=ChangeLogSource.FULL_SYNC.value, field_path="cost", before=1, after=2
    )
    SupplierProductChangeLog.objects.filter(pk=entry.pk).update(created_at=timezone.now() - timedelta(days=days_old))
    return entry


def test_prune_task_uses_settings_retention_window():
    sp = SupplierProductFactory()
    settings = SupplierSettings.load()
    settings.change_log_retention_days = 30
    settings.save()
    _make_old_entry(sp, days_old=45)  # to delete
    _make_old_entry(sp, days_old=10)  # to keep
    result = prune_supplier_change_logs_task()
    assert result == {"deleted": 1, "retention_days": 30}
    assert SupplierProductChangeLog.objects.count() == 1


def test_prune_command_with_days_override_zero_deletes_everything():
    sp = SupplierProductFactory()
    audit_service.log_change(
        supplier_product=sp, source=ChangeLogSource.DELTA_SYNC.value, field_path="stock", before=1, after=2
    )
    call_command("prune_supplier_change_logs", "--days", "0")
    assert SupplierProductChangeLog.objects.count() == 0
