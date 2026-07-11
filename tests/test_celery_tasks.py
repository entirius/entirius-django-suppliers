# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for tasks package re-exports.

Celery's autodiscover_tasks() walks the configured app list and imports
``django_suppliers.tasks``. Without explicit re-exports the submodule
decorators run only when something else (admin view, signal handler, test)
imports them by full path. tasks/__init__.py re-exports
all 5 @shared_task definitions so the worker sees them under the canonical
``django_suppliers.tasks.<name>`` paths.
"""

from unittest.mock import MagicMock, patch

import pytest
from django.core.cache import cache

from django_suppliers import tasks
from django_suppliers.enums import LogStatus
from django_suppliers.services import data_inspector_service
from django_suppliers.tasks.feed_execution import execute_feed_task

EXPECTED_TASKS = (
    "execute_feed_task",
    "download_supplier_images_task",
    "push_approved_for_supplier_task",
    "prune_supplier_events_task",
    "process_scraper_results_task",
    # auto-preferred batch evaluator (celery beat target).
    "evaluate_preferred_suppliers_task",
)


def test_all_tasks_reexported_on_package():
    """Importing django_suppliers.tasks must expose every @shared_task by short name."""
    for name in EXPECTED_TASKS:
        assert hasattr(tasks, name), f"django_suppliers.tasks is missing {name!r} re-export"


def test_reexported_tasks_are_callable_celery_tasks():
    """Each re-export must be a real Celery task object (has .delay / .apply_async)."""
    for name in EXPECTED_TASKS:
        task = getattr(tasks, name)
        assert callable(task), f"{name} re-export is not callable"
        assert hasattr(task, "delay"), f"{name} is not a Celery @shared_task (no .delay)"
        assert hasattr(task, "apply_async"), f"{name} is not a Celery @shared_task (no .apply_async)"


def test_all_listed_in_dunder_all():
    """__all__ must enumerate every re-exported task — keeps wildcard imports honest."""
    assert set(tasks.__all__) == set(EXPECTED_TASKS)


@pytest.mark.django_db
def test_execute_feed_task_invalidates_data_keys_on_success(language, currency):
    """A successful feed run must invalidate the data-keys cache for the supplier.

    Otherwise the CMS combobox keeps showing stale keys until TTL expires.
    """
    from .factories import FeedFactory

    feed = FeedFactory()
    cache.set(data_inspector_service.cache_key(feed.supplier.idx), {"sentinel": True}, 300)

    fake_log = MagicMock(status=LogStatus.SUCCESS.value, run_id="0" * 36)
    with patch("django_suppliers.tasks.feed_execution.import_service.execute_feed", return_value=fake_log):
        execute_feed_task.run(feed.pk)

    assert cache.get(data_inspector_service.cache_key(feed.supplier.idx)) is None


@pytest.mark.django_db
def test_execute_feed_task_keeps_cache_on_failure(language, currency):
    """If the feed run fails, SupplierProduct.data didn't change → cache stays warm."""
    from .factories import FeedFactory

    feed = FeedFactory()
    cache.set(data_inspector_service.cache_key(feed.supplier.idx), {"sentinel": True}, 300)

    fake_log = MagicMock(status=LogStatus.FAILED.value, run_id="0" * 36)
    with patch("django_suppliers.tasks.feed_execution.import_service.execute_feed", return_value=fake_log):
        execute_feed_task.run(feed.pk)

    assert cache.get(data_inspector_service.cache_key(feed.supplier.idx)) == {"sentinel": True}


@pytest.mark.django_db
def test_execute_feed_task_invalidates_data_values_on_success(language, currency):
    """A successful feed run must drop every cached data-values slot for the supplier.

    Source values change with each feed pull (new categories appear, old ones drop) — stale
    cache would mislead the operator's picker in CategoryMapping rows.
    """
    from .factories import FeedFactory

    feed = FeedFactory()
    supplier_idx = feed.supplier.idx
    # Two cached source_field slots + registry entry (mimics what list_values writes)
    cache.set(data_inspector_service.cache_key_values(supplier_idx, "category_path"), {"sentinel": "cat"}, 300)
    cache.set(data_inspector_service.cache_key_values(supplier_idx, "color"), {"sentinel": "col"}, 300)
    cache.set(data_inspector_service.cache_key_values_registry(supplier_idx), ["category_path", "color"], 600)

    fake_log = MagicMock(status=LogStatus.SUCCESS.value, run_id="0" * 36)
    with patch("django_suppliers.tasks.feed_execution.import_service.execute_feed", return_value=fake_log):
        execute_feed_task.run(feed.pk)

    assert cache.get(data_inspector_service.cache_key_values(supplier_idx, "category_path")) is None
    assert cache.get(data_inspector_service.cache_key_values(supplier_idx, "color")) is None
    assert cache.get(data_inspector_service.cache_key_values_registry(supplier_idx)) is None
