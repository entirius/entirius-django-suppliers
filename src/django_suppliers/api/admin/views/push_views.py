# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API view for bulk push (cross-supplier or single supplier).

C4: ZERO Django models imported here — all ORM via supplier_service / push_service.
"""

from django_utils.api.v2_errors import raise_pydantic_as_drf
from drf_spectacular.utils import extend_schema
from pydantic import ValidationError
from rest_framework import status
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_suppliers.api.admin.permissions import IsAdminUser
from django_suppliers.schemas.requests.push import BulkPushRequest
from django_suppliers.schemas.responses.product import BulkPushResponse
from django_suppliers.services import push_service, supplier_service

_TAGS = ["Supplier Push"]


class BulkPushView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "suppliers.products"

    @extend_schema(
        tags=_TAGS,
        summary="Push approved supplier products to PIM",
        description="Push every approved SP for one supplier (supplier_idx given) or all active suppliers.",
        request=BulkPushRequest,
        responses={200: BulkPushResponse, 400: None, 404: None},
    )
    def post(self, request: Request) -> Response:
        try:
            data = BulkPushRequest(**(request.data or {}))
        except ValidationError as exc:
            raise_pydantic_as_drf(exc)

        if data.supplier_idx:
            try:
                suppliers = [supplier_service.get_supplier(data.supplier_idx)]
            except Exception:  # noqa: BLE001 — Supplier.DoesNotExist; map to 404 without ORM import
                return Response(
                    {"detail": f"Supplier '{data.supplier_idx}' not found"}, status=status.HTTP_404_NOT_FOUND
                )
        else:
            suppliers = supplier_service.list_active_suppliers()

        total_success = 0
        total_failed = 0
        preflight_failed: list[str] = []
        for supplier in suppliers:
            result = push_service.push_approved_for_supplier(supplier.id, request.user)
            total_success += result.get("success", 0)
            total_failed += result.get("failed", 0)
            if result.get("preflight_failed"):
                preflight_failed.append(supplier.idx)
        return Response(
            {
                "suppliers_processed": len(suppliers),
                "success": total_success,
                "failed": total_failed,
                "preflight_failed": preflight_failed,
            }
        )
