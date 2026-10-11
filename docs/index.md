---
title: Suppliers
description: Supplier registry, feed-driven import pipeline, review workflow, and one-time push to PIM with cyclic delta refresh.
sidebar:
  label: Overview
  collapsed: true
---

`django-suppliers` runs the supplier side of the catalog. Suppliers register, push their feeds, an operator reviews candidate products, and approved items land in PIM with attribution kept on `ProductSupplierLink`. After the initial push, only cost / qty / physical attributes refresh on a delta cadence — content stays curator-owned.

## What It Does

- Maintains a global Supplier registry; multi-channel targeting via `SupplierMappingProfile`.
- Imports feeds (XML, scraper-callback) into a staging table (`SupplierProduct`).
- Provides a CMS review queue (Swipe / List / Events / Updated modes — the 4th mode shipped in etap-07).
- One-time INIT push to PIM (`is_enabled=False`, multi-channel from mapping profiles).
- Auto EAN-match against existing RealProduct on push (etap-13a, per-supplier tolerance + opt-out).
- Cyclic delta refresh: COST → preferred-supplier write to Pricemanager `CurrentPrice` (etap-11), QTY → QMS Warehouse, RealProduct physical → PIM (preferred-only by default, etap-10).
- Auto-preferred selection (etap-13b): hysteresis + cooldown + cron daily 03:00 UTC + emergency switch on stock=0 + manual operator override.
- Per-field audit log (`SupplierProductChangeLog`, etap-04) surfaced in PIM + Suppliers CMS panels as `Updated` badges, timeline drawer, and diff renderers.
- Force re-push on operator demand (overwrite enrichment, confirmation modal). Race detection still applies — non-preferred suppliers can't overwrite physical fields without explicit opt-in.

## Where It Plugs In

| Module | Mode | What changes |
|---|---|---|
| `django-pim` | HARD dep | Push writes RealProduct + Product + ProductAttribute + ProductPicture per channel. PIM does NOT import suppliers — CMS gates the PIM ↔ suppliers bridge via `useMuninStore().isPanelEnabled('suppliers')`. |
| `django-pricemanager` | SOFT dep (etap-11) | No `import django_pricemanager` from suppliers code. Cost flows out as `cost_updated_signal` + `IntegrationEvent('cost_updated')`. Pricemanager-side subscriber writes preferred-supplier cost into `CurrentPrice` (`source="supplier_cost"`) + `PriceHistory`. |
| `django-qms` | SOFT dep | `try/except (ImportError, RuntimeError)`. No QMS = stock writes silently skipped (info event). |
| `django-regional` | HARD dep | Language, Currency, Country FKs. |
| `django-utils` | HARD dep | `BaseModel` (created_at, modified_at). |

## Architecture (one-pager)

```
Supplier feed → Connector (xml_feed | scraper) → Import service
  → SupplierProduct (staging)
    → Review queue (CMS)
      → Push service (operator approve)
        → PIM RealProduct + Product per channel (INIT)
        → ProductSupplierLink (attribution)
        → cost_updated_signal (Pricemanager subscriber, future)
        → QMS Warehouse stock (soft dep)

Cyclic delta sync: cost / qty / physical only — never content.
Force re-push: operator-triggered, replaces enrichment, keeps is_enabled.
```

Layer rule: `API → Services → Models → DB`. ViewSets do not import models — only services.

## Data Flow

### Two sync paths

Every supplier feed runs in one of two modes; `import_service` picks by feed config and time-since-last-run.

| Mode | Trigger | Side effects | Counter source |
|---|---|---|---|
| **Full sync** | Manual / first run / `--mode full` | Re-imports every row; stages new/changed `SupplierProduct` rows; never touches PIM directly | `ImportLog.imported_count` / `created_count` / `updated_count` |
| **Delta sync** | Cron / `--mode delta` | Reads incremental changes; routes COST → `cost_updated_signal` + pricemanager, QTY → `qms_writer` (soft dep), physical → `_apply_physical_update_to_real_product` (preferred-only gate) | `ImportLog.physical_*_count`, `cost_updated` events, QMS info events |

### Automatic propagation after first INIT push

After `init_push_to_channel` puts a SupplierProduct in PIM, only these flow back automatically on each delta sync:

| Channel | What updates | Gating |
|---|---|---|
| Cost → Pricemanager | `CurrentPrice` rewrite (`source=supplier_cost`) + `PriceHistory` row | Only when SP's link `is_preferred=True` AND `CurrentPrice.source != admin_edit` |
| Stock → QMS Warehouse | Stock write via `qms_writer.write_stock` | Only when `_qms_available()` returns True (django_qms installed AND in INSTALLED_APPS) |
| Physical → RealProduct | `weight` / `ean` / `width` / `height` / `deep` overwrite | Preferred-only by default. Opt-in via `Supplier.allow_physical_writes_from_non_preferred=True` |
| Language resolution | 3-step hierarchy: `profile.import_language` → `supplier.default_language ∈ channel.languages` → `channel.default_language` | Fallback emits `language_fallback` event + audit row |
| Mapping value transforms | Numeric (grams/kg, mm/cm, currency minor/major) + string (trim/lowercase/uppercase) per `SupplierAttributeMapping.modifier` | Failure soft — raw value used, `mapping_transform_failed` event emitted |
| Auto EAN-match | New SP matches existing RealProduct by EAN within tolerance | Per-supplier opt-out via `Supplier.disable_ean_auto_link=True` |
| Auto-preferred selection | Cron daily 03:00 UTC + inline after `_write_qms_cost_link` + inline emergency on stock=0 | Manual override sticky bit blocks all three triggers until `reset_to_auto` |

