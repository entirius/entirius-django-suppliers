---
title: Auto EAN Match
description: Automatic supplier-product to PIM-product matching by EAN.
---

When a supplier feed delivers a `SupplierProduct` whose EAN matches an existing
`RealProduct` in PIM, the push pipeline now attaches the SP to that existing
RealProduct via `ProductSupplierLink` instead of minting a per-supplier-prefixed
SKU. That's how `multi_supplier_overlap` finally happens in the natural flow.

Step 2 — auto-preferred selection with
hysteresis / cooldown / cron / emergency switch — lands in a follow-up stage.

## Flow

```
init_push_to_channel(sp, profile, channel_idx, user)
    │
    ▼
realproduct_match_service.find_match_by_ean(sp, supplier)
    │
    ├── supplier.disable_ean_auto_link?         → None  → standard flow
    │
    ├── sp.ean blank?                            → None  → standard flow
    │
    └── RealProduct.filter(ean=sp.ean)            → existing_rp or None
        .order_by("id").first()
                │
                ├── None → standard flow:
                │         RealProduct.get_or_create(sku=generate_sku(...), defaults=rp_defaults)
                │         _detect_multi_supplier_overlap(sp, supplier, sku)
                │
                └── existing_rp → physical_tolerance_check(rp_defaults, existing_rp, supplier)
                                        │
                                        ├── passed → AUTO-LINK:
                                        │            real_product = existing_rp
                                        │            _emit_auto_link_event(info)
                                        │            _log_auto_link_audit(source=auto_link)
                                        │
                                        └── failed → FALLBACK:
                                                    _emit_tolerance_violation_event(warning)
                                                    RealProduct.get_or_create(sku=generate_sku(...))
```

After the decision the standard pipeline continues unchanged: `Product.get_or_create`,
attribute / category mappings, `_persist_sp_after_push`, `_write_qms_cost_link`,
`_emit_pushed_signal`, audit baseline. The only call site that changed downstream
is `_write_qms_cost_link` — it now passes `set_preferred_if_first=True` to
`product_link_service.upsert_for_push` so the first link for a SKU becomes the
preferred one.

## Tolerance check

Compared physical fields: **`weight`, `width`, `height`, `deep`**.

`new_value` comes from `_real_product_defaults(sp, profile)` — the same defaults
dict that would be used to seed a new RealProduct. `existing_value` comes from
the candidate `RealProduct` columns. Per-field diff:

```
diff_pct = abs(new - existing) / max(abs(new), abs(existing), 0.001) * 100
field fails if diff_pct > Supplier.realproduct_match_tolerance_pct (default 10)
```

The denominator floor (0.001) keeps 0-vs-near-0 comparisons stable.

| Supplier knob | Default | Effect |
|---|---|---|
| `realproduct_match_tolerance_pct` | 10 | Max acceptable per-field diff (%). |
| `realproduct_match_strict` | False | When True, missing field on either side = fail. |
| `disable_ean_auto_link` | False | When True, skip lookup entirely (always new RP). |

Non-strict (default) skips comparison for fields missing on either side. Pass requires
**zero failed fields**. Sample comparable seed (Foliopak 350×450, EAN `5906214804074`):

| Side | weight | width | height | deep |
|---|---|---|---|---|
| existing RP (Fortrade) | `0.150` | `20.00` | `10.00` | `5.00` |
| SP (Kinghoff) | `0.155` (3.3%) | `20.50` (2.4%) | `10.10` (1.0%) | `5.05` (1.0%) |

All four fields under 10% → **auto-link**. ProductSupplierLink for Kinghoff is
created with `is_preferred=False`; existing Fortrade link stays preferred. Auto-preferred
re-evaluation belongs to the auto-preferred stage.

## Audit + events

| Outcome | IntegrationEvent | SupplierProductChangeLog |
|---|---|---|
| Auto-link | `auto_linked_to_existing_realproduct` (info) | `source=auto_link`, `field_path=real_product.link`, after={sku, ean, supplier_idx, diffs_pct, skipped_fields} |
| Tolerance fail (fallback) | `physical_tolerance_violation` (warning) | (no audit — no link was made) |
| Manual unlink | `manual_unlink_from_realproduct` (info) | `source=manual_unlink`, `field_path=real_product.unlink`, before={sku: old}, after={sku: new, ean} |

