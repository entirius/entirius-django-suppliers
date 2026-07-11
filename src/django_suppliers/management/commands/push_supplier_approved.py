# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Push approved SupplierProducts for a supplier (sync default; --async via Celery)."""

from django.core.management.base import BaseCommand, CommandError

from django_suppliers.models import Supplier
from django_suppliers.services import push_service


class Command(BaseCommand):
    help = "Push every approved SupplierProduct for a supplier."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--supplier-idx", type=str, required=True)
        parser.add_argument("--async", dest="run_async", action="store_true", default=False)

    def handle(self, *args, **options) -> None:
        supplier_idx = options["supplier_idx"]
        try:
            supplier = Supplier.objects.get(idx=supplier_idx)
        except Supplier.DoesNotExist as exc:
            raise CommandError(f"Supplier '{supplier_idx}' not found") from exc

        if options["run_async"]:
            from django_suppliers.tasks.push_pipeline import push_approved_for_supplier_task

            result = push_approved_for_supplier_task.delay(supplier_id=supplier.id, user_id=None)
            self.stdout.write(f"Dispatched async: task_id={result.id}, supplier='{supplier_idx}'")
            return

        try:
            counts = push_service.push_approved_for_supplier(supplier.id, user=None)
        except Exception as exc:  # noqa: BLE001
            raise CommandError(str(exc)) from exc
        self.stdout.write(
            f"supplier='{supplier_idx}' success={counts.get('success', 0)} failed={counts.get('failed', 0)} "
            f"preflight_failed={counts.get('preflight_failed', False)}"
        )
