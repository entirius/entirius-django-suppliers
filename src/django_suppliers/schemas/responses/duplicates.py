# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Response schemas for GET /admin/duplicates/."""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field


class DuplicatesSupplier(BaseModel):
    idx: str = Field(description="Supplier.idx", examples=["fortrade"])
    name: str = Field(description="Supplier display name", examples=["Fortrade Sp. z o.o."])
    is_preferred: bool = Field(description="Operator-controlled preferred flag for this link.")


class DuplicateRP(BaseModel):
    sku: str = Field(description="RealProduct.sku", examples=["FT-ce5b9e3089ff"])
    ean: str = Field(description="EAN (shared across the group)", examples=["5906214804074"])
    weight: Decimal | None = Field(description="Physical weight (kg)", examples=["0.150"])
    width: Decimal | None = Field(description="Physical width (cm)")
    height: Decimal | None = Field(description="Physical height (cm)")
    deep: Decimal | None = Field(description="Physical depth (cm)")
    suppliers: list[DuplicatesSupplier] = Field(description="Active ProductSupplierLink for this RealProduct.")


class DuplicateGroupResponse(BaseModel):
    ean: str = Field(description="Shared EAN across the group.", examples=["5906214804074"])
    realproducts: list[DuplicateRP] = Field(description="RealProducts that share this EAN (>=2 by definition).")
    suggestion: Literal["merge", "review"] = Field(
        description="`merge` if max pairwise weight diff <= tolerance_pct, `review` otherwise (or any missing weight)."
    )
    suggestion_detail: str = Field(
        description="Human-readable rationale for the suggestion (weight diff vs tolerance, or missing-weight note)."
    )


class DuplicatesListResponse(BaseModel):
    count: int = Field(description="Number of EAN groups returned (no pagination — dataset small).")
    results: list[DuplicateGroupResponse] = Field(description="EAN groups with >1 RealProduct.")
