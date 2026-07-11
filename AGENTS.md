# AGENTS.md

Suppliers Django module for the Volkanos ecommerce platform — distribution `entirius-django-suppliers`,
Django app `django_suppliers`. Manages supplier registry, feed-driven product import pipeline,
review/approval workflow, one-time push to PIM, and cyclic delta refresh of cost / qty / physical
attributes. Owns the cross-supplier story: auto EAN-match, preferred-supplier selection (auto + manual
override + emergency switch), per-field audit log, and a CMS bridge that surfaces "supplier touched
this PIM SKU" badges.

**Tech:** Python >=3.11, Django >=5.0, DRF, Pydantic, drf-spectacular, lxml, requests,
Celery 5+, entirius-django-utils (BaseModel), entirius-django-regional, entirius-django-pim.
Optional: django_qms (soft dep, try/except), django_pricemanager (soft dep — consumes
`cost_updated_signal` if installed).

## Commands

| Command | Meaning |
|---|---|
| `make install` | sync dependencies (uv, incl. extras) |
| `make check` | lint + format-check (ruff) |
| `make fix` | auto-fix lint + format |
| `make test` | test suite (pytest + pytest-django) |

## Conventions

- English only: code, docs, commits, branches, PRs.
- MPL-2.0: every non-trivial source file carries the license header (pre-commit inserts it).
- Toolchain: uv + ruff + hatchling + pytest; all config in `pyproject.toml`; `uv.lock` committed.
- Git flow: `master` (production) + `develop` (integration); changes land via PR; semver tag on `master`.
- Never rename the package / Django app_label / DB table prefix `django_suppliers` — it is a schema contract.
- Migrations are part of the public contract — never edit an already released migration.
- Default: do not commit — git is the user's call.

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

## Architecture

```
src/django_suppliers/
├── apps.py, settings.py, urls.py, enums.py, admin.py
│
├── models/                                # 11 ORM models, one file per entity
│   ├── supplier.py                        #   Supplier + matching/preferred/race knobs
│   ├── supplier_settings.py               #   Singleton (auto_push, delta_sync, retentions)
│   ├── supplier_feed.py                   #   Per-supplier feed config
│   ├── supplier_product.py                #   Staging row + real_product FK
│   ├── supplier_mapping_profile.py        #   Multi-channel target + value mapping
│   ├── supplier_attribute_mapping.py      #   source_field → PIM Feature/RealProduct + modifier
│   ├── supplier_category_mapping.py       #   source_value → PIM Category idx
│   ├── product_supplier_link.py           #   Multi-supplier per PIM SKU + manual_override
│   ├── import_log.py                      #   Per-run summary + physical_* counters
│   ├── integration_event.py               #   Per-event severity log (50+ event types)
│   └── supplier_product_change_log.py     #   Per-field audit — 17 active source variants
│
├── schemas/
│   ├── contract/                          #   RawProduct, PriceStockUpdate (connector boundary)
│   ├── requests/                          #   Pydantic create/update/bulk schemas
│   └── responses/                         #   Pydantic list/detail schemas
│
├── services/                              # 26 modules, framework-agnostic
├── connectors/                            # base (Sync/Async), xml_feed, scraper
├── signals/                               # definitions, handlers, killswitch
├── tasks/                                 # feed_execution, push_pipeline, image_download,
│                                          # scraper_callback, retention, preferred_strategy
├── api/admin/                             # views/, permissions, pagination, throttling, urls
└── management/commands/                   # 7 commands (see § Commands)
```

Layer rule: `API → Services → Models → DB`. ViewSets do not import models — every ORM access goes
through services. Schemas never import Django models.

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

## API Surface

URL prefix: `/api/suppliers/v2/admin/`. Auth: `JWTAuthentication` + `IsAdminUser` (custom subclass:
`is_staff or is_superuser`). Throttling: `suppliers_change_log_bulk` 120/min on `pim-sku/has-changes/`.

