# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Role gate for monitoring suppliers.

`monitoring` suppliers are read-only observers (competitor price watching):
their SupplierProducts exist, receive delta cost/stock updates and change-log
history, but MUST NEVER write to PIM/QMS/PriceManager and never participate
in preferred-supplier selection. Manual links to EXISTING RealProducts stay
allowed — that is how future price alerts attach to the catalog.
"""

from django_suppliers.enums import SupplierRole
from django_suppliers.models import Supplier

MONITORING_PUSH_BLOCKED = "supplier_role_monitoring_push_blocked"


def is_monitoring(supplier: Supplier) -> bool:
    return supplier.supplier_role == SupplierRole.MONITORING.value


def assert_not_monitoring(supplier: Supplier, action: str) -> None:
    """Raise ValueError when a monitoring supplier attempts a PIM-bound action."""
    if is_monitoring(supplier):
        raise ValueError(f"Supplier '{supplier.idx}' has role 'monitoring' — {action} is not allowed")


__all__ = ["MONITORING_PUSH_BLOCKED", "assert_not_monitoring", "is_monitoring"]
