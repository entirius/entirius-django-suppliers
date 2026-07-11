# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Integration tests for audit_log emission from process_delta_sync.

D5 cost defer means cost/currency/stock get `applied_to_pim=False` (pricing not yet
propagated); physical fields go directly to RealProduct so `applied_to_pim=True`.
"""

from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from django_suppliers.connectors.base import SyncConnector
from django_suppliers.enums import ChangeLogSource, ProductStatus
from django_suppliers.models import SupplierProductChangeLog
from django_suppliers.schemas.contract import PriceStockUpdate
from django_suppliers.services import import_service, log_service
from tests.factories import FeedFactory, SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db


def _run_delta(feed, updates):
    connector = MagicMock(spec=SyncConnector)
    connector.is_async = False
    connector.fetch_delta.return_value = iter(updates)
    log = log_service.start_import_log(feed, mode="delta")
    import_service.process_delta_sync(feed, connector, log)


def test_delta_cost_change_pushed_sp_audit_applied_to_pim_false():
    supplier = SupplierFactory()
    feed = FeedFactory(supplier=supplier, sync_mode="delta")
    SupplierProductFactory(
        supplier=supplier,
        feed=feed,
        external_id="SKU-D",
        cost=Decimal("100.00"),
        stock=10,
        status=ProductStatus.PUSHED.value,
    )
    _run_delta(feed, [PriceStockUpdate(external_id="SKU-D", cost=Decimal("110.00"), stock=12)])
    cost = SupplierProductChangeLog.objects.get(source=ChangeLogSource.DELTA_SYNC.value, field_path="cost")
    assert Decimal(cost.before) == Decimal("100.00")
    assert Decimal(cost.after) == Decimal("110.00")
    assert cost.applied_to_pim is False
    stock = SupplierProductChangeLog.objects.get(source=ChangeLogSource.DELTA_SYNC.value, field_path="stock")
    assert stock.before == 10
    assert stock.after == 12


def test_delta_physical_change_audit_applied_to_pim_true():
    """Physical fields (weight/ean/dims) update RealProduct directly → applied_to_pim=True.

    The SP MUST own a preferred ProductSupplierLink for the physical write to land;
    non-preferred / no-link branches go to the race-skip path now. In production this link
    is created by `upsert_for_push` during init push.
    """
    from django_pim.models.real_product import RealProduct

    from django_suppliers.models import ProductSupplierLink

    supplier = SupplierFactory()
    feed = FeedFactory(supplier=supplier, sync_mode="delta")
    rp = RealProduct.objects.create(sku="sku-delta-phys", weight=Decimal("1.00"))
    sp = SupplierProductFactory(supplier=supplier, feed=feed, external_id="SKU-E", status=ProductStatus.PUSHED.value)
    sp.real_product = rp
    sp.save(update_fields=["real_product"])
    ProductSupplierLink.objects.create(real_product_sku=rp.sku, supplier=supplier, is_preferred=True, is_active=True)
    _run_delta(feed, [PriceStockUpdate(external_id="SKU-E", physical={"weight": "2.50"})])
    phys = SupplierProductChangeLog.objects.get(field_path="physical.weight")
    assert phys.applied_to_pim is True
    assert phys.applied_to_pim_at is not None
    assert phys.real_product_sku == "sku-delta-phys"


def test_delta_unchanged_values_no_audit():
    supplier = SupplierFactory()
    feed = FeedFactory(supplier=supplier, sync_mode="delta")
    SupplierProductFactory(
        supplier=supplier,
        feed=feed,
        external_id="SKU-F",
        cost=Decimal("50.00"),
        stock=20,
        status=ProductStatus.PUSHED.value,
    )
    _run_delta(feed, [PriceStockUpdate(external_id="SKU-F", cost=Decimal("50.00"), stock=20)])
    assert SupplierProductChangeLog.objects.filter(source=ChangeLogSource.DELTA_SYNC.value).count() == 0
