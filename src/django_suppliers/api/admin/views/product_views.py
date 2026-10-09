# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API views for SupplierProduct (cross-supplier list + review/push actions).

C4: ZERO Django models imported here — all ORM via product_service / review_service / push_service / supplier_service.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django_utils.api.v2_errors import raise_pydantic_as_drf
from drf_spectacular.utils import OpenApiParameter, extend_schema
from pydantic import ValidationError
from rest_framework import exceptions as drf_exceptions
from rest_framework import viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_suppliers.api.admin.pagination import AdminPageNumberPagination
from django_suppliers.api.admin.permissions import IsAdminUser
from django_suppliers.api.admin.views._helpers import raise_as_drf
from django_suppliers.schemas.requests.product import (
    BulkApproveRequest,
    BulkRejectRequest,
    BulkRequeueRequest,
    SupplierProductPartialUpdateRequest,
)
from django_suppliers.schemas.responses.product import (
    BulkActionResponse,
    PushResponse,
    SupplierProductListResponse,
    SupplierProductResponse,
    UnlinkFromRealProductResponse,
)
from django_suppliers.services import (
    product_link_service,
    product_service,
    push_service,
    review_service,
    supplier_service,
)


def _serialize(sp) -> dict:
    return SupplierProductResponse.model_validate(sp).model_dump(mode="json")


_TAGS = ["Supplier Products"]


