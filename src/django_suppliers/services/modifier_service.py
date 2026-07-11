# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Stock quantity modifiers — `qty_subtract` and `qty_minimum` per supplier.

Margin/rounding modifiers (Pricemanager-side) are out of scope for MVP (decision #18).
"""

from django_suppliers.models import Supplier


def apply_qty_modifiers(raw_qty: int | None, supplier: Supplier) -> int:
    """Apply supplier-level qty_subtract + qty_minimum to a raw stock value.

    - `raw_qty=None` is treated as 0.
    - Result is clamped to >= 0 (defensive — model validators reject negative qty_subtract,
      but raw_qty from a delta sync could theoretically be negative).
    - `qty_minimum` is the floor: even if subtract would zero out, minimum kicks in.
    """
    base = raw_qty or 0
    result = max(supplier.qty_minimum, base - supplier.qty_subtract)
    return max(0, result)