| Resource | Path |
|---|---|
| Suppliers | `suppliers/` (GET/POST), `suppliers/{idx}/` (GET/PATCH/DELETE), `suppliers/{idx}/delete-impact/`, `suppliers/{idx}/data-keys/` (flattened `SupplierProduct.data` keys + reserved tokens; cached 5 min, invalidated by `execute_feed_task`), `suppliers/{idx}/data-values/?source_field=X&limit=N`, `suppliers/{idx}/credentials/` (audited) |
| Feeds | `suppliers/{idx}/feeds/...` (CRUD + `/trigger/` + `/test/`) |
| Mapping profiles | `suppliers/{idx}/mapping-profiles/...` (CRUD + `/validate/` — returns `warnings: list[MappingWarning]` with structured codes; `mapping_id` present for category/attribute warnings) |
| Attribute mappings | `mapping-profiles/{pk}/attribute-mappings/...` (CRUD — per-row `modifier`; see `docs/mapping-modifiers.md`) |
| Category mappings | `mapping-profiles/{pk}/category-mappings/...` (CRUD) |
| Products (cross-supplier) | `products/` (GET — denormalises `real_product_sku`, `supplier_idx`, `supplier_name`), `products/{pk}/` (GET/PATCH), bulk-approve/reject/requeue, per-pk approve/reject/skip/queue/push/force-repush, `products/{pk}/unlink-from-realproduct/` (POST — audits as `manual_unlink`) |
| Product Supplier Links | `product-links/` (CRUD) + `/{pk}/set-preferred/` |
| Import Logs | `import-logs/` (GET) + retrieve |
| Events | `events/` (GET) + retrieve + `/{pk}/acknowledge/` |
| PIM SKU bridge | `pim-sku/has-changes/?skus=A,B,...` (bulk badge lookup, max 100 SKUs, throttled), `pim-sku/{sku}/changes/` (timeline with `?since`, `?source`, `?unseen_only`), `pim-sku/{sku}/acknowledge/` (POST — idempotent), `pim-sku/{sku}/force-repush/` (POST — partial-success semantics) |
| Preferred override | `pim-sku/{sku}/set-preferred-supplier/` (POST — sets `manual_override=True`), `pim-sku/{sku}/reset-preferred-to-auto/` (POST — clears override + inline auto-eval with cooldown bypass) |
| RealProducts (cross-supplier) | `realproducts/merge-by-ean/` (POST — atomic winner/loser merge + audit `source=manual_merge`), `auto-matched/` (GET — distinct-SKU listing of auto-linked RealProducts), `duplicates/?tolerance_pct=10` (GET — per-EAN group with merge/review suggestion) |
| Push | `push/` (BulkPushView) |
| Settings | `settings/` (GET/PATCH singleton) |
| Connectors | `connectors/` (entry_points discovery list) |

OpenAPI: `extend_schema` everywhere, `SchemaGenerator().get_schema()` validates with 0 warnings, 0 errors.

## Testing

Tests run on postgres via `DATABASE_URL` (CI provides a postgres service; locally point it at any
postgres 15+).

```bash
make install
make test
```

INSTALLED_APPS in tests: `django_regional`, `django_pim`, `django_suppliers`.
**NOT** `django_qms` / `django_pricemanager` (soft deps, out of scope).

## Commands (management)

| Command | Purpose |
|---|---|
| `execute_supplier_feed --feed-id N \| --supplier-idx X --feed-idx Y [--mode full\|delta] [--async]` | Trigger feed run from CLI |
| `push_supplier_approved --supplier-idx X [--async]` | Push all approved SPs for supplier |
| `prune_supplier_events` | Apply IntegrationEvent retention policy (Celery beat target) |
| `prune_supplier_change_logs [--days N]` | Apply SupplierProductChangeLog retention (Celery beat target) |
| `evaluate_preferred_suppliers [--sku SKU] [--bypass-safety]` | Synchronous auto-preferred batch. Equivalent to the celery beat task |
| `find_duplicate_realproducts --by ean [--tolerance-pct N]` | Read-only EAN-collision triage — shared with the REST endpoint |
| `supplier_status` | Health summary: settings + counts + top 5 logs + unack events |

## Celery Queues (must be configured in service)

| Queue | Tasks |
|---|---|
| `supplier_default` | `execute_feed_task`, `push_approved_for_supplier_task`, `prune_supplier_events_task`, `prune_supplier_change_logs_task`, `evaluate_preferred_suppliers_task` |
| `supplier_images` | `download_supplier_images_task` (separate queue: slow IO) |
| `supplier_results` | `process_scraper_results_task` (callback from scraper workers) |

`evaluate_preferred_suppliers_task` is the auto-preferred batch evaluator — register on celery beat
(default 03:00 UTC daily). Falls back to `python manage.py evaluate_preferred_suppliers` when beat is
down or the operator wants synchronous output.

## Gotchas

