# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Integration tests for the preferred-switch admin endpoints.

POST /api/suppliers/v2/admin/pim-sku/{sku}/set-preferred-supplier/
POST /api/suppliers/v2/admin/pim-sku/{sku}/reset-preferred-to-auto/
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.urls import reverse
from django.utils import timezone

from django_suppliers.models import ProductSupplierLink
from tests.factories import SupplierFactory, SupplierProductFactory

pytestmark = pytest.mark.django_db


def _create_pim_real_product(sku="api-rp"):
    from django_pim.models.real_product import RealProduct

    return RealProduct.objects.create(sku=sku)


def _link(
    sku, supplier, external_id, *, cost, stock, is_preferred=False, preferred_changed_at=None, manual_override=False
):
    SupplierProductFactory(supplier=supplier, external_id=external_id, cost=Decimal(str(cost)), stock=stock)
    return ProductSupplierLink.objects.create(
        real_product_sku=sku,
        supplier=supplier,
        external_id=external_id,
        is_preferred=is_preferred,
        preferred_changed_at=preferred_changed_at,
        manual_override=manual_override,
    )


def test_set_preferred_supplier_happy_path(admin_client):
    _create_pim_real_product("api-rp-set")
    ft = SupplierFactory(idx="ft-set")
    kh = SupplierFactory(idx="kh-set")
    _link("api-rp-set", ft, "f1", cost="0.14", stock=100)
    kh_link = _link("api-rp-set", kh, "k1", cost="0.13", stock=100, is_preferred=True)
    url = reverse("admin-pim-sku-set-preferred-supplier", kwargs={"sku": "api-rp-set"})
    response = admin_client.post(url, {"supplier_idx": "ft-set", "reason": "strategic partner"}, format="json")
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["preferred_supplier_idx"] == "ft-set"
    assert data["previous_preferred_supplier_idx"] == "kh-set"
    assert data["manual_override"] is True
    assert isinstance(data["events"], list)
    kh_link.refresh_from_db()
    assert kh_link.is_preferred is False


def test_set_preferred_supplier_short_reason_400(admin_client):
    _create_pim_real_product("api-rp-bad")
    ft = SupplierFactory(idx="ft-bad")
    _link("api-rp-bad", ft, "f1", cost="0.14", stock=100)
    url = reverse("admin-pim-sku-set-preferred-supplier", kwargs={"sku": "api-rp-bad"})
    response = admin_client.post(url, {"supplier_idx": "ft-bad", "reason": "x"}, format="json")
    assert response.status_code == 400
    body = response.json()
    assert "debug_id" in body


def test_set_preferred_supplier_unknown_supplier_404(admin_client):
    _create_pim_real_product("api-rp-unk")
    ft = SupplierFactory(idx="ft-unk")
    _link("api-rp-unk", ft, "f1", cost="0.14", stock=100)
    url = reverse("admin-pim-sku-set-preferred-supplier", kwargs={"sku": "api-rp-unk"})
    response = admin_client.post(url, {"supplier_idx": "ghost-supplier", "reason": "test reason"}, format="json")
    assert response.status_code == 404
    assert "debug_id" in response.json()


def test_reset_preferred_to_auto_switches_after_clear(admin_client):
    _create_pim_real_product("api-rp-reset")
    ft = SupplierFactory(idx="ft-reset-api")
    kh = SupplierFactory(idx="kh-reset-api")
    ft_link = _link("api-rp-reset", ft, "f1", cost="0.20", stock=100, is_preferred=True, manual_override=True)
    _link("api-rp-reset", kh, "k1", cost="0.10", stock=100)
    url = reverse("admin-pim-sku-reset-preferred-to-auto", kwargs={"sku": "api-rp-reset"})
    response = admin_client.post(url, {}, format="json")
    assert response.status_code == 200, response.content
    data = response.json()
    assert data["switched"] is True
    assert data["new_preferred_supplier_idx"] == "kh-reset-api"
    assert data["previous_preferred_supplier_idx"] == "ft-reset-api"
    ft_link.refresh_from_db()
    assert ft_link.manual_override is False
    assert ft_link.is_preferred is False


def test_reset_preferred_to_auto_unknown_sku_404(admin_client):
    url = reverse("admin-pim-sku-reset-preferred-to-auto", kwargs={"sku": "non-existent-sku"})
    response = admin_client.post(url, {}, format="json")
    assert response.status_code == 404
    assert "debug_id" in response.json()


def test_set_preferred_requires_admin(api_client, regular_user):
    _create_pim_real_product("api-rp-auth")
    ft = SupplierFactory(idx="ft-auth")
    _link("api-rp-auth", ft, "f1", cost="0.14", stock=100)
    api_client.force_authenticate(user=regular_user)
    url = reverse("admin-pim-sku-set-preferred-supplier", kwargs={"sku": "api-rp-auth"})
    response = api_client.post(url, {"supplier_idx": "ft-auth", "reason": "test"}, format="json")
    assert response.status_code == 403


def test_set_preferred_emits_audit_row(admin_client):
    """Audit row appears on the SP of the new preferred supplier."""
    from django_suppliers.enums import ChangeLogSource
    from django_suppliers.models import SupplierProductChangeLog

    _create_pim_real_product("api-rp-audit")
    ft = SupplierFactory(idx="ft-audit")
    kh = SupplierFactory(idx="kh-audit")
    _link("api-rp-audit", ft, "f1", cost="0.14", stock=100)
    _link("api-rp-audit", kh, "k1", cost="0.13", stock=100, is_preferred=True)
    url = reverse("admin-pim-sku-set-preferred-supplier", kwargs={"sku": "api-rp-audit"})
    admin_client.post(url, {"supplier_idx": "ft-audit", "reason": "manual override audit"}, format="json")
    audits = SupplierProductChangeLog.objects.filter(
        real_product_sku="api-rp-audit", source=ChangeLogSource.MANUAL_OVERRIDE.value
    )
    assert audits.count() == 1


def test_reset_to_auto_no_change_when_only_one_link(admin_client):
    _create_pim_real_product("api-rp-only")
    ft = SupplierFactory(idx="ft-only")
    _link(
        "api-rp-only",
        ft,
        "f1",
        cost="0.14",
        stock=100,
        is_preferred=True,
        preferred_changed_at=timezone.now() - timedelta(hours=48),
    )
    url = reverse("admin-pim-sku-reset-preferred-to-auto", kwargs={"sku": "api-rp-only"})
    response = admin_client.post(url, {}, format="json")
    assert response.status_code == 200
    assert response.json()["switched"] is False