Content (`name`, `description`, `short_description`, `category_idxs`, all non-physical features) is
**curator-owned** post-INIT — only `force_repush` overwrites enrichment.

### Force re-push semantics

`force_repush_supplier_product` / `pim-sku/{sku}/force-repush/` is the operator escape hatch.
It iterates every active `ProductSupplierLink` for the SKU, re-applies mappings as if pushing for the
first time, and emits per-SP audit rows with `source=force_repush`. Partial failures are captured in
`failed[]` (response key) — successes commit, failures roll back per-SP. Physical race detection still
applies — non-preferred SPs cannot overwrite RealProduct physical fields via force_repush either.

## Signals

Four signals; receivers MUST be idempotent (Celery retries, delta sync re-runs, force re-push can fire
the same `(supplier_product, channel_idx)` pair repeatedly).

| Signal | Sender | Args | Subscribers |
|---|---|---|---|
| `supplier_products_imported_signal` | `import_service.execute_feed` after a successful full sync / scraper-callback finalize | `feed`, `import_log` | `signals.handlers.on_supplier_products_imported` → auto-push pipeline (gated by `SupplierSettings.auto_push_enabled`, wrapped in `transaction.on_commit`) |
| `cost_updated_signal` | `pricemanager_writer.write_cost` after a SupplierProduct cost lands for a target channel | `supplier_product`, `channel_idx`, `cost`, `currency` | `django_pricemanager.signals.supplier_cost.on_supplier_cost_updated`: writes preferred-supplier cost into `CurrentPrice` + `PriceHistory` with `source="supplier_cost"`. Suppliers stays unaware — soft coupling preserved. |
| `supplier_product_pushed_signal` | `push_service` after status transitions to `pushed_pending_images` / `pushed` | `supplier_product`, `real_product_sku`, `channel_idx` | `signals.handlers.on_supplier_product_pushed` → image-dispatch task (separate `supplier_images` Celery queue) |
| `preferred_switched_signal` | `preferred_strategy_service.apply_preferred_switch` after every `is_preferred` flip (auto / manual / emergency / cron) | `real_product_sku`, `from_supplier_idx`, `to_supplier_idx`, `reason`, `source` | None in tree yet. Reserved for downstream notifier / search reindex / cache invalidation. The cost subscriber reads `is_preferred` via ORM, not via this signal. |

Killswitches: `signals.killswitch.suppress_supplier_signals()` (thread-local context) and
`SupplierSettings.auto_push_enabled` (DB-level, cached 60s).

## Pages

- [Configuration](/volkanos/modules/suppliers/configuration/) — settings, env vars, Celery queues, per-supplier knobs (etap-13a/13b/10)
- [Data Model](/volkanos/modules/suppliers/data-model/) — every entity, every field, every constraint (incl. SupplierProductChangeLog)
- [ERD](/volkanos/modules/suppliers/erd/) — auto-generated D2 diagrams
- [Review Workflow](/volkanos/modules/suppliers/review-workflow/) — statuses, transitions, bulk actions, 4 review modes
- [Push Workflow](/volkanos/modules/suppliers/push-workflow/) — INIT push, force re-push, image async, EAN auto-match, language resolution, value modifiers, physical race detection
- [Connector Contract](/volkanos/modules/suppliers/connector-contract/) — RawProduct, RabbitMQ queues, entry_points
- [Price Handling](/volkanos/modules/suppliers/price-handling/) — cost subscriber decision tree (etap-11 D5 RESOLVED)
- [Preferred Strategy](/volkanos/modules/suppliers/preferred-strategy/) — auto-preferred selection (hysteresis + cooldown + cron + emergency switch + manual override)
- [Monitoring](/volkanos/modules/suppliers/monitoring/) — IntegrationEvent taxonomy (50+ types), Grafana queries
- [API Surface](/volkanos/modules/suppliers/api/) — URL prefix, auth, throttling, resources
- [Management Commands](/volkanos/modules/suppliers/commands/) — feed runs, push, retention, triage
- [Testing](/volkanos/modules/suppliers/testing/) — PostgreSQL requirement, how to run the suite
- [Changelog](/volkanos/modules/suppliers/changelog/) — version history (roadmap closed 2026-05-25)

For dev patterns (architecture, gotchas, services map): see `repos/django-apps/django-suppliers/AGENTS.md`.