1. **Naming is plural** — repo and package are `django-suppliers` / `django_suppliers`.
2. **Pricemanager subscriber is live** — zero `import django_pricemanager` in suppliers code, but `cost_updated_signal` has a real subscriber when pricemanager is installed. Soft coupling preserved.
3. **QMS soft dep** — `try/except (ImportError, RuntimeError)` via `_qms_available()` cached. `RuntimeError` catches the dev case where `django_qms` is on PYTHONPATH but not in INSTALLED_APPS. 4 fail paths emit info events; failure NEVER re-raised.
4. **Scraper workers live outside this module** — they own scraper drivers + anti-ban infra. Contract via RabbitMQ queues `scraping` (push) and `supplier_results` (callback).
5. **`transaction.on_commit` mandatory** — every Celery `.delay()` in signal handlers MUST be wrapped. DB rollback after fail must NOT leave a dispatched task running against missing rows.
6. **`select_for_update(of=("self",))` on push** — bare `select_for_update()` fails when `select_related("feed")` joins nullable FK (Postgres NotSupportedError).
7. **`ProductAttribute.create()` per row** — NOT `bulk_create`. PIM `post_save` signals (cache invalidation, search reindex, inheritance) must fire.
8. **PIM `upload_picture` race not safe** — functionally idempotent under sequential re-run, NOT under true concurrent access.
9. **Active mapping profiles disjoint** — same supplier's active profiles cannot share `target_channel_idxs`. UI filters available channels; service raises `ValueError`.
10. **`upsert_for_push` minimal touch** — `defaults={"external_id": ...}` only on the upsert. Operator owns `is_preferred` / `priority` / `notes` / `is_active` on UPDATE. The keyword-only `set_preferred_if_first=False` kwarg lets `pim_writer._write_qms_cost_link` pass `True` so the FIRST link for a SKU becomes `is_preferred=True` at creation.
11. **Language resolution** — `pim_writer.resolve_language_for_channel(profile, supplier, channel)` returns a `LanguageResolution` dataclass. Fallback emits `IntegrationEvent.language_fallback` + audit row. Callers pass `event_sink=[]` to push functions; `PushResponse.events` carries the dicts to CMS for toast notifications.
12. **Mapping value modifiers** — applied by `services.value_transformer.transform(value, modifier)` between `_lookup_value` and the PIM write. Numeric modifiers operate on `Decimal`; string modifiers require `isinstance(value, str)`. Failures never raise — raw value used + warning event. Reference: `docs/mapping-modifiers.md`.
13. **Auto EAN-match** — `init_push_to_channel` calls `realproduct_match_service.find_match_by_ean` BEFORE `RealProduct.get_or_create`. Tolerance fail falls back to fresh RealProduct + warning event. Reference: `docs/auto-ean-match.md`.
14. **Auto-preferred selection** — `preferred_strategy_service` owns the decision; `apply_preferred_switch` performs the atomic flip. Three triggers: inline after `_write_qms_cost_link`, inline emergency on stock=0 (bypass cooldown + hysteresis), celery beat daily. `manual_override` sticky bit blocks all three until `reset_to_auto`.
15. **Cross-supplier merge** — `realproduct_merge_service.merge_realproducts(winner_sku, loser_sku, reason, actor)` is atomic: link redirect + `SupplierProduct.real_product` back-fill + loser RP delete + audit + event. Re-validates EAN equality first.
16. **Physical race detection** — `_apply_physical_update_to_real_product` returns `PHYSICAL_OUTCOME_{NOOP, APPLIED, SKIPPED_NON_PREFERRED, OVERWRITTEN}`. Preferred-only default; per-supplier opt-in restores last-write-wins with warning + audit. Edge case: SP with `real_product` but no `ProductSupplierLink` is treated as non-preferred (skip).
17. **Preferred knobs partially wired** — `Supplier.preferred_strategy` / cooldown / hysteresis / `eval_frequency` are persisted and readable, but `SupplierUpdateRequest` schema and `_EDITABLE_FIELDS` whitelist do NOT include them; Pydantic v2 silently strips extra fields, so CMS "Saved" toast lies. Workaround: Django admin or shell until the schema is patched.
18. **`pim-sku/{sku}/changes/` uses `changes:` key (not `results:`)** — inconsistent with the standard pagination shape; clients should read `response.changes`.
19. **Decimal precision in tests** — `RealProduct.weight` has `decimal_places=2`, so `0.150` becomes `0.16` on save. Physical-race tests must use 2-decimal-place values.

## Reference Docs

| File | Content |
|---|---|
| `docs/erd-config.yaml` | ERD diagram groups (core-registry, feeds-and-import, mapping, monitoring) |
| `docs/audit-log.md` | `SupplierProductChangeLog` write paths, 17 source variants, retention behaviour |
| `docs/auto-ean-match.md` | Auto-link path, tolerance checks, operator unlink API |
| `docs/mapping-modifiers.md` | Modifier choices, value_transformer dispatch, failure semantics |
