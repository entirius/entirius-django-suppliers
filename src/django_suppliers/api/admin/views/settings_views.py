# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API view for SupplierSettings singleton (GET + PATCH).

C4: ZERO Django models imported here — all ORM via settings_service.
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
from django_suppliers.api.admin.views._helpers import raise_as_drf
from django_suppliers.schemas.requests.settings import SupplierSettingsUpdateRequest
from django_suppliers.schemas.responses.settings import SupplierSettingsResponse
from django_suppliers.services import settings_service


def _serialize(s) -> dict:
    return SupplierSettingsResponse.model_validate(s).model_dump(mode="json")


_TAGS = ["Supplier Settings"]


class SupplierSettingsView(APIView):
    authentication_classes = [JWTAuthentication]
    permission_classes = [IsAdminUser]

    @extend_schema(
        tags=_TAGS, summary="Retrieve supplier settings (singleton)", responses={200: SupplierSettingsResponse}
    )
    def get(self, request: Request) -> Response:
        return Response(_serialize(settings_service.get_settings()))

    @extend_schema(
        tags=_TAGS,
        summary="Patch supplier settings",
        request=SupplierSettingsUpdateRequest,
        responses={200: SupplierSettingsResponse, 400: None},
    )
    def patch(self, request: Request) -> Response:
        try:
            data = SupplierSettingsUpdateRequest(**request.data)
        except ValidationError as exc:
            raise_pydantic_as_drf(exc)
        try:
            settings = settings_service.update_settings(**data.model_dump(exclude_unset=True))
        except ValueError as exc:
            raise_as_drf(exc)
        return Response(_serialize(settings), status=status.HTTP_200_OK)
