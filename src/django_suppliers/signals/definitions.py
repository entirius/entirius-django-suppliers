# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Signal definitions for django_suppliers.

All signals follow Django dispatcher idioms (`Signal()`, `connect`/`send`).
Receivers MUST be idempotent — signals may fire repeatedly on retries
(Celery autoretry, delta sync re-run, force re-push). The same
(supplier_product, channel_idx) pair may receive >1 signal in a short window.

Stage 4 wires push handlers (image dispatch via `transaction.on_commit`).
Stage 5 wires pricemanager_writer (logs cost + emits event).
Stage 6 wires the auto-push handler.

Killswitches: `signals.killswitch.suppress_supplier_signals()` (thread-local
context) and `SupplierSettings.auto_push_enabled` (DB-level, cached 60s).
"""

from django.dispatch import Signal

# Sent by import_service.execute_feed after a successful import finalization
# (full sync end OR async scraper-callback finalize).
# providing_args:
#   feed: SupplierFeed instance
#   import_log: ImportLog instance (status='success' or 'partial')
# Stage 6 connects the auto-push handler (transaction.on_commit).
supplier_products_imported_signal = Signal()


# Emitted by pricemanager_writer / import_service after a SupplierProduct cost
# is recorded for a target channel. Decoupled from the future Price Handling
# layer (decision #5 — pricemanager DEFER in MVP).
# providing_args:
#   supplier_product: SupplierProduct instance
#   channel_idx: str (PIM Channel.idx the cost applies to)
#   cost: Decimal
#   currency: str (ISO 4217, e.g. "EUR")
# Receivers MUST be idempotent — same (supplier_product, channel_idx) may
# fire repeatedly during delta sync retry, full re-import, or force re-push.
cost_updated_signal = Signal()


# Emitted by push_service after a SupplierProduct is pushed to a PIM channel
# (status transitions to pushed_pending_images or pushed). One emission per
# (supplier_product, channel_idx) pair per push.
# providing_args:
#   supplier_product: SupplierProduct instance
#   real_product_sku: str
#   channel_idx: str
# Stage 4 wires the image-dispatch handler (per-channel image download task).
supplier_product_pushed_signal = Signal()


# emitted by preferred_strategy_service.apply_preferred_switch every time
# the auto-preferred (or manual override / emergency / cron) flips is_preferred on a
# new link for a RealProduct.
# providing_args:
#   real_product_sku: str
#   from_supplier_idx: str | None  (None when no prior preferred)
#   to_supplier_idx: str
#   reason: str  (PreferredSkipReason.NONE.value when applied; "manual_override"
#                 / "emergency" / "auto" identifying the trigger source)
#   source: str  (ChangeLogSource value driving the audit row)
# No subscribers in suppliers/pricemanager yet — the cost subscriber reads
# is_preferred via ORM, not via signal. Signal exists so downstream services
# (future Slack notifier, search reindex, cache invalidation) can hook in
# without modifying preferred_strategy_service. Receivers MUST be idempotent
# (transaction.on_commit wrappers in the service make multiple emissions
# possible on retry).
preferred_switched_signal = Signal()
