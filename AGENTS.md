# AGENTS.md

`entirius-django-suppliers` (Django app `django_suppliers`) — suppliers module of Entirius. Manages supplier
registry, feed-driven product import pipeline, review/approval workflow, one-time push to PIM, and cyclic delta
refresh of cost / qty / physical attributes. Owns the cross-supplier story: auto EAN-match, preferred-supplier
selection (auto + manual override + emergency switch), per-field audit log, and a CMS bridge that surfaces
"supplier touched this PIM SKU" badges.

Hard deps: entirius-django-utils (BaseModel), entirius-django-regional, entirius-django-pim. Optional:
django_qms (soft dep, try/except), django_pricemanager (soft dep — consumes `cost_updated_signal` if installed).

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
- Access: areas live on the AppConfig (`access_areas`, `access_route_rules`), every admin view carries
  `access_area`; a new admin route without one fails `tests/test_access_ownership.py`.
- Default: do not commit — git is the user's call.
- No AI attribution in commits or PR descriptions.

## Architecture

```
src/django_suppliers/
├── apps.py, settings.py, urls.py, enums.py, admin.py
│
├── models/                                # ORM models, one file per entity
│   ├── supplier.py                        #   Supplier + matching/preferred/race knobs
│   ├── supplier_settings.py               #   Singleton (auto_push, delta_sync, retentions)
│   ├── supplier_feed.py                   #   Per-supplier feed config
│   ├── supplier_product.py                #   Staging row + real_product FK
│   ├── supplier_mapping_profile.py        #   Multi-channel target + value mapping
│   ├── supplier_attribute_mapping.py      #   source_field → PIM Feature/RealProduct + modifier
│   ├── supplier_category_mapping.py       #   source_value → PIM Category idx
│   ├── product_supplier_link.py           #   Multi-supplier per PIM SKU + manual_override
│   ├── import_log.py                      #   Per-run summary + physical_* counters
│   ├── integration_event.py               #   Per-event severity log
│   └── supplier_product_change_log.py     #   Per-field audit
│
├── schemas/
│   ├── contract/                          #   RawProduct, PriceStockUpdate (connector boundary)
│   ├── requests/                          #   Pydantic create/update/bulk schemas
│   └── responses/                         #   Pydantic list/detail schemas
│
├── services/                              # framework-agnostic business logic
├── connectors/                            # base (Sync/Async), xml_feed, scraper
├── signals/                               # definitions, handlers, killswitch
├── tasks/                                 # feed_execution, push_pipeline, image_download,
│                                          # scraper_callback, retention, preferred_strategy
├── api/admin/                             # views/, permissions, pagination, throttling, urls
└── management/commands/                   # see docs/commands.md
```

Layer rule: `API → Services → Models → DB`. ViewSets do not import models — every ORM access goes
through services. Schemas never import Django models.

Signals (`signals/definitions.py`): `supplier_products_imported_signal`, `cost_updated_signal`,
`supplier_product_pushed_signal`, `preferred_switched_signal`. Receivers MUST be idempotent. Killswitches:
`signals.killswitch.suppress_supplier_signals()` and `SupplierSettings.auto_push_enabled` (DB-level, cached 60s).

| Topic | Where |
|---|---|
| Overview, data flow (sync paths, propagation, force re-push), signals | `docs/index.md` |
| Settings, runtime singleton, per-supplier knobs, Celery queues and beat, URL mounting | `docs/configuration.md` |
| Admin API resources | `docs/api.md` |
| Management commands | `docs/commands.md` |
| Running the tests | `docs/testing.md` |
| Entities, fields, constraints | `docs/data-model.md`, `docs/erd.md` |
| ERD diagram groups (core-registry, feeds-and-import, mapping, monitoring) | `docs/erd-config.yaml` |
| Review statuses and transitions; INIT push and force re-push | `docs/review-workflow.md`, `docs/push-workflow.md` |
| Connector contract, RabbitMQ queues, entry points | `docs/connector-contract.md` |
| Cost subscriber, preferred-supplier selection | `docs/price-handling.md`, `docs/preferred-strategy.md` |
| `SupplierProductChangeLog` write paths, source variants, retention behaviour | `docs/audit-log.md` |
| Auto-link path, tolerance checks, operator unlink API | `docs/auto-ean-match.md` |
| Modifier choices, value_transformer dispatch, failure semantics | `docs/mapping-modifiers.md` |
| IntegrationEvent taxonomy | `docs/monitoring.md` |


## Settings

Module settings (`SUPPLIER_*`, read in `src/django_suppliers/settings.py`), the `SupplierSettings` DB singleton and
the per-supplier knobs are described in `docs/configuration.md`.

Celery queues the service must configure: `supplier_default`, `supplier_images` (image downloads, slow IO),
`supplier_results` (callback from scraper workers).

## Gotchas

1. **Naming is plural** — distribution `entirius-django-suppliers`, package `django_suppliers`.
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
