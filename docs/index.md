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
- [Changelog](/volkanos/modules/suppliers/changelog/) — version history (roadmap closed 2026-05-25)

For dev patterns (architecture, gotchas, services map): see `repos/django-apps/django-suppliers/AGENTS.md`.
