# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Integration tests for GET /suppliers/{idx}/data-values/."""

import pytest
from django.core.cache import cache
from rest_framework.test import APIClient

from .factories import SupplierFactory, SupplierProductFactory

URL = "/api/suppliers/v2/admin/suppliers/{idx}/data-values/"


@pytest.fixture(autouse=True)
def _clear_cache():
    cache.clear()
    yield
    cache.clear()


@pytest.mark.django_db
def test_data_values_requires_auth(api_client: APIClient, language, currency):
    supplier = SupplierFactory(idx="auth-vsup", default_language=language, default_currency=currency)
    response = api_client.get(URL.format(idx=supplier.idx) + "?source_field=category_path")
    assert response.status_code == 401


@pytest.mark.django_db
def test_data_values_happy_path_returns_counts(admin_client: APIClient, language, currency):
    supplier = SupplierFactory(idx="happy-vsup", default_language=language, default_currency=currency)
    SupplierProductFactory(supplier=supplier, external_id="a", data={"category_path": "Pozostałe"})
    SupplierProductFactory(supplier=supplier, external_id="b", data={"category_path": "Pozostałe"})
    SupplierProductFactory(supplier=supplier, external_id="c", data={"category_path": "Dom"})

    response = admin_client.get(URL.format(idx=supplier.idx) + "?source_field=category_path")

    assert response.status_code == 200
    body = response.json()
    assert body["source_field"] == "category_path"
    assert body["truncated"] is False
    assert body["total_distinct"] is None
    assert body["sample_scope"] == "all"
    by_value = {v["value"]: v["count"] for v in body["values"]}
    assert by_value == {"Pozostałe": 2, "Dom": 1}


@pytest.mark.django_db
def test_data_values_unknown_supplier_returns_404(admin_client: APIClient):
    response = admin_client.get(URL.format(idx="ghost-supplier") + "?source_field=k")
    assert response.status_code == 404


@pytest.mark.django_db
def test_data_values_missing_source_field_returns_400(admin_client: APIClient, language, currency):
    supplier = SupplierFactory(idx="bad-sf-sup", default_language=language, default_currency=currency)
    response = admin_client.get(URL.format(idx=supplier.idx))
    assert response.status_code == 400


@pytest.mark.django_db
def test_data_values_invalid_limit_returns_400(admin_client: APIClient, language, currency):
    supplier = SupplierFactory(idx="bad-lim-sup", default_language=language, default_currency=currency)
    response = admin_client.get(URL.format(idx=supplier.idx) + "?source_field=k&limit=abc")
    assert response.status_code == 400
    response = admin_client.get(URL.format(idx=supplier.idx) + "?source_field=k&limit=0")
    assert response.status_code == 400
    response = admin_client.get(URL.format(idx=supplier.idx) + "?source_field=k&limit=10000")
    assert response.status_code == 400


@pytest.mark.django_db
def test_data_values_truncation_returns_total_distinct(admin_client: APIClient, language, currency):
    supplier = SupplierFactory(idx="trunc-vsup", default_language=language, default_currency=currency)
    for i in range(6):
        SupplierProductFactory(supplier=supplier, external_id=f"sku-{i}", data={"k": f"v-{i}"})

    response = admin_client.get(URL.format(idx=supplier.idx) + "?source_field=k&limit=2")

    assert response.status_code == 200
    body = response.json()
    assert body["truncated"] is True
    assert len(body["values"]) == 2
    assert body["total_distinct"] == 6


@pytest.mark.django_db
def test_data_values_unknown_source_field_returns_empty_values(admin_client: APIClient, language, currency):
    supplier = SupplierFactory(idx="empty-vsup", default_language=language, default_currency=currency)
    SupplierProductFactory(supplier=supplier, external_id="a", data={"other_key": "x"})

    response = admin_client.get(URL.format(idx=supplier.idx) + "?source_field=nonexistent")

    assert response.status_code == 200
    body = response.json()
    assert body["values"] == []
    assert body["truncated"] is False
