# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Integration tests for process_delta_sync counts split + audit/event surface.

Unit-level branch coverage lives in `test_physical_race.py`. These tests exercise the
full delta batch path including the new `physical_*_count` keys and the regression
guard that `updated_count` stays cost/qty-only.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from django_suppliers.connectors.base import SyncConnector
from django_suppliers.enums import ChangeLogSource, EventType, ProductStatus
from django_suppliers.models import IntegrationEvent, ProductSupplierLink, SupplierProductChangeLog
from django_suppliers.schemas.contract import PriceStockUpdate
from django_suppliers.services import import_service, log_service
from tests.factories import FeedFactory, SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db


def _run_delta(feed, updates) -> dict:
    connector = MagicMock(spec=SyncConnector)
    connector.is_async = False
    connector.fetch_delta.return_value = iter(updates)
    log = log_service.start_import_log(feed, mode="delta")
    return import_service.process_delta_sync(feed, connector, log)


def _pushed_sp_with_rp(*, supplier, feed, external_id: str, sku: str, initial_weight: Decimal):
    from django_pim.models.real_product import RealProduct

    rp = RealProduct.objects.create(sku=sku, weight=initial_weight)
    sp = SupplierProductFactory(
        supplier=supplier,
        feed=feed,
        external_id=external_id,
        status=ProductStatus.PUSHED.value,
        cost=Decimal("10.00"),
        currency="EUR",
        stock=5,
    )
    sp.real_product = rp
    sp.save(update_fields=["real_product"])
    return sp, rp


def test_delta_sync_counts_split_physical_outcomes():
    """Two SPs in one delta batch — preferred applies, non-preferred skips.

    Locks the contract: `updated_count` reflects cost/qty change on the preferred SP only;
    `physical_updated_count` and `physical_skipped_non_preferred_count` are split.
    """
    preferred_supplier = SupplierFactory(idx="delta-race-pref")
    non_pref_supplier = SupplierFactory(idx="delta-race-non")
    feed_pref = FeedFactory(supplier=preferred_supplier, sync_mode="delta", idx="pref-feed")
    feed_non = FeedFactory(supplier=non_pref_supplier, sync_mode="delta", idx="non-feed")

    sp_pref, rp_pref = _pushed_sp_with_rp(
        supplier=preferred_supplier,
        feed=feed_pref,
        external_id="DELTA-PREF",
        sku="delta-pref-sku",
        initial_weight=Decimal("0.15"),
    )
    sp_non, rp_non = _pushed_sp_with_rp(
        supplier=non_pref_supplier,
        feed=feed_non,
        external_id="DELTA-NON",
        sku="delta-non-sku",
        initial_weight=Decimal("0.15"),
    )
    # Preferred link for sp_pref's RP.
    ProductSupplierLink.objects.create(
        real_product_sku=rp_pref.sku, supplier=preferred_supplier, is_preferred=True, is_active=True
    )
    # For sp_non: a preferred link belongs to a third supplier, sp_non itself is non-preferred.
    other_pref = SupplierFactory(idx="delta-race-other-pref")
    ProductSupplierLink.objects.create(
        real_product_sku=rp_non.sku, supplier=other_pref, is_preferred=True, is_active=True
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp_non.sku, supplier=non_pref_supplier, is_preferred=False, is_active=True
    )

    # Preferred batch: cost change (1) + physical weight change (1) → updated_count=1, physical_updated_count=1.
    counts_pref = _run_delta(
        feed_pref, [PriceStockUpdate(external_id="DELTA-PREF", cost=Decimal("12.00"), physical={"weight": "0.25"})]
    )
    assert counts_pref["updated_count"] == 1, "cost change increments updated_count"
    assert counts_pref["physical_updated_count"] == 1
    assert counts_pref["physical_skipped_non_preferred_count"] == 0
    assert counts_pref["physical_overwrite_count"] == 0
    rp_pref.refresh_from_db()
    assert rp_pref.weight == Decimal("0.25")

    # Non-preferred batch: only physical change → no updated_count change, skip increment.
    counts_non = _run_delta(feed_non, [PriceStockUpdate(external_id="DELTA-NON", physical={"weight": "2.50"})])
    assert counts_non["updated_count"] == 0, "physical-only delta MUST NOT bump updated_count"
    assert counts_non["physical_updated_count"] == 0
    assert counts_non["physical_skipped_non_preferred_count"] == 1
    assert counts_non["physical_overwrite_count"] == 0
    rp_non.refresh_from_db()
    assert rp_non.weight == Decimal("0.15"), "race-skipped RealProduct.weight unchanged"
    # Audit + event trail on the skip path.
    assert SupplierProductChangeLog.objects.filter(
        source=ChangeLogSource.PHYSICAL_SKIPPED.value, supplier_product=sp_non
    ).exists()
    assert IntegrationEvent.objects.filter(
        event_type=EventType.PHYSICAL_UPDATE_SKIPPED_NON_PREFERRED.value, supplier_product=sp_non
    ).exists()


def test_delta_sync_overwrite_path_counts_separately():
    """allow_physical_writes_from_non_preferred=True on the non-preferred supplier — write lands as overwrite."""
    preferred_supplier = SupplierFactory(idx="delta-overwrite-pref")
    opt_in_supplier = SupplierFactory(idx="delta-overwrite-optin", allow_physical_writes_from_non_preferred=True)
    feed = FeedFactory(supplier=opt_in_supplier, sync_mode="delta", idx="optin-feed")
    sp, rp = _pushed_sp_with_rp(
        supplier=opt_in_supplier,
        feed=feed,
        external_id="DELTA-OPTIN",
        sku="delta-overwrite-sku",
        initial_weight=Decimal("0.15"),
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=preferred_supplier, is_preferred=True, is_active=True
    )
    ProductSupplierLink.objects.create(
        real_product_sku=rp.sku, supplier=opt_in_supplier, is_preferred=False, is_active=True
    )

    counts = _run_delta(feed, [PriceStockUpdate(external_id="DELTA-OPTIN", physical={"weight": "3.00"})])

    assert counts["physical_overwrite_count"] == 1
    assert counts["physical_updated_count"] == 0
    assert counts["physical_skipped_non_preferred_count"] == 0
    rp.refresh_from_db()
    assert rp.weight == Decimal("3.00")
    assert SupplierProductChangeLog.objects.filter(
        source=ChangeLogSource.PHYSICAL_OVERWRITE.value, supplier_product=sp
    ).exists()
    assert IntegrationEvent.objects.filter(
        event_type=EventType.PHYSICAL_UPDATE_OVERWRITE.value, supplier_product=sp
    ).exists()
