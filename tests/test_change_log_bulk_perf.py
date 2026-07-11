# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Performance gate for the bulk has-changes endpoint.

Constraint: 100 SKU lookup must hit DB at most 3 times and complete
under 100ms when audit data is dense. The model index
`supp_changelog_sku_unseen_idx (real_product_sku, applied_to_pim, -created_at)`
makes the aggregate cheap; this test catches regressions if a future change
adds an N+1 or drops the FILTER annotation.
"""

import time

import pytest

from django_suppliers.enums import ChangeLogSource
from django_suppliers.models import ProductSupplierLink, SupplierProductChangeLog
from django_suppliers.services import change_log_service
from tests.factories import SupplierProductFactory

pytestmark = pytest.mark.django_db


def _seed_bulk(skus: list[str], rows_per_sku: int) -> None:
    sp = SupplierProductFactory()
    ProductSupplierLink.objects.bulk_create(
        [
            ProductSupplierLink(real_product_sku=sku, supplier=sp.supplier, is_active=True, is_preferred=False)
            for sku in skus
        ]
    )
    log_rows: list[SupplierProductChangeLog] = []
    for sku in skus:
        for i in range(rows_per_sku):
            log_rows.append(
                SupplierProductChangeLog(
                    supplier_product=sp,
                    real_product_sku=sku,
                    source=ChangeLogSource.DELTA_SYNC.value,
                    field_path=f"field-{i}",
                    before=i,
                    after=i + 1,
                    applied_to_pim=(i % 2 == 0),
                )
            )
    SupplierProductChangeLog.objects.bulk_create(log_rows, batch_size=500)


def test_bulk_has_changes_100_skus_few_queries(django_assert_max_num_queries):
    skus = [f"PERF-{i:04d}" for i in range(100)]
    _seed_bulk(skus, rows_per_sku=10)
    with django_assert_max_num_queries(3):
        result = change_log_service.bulk_has_changes(skus)
    assert len(result) == 100


def test_bulk_has_changes_100_skus_under_100ms():
    skus = [f"PERF-{i:04d}" for i in range(100)]
    _seed_bulk(skus, rows_per_sku=10)
    start = time.perf_counter()
    result = change_log_service.bulk_has_changes(skus)
    elapsed = time.perf_counter() - start
    assert len(result) == 100
    assert elapsed < 0.1, f"bulk_has_changes(100) took {elapsed * 1000:.1f}ms (>100ms)"