class SupplierProductViewSet(viewsets.ViewSet):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "suppliers.products"
    pagination_class = AdminPageNumberPagination
    serializer_class = None

    @extend_schema(
        tags=_TAGS,
        summary="List supplier products (cross-supplier)",
        parameters=[
            OpenApiParameter(
                "supplier", str, OpenApiParameter.QUERY, required=False, description="Filter by Supplier.idx"
            ),
            OpenApiParameter("status", str, OpenApiParameter.QUERY, required=False, description="Filter by status"),
            OpenApiParameter("ean", str, OpenApiParameter.QUERY, required=False, description="Filter by exact EAN"),
            OpenApiParameter("cost_min", float, OpenApiParameter.QUERY, required=False, description="Minimum cost"),
            OpenApiParameter("cost_max", float, OpenApiParameter.QUERY, required=False, description="Maximum cost"),
            OpenApiParameter(
                "search", str, OpenApiParameter.QUERY, required=False, description="Search name/external_id"
            ),
            OpenApiParameter(
                "ordering",
                str,
                OpenApiParameter.QUERY,
                required=False,
                description="Order by field (default -data_changed_at)",
            ),
            OpenApiParameter("page", int, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("page_size", int, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: SupplierProductListResponse},
    )
    def list(self, request: Request) -> Response:
        supplier_idx = request.query_params.get("supplier")
        supplier_id: int | None = None
        if supplier_idx:
            supplier_id = supplier_service.resolve_id_by_idx(supplier_idx)
            if supplier_id is None:
                return Response({"count": 0, "next": None, "previous": None, "results": []})

        cost_min = cost_max = None
        try:
            if "cost_min" in request.query_params:
                cost_min = Decimal(request.query_params["cost_min"])
            if "cost_max" in request.query_params:
                cost_max = Decimal(request.query_params["cost_max"])
        except InvalidOperation as exc:
            raise drf_exceptions.ValidationError("cost_min/cost_max must be decimal") from exc

        status_param = request.query_params.get("status")
        qs = review_service.list_for_review(
            supplier_id=supplier_id,
            status=status_param,
            search=request.query_params.get("search"),
            ean=request.query_params.get("ean"),
            cost_min=cost_min,
            cost_max=cost_max,
            ordering=request.query_params.get("ordering") or "-data_changed_at",
        )
        paginator = AdminPageNumberPagination()
        page = paginator.paginate_queryset(qs, request)
        return Response(
            {
                "count": qs.count(),
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": [_serialize(sp) for sp in page],
            }
        )

    @extend_schema(tags=_TAGS, summary="Retrieve supplier product", responses={200: SupplierProductResponse, 404: None})
    def retrieve(self, request: Request, pk: int) -> Response:
        try:
            sp = product_service.get_sp(pk)
        except ValueError as exc:
            raise drf_exceptions.NotFound(str(exc) or "SupplierProduct not found") from exc
        return Response(_serialize(sp))

    @extend_schema(
        tags=_TAGS,
        summary="Patch supplier product (limited fields)",
        request=SupplierProductPartialUpdateRequest,
        responses={200: SupplierProductResponse, 400: None, 404: None},
    )
    def partial_update(self, request: Request, pk: int) -> Response:
        try:
            data = SupplierProductPartialUpdateRequest(**request.data)
        except ValidationError as exc:
            raise_pydantic_as_drf(exc)
        update_kwargs = {k: v for k, v in data.model_dump(exclude_unset=True).items() if v is not None}
        try:
            if update_kwargs:
                sp = product_service.update_sp(pk, triggered_by=request.user, **update_kwargs)
            else:
                sp = product_service.get_sp(pk)
        except ValueError as exc:
            raise_as_drf(exc)
        return Response(_serialize(sp))

    # --- Single-SP review actions ----------------------------------------

    def _review_action(self, pk: int, fn, user) -> Response:
        try:
            sp = fn(pk, user)
        except ValueError as exc:
            raise_as_drf(exc)
        except Exception as exc:  # noqa: BLE001 — service raises model DoesNotExist; map to 404
            if "DoesNotExist" in exc.__class__.__name__:
                raise drf_exceptions.NotFound("SupplierProduct not found") from exc
            raise
        return Response(_serialize(sp))

    @extend_schema(
        tags=_TAGS, summary="Approve supplier product", responses={200: SupplierProductResponse, 400: None, 404: None}
    )
    def approve(self, request: Request, pk: int) -> Response:
        return self._review_action(pk, review_service.approve, request.user)

    @extend_schema(
        tags=_TAGS, summary="Reject supplier product", responses={200: SupplierProductResponse, 400: None, 404: None}
    )
    def reject(self, request: Request, pk: int) -> Response:
        return self._review_action(pk, review_service.reject, request.user)

    @extend_schema(
        tags=_TAGS,
        summary="Skip queued supplier product (queued -> new)",
        responses={200: SupplierProductResponse, 400: None, 404: None},
    )
    def skip(self, request: Request, pk: int) -> Response:
        return self._review_action(pk, review_service.skip, request.user)

    @extend_schema(
        tags=_TAGS, summary="Queue supplier product", responses={200: SupplierProductResponse, 400: None, 404: None}
    )
    def queue(self, request: Request, pk: int) -> Response:
        return self._review_action(pk, review_service.queue, request.user)

    # --- Bulk review actions --------------------------------------------

    def _bulk(self, request: Request, request_schema, fn) -> Response:
        try:
            data = request_schema(**request.data)
        except ValidationError as exc:
            raise_pydantic_as_drf(exc)
        result = fn(data.ids, request.user)
        return Response(result)

    @extend_schema(tags=_TAGS, summary="Bulk approve", request=BulkApproveRequest, responses={200: BulkActionResponse})
    def bulk_approve(self, request: Request) -> Response:
        return self._bulk(request, BulkApproveRequest, review_service.bulk_approve)

    @extend_schema(tags=_TAGS, summary="Bulk reject", request=BulkRejectRequest, responses={200: BulkActionResponse})
    def bulk_reject(self, request: Request) -> Response:
        return self._bulk(request, BulkRejectRequest, review_service.bulk_reject)

    @extend_schema(
        tags=_TAGS,
        summary="Bulk re-queue (rejected -> queued)",
        request=BulkRequeueRequest,
        responses={200: BulkActionResponse},
    )
    def bulk_requeue(self, request: Request) -> Response:
        return self._bulk(request, BulkRequeueRequest, review_service.bulk_requeue)

    # --- Push actions ----------------------------------------------------

    @extend_schema(tags=_TAGS, summary="Push approved SP to PIM", responses={200: PushResponse, 400: None, 404: None})
    def push(self, request: Request, pk: int) -> Response:
        events: list[dict] = []
        try:
            with transaction.atomic():
                products = push_service.push_supplier_product(pk, request.user, event_sink=events)
        except ValueError as exc:
            raise_as_drf(exc)
        except Exception as exc:  # noqa: BLE001 — see _review_action
            if "DoesNotExist" in exc.__class__.__name__:
                raise drf_exceptions.NotFound("SupplierProduct not found") from exc
            raise
        sp = product_service.get_sp(pk)
        return Response({"pushed_channels_count": len(products), "status": sp.status, "events": events})

    @extend_schema(
        tags=_TAGS,
        summary="Force re-push (overwrite enrichment in already-pushed channels)",
        responses={200: PushResponse, 400: None, 404: None},
    )
    def force_repush(self, request: Request, pk: int) -> Response:
        events: list[dict] = []
        try:
            products = push_service.force_repush_supplier_product(pk, request.user, event_sink=events)
        except ValueError as exc:
            raise_as_drf(exc)
        except Exception as exc:  # noqa: BLE001 — see _review_action
            if "DoesNotExist" in exc.__class__.__name__:
                raise drf_exceptions.NotFound("SupplierProduct not found") from exc
            raise
        sp = product_service.get_sp(pk)
        return Response({"pushed_channels_count": len(products), "status": sp.status, "events": events})

    @extend_schema(
        tags=_TAGS,
        summary="Unlink SP from RealProduct",
        responses={200: UnlinkFromRealProductResponse, 400: None, 404: None},
    )
    def unlink_from_realproduct(self, request: Request, pk: int) -> Response:
        events: list[dict] = []
        try:
            result = product_link_service.unlink_sp_from_realproduct(pk, request.user, event_sink=events)
        except ValueError as exc:
            raise_as_drf(exc)
        except Exception as exc:  # noqa: BLE001 — see _review_action
            if "DoesNotExist" in exc.__class__.__name__:
                raise drf_exceptions.NotFound("SupplierProduct not found") from exc
            raise
        return Response(
            {
                "previous_real_product_sku": result["previous_real_product_sku"],
                "new_real_product_sku": result["new_real_product_sku"],
                "events": events,
            }
        )
