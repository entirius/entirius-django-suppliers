# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API for cross-supplier RealProduct ops.

Three endpoints share this ViewSet because they all operate on RealProduct
(which lives in django-pim, not here) -- co-locating them keeps URL discovery
obvious and matches the pattern of `product_link_views.py`.

C4: ViewSets MUST NOT import Django models. All ORM access goes through
services (`realproduct_merge_service`, `duplicate_detection_service`,
`auto_matched_service`).
"""

from datetime import datetime

from django_utils.api.v2_errors import raise_pydantic_as_drf
from drf_spectacular.utils import OpenApiParameter, extend_schema
from pydantic import ValidationError
from rest_framework import exceptions as drf_exceptions
from rest_framework import status, viewsets
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.authentication import JWTAuthentication

from django_suppliers.api.admin.pagination import AdminPageNumberPagination
from django_suppliers.api.admin.permissions import IsAdminUser
from django_suppliers.schemas.requests.merge import MergeByEanRequest
from django_suppliers.schemas.responses.auto_matched import AutoMatchedListResponse, AutoMatchedRow, AutoMatchedSupplier
from django_suppliers.schemas.responses.duplicates import (
    DuplicateGroupResponse,
    DuplicateRP,
    DuplicatesListResponse,
    DuplicatesSupplier,
)
from django_suppliers.schemas.responses.merge import MergeByEanResponse
from django_suppliers.services import auto_matched_service, duplicate_detection_service, realproduct_merge_service

_TAGS = ["RealProducts (cross-supplier)"]


def _parse_bool(raw: str | None) -> bool:
    return (raw or "").lower() in ("true", "1", "yes")


def _parse_since(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise drf_exceptions.ValidationError(f"Invalid `since` value: {raw!r}. Use ISO 8601.") from exc


class RealProductCrossSupplierViewSet(viewsets.ViewSet):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]
    access_area = "suppliers.products"
    pagination_class = AdminPageNumberPagination
    serializer_class = None

    @extend_schema(
        tags=_TAGS,
        summary="Merge two RealProducts sharing an EAN",
        description=(
            "Atomic merge: redirect every ProductSupplierLink (by real_product_sku) and "
            "SupplierProduct.real_product FK from loser to winner, delete the loser, "
            "audit as `source=manual_merge`, emit `realproduct_manually_merged` event."
        ),
        request=MergeByEanRequest,
        responses={200: MergeByEanResponse, 400: None, 404: None},
    )
    def merge_by_ean(self, request: Request) -> Response:
        from django_pim.models.real_product import (  # local import: ViewSets must not import models at module scope
            RealProduct,
        )

        try:
            data = MergeByEanRequest(**request.data)
        except ValidationError as exc:
            raise_pydantic_as_drf(exc)
        try:
            result = realproduct_merge_service.merge_realproducts(
                winner_sku=data.winner_sku,
                loser_sku=data.loser_sku,
                reason=data.reason,
                actor=request.user if request.user and request.user.is_authenticated else None,
            )
        except RealProduct.DoesNotExist as exc:
            raise drf_exceptions.NotFound(f"RealProduct not found: {exc}") from exc
        except ValueError as exc:
            raise drf_exceptions.ValidationError(str(exc)) from exc
        return Response(
            MergeByEanResponse(
                winner_sku=result.winner_sku,
                loser_sku=result.loser_sku,
                links_redirected=result.links_redirected,
                supplier_products_repointed=result.supplier_products_repointed,
                audit_id=result.audit_id,
            ).model_dump(mode="json"),
            status=status.HTTP_200_OK,
        )

    @extend_schema(
        tags=_TAGS,
        summary="List RealProducts touched by auto-EAN-match (dashboard)",
        parameters=[
            OpenApiParameter(
                "supplier", str, OpenApiParameter.QUERY, required=False, description="Supplier.idx filter"
            ),
            OpenApiParameter(
                "since",
                str,
                OpenApiParameter.QUERY,
                required=False,
                description="ISO 8601 datetime — only audit rows on/after this timestamp",
            ),
            OpenApiParameter(
                "has_violations",
                bool,
                OpenApiParameter.QUERY,
                required=False,
                description="When true, only SKUs with at least one physical_tolerance_violation event.",
            ),
            OpenApiParameter(
                "manual_override_only",
                bool,
                OpenApiParameter.QUERY,
                required=False,
                description="When true, only SKUs whose any active link has manual_override=True.",
            ),
            OpenApiParameter("page", int, OpenApiParameter.QUERY, required=False),
            OpenApiParameter("page_size", int, OpenApiParameter.QUERY, required=False),
        ],
        responses={200: AutoMatchedListResponse},
    )
    def auto_matched_list(self, request: Request) -> Response:
        since = _parse_since(request.query_params.get("since"))
        has_violations_only = _parse_bool(request.query_params.get("has_violations"))
        manual_override_only = _parse_bool(request.query_params.get("manual_override_only"))

        distinct_qs = auto_matched_service.list_distinct_sku_qs(
            supplier_idx=request.query_params.get("supplier"), since=since, manual_override_only=manual_override_only
        )

        paginator = AdminPageNumberPagination()
        page = paginator.paginate_queryset(list(distinct_qs), request)
        rows = auto_matched_service.build_rows(list(page), has_violations_only=has_violations_only)

        # When has_violations_only filtered the page rows, the paginator's `count`
        # still reflects the unfiltered distinct SKUs -- we re-count on the fly.
        # That's a minor inconsistency but cheaper than running the full filter
        # over every SKU twice. Documented surprise.
        total = distinct_qs.count()
        results = [
            AutoMatchedRow(
                sku=r.sku,
                ean=r.ean,
                suppliers=[
                    AutoMatchedSupplier(idx=s.idx, name=s.name, is_preferred=s.is_preferred) for s in r.suppliers
                ],
                has_tolerance_violation=r.has_tolerance_violation,
                has_manual_override=r.has_manual_override,
                last_auto_link_at=r.last_auto_link_at,
            ).model_dump(mode="json")
            for r in rows
        ]
        return Response(
            {
                "count": total,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "results": results,
            }
        )

    @extend_schema(
        tags=_TAGS,
        summary="List RealProduct EAN-duplicate groups (operator triage)",
        parameters=[
            OpenApiParameter(
                "tolerance_pct",
                float,
                OpenApiParameter.QUERY,
                required=False,
                description="Weight diff threshold (1.0-50.0). Default: 10.0",
            )
        ],
        responses={200: DuplicatesListResponse, 400: None},
    )
    def duplicates_list(self, request: Request) -> Response:
        raw = request.query_params.get("tolerance_pct", "10.0")
        try:
            tolerance_pct = float(raw)
        except ValueError as exc:
            raise drf_exceptions.ValidationError(f"Invalid tolerance_pct: {raw!r}") from exc
        if not (1.0 <= tolerance_pct <= 50.0):
            raise drf_exceptions.ValidationError("tolerance_pct must be between 1.0 and 50.0")

        groups = duplicate_detection_service.find_duplicates_by_ean(tolerance_pct=tolerance_pct)
        results = [
            DuplicateGroupResponse(
                ean=g.ean,
                realproducts=[
                    DuplicateRP(
                        sku=rp.sku,
                        ean=rp.ean,
                        weight=rp.weight,
                        width=rp.width,
                        height=rp.height,
                        deep=rp.deep,
                        suppliers=[
                            DuplicatesSupplier(idx=s["idx"], name=s["name"], is_preferred=s["is_preferred"])
                            for s in rp.suppliers
                        ],
                    )
                    for rp in g.realproducts
                ],
                suggestion=g.suggestion,
                suggestion_detail=g.suggestion_detail,
            ).model_dump(mode="json")
            for g in groups
        ]
        return Response({"count": len(results), "results": results})
