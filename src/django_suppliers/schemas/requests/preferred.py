# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Request schemas for `/pim-sku/{sku}/set-preferred-supplier/`
and `/pim-sku/{sku}/reset-preferred-to-auto/`."""

from pydantic import BaseModel, Field, field_validator


class SetPreferredSupplierRequest(BaseModel):
    """Body for the manual override endpoint.

    `reason` is required and audited — operator-supplied free-text so the
    'why' is preserved in the change log for future review (Slack escalation,
    "we promised this supplier", contract obligation, ...).
    """

    supplier_idx: str = Field(min_length=1, max_length=64, description="Target supplier idx to force as preferred.")
    reason: str = Field(min_length=3, max_length=512, description="Operator-supplied audit text.")

    @field_validator("reason")
    @classmethod
    def _trim(cls, value: str) -> str:
        trimmed = value.strip()
        if len(trimmed) < 3:
            raise ValueError("reason must contain at least 3 non-whitespace characters")
        return trimmed


class ResetPreferredToAutoRequest(BaseModel):
    """Empty body OR optional audit note for the reset endpoint.

    No required fields — the action itself is the audit-worthy event. `reason`
    is preserved purely so the operator can leave a why-was-this-reset note.
    """

    reason: str | None = Field(default=None, max_length=512, description="Optional audit note.")