Auto-link event details ship `existing_supplier_idxs` so the operator can see
who else owns the RealProduct in one panel hop.

## Manual unlink — operator escape hatch

```
POST /api/suppliers/v2/admin/products/{pk}/unlink-from-realproduct/
```

Creates a fresh RealProduct (per-supplier-prefixed SKU via `generate_sku`),
moves the SP, deletes the old link, creates a new one with `is_preferred=True`.
Original RealProduct stays — other suppliers' links may still reference it.

Use when the auto-link decision is wrong (e.g. shared EAN actually points to
different physical products that slipped under the tolerance threshold).

400 — SP not linked, or generated SKU collides with an existing RealProduct.
404 — SP missing.

## Duplicate triage — `find_duplicate_realproducts`

```
docker compose exec volkanos python manage.py find_duplicate_realproducts --by ean
```

Read-only. Reports each EAN with multiple RealProducts plus a MERGE/REVIEW
suggestion based on the max pairwise weight diff:

```
Found 1 EAN group(s) with multiple RealProduct:

  EAN 5906214804074 (2 RealProducts):
    • FT-ce5b9e3089ff weight=0.150 width=20.00 height=10.00 deep=5.00 suppliers=fortrade ★
    • KH-8a1beaee63fe weight=0.155 width=20.50 height=10.10 deep=5.05 suppliers=kinghoff
    Suggestion: MERGE (max weight diff 3.3% within 10% tolerance)
```

Operator decides per group:

- **MERGE** — use the unlink endpoint backwards.
- **REVIEW** — physical fields diverge too much, probably different products.
- **KEEP SEPARATE** — currently a manual decision; a `RealProduct.verified_separate`
  flag is a candidate for a future polish.

## Edge cases

| Case | Behaviour |
|---|---|
| Empty / null SP EAN | No lookup. Standard `get_or_create(sku=generate_sku(...))`. |
| RealProduct.ean blank, SP has EAN | No match (the lookup filters on `ean=sp.ean` literally). Auto-link does NOT backfill the EAN onto the existing RP. |
| Multiple RealProducts share the EAN | `.order_by("id").first()` picks the oldest. Operator can disambiguate via the management command. |
| Tolerance fail | Always falls back to new RealProduct; never raises, never blocks the push. |
| `disable_ean_auto_link=True` | Skip lookup entirely. Per-supplier opt-out for "noisy data" suppliers. |
| Auto-link wrong → operator unlinks | Endpoint moves SP to fresh RealProduct; original RP untouched. |
| 2+ suppliers identical cost+stock | Out of scope here — auto-preferred selection handles it (deterministic by cost ASC, supplier.id ASC). |
| EAN-rename on supplier (D38) | Out of scope here — `RealProduct.ean` is not modified by auto-link. |

## First-link-preferred semantics

`product_link_service.upsert_for_push(sku, supplier, external_id, set_preferred_if_first=True)`
sets `is_preferred=True` when (and only when) the link is freshly created AND no
other link exists for this SKU. D25 is preserved on UPDATE — we never overwrite
operator state. The flip on CREATE was necessary so the pricemanager cost subscriber
stops ignoring single-supplier links that previously defaulted to `is_preferred=False`.

Auto-linked SPs (second supplier on an existing RP) get `is_preferred=False` at
creation — the auto-preferred strategy is what flips it.

## Related code

- `services/realproduct_match_service.py` — `find_match_by_ean`, `physical_tolerance_check`, `ToleranceResult`.
- `services/pim_writer.py:_emit_auto_link_event` / `_emit_tolerance_violation_event` / `_log_auto_link_audit` — event + audit emit helpers.
- `services/product_link_service.py:unlink_sp_from_realproduct` — operator force-unlink service.
- `api/admin/views/product_views.py:unlink_from_realproduct` — endpoint wrapper.
- `management/commands/find_duplicate_realproducts.py` — duplicate triage CLI.
- `models/supplier.py` — `realproduct_match_tolerance_pct`, `realproduct_match_strict`, `disable_ean_auto_link`.
- `migrations/0013_etap_13a_ean_match.py` — schema migration.

## Related docs

- `docs/audit-log.md` — full ChangeLogSource enum + write paths.
- `entirius-docs/.../volkanos/modules/suppliers/push-workflow.md` — `multi_supplier_overlap` event spec (now actually fires).
