# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Integration tests for pim_writer.init_push_to_channel auto-EAN-match branch.

Verifies the end-to-end flow: SP with EAN matching an existing RealProduct gets attached
via ProductSupplierLink instead of creating a duplicate; tolerance violation falls back
to a fresh RealProduct + warning event; per-supplier opt-out skips the lookup entirely;
empty EAN behaves like the legacy single-supplier flow.
"""

from decimal import Decimal

import pytest
from django_pim.models.real_product import RealProduct

from django_suppliers.enums import ChangeLogSource, EventType
from django_suppliers.models import (
    AttributeMappingTargetType,
    IntegrationEvent,
    ProductSupplierLink,
    SupplierProductChangeLog,
)
from django_suppliers.services import pim_writer
from tests.factories import AttributeMappingFactory, MappingProfileFactory, SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db

_EAN = "5906214804074"


def _register_feature_in_feature_set(feature, feature_set):
    from django_pim.models.feature_set import FeatureInFeatureSet

    FeatureInFeatureSet.objects.get_or_create(feature=feature, feature_set=feature_set)


def _build_push_scaffolding(
    language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
):
    """Helper: channel + feature_set + name feature wiring + AttributeMapping factory closure."""
    from django_pim.models.feature import FeatureTypeEnum

    channel = pim_channel_factory("ch-auto-link")
    fs = pim_feature_set_factory("fs-auto-link")
    feat = pim_typed_feature_factory("nm-al", int(FeatureTypeEnum.TEXT))
    _register_feature_in_feature_set(feat, fs)
    return channel, fs, feat


def _make_supplier_with_profile(language, currency, channel, fs, feat, **supplier_kwargs):
    sup = SupplierFactory(
        default_language=language, default_currency=currency, default_feature_set_idx=fs.idx, **supplier_kwargs
    )
    profile = MappingProfileFactory(supplier=sup, target_channel_idxs=[channel.idx], feature_set_idx=fs.idx)
    AttributeMappingFactory(
        profile=profile,
        source_field="__name__",
        target_type=AttributeMappingTargetType.FEATURE.value,
        target_identifier=feat.idx,
    )
    # weight mapping → exercises tolerance comparison via _real_product_defaults
    AttributeMappingFactory(
        profile=profile,
        source_field="weight",
        target_type=AttributeMappingTargetType.REAL_PRODUCT.value,
        target_identifier="weight",
    )
    return sup, profile


def test_first_supplier_creates_new_rp_and_first_link_preferred(
    admin_user, language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
):
    channel, fs, feat = _build_push_scaffolding(
        language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
    )
    sup, profile = _make_supplier_with_profile(language, currency, channel, fs, feat, sku_prefix="FT")
    sp = SupplierProductFactory(supplier=sup, external_id="6620", ean=_EAN, data={"weight": "0.150"})

    pim_writer.init_push_to_channel(sp, profile, channel.idx, admin_user)

    rps = RealProduct.objects.filter(ean=_EAN)
    assert rps.count() == 1
    link = ProductSupplierLink.objects.get(real_product_sku=rps.first().sku, supplier=sup)
    assert link.is_preferred is True
    # Standard SKU prefix path used (no existing RealProduct to match against).
    assert rps.first().sku.startswith("FT-")
    # No auto-link event because there was nothing to match.
    assert not IntegrationEvent.objects.filter(event_type=EventType.AUTO_LINKED_TO_EXISTING_REALPRODUCT.value).exists()


def test_second_supplier_auto_links_existing_and_non_preferred(
    admin_user, language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
):
    channel, fs, feat = _build_push_scaffolding(
        language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
    )
    # Supplier A pushes first → creates RP + preferred link.
    sup_a, profile_a = _make_supplier_with_profile(language, currency, channel, fs, feat, sku_prefix="FT")
    sp_a = SupplierProductFactory(supplier=sup_a, external_id="6620", ean=_EAN, data={"weight": "0.150"})
    pim_writer.init_push_to_channel(sp_a, profile_a, channel.idx, admin_user)
    rp = RealProduct.objects.get(ean=_EAN)
    rp_count_before = RealProduct.objects.count()

    # Supplier B with same EAN + weight within tolerance.
    sup_b, profile_b = _make_supplier_with_profile(language, currency, channel, fs, feat, sku_prefix="KH")
    # Different channel for sp_b to avoid Product collision on the same channel.
    other_channel = pim_channel_factory("ch-auto-link-b")
    profile_b.target_channel_idxs = [other_channel.idx]
    profile_b.save()
    sp_b = SupplierProductFactory(supplier=sup_b, external_id="12345", ean=_EAN, data={"weight": "0.155"})  # 3.3% diff

    pim_writer.init_push_to_channel(sp_b, profile_b, other_channel.idx, admin_user)

    # No new RealProduct created — auto-linked to existing one.
    assert RealProduct.objects.count() == rp_count_before
    sp_b.refresh_from_db()
    assert sp_b.real_product_id == rp.id

    # ProductSupplierLink for sup_b created with is_preferred=False (auto-link semantics).
    link_b = ProductSupplierLink.objects.get(real_product_sku=rp.sku, supplier=sup_b)
    assert link_b.is_preferred is False
    # sup_a link still preferred (D25 — never reset on update).
    link_a = ProductSupplierLink.objects.get(real_product_sku=rp.sku, supplier=sup_a)
    assert link_a.is_preferred is True

    # Auto-link event recorded with diagnostic details.
    events = IntegrationEvent.objects.filter(event_type=EventType.AUTO_LINKED_TO_EXISTING_REALPRODUCT.value)
    assert events.count() == 1
    event = events.first()
    assert event.details["ean"] == _EAN
    assert event.details["matched_sku"] == rp.sku
    assert event.details["new_supplier_idx"] == sup_b.idx
    assert sup_a.idx in event.details["existing_supplier_idxs"]

    # Audit row written with source=auto_link.
    audit_rows = SupplierProductChangeLog.objects.filter(supplier_product=sp_b, source=ChangeLogSource.AUTO_LINK.value)
    assert audit_rows.count() == 1
    assert audit_rows.first().after["matched_via"] == "ean"


def test_tolerance_fail_creates_new_rp_and_warning_event(
    admin_user, language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
):
    channel, fs, feat = _build_push_scaffolding(
        language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
    )
    # Seed an existing RP+link with weight 0.150 (sup_a).
    sup_a, profile_a = _make_supplier_with_profile(language, currency, channel, fs, feat, sku_prefix="FT")
    sp_a = SupplierProductFactory(supplier=sup_a, external_id="6620", ean=_EAN, data={"weight": "0.150"})
    pim_writer.init_push_to_channel(sp_a, profile_a, channel.idx, admin_user)

    # Supplier B same EAN but vastly different weight (2.5 vs 0.150 → ~94% diff).
    sup_b, profile_b = _make_supplier_with_profile(language, currency, channel, fs, feat, sku_prefix="KH")
    other_channel = pim_channel_factory("ch-tol-fail-b")
    profile_b.target_channel_idxs = [other_channel.idx]
    profile_b.save()
    sp_b = SupplierProductFactory(supplier=sup_b, external_id="99999", ean=_EAN, data={"weight": "2.500"})

    pim_writer.init_push_to_channel(sp_b, profile_b, other_channel.idx, admin_user)

    # Two RealProducts exist now (fallback created a new one).
    assert RealProduct.objects.filter(ean=_EAN).count() == 2
    # Warning event surfaces the tolerance failure with details.
    events = IntegrationEvent.objects.filter(event_type=EventType.PHYSICAL_TOLERANCE_VIOLATION.value)
    assert events.count() == 1
    event = events.first()
    assert "weight" in event.details["failed_fields"]
    assert event.details["tolerance_pct"] == 10
    # No auto_link audit (we fell back).
    assert not SupplierProductChangeLog.objects.filter(
        supplier_product=sp_b, source=ChangeLogSource.AUTO_LINK.value
    ).exists()


def test_disable_ean_auto_link_always_creates_new_rp(
    admin_user, language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
):
    channel, fs, feat = _build_push_scaffolding(
        language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
    )
    sup_a, profile_a = _make_supplier_with_profile(language, currency, channel, fs, feat, sku_prefix="FT")
    sp_a = SupplierProductFactory(supplier=sup_a, external_id="6620", ean=_EAN, data={"weight": "0.150"})
    pim_writer.init_push_to_channel(sp_a, profile_a, channel.idx, admin_user)

    # Supplier B has disable_ean_auto_link=True → skip lookup entirely.
    sup_b, profile_b = _make_supplier_with_profile(
        language, currency, channel, fs, feat, sku_prefix="KH", disable_ean_auto_link=True
    )
    other_channel = pim_channel_factory("ch-disable-b")
    profile_b.target_channel_idxs = [other_channel.idx]
    profile_b.save()
    sp_b = SupplierProductFactory(supplier=sup_b, external_id="12345", ean=_EAN, data={"weight": "0.151"})

    pim_writer.init_push_to_channel(sp_b, profile_b, other_channel.idx, admin_user)

    # Two RealProducts despite weight being well within tolerance.
    assert RealProduct.objects.filter(ean=_EAN).count() == 2
    assert not IntegrationEvent.objects.filter(event_type=EventType.AUTO_LINKED_TO_EXISTING_REALPRODUCT.value).exists()
    # Bonus: opt-out supplier's link is preferred (it's the only link for the new RP).
    link_b = ProductSupplierLink.objects.get(supplier=sup_b)
    assert link_b.is_preferred is True


def test_empty_ean_falls_back_to_standard_flow(
    admin_user, language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
):
    channel, fs, feat = _build_push_scaffolding(
        language, currency, pim_channel_factory, pim_feature_set_factory, pim_typed_feature_factory
    )
    # Existing RP shares an EAN with no SP.
    RealProduct.objects.create(sku="OTHER-001", ean=_EAN, weight=Decimal("0.150"))

    sup, profile = _make_supplier_with_profile(language, currency, channel, fs, feat, sku_prefix="EMP")
    sp = SupplierProductFactory(supplier=sup, external_id="empty-ean-1", ean="", data={"weight": "0.151"})

    pim_writer.init_push_to_channel(sp, profile, channel.idx, admin_user)

    # SP without an EAN gets the standard prefix-based SKU path.
    sp.refresh_from_db()
    assert sp.real_product.sku.startswith("EMP-")
    assert sp.real_product.sku != "OTHER-001"
    assert not IntegrationEvent.objects.filter(event_type=EventType.AUTO_LINKED_TO_EXISTING_REALPRODUCT.value).exists()
