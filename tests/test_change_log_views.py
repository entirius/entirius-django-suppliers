# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Integration tests for PimSkuChangeLogViewSet.

Exercises the HTTP boundary: auth/permissions, request schema validation,
response shape contract, v2 error envelope on validation failures.
"""

import pytest

from django_suppliers.enums import ChangeLogSource
from django_suppliers.models import ProductSupplierLink, SupplierProductChangeLog
from django_suppliers.services import audit_service
from tests.factories import SupplierProductFactory

pytestmark = pytest.mark.django_db


def _emit(sp, *, sku, applied=False, field_path="cost"):
    return audit_service.log_change(
        supplier_product=sp,
        source=ChangeLogSource.DELTA_SYNC.value,
        field_path=field_path,
        before=1,
        after=2,
        applied_to_pim=applied,
        real_product_sku=sku,
    )


def _link(supplier, sku, *, is_preferred=False):
    return ProductSupplierLink.objects.create(
        real_product_sku=sku, supplier=supplier, is_preferred=is_preferred, is_active=True
    )


# ---------------------------------------------------------------------------
# GET /pim-sku/{sku}/changes/
# ---------------------------------------------------------------------------


class TestChangesForSku:
    def url(self, sku: str) -> str:
        return f"/api/suppliers/v2/admin/pim-sku/{sku}/changes/"

    def test_no_auth_returns_401(self, api_client):
        assert api_client.get(self.url("X")).status_code == 401

    def test_regular_user_returns_403(self, api_client, regular_user):
        api_client.force_authenticate(user=regular_user)
        assert api_client.get(self.url("X")).status_code == 403

    def test_unknown_sku_returns_404(self, admin_client):
        resp = admin_client.get(self.url("UNKNOWN-SKU"))
        assert resp.status_code == 404

    def test_happy_returns_timeline(self, admin_client):
        sp = SupplierProductFactory()
        _link(sp.supplier, "FT-1", is_preferred=True)
        _emit(sp, sku="FT-1")
        _emit(sp, sku="FT-1", field_path="stock")
        resp = admin_client.get(self.url("FT-1"))
        assert resp.status_code == 200
        assert resp.data["real_product_sku"] == "FT-1"
        assert resp.data["has_supplier"] is True
        assert resp.data["unseen_count"] == 2
        assert resp.data["supplier"]["idx"] == sp.supplier.idx
        assert len(resp.data["changes"]) == 2

    def test_unseen_only_filters_applied_rows(self, admin_client):
        sp = SupplierProductFactory()
        _link(sp.supplier, "FT-2")
        _emit(sp, sku="FT-2", applied=False)
        _emit(sp, sku="FT-2", applied=True, field_path="stock")
        resp = admin_client.get(self.url("FT-2") + "?unseen_only=true")
        assert resp.status_code == 200
        assert len(resp.data["changes"]) == 1
        assert resp.data["changes"][0]["applied_to_pim"] is False

    def test_invalid_since_returns_400(self, admin_client):
        sp = SupplierProductFactory()
        _link(sp.supplier, "FT-3")
        _emit(sp, sku="FT-3")
        resp = admin_client.get(self.url("FT-3") + "?since=not-a-date")
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# GET /pim-sku/has-changes/
# ---------------------------------------------------------------------------


class TestHasChangesBulk:
    URL = "/api/suppliers/v2/admin/pim-sku/has-changes/"

    def test_no_auth_returns_401(self, api_client):
        assert api_client.get(self.URL + "?skus=A").status_code == 401

    def test_missing_skus_returns_400(self, admin_client):
        resp = admin_client.get(self.URL)
        assert resp.status_code == 400

    def test_too_many_skus_returns_400(self, admin_client):
        skus = ",".join(f"S{i}" for i in range(150))
        resp = admin_client.get(f"{self.URL}?skus={skus}")
        assert resp.status_code == 400

    def test_happy_returns_map(self, admin_client):
        sp = SupplierProductFactory()
        _link(sp.supplier, "FT-A", is_preferred=True)
        _emit(sp, sku="FT-A", applied=False)
        resp = admin_client.get(f"{self.URL}?skus=FT-A,UNKNOWN")
        assert resp.status_code == 200
        assert set(resp.data["skus"]) == {"FT-A", "UNKNOWN"}
        assert resp.data["skus"]["FT-A"]["has_supplier"] is True
        assert resp.data["skus"]["FT-A"]["unseen_count"] == 1
        assert resp.data["skus"]["UNKNOWN"]["has_supplier"] is False


# ---------------------------------------------------------------------------
# POST /pim-sku/{sku}/acknowledge/
# ---------------------------------------------------------------------------


class TestAcknowledge:
    def url(self, sku: str) -> str:
        return f"/api/suppliers/v2/admin/pim-sku/{sku}/acknowledge/"

    def test_no_auth_returns_401(self, api_client):
        assert api_client.post(self.url("X"), {"all_unseen": True}, format="json").status_code == 401

    def test_validation_error_both_modes_returns_400_with_envelope(self, admin_client):
        resp = admin_client.post(self.url("FT-1"), {"change_ids": [1], "all_unseen": True}, format="json")
        assert resp.status_code == 400
        assert "debug_id" in resp.data

    def test_validation_error_neither_mode_returns_400(self, admin_client):
        resp = admin_client.post(self.url("FT-1"), {}, format="json")
        assert resp.status_code == 400

    def test_all_unseen_happy(self, admin_client):
        sp = SupplierProductFactory()
        _emit(sp, sku="FT-ACK", applied=False)
        _emit(sp, sku="FT-ACK", applied=False, field_path="stock")
        resp = admin_client.post(self.url("FT-ACK"), {"all_unseen": True}, format="json")
        assert resp.status_code == 200
        assert resp.data["acknowledged_count"] == 2
        assert resp.data["sku"] == "FT-ACK"
        assert SupplierProductChangeLog.objects.filter(real_product_sku="FT-ACK", applied_to_pim=False).count() == 0

    def test_change_ids_cross_sku_returns_404_or_400(self, admin_client):
        sp = SupplierProductFactory()
        other_sp = SupplierProductFactory()
        own = _emit(sp, sku="FT-OWN", applied=False)
        foreign = _emit(other_sp, sku="FT-OTHER", applied=False)
        resp = admin_client.post(self.url("FT-OWN"), {"change_ids": [own.pk, foreign.pk]}, format="json")
        # service raises ValueError → raise_as_drf maps "not found" → 404, others → 400
        # "do not belong" doesn't end with "not found", so → 400
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# POST /pim-sku/{sku}/force-repush/
# ---------------------------------------------------------------------------


class TestForceRepushBySku:
    def url(self, sku: str) -> str:
        return f"/api/suppliers/v2/admin/pim-sku/{sku}/force-repush/"

    def test_no_auth_returns_401(self, api_client):
        assert api_client.post(self.url("X"), {}, format="json").status_code == 401

    def test_no_active_links_returns_404(self, admin_client):
        resp = admin_client.post(self.url("UNKNOWN"), {}, format="json")
        assert resp.status_code == 404

    def test_happy_delegates_to_push_service(self, admin_client, monkeypatch):
        sp = SupplierProductFactory()
        _link(sp.supplier, "FT-FR")
        from django_pim.models.real_product import RealProduct

        rp = RealProduct.objects.create(sku="FT-FR")
        sp.real_product = rp
        sp.save(update_fields=["real_product"])

        monkeypatch.setattr(
            "django_suppliers.services.change_log_service.push_service.force_repush_supplier_product",
            lambda sp_id, user: ["c1", "c2"],
        )
        resp = admin_client.post(self.url("FT-FR"), {}, format="json")
        assert resp.status_code == 200
        assert resp.data["sku"] == "FT-FR"
        assert resp.data["processed_sp_ids"] == [sp.pk]
        assert resp.data["pushed_channels_count"] == 2
        assert resp.data["failed"] == []
