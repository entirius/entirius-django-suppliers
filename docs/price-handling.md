---
title: Price Handling
description: What suppliers provides on cost, what Phase 2 Price Handling layer subscribes to.
sidebar:
  label: Price Handling
---

`django-suppliers` does not write prices. It captures **cost** from the supplier and lets a future Price Handling layer derive sell prices. This page documents the contract.

**D5 RESOLVED (etap-11, 2026-05-24):** `django_pricemanager` ships a live subscriber. When the pricemanager is installed alongside suppliers, preferred-supplier cost lands in `CurrentPrice` (`source="supplier_cost"`) + `PriceHistory` automatically. The contract below still describes the soft-coupling boundary — implement a different subscriber if you need to bypass pricemanager.

## What Supplier Provides

- **`SupplierProduct.cost`** + **`SupplierProduct.currency`** — refreshed every delta sync.
- **`cost_updated_signal`** — Django signal emitted from `pricemanager_writer.log_cost` per channel after every cost change.
- **`IntegrationEvent('cost_updated')`** — auditable log per channel, structured `details` payload (`{channel_idx, old_cost, new_cost, currency}`).

## What Supplier Does NOT Do

- No `import django_pricemanager` anywhere in the supplier code (D5 enforced — `grep -r "from django_pricemanager" src/django_suppliers/` must return zero matches).
- No write to `pricemanager.CurrentPrice` / `pricemanager.Price`.
- No margin / rounding / promo logic.
- No competitor-price tracking (Phase 2 backlog: `monitoring` supplier_role).

## Live subscriber: `django_pricemanager` (etap-11)

The `django_pricemanager.signals.supplier_cost.on_supplier_cost_updated` receiver implements a decision tree before any DB write — every branch produces an audit row via `services/audit_service` so operators can answer "what happened to this cost?" from the timeline.

| Branch | Source variant | When it fires |
|---|---|---|
| `cost_signal_received` | preferred-supplier path | Active `ProductSupplierLink.is_preferred=True` AND existing `CurrentPrice.source != admin_edit` — writes `CurrentPrice` + `PriceHistory` |
| `cost_ignored_no_link` | no link | SP has no active `ProductSupplierLink` — skip |
| `cost_ignored_non_preferred` | non-preferred | Link exists but `is_preferred=False` — skip |
| `cost_skipped_admin_override` | anti-clobber | `CurrentPrice.source=admin_edit` exists — operator override preserved |
| `cost_skipped_resolution_failed` | resolve fail | Could not resolve PIM Product / Channel / Currency / TaxRate — skip |

Idempotency: when the new cost equals the existing `CurrentPrice.net`, the subscriber returns early without audit spam.

## Implementing a different subscriber

If you need a fully custom rule layer (margin matrix, dynamic pricing, B2B price lists), the original D5 contract still applies — define your subscriber in its own module and rely solely on the supplier's outgoing signal:

## Signal Payload

```python
# Subscribe in receiving module
from django.dispatch import receiver
from django_suppliers.signals.definitions import cost_updated_signal

@receiver(cost_updated_signal, dispatch_uid="my_pricing.handle_cost_update")
def handle_cost_update(sender, **kwargs):
    sp_id: int = kwargs["supplier_product_id"]
    real_product_sku: str = kwargs["real_product_sku"]
    channel_idx: str = kwargs["channel_idx"]
    cost: Decimal = kwargs["cost"]
    currency: str = kwargs["currency"]
    # Derive sell price, write pricemanager...
```

`sender` is set to `SupplierProduct` model. Always use `dispatch_uid` to prevent duplicate connections.

## IntegrationEvent Payload

```json
{
  "event_type": "cost_updated",
  "severity": "info",
  "supplier_id": 12,
  "supplier_product_id": 4567,
  "details": {
    "channel_idx": "default-europe",
    "old_cost": "129.99",
    "new_cost": "139.99",
    "currency": "EUR"
  }
}
```

Use this for audit / Grafana / replay if signal subscriber missed an event.

## Performance Note

D35: `pricemanager_writer.log_cost` uses `event_service.record_bulk()` for delta sync. 50k SP × 5 channels = 250k events written in ~500-batch INSERTs (no N+1). Future Price Handling subscriber should batch its writes equivalently.

## Migration Path

When Price Handling ships:

1. Subscriber connects to `cost_updated_signal` in its `apps.py` `ready()`.
2. Existing supplier behavior unchanged — cost still recorded, signal still fires.
3. No DB migration in supplier needed.
4. No API contract change.

The supplier module never imports the price layer. The price layer always imports the signal definition. Direction of dependency stays one-way.
