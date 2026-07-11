# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Celery task: prune IntegrationEvent rows older than retention window."""

from celery import shared_task

from django_suppliers.models import SupplierSettings
from django_suppliers.services import event_service
from django_suppliers.settings import QUEUE_DEFAULT


@shared_task(name="django_suppliers.prune_supplier_events", queue=QUEUE_DEFAULT)
def prune_supplier_events_task() -> dict:
    settings = SupplierSettings.load()
    days = settings.integration_event_retention_days
    deleted = event_service.prune_older_than(days)
    return {"deleted": deleted, "retention_days": days}
