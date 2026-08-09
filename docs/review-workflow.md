---
title: Review Workflow
description: SupplierProduct status transitions, CMS Swipe vs List vs Events modes, bulk actions.
sidebar:
  label: Review Workflow
---

`SupplierReview` panel is where operators decide what enters PIM. Four modes cover different review patterns (the 4th `Updated` mode shipped in etap-07).

## Status Workflow

```
new        → queued     │ new → approved (bulk)         │ new → rejected (bulk)
queued     → approved   │ queued → rejected             │ queued → new (skip)
rejected   → queued     │ approved → rejected           │ approved → pushed_pending_images
pushed_pending_images → pushed                          │ pushed → approved (force re-push)
```

| From | Allowed → |
|---|---|
| `new` | `queued`, `approved`, `rejected` |
| `queued` | `new` (skip), `approved`, `rejected` |
| `approved` | `rejected`, `pushed_pending_images` |
| `rejected` | `queued` |
| `pushed_pending_images` | `pushed` |
| `pushed` | `approved` (force re-push only) |

**Forbidden:** `new → pushed`, `rejected → pushed`. Every push goes through `approved`.

## Modes

### Swipe Mode

One SP at a time. Card layout. Buttons: Approve, Reject, Skip (queued).
Use case: high-attention review, small batches, mobile-friendly.

### List Mode

Paginated grid. Multi-select checkbox. Bulk actions: Approve, Reject, Re-queue (D29 — rejected → queued).
Use case: bulk decisions on similar products, post-import triage.

### Events Mode

Cross-supplier `IntegrationEvent` dashboard. Filters: severity, supplier, event_type, acknowledged, date range.
Use case: ops monitoring, ack flow, drill-down on warnings.

### Updated Mode (etap-07)

Cross-supplier "what changed since last push" view. Lists SPs in `pushed` / `pushed_pending_images` status where `data_changed_at > pushed_at`. Sidebar shows per-supplier counts (`All N | Kinghoff N | Fortrade N`); main grid shows each row with the same `BulkActionBar` as ProductsTab (force-repush per-SP, acknowledge per-SKU). Backend: `GET /products/?status=pushed&ordering=-data_changed_at&page_size=100` + client-side `isRowUpdated()` filter — no dedicated endpoint, just the standard `products/` list with denormalised `real_product_sku` / `supplier_idx` / `supplier_name`.
Use case: operator triage of "supplier touched this PIM SKU after we curated it" without per-SKU SQL.

## Bulk Actions

| Action | Allowed from | Result |
|---|---|---|
| `bulk_approve` | new, queued | status='approved', sets `reviewed_by`/`reviewed_at` |
| `bulk_reject` | new, queued, approved | status='rejected' |
| `bulk_requeue` (D29) | rejected | status='queued', preserves original `reviewed_by`/`reviewed_at` |

All bulk actions confirm via modal showing affected count. No undo for individual SP transitions, but `bulk_requeue` ratiates accidental mass-reject.

## Pre-flight in UI

Before push, CMS hits `POST /mapping-profiles/{idx}/validate/`. Returns `{ok: bool, errors: [...]}`. UI blocks push button when errors present and shows them inline.

Errors include:
- `pim_feature_missing:{idx}` — mapping references non-existent PIM Feature.
- `pim_category_missing_all_channels:{idx}` — category not in any active target channel.
- `profile_no_target_channels` — active profile with empty `target_channel_idxs`.
- `no_active_mapping_profile` — supplier has no active profile.

## Audit Trail

| Field | Set when |
|---|---|
| `reviewed_by` | First approve/reject sets, subsequent re-reviews update |
| `reviewed_at` | Same |
| `pushed_by` | Push action sets, force re-push overwrites |
| `pushed_at` | Same |

D29: `bulk_requeue` is NOT a re-review — it does not update `reviewed_by`/`reviewed_at`. Operator regrets bulk-reject? Re-queueing keeps original audit intact.

## Notification Patterns

After approve/reject:
- Toast in CMS (success).
- No email by default. Phase 2 backlog: configurable per-supplier email on push events.

After bulk action:
- Toast with count: "Approved 12 products."
- Auto-refresh of list.
