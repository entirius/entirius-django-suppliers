# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""SupplierProduct read + small-update helpers (used by Admin API view layer).

Bulk import + state transitions live in import_service / review_service. This
module is the simple lookup + targeted-field update layer for views.
"""

import logging
from typing import Any

from django.contrib.auth.models import AbstractBaseUser

from django_suppliers.enums import ChangeLogSource
from django_suppliers.models import SupplierProduct
from django_suppliers.services import audit_service

logger = logging.getLogger(__name__)

_EDITABLE_FIELDS = frozenset({"feature_set_idx_override"})


def get_sp(pk: int) -> SupplierProduct:
    try:
        return SupplierProduct.objects.get(pk=pk)
    except SupplierProduct.DoesNotExist as exc:
        raise ValueError(f"SupplierProduct with pk={pk} not found") from exc


def update_sp(pk: int, *, triggered_by: AbstractBaseUser | None = None, **fields: Any) -> SupplierProduct:
    invalid = set(fields) - _EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_sp: {sorted(invalid)}")
    sp = get_sp(pk)
    update_fields: list[str] = []
    diffs: list[tuple[str, Any, Any]] = []
    for field, value in fields.items():
        # Empty string for feature_set_idx_override means "clear" (set to None).
        if field == "feature_set_idx_override":
            value = value or None
        prev = getattr(sp, field)
        if prev == value:
            continue
        diffs.append((field, prev, value))
        setattr(sp, field, value)
        update_fields.append(field)
    if update_fields:
        update_fields.append("modified_at")
        sp.save(update_fields=update_fields)
        # Audit operator edits — applied_to_pim=False (this is staging SP, not PIM propagation).
        if diffs:
            drafts = [
                {
                    "supplier_product": sp,
                    "source": ChangeLogSource.OPERATOR_SP_EDIT.value,
                    "field_path": field,
                    "before": prev,
                    "after": new,
                    "triggered_by": triggered_by,
                    "applied_to_pim": False,
                }
                for field, prev, new in diffs
            ]
            try:
                audit_service.log_changes_bulk(drafts)
            except Exception:  # noqa: BLE001 — audit must be best-effort
                logger.warning("audit_service.log_changes_bulk failed in operator_sp_edit", exc_info=True)
    return sp
