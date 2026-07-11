# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Unit tests for preferred_strategy_service."""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from django_suppliers.enums import ChangeLogSource, EventSeverity, EventType, PreferredSkipReason
from django_suppliers.models import IntegrationEvent, ProductSupplierLink, SupplierProductChangeLog
from django_suppliers.services import preferred_strategy_service
from tests.factories import SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db


def _create_pim_real_product(sku="rp-1"):
    from django_pim.models.real_product import RealProduct

    return RealProduct.objects.create(sku=sku)


def _link_supplier(sku, supplier, external_id, *, cost, stock, is_preferred=False, preferred_changed_at=None):
    """Helper: create SP + ProductSupplierLink with matching external_id."""
    sp = SupplierProductFactory(supplier=supplier, external_id=external_id, cost=Decimal(str(cost)), stock=stock)
    link = ProductSupplierLink.objects.create(
        real_product_sku=sku,
        supplier=supplier,
        external_id=external_id,
        is_preferred=is_preferred,
        preferred_changed_at=preferred_changed_at,
    )
    return sp, link


def test_evaluate_single_link_returns_no_candidates_skip():
    _create_pim_real_product("rp-single")
    sup = SupplierFactory(idx="ft")
    _link_supplier("rp-single", sup, "ft-1", cost="0.14", stock=100, is_preferred=True)
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-single")
    # Only one link → second supplier needed for a real eval; here winner == current
    # so the result is NO_CHANGE (the link IS the candidate).
    assert result.should_switch is False
    assert result.skip_reason == PreferredSkipReason.NO_CHANGE
    assert result.new_preferred.supplier.idx == "ft"


def test_evaluate_two_links_no_current_preferred_selects_lowest_cost():
    _create_pim_real_product("rp-2a")
    ft = SupplierFactory(idx="ft-2a")
    kh = SupplierFactory(idx="kh-2a")
    _link_supplier("rp-2a", ft, "ft-1", cost="0.14", stock=100)
    _link_supplier("rp-2a", kh, "kh-1", cost="0.13", stock=100)
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-2a")
    assert result.should_switch is True
    assert result.new_preferred.supplier.idx == "kh-2a"
    assert result.current_preferred is None


def test_evaluate_hysteresis_blocks_small_improvement():
    _create_pim_real_product("rp-hyst")
    ft = SupplierFactory(idx="ft-hyst", preferred_switch_hysteresis_pct=2)
    kh = SupplierFactory(idx="kh-hyst", preferred_switch_hysteresis_pct=2)
    _link_supplier("rp-hyst", ft, "ft-1", cost="0.140", stock=100, is_preferred=True)
    _link_supplier("rp-hyst", kh, "kh-1", cost="0.139", stock=100)  # 0.71% diff
    event_sink: list[dict] = []
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-hyst", event_sink=event_sink)
    assert result.should_switch is False
    assert result.skip_reason == PreferredSkipReason.HYSTERESIS
    assert any(e["event_type"] == EventType.PREFERRED_SWITCH_SKIPPED_HYSTERESIS.value for e in event_sink)


def test_evaluate_cost_improvement_above_hysteresis_returns_should_switch():
    _create_pim_real_product("rp-big")
    ft = SupplierFactory(idx="ft-big", preferred_switch_hysteresis_pct=2)
    kh = SupplierFactory(idx="kh-big", preferred_switch_hysteresis_pct=2)
    _link_supplier(
        "rp-big",
        ft,
        "ft-1",
        cost="0.14",
        stock=100,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=48),
    )
    _link_supplier("rp-big", kh, "kh-1", cost="0.13", stock=100)
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-big")
    assert result.should_switch is True
    assert result.new_preferred.supplier.idx == "kh-big"


def test_evaluate_cooldown_blocks_fresh_switch():
    _create_pim_real_product("rp-cool")
    ft = SupplierFactory(idx="ft-cool", preferred_switch_cooldown_hours=24)
    kh = SupplierFactory(idx="kh-cool", preferred_switch_cooldown_hours=24)
    _link_supplier(
        "rp-cool",
        ft,
        "ft-1",
        cost="0.20",
        stock=100,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=2),
    )
    _link_supplier("rp-cool", kh, "kh-1", cost="0.10", stock=100)  # 50% cheaper
    event_sink: list[dict] = []
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-cool", event_sink=event_sink)
    assert result.should_switch is False
    assert result.skip_reason == PreferredSkipReason.COOLDOWN
    assert any(e["event_type"] == EventType.PREFERRED_SWITCH_SKIPPED_COOLDOWN.value for e in event_sink)


def test_evaluate_cooldown_bypassed_in_emergency():
    _create_pim_real_product("rp-emerg")
    ft = SupplierFactory(idx="ft-emerg", preferred_switch_cooldown_hours=24)
    kh = SupplierFactory(idx="kh-emerg", preferred_switch_cooldown_hours=24)
    _link_supplier(
        "rp-emerg",
        ft,
        "ft-1",
        cost="0.20",
        stock=0,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=2),
    )
    _link_supplier("rp-emerg", kh, "kh-1", cost="0.10", stock=100)
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-emerg", bypass_safety=True)
    assert result.should_switch is True
    assert result.new_preferred.supplier.idx == "kh-emerg"


