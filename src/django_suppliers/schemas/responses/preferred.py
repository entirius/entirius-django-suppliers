# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Response schemas for `/pim-sku/{sku}/set-preferred-supplier/`
and `/pim-sku/{sku}/reset-preferred-to-auto/`.

Both endpoints surface `events: list[dict]` so the CMS can render toasts
for emitted IntegrationEvents (same convention as the push response).
"""

from typing import Any

from pydantic import BaseModel, Field


class SetPreferredSupplierResponse(BaseModel):
    real_product_sku: str = Field(description="PIM SKU echoed back.")
    preferred_supplier_idx: str = Field(description="The supplier that is now preferred.")
    previous_preferred_supplier_idx: str | None = Field(
        description="Supplier that WAS preferred before this call. Null when no prior preferred."
    )
    manual_override: bool = Field(description="Always true on this endpoint — sticky flag now set.")
    events: list[dict[str, Any]] = Field(
        default_factory=list,
        description="IntegrationEvents emitted during the switch (forced_warning + any skipped subsidiary events).",
    )


class ResetPreferredToAutoResponse(BaseModel):
    real_product_sku: str = Field(description="PIM SKU echoed back.")
    previous_preferred_supplier_idx: str | None = Field(
        description="Supplier that WAS preferred (and had manual_override=True). Null if no prior preferred."
    )
    new_preferred_supplier_idx: str | None = Field(
        description="Supplier auto-strategy now picks. Null when no candidates with stock."
    )
    switched: bool = Field(description="True when auto-strategy actually flipped the preferred link.")
    skip_reason: str = Field(description="PreferredSkipReason value when switched=False; 'none' when switched=True.")
    events: list[dict[str, Any]] = Field(
        default_factory=list, description="IntegrationEvents emitted during the inline re-evaluation."
    )
