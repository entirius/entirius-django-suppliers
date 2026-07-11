# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Signal handlers for django_suppliers.

Stage 4: image dispatch handler — listens to `supplier_product_pushed_signal`,
schedules per-channel image download via Celery (`transaction.on_commit` per Decyzja #32).

Stage 6: auto-push handler — listens to `supplier_products_imported_signal`,
schedules `push_approved_for_supplier_task` for suppliers with `review_mode='auto'`
(`transaction.on_commit` per Decyzja #32).
"""

from django.db import transaction

from django_suppliers.enums import ReviewMode
from django_suppliers.signals.killswitch import is_auto_push_enabled, is_suppressed


def on_supplier_product_pushed(sender, supplier_product, real_product_sku, channel_idx, **kwargs):  # noqa: ARG001
    """Dispatch image download task after push commit.

    Decyzja #32: `transaction.on_commit` ensures task is dispatched only after
    the surrounding push transaction COMMITS. Without it, a rollback after dispatch
    would leave the task running against a non-existent Product → image_failed cascade.
    """
    if is_suppressed():
        return
    if not supplier_product.image_urls:
        return  # status already 'pushed' — nothing to download

    sp_id = supplier_product.id
    from django_suppliers.tasks.image_download import download_supplier_images_task

    transaction.on_commit(lambda: download_supplier_images_task.delay(sp_id, channel_idx))


def on_supplier_products_imported(sender, feed, import_log, **kwargs):  # noqa: ARG001
    """Auto-push handler: enqueue push_approved_for_supplier_task after import commit.

    Skipped when:
      - signals suppressed (`is_suppressed()` thread-local context)
      - import_log.mode is not 'full' or 'delta' (e.g. 'test' init-feed sample)
      - SupplierSettings.auto_push_enabled is False (DB killswitch, cached 60s)
      - supplier.review_mode != 'auto' (manual review still required)

    Decyzja #32: `transaction.on_commit` ensures the task is dispatched only after
    the import-finalization transaction COMMITS. A rollback (failed ImportLog finalize)
    must NOT spawn an auto-push against a half-applied import.
    """
    if is_suppressed():
        return
    if import_log.mode not in ("full", "delta"):
        return
    if not is_auto_push_enabled():
        return
    supplier = feed.supplier
    if supplier.review_mode != ReviewMode.AUTO.value:
        return
    from django_suppliers.services import role_guard

    # Monitoring suppliers never push — don't even enqueue (preflight would refuse anyway).
    if role_guard.is_monitoring(supplier):
        return

    supplier_id = supplier.id
    from django_suppliers.tasks.push_pipeline import push_approved_for_supplier_task

    transaction.on_commit(lambda: push_approved_for_supplier_task.delay(supplier_id=supplier_id, user_id=None))