def test_evaluate_manual_override_sticky_blocks_eval():
    _create_pim_real_product("rp-manual")
    ft = SupplierFactory(idx="ft-manual")
    kh = SupplierFactory(idx="kh-manual")
    _, ft_link = _link_supplier("rp-manual", ft, "ft-1", cost="0.20", stock=100, is_preferred=True)
    ft_link.manual_override = True
    ft_link.save(update_fields=["manual_override"])
    _link_supplier("rp-manual", kh, "kh-1", cost="0.10", stock=100)
    event_sink: list[dict] = []
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-manual", event_sink=event_sink)
    assert result.should_switch is False
    assert result.skip_reason == PreferredSkipReason.MANUAL_OVERRIDE
    assert any(e["event_type"] == EventType.PREFERRED_SWITCH_SKIPPED_MANUAL_OVERRIDE.value for e in event_sink)


def test_evaluate_no_candidates_when_all_out_of_stock():
    _create_pim_real_product("rp-empty")
    ft = SupplierFactory(idx="ft-empty")
    kh = SupplierFactory(idx="kh-empty")
    _link_supplier("rp-empty", ft, "ft-1", cost="0.14", stock=0, is_preferred=True)
    _link_supplier("rp-empty", kh, "kh-1", cost="0.13", stock=0)
    result = preferred_strategy_service.evaluate_preferred_supplier("rp-empty")
    assert result.should_switch is False
    assert result.skip_reason == PreferredSkipReason.NO_CANDIDATES


def test_apply_preferred_switch_flips_atomically_and_audits():
    _create_pim_real_product("rp-apply")
    ft = SupplierFactory(idx="ft-apply")
    kh = SupplierFactory(idx="kh-apply")
    _, ft_link = _link_supplier("rp-apply", ft, "ft-1", cost="0.14", stock=100, is_preferred=True)
    _, kh_link = _link_supplier("rp-apply", kh, "kh-1", cost="0.13", stock=100)
    preferred_strategy_service.apply_preferred_switch(
        new_link=kh_link,
        previous_link=ft_link,
        reason_source=ChangeLogSource.AUTO_PREFERRED_SWITCH,
        event_type=EventType.PREFERRED_SUPPLIER_SWITCHED,
        severity=EventSeverity.INFO,
        decision_audit={"strategy": "lowest_cost_with_stock"},
    )
    ft_link.refresh_from_db()
    kh_link.refresh_from_db()
    assert ft_link.is_preferred is False
    assert kh_link.is_preferred is True
    assert kh_link.preferred_changed_at is not None
    assert (
        SupplierProductChangeLog.objects.filter(
            real_product_sku="rp-apply", source=ChangeLogSource.AUTO_PREFERRED_SWITCH.value
        ).count()
        == 1
    )
    assert IntegrationEvent.objects.filter(event_type=EventType.PREFERRED_SUPPLIER_SWITCHED.value).count() == 1


def test_force_set_preferred_sets_manual_override_and_emits_warning_event():
    _create_pim_real_product("rp-force")
    ft = SupplierFactory(idx="ft-force")
    kh = SupplierFactory(idx="kh-force")
    _, ft_link = _link_supplier("rp-force", ft, "ft-1", cost="0.14", stock=100)
    _, kh_link = _link_supplier("rp-force", kh, "kh-1", cost="0.13", stock=100, is_preferred=True)
    result = preferred_strategy_service.force_set_preferred(
        real_product_sku="rp-force", supplier_idx="ft-force", reason="strategic partner"
    )
    ft_link.refresh_from_db()
    kh_link.refresh_from_db()
    assert result.pk == ft_link.pk
    assert ft_link.is_preferred is True
    assert ft_link.manual_override is True
    assert kh_link.is_preferred is False
    assert kh_link.manual_override is False
    assert IntegrationEvent.objects.filter(event_type=EventType.PREFERRED_SUPPLIER_FORCED.value).count() == 1


def test_force_set_preferred_rejects_short_reason():
    _create_pim_real_product("rp-force-bad")
    ft = SupplierFactory(idx="ft-force-bad")
    _link_supplier("rp-force-bad", ft, "ft-1", cost="0.14", stock=100)
    with pytest.raises(ValueError, match="reason"):
        preferred_strategy_service.force_set_preferred(
            real_product_sku="rp-force-bad", supplier_idx="ft-force-bad", reason="ok"
        )


def test_reset_to_auto_clears_manual_override_and_re_evaluates():
    _create_pim_real_product("rp-reset")
    ft = SupplierFactory(idx="ft-reset")
    kh = SupplierFactory(idx="kh-reset")
    _, ft_link = _link_supplier("rp-reset", ft, "ft-1", cost="0.20", stock=100, is_preferred=True)
    ft_link.manual_override = True
    ft_link.save(update_fields=["manual_override"])
    _, kh_link = _link_supplier("rp-reset", kh, "kh-1", cost="0.10", stock=100)
    result = preferred_strategy_service.reset_to_auto(real_product_sku="rp-reset")
    ft_link.refresh_from_db()
    kh_link.refresh_from_db()
    assert ft_link.manual_override is False
    assert ft_link.is_preferred is False
    assert kh_link.is_preferred is True
    assert result.should_switch is True
    assert result.new_preferred.supplier.idx == "kh-reset"


def test_iter_multi_supplier_skus_returns_only_multi_link():
    _create_pim_real_product("rp-multi")
    _create_pim_real_product("rp-single")
    sup_a = SupplierFactory(idx="sup-a")
    sup_b = SupplierFactory(idx="sup-b")
    _link_supplier("rp-multi", sup_a, "a-1", cost="0.1", stock=10)
    _link_supplier("rp-multi", sup_b, "b-1", cost="0.2", stock=20)
    _link_supplier("rp-single", sup_a, "a-2", cost="0.3", stock=30)
    skus = preferred_strategy_service.iter_multi_supplier_real_product_skus()
    assert "rp-multi" in skus
    assert "rp-single" not in skus
