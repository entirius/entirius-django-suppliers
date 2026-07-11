# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Pricemanager-side cost logger — DEFER to future Price Handling layer (decision #5).

CRITICAL: this file MUST NOT import django_pricemanager. The supplier module logs cost
deltas as IntegrationEvents and emits `cost_updated_signal`. A future Price Handling
layer subscribes to that signal — supplier doesn't know or care what it does with it.

Decision #35: bulk volume scenarios (50k SP × 5 channels delta) use `event_service.record_bulk`
to issue 1 batch INSERT instead of N×INSERT.
"""

from django_suppliers.enums import EventSeverity, EventType
from django_suppliers.models import Supplier, SupplierProduct, SupplierSettings
from django_suppliers.services import event_service
from django_suppliers.signals import cost_updated_signal


def log_cost(sp: SupplierProduct, supplier: Supplier, channel_idxs: list[str], context: str = "push") -> None:
    """Log a cost change per channel and emit `cost_updated_signal` per channel.

    - 'push' context always runs.
    - 'delta' context respects `SupplierSettings.delta_sync_enabled` killswitch (silent skip).
    - Missing cost / currency / real_product → warning event, skip.

    Bulk insert via event_service.record_bulk for the IntegrationEvents (decision #35).
    Signal emit stays per-channel (receivers expect discrete events).
    """
    if context == "delta" and not SupplierSettings.load().delta_sync_enabled:
        return
    if sp.cost is None:
        event_service.record(
            event_type=EventType.COST_MISSING.value,
            severity=EventSeverity.WARNING.value,
            supplier=supplier,
            supplier_product=sp,
            message=f"SP {sp.id} has no cost, log skipped",
        )
        return
    if not sp.currency:
        event_service.record(
            event_type=EventType.CURRENCY_MISSING.value,
            severity=EventSeverity.WARNING.value,
            supplier=supplier,
            supplier_product=sp,
            message=f"SP {sp.id} has no currency, log skipped",
        )
        return
    if sp.real_product_id is None:
        event_service.record(
            event_type=EventType.COST_MISSING.value,
            severity=EventSeverity.WARNING.value,
            supplier=supplier,
            supplier_product=sp,
            message=f"SP {sp.id} has no real_product FK, cost log skipped",
        )
        return

    sku = sp.real_product.sku
    cost_str = str(sp.cost)
    events_batch = [
        {
            "event_type": EventType.COST_UPDATED.value,
            "severity": EventSeverity.INFO.value,
            "supplier": supplier,
            "supplier_product": sp,
            "message": f"cost {cost_str} {sp.currency} for sku {sku} channel {channel_idx}",
            "details": {
                "supplier_idx": supplier.idx,
                "sku": sku,
                "cost": cost_str,
                "currency": sp.currency,
                "channel_idx": channel_idx,
                "context": context,
            },
        }
        for channel_idx in channel_idxs
    ]
    if events_batch:
        event_service.record_bulk(events_batch)

    for channel_idx in channel_idxs:
        cost_updated_signal.send(
            sender=type(sp), supplier_product=sp, channel_idx=channel_idx, cost=sp.cost, currency=sp.currency
        )
