# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Tests for the auto-preferred Celery task + management command.

`evaluate_all` is the synchronous core that both `evaluate_preferred_suppliers_task`
(Celery beat target) and `python manage.py evaluate_preferred_suppliers` invoke.
Idempotency is exercised explicitly: two consecutive runs should not cause a flap.
"""

from datetime import timedelta
from decimal import Decimal
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone

from django_suppliers.enums import EvalFrequency
from django_suppliers.models import ProductSupplierLink
from django_suppliers.tasks.preferred_strategy import evaluate_all, evaluate_preferred_suppliers_task
from tests.factories import SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db


def _create_pim_real_product(sku):
    from django_pim.models.real_product import RealProduct

    return RealProduct.objects.create(sku=sku)


def _link(sku, supplier, external_id, *, cost, stock, is_preferred=False, preferred_changed_at=None):
    SupplierProductFactory(supplier=supplier, external_id=external_id, cost=Decimal(str(cost)), stock=stock)
    return ProductSupplierLink.objects.create(
        real_product_sku=sku,
        supplier=supplier,
        external_id=external_id,
        is_preferred=is_preferred,
        preferred_changed_at=preferred_changed_at,
    )


def test_evaluate_all_switches_multi_supplier_rp():
    _create_pim_real_product("cron-rp-1")
    ft = SupplierFactory(idx="ft-cron-1", preferred_switch_cooldown_hours=0)  # no cooldown
    kh = SupplierFactory(idx="kh-cron-1", preferred_switch_cooldown_hours=0)
    _link(
        "cron-rp-1",
        ft,
        "f1",
        cost="0.14",
        stock=100,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=48),
    )
    _link("cron-rp-1", kh, "k1", cost="0.10", stock=100)
    summary = evaluate_all()
    assert summary["evaluated"] >= 1
    assert summary["switched"] >= 1
    kh_link = ProductSupplierLink.objects.get(real_product_sku="cron-rp-1", supplier=kh)
    assert kh_link.is_preferred is True


def test_evaluate_all_skips_single_supplier_rp():
    _create_pim_real_product("cron-rp-single")
    ft = SupplierFactory(idx="ft-cron-single")
    _link("cron-rp-single", ft, "f1", cost="0.14", stock=100, is_preferred=True)
    summary = evaluate_all()
    # Single-supplier RPs don't show up in the iteration set at all.
    assert summary["evaluated"] == 0


def test_evaluate_all_is_idempotent():
    """Run twice — the second pass must register zero new switches."""
    _create_pim_real_product("cron-rp-idem")
    ft = SupplierFactory(idx="ft-cron-idem", preferred_switch_cooldown_hours=0)
    kh = SupplierFactory(idx="kh-cron-idem", preferred_switch_cooldown_hours=0)
    _link(
        "cron-rp-idem",
        ft,
        "f1",
        cost="0.14",
        stock=100,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=48),
    )
    _link("cron-rp-idem", kh, "k1", cost="0.10", stock=100)
    first = evaluate_all()
    second = evaluate_all()
    assert first["switched"] >= 1
    assert second["switched"] == 0


def test_evaluate_all_skips_manual_override_sku():
    _create_pim_real_product("cron-rp-manual")
    ft = SupplierFactory(idx="ft-cron-manual")
    kh = SupplierFactory(idx="kh-cron-manual")
    ft_link = _link("cron-rp-manual", ft, "f1", cost="0.14", stock=100, is_preferred=True)
    ft_link.manual_override = True
    ft_link.save(update_fields=["manual_override"])
    _link("cron-rp-manual", kh, "k1", cost="0.10", stock=100)
    summary = evaluate_all()
    assert summary["skipped_manual_override"] >= 1
    ft_link.refresh_from_db()
    assert ft_link.is_preferred is True  # unchanged


def test_evaluate_all_excludes_manual_frequency_suppliers():
    """A supplier with eval_frequency=manual opts the SKU out of cron."""
    _create_pim_real_product("cron-rp-freq")
    ft = SupplierFactory(
        idx="ft-cron-freq", eval_frequency=EvalFrequency.MANUAL.value, preferred_switch_cooldown_hours=0
    )
    kh = SupplierFactory(
        idx="kh-cron-freq", eval_frequency=EvalFrequency.MANUAL.value, preferred_switch_cooldown_hours=0
    )
    _link(
        "cron-rp-freq",
        ft,
        "f1",
        cost="0.14",
        stock=100,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=48),
    )
    _link("cron-rp-freq", kh, "k1", cost="0.10", stock=100)
    summary = evaluate_all()
    # Both suppliers are manual-only → the SKU is excluded from the iteration set.
    assert summary["evaluated"] == 0


def test_celery_task_invokes_evaluate_all():
    """Smoke test the Celery task entry — calls evaluate_all and returns the summary."""
    _create_pim_real_product("cron-rp-celery")
    ft = SupplierFactory(idx="ft-cron-celery", preferred_switch_cooldown_hours=0)
    kh = SupplierFactory(idx="kh-cron-celery", preferred_switch_cooldown_hours=0)
    _link(
        "cron-rp-celery",
        ft,
        "f1",
        cost="0.14",
        stock=100,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=48),
    )
    _link("cron-rp-celery", kh, "k1", cost="0.10", stock=100)
    result = evaluate_preferred_suppliers_task()
    assert "evaluated" in result and "switched" in result


def test_management_command_prints_summary():
    _create_pim_real_product("cron-rp-cmd")
    ft = SupplierFactory(idx="ft-cron-cmd")
    _link("cron-rp-cmd", ft, "f1", cost="0.14", stock=100, is_preferred=True)
    out = StringIO()
    call_command("evaluate_preferred_suppliers", stdout=out)
    output = out.getvalue()
    assert "Auto-preferred evaluation summary" in output
    assert "evaluated" in output


def test_management_command_with_sku_argument():
    _create_pim_real_product("cron-rp-cmd-sku")
    ft = SupplierFactory(idx="ft-cron-cmd-sku")
    _link("cron-rp-cmd-sku", ft, "f1", cost="0.14", stock=100, is_preferred=True)
    out = StringIO()
    call_command("evaluate_preferred_suppliers", "--sku", "cron-rp-cmd-sku", stdout=out)
    output = out.getvalue()
    assert "cron-rp-cmd-sku" in output
