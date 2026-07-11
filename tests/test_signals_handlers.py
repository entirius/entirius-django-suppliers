# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stage 6: auto-push signal handler.

Tests cover review_mode gating, killswitch (auto_push_enabled, suppress_supplier_signals),
mode='test' bypass, dispatch_uid registration, and decision #32 (transaction.on_commit
must NOT fire after a rollback).
"""

from unittest.mock import patch
from uuid import uuid4

import pytest
from django.test import TestCase

from django_suppliers.enums import LogStatus
from django_suppliers.models import ImportLog, Supplier, SupplierFeed, SupplierSettings
from django_suppliers.signals.definitions import supplier_products_imported_signal
from django_suppliers.signals.handlers import on_supplier_products_imported
from django_suppliers.signals.killswitch import suppress_supplier_signals


@pytest.fixture
def auto_supplier(db, language, currency):
    return Supplier.objects.create(
        idx="auto-sup", name="Auto", default_language=language, default_currency=currency, review_mode="auto"
    )


@pytest.fixture
def manual_supplier(db, language, currency):
    return Supplier.objects.create(
        idx="manual-sup", name="Manual", default_language=language, default_currency=currency, review_mode="manual"
    )


def _make_log(supplier, *, mode: str = "full") -> tuple[SupplierFeed, ImportLog]:
    feed = SupplierFeed.objects.create(
        supplier=supplier, idx=f"f-{uuid4().hex[:6]}", connector_kind="xml_feed", feed_config={}
    )
    log = ImportLog.objects.create(feed=feed, run_id=uuid4(), mode=mode, source="api", status=LogStatus.SUCCESS.value)
    return feed, log


@pytest.mark.django_db
def test_review_mode_auto_dispatches_after_commit(auto_supplier):
    feed, log = _make_log(auto_supplier)
    with patch("django_suppliers.tasks.push_pipeline.push_approved_for_supplier_task.delay") as mock_delay:
        with TestCase.captureOnCommitCallbacks(execute=True):
            on_supplier_products_imported(sender=None, feed=feed, import_log=log)
        assert mock_delay.call_count == 1
        mock_delay.assert_called_with(supplier_id=auto_supplier.id, user_id=None)


@pytest.mark.django_db
def test_review_mode_manual_does_not_dispatch(manual_supplier):
    feed, log = _make_log(manual_supplier)
    with patch("django_suppliers.tasks.push_pipeline.push_approved_for_supplier_task.delay") as mock_delay:
        with TestCase.captureOnCommitCallbacks(execute=True):
            on_supplier_products_imported(sender=None, feed=feed, import_log=log)
        assert mock_delay.call_count == 0


@pytest.mark.django_db
def test_killswitch_auto_push_disabled(auto_supplier):
    s = SupplierSettings.load()
    s.auto_push_enabled = False
    s.save()
    from django_suppliers.signals import killswitch as ks

    ks.invalidate_auto_push_cache()
    feed, log = _make_log(auto_supplier)
    with patch("django_suppliers.tasks.push_pipeline.push_approved_for_supplier_task.delay") as mock_delay:
        with TestCase.captureOnCommitCallbacks(execute=True):
            on_supplier_products_imported(sender=None, feed=feed, import_log=log)
        assert mock_delay.call_count == 0


@pytest.mark.django_db
def test_suppression_context_skips_handler(auto_supplier):
    feed, log = _make_log(auto_supplier)
    with patch("django_suppliers.tasks.push_pipeline.push_approved_for_supplier_task.delay") as mock_delay:
        with TestCase.captureOnCommitCallbacks(execute=True):
            with suppress_supplier_signals():
                on_supplier_products_imported(sender=None, feed=feed, import_log=log)
        assert mock_delay.call_count == 0


@pytest.mark.django_db
def test_mode_test_does_not_dispatch(auto_supplier):
    feed, log = _make_log(auto_supplier, mode="test")
    with patch("django_suppliers.tasks.push_pipeline.push_approved_for_supplier_task.delay") as mock_delay:
        with TestCase.captureOnCommitCallbacks(execute=True):
            on_supplier_products_imported(sender=None, feed=feed, import_log=log)
        assert mock_delay.call_count == 0


@pytest.mark.django_db
def test_signal_handler_connected_with_dispatch_uid(db):
    uids = {r[0][0] for r in supplier_products_imported_signal.receivers}
    assert "django_suppliers.auto_push" in uids


@pytest.mark.django_db
def test_on_commit_does_not_fire_on_rollback(auto_supplier):
    """Decision #32: rollback discards on_commit callbacks → task.delay() NOT invoked.

    Pattern: captureOnCommitCallbacks(execute=True) + transaction.atomic() that raises;
    rollback discards the registered callbacks before they would have fired.
    """
    from django.db import transaction

    feed, log = _make_log(auto_supplier)
    with patch("django_suppliers.tasks.push_pipeline.push_approved_for_supplier_task.delay") as mock_delay:
        try:
            with TestCase.captureOnCommitCallbacks(execute=True):
                with transaction.atomic():
                    on_supplier_products_imported(sender=None, feed=feed, import_log=log)
                    raise RuntimeError("force rollback")
        except RuntimeError:
            pass
        assert mock_delay.call_count == 0
