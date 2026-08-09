---
title: Configuration
description: Settings, env vars, Celery queues, connector entry_points for django-suppliers.
sidebar:
  label: Configuration
---

## Django Settings

Module reads these from Django settings (with fallbacks). Set in service `settings/__init__.py` or env vars.

| Setting | Default | Purpose |
|---|---|---|
| `SUPPLIER_TEST_DB_HOST` | `localhost` | Test DB host. Docker: `db` |
| `SUPPLIER_BULK_BATCH_SIZE` | `500` | `bulk_create`/`bulk_update` batch size in `import_service` |
| `SUPPLIER_IMAGE_DOWNLOAD_TIMEOUT_S` | `30` | HTTP GET timeout for image downloads |
| `SUPPLIER_IMAGE_DOWNLOAD_RETRIES` | `2` | Celery retries for `download_supplier_images_task` |
| `SUPPLIER_IMAGE_DOWNLOAD_USER_AGENT` | `volkanos-supplier/1.0` | User-Agent for image fetches |

## Runtime Settings (DB singleton)

`SupplierSettings` (`pk=1`). Read via `SupplierSettings.load()`. Writable via `/api/suppliers/v2/admin/settings/` PATCH.

| Field | Default | Effect when set false / 0 |
|---|---|---|
| `auto_push_enabled` | true | `on_supplier_products_imported` handler skips Celery dispatch |
| `scraper_dispatch_enabled` | true | `ScraperConnector.dispatch_fetch*` emits `scraper_dispatch_skipped` event, no RabbitMQ call |
| `delta_sync_enabled` | true | Delta-context QMS / Pricemanager writes silently skipped (`process_delta_sync` only updates SP fields) |
| `integration_event_retention_days` | 90 | `prune_supplier_events_task` keeps last N days |
| `change_log_retention_days` | 180 | `prune_supplier_change_logs_task` (etap-04) keeps last N days of `SupplierProductChangeLog` rows |

Cache: `auto_push_enabled` cached 60s under Redis key `supplier:auto_push_enabled`. Invalidated on save via `transaction.on_commit` (D32) — rollback after failed update will not leave stale cache.

## Per-supplier knobs

Each Supplier carries optional configuration for the cross-supplier auto-match path (etap-13a), the auto-preferred selection (etap-13b), and the physical-race detection (etap-10). All have safe defaults — touch only when you know what changes.

| Field | Default | Origin | Purpose |
|---|---|---|---|
| `realproduct_match_tolerance_pct` | 10 | etap-13a | Max % difference on `weight` / `width` / `height` / `deep` for EAN-based auto-link to attach to an existing RealProduct |
| `disable_ean_auto_link` | false | etap-13a | Skip the EAN match entirely — every supplier push creates a fresh RealProduct |
| `allow_physical_writes_from_non_preferred` | false | etap-10 | Opt-in to the legacy last-write-wins behaviour on shared RealProducts. Audited as `physical_update_overwrite` warning |
| `preferred_strategy` | `lowest_cost_with_stock` | etap-13b | Auto-preferred picker. Other choices: `highest_stock`, `manual_only` |
| `preferred_switch_cooldown_hours` | 24 | etap-13b | Minimum hours between consecutive auto-preferred switches on the same RealProduct |
| `preferred_switch_hysteresis_pct` | 2 | etap-13b | Minimum cost-improvement (%) required before the auto-picker swaps the winner |
| `eval_frequency` | `daily` | etap-13b | Cron evaluation cadence — `daily` / `hourly` / `manual` (the last opts the supplier out of cron) |
| `default_product_class` | `0` (ProductBase) | etap-12 | PIM ProductClass used on INIT push. Set to `1` (ProductSimple) for storefront-renderable products |

**Known gotcha:** the four etap-13b knobs persist correctly and are readable via `GET /suppliers/{idx}/`, but the `SupplierUpdateRequest` Pydantic schema does NOT currently whitelist them. CMS PATCH calls silently strip them. Use Django admin or `manage.py shell` until the schema patch lands.

## Celery Queues

Worker MUST listen on these three queues (or routing rules direct tasks correctly).

| Queue | Tasks | Notes |
|---|---|---|
| `supplier_default` | `execute_feed_task`, `push_approved_for_supplier_task`, `prune_supplier_events_task` | General work |
| `supplier_images` | `download_supplier_images_task` | Separate queue avoids head-of-line blocking by image download (slow IO) |
| `supplier_results` | `process_scraper_results_task` | Callback from `scraper-workers` (external repo) |

Beat schedule (recommended in service):

```python
CELERY_BEAT_SCHEDULE = {
    "prune_supplier_events_daily": {
        "task": "django_suppliers.tasks.retention.prune_supplier_events_task",
        "schedule": crontab(hour=3, minute=0),  # 03:00 UTC
    },
    "prune_supplier_change_logs_daily": {
        "task": "django_suppliers.tasks.retention.prune_supplier_change_logs_task",
        "schedule": crontab(hour=3, minute=15),  # 03:15 UTC
    },
    "evaluate_preferred_suppliers_daily": {
        "task": "django_suppliers.tasks.preferred_strategy.evaluate_preferred_suppliers_task",
        "schedule": crontab(hour=3, minute=30),  # 03:30 UTC — etap-13b auto-preferred batch
    },
}
```

## Connector Entry_points

Connectors discovered via Python `entry_points` group `supplier_connectors`. The module ships `xml_feed` and `scraper`. Custom connectors register from any installed package.

```toml
# Custom connector package pyproject.toml
[project.entry-points."supplier_connectors"]
my_rest_api = "my_package.connectors:MyRestApiConnector"
```

Discovery happens at app `ready()` via `connector_registry.list_connectors()`. New connectors visible at `/api/suppliers/v2/admin/connectors/`.

See [Connector Contract](/volkanos/modules/suppliers/connector-contract/) for SyncConnector / AsyncConnector interfaces.

## URL Mounting

Service `urls.py`:

```python
urlpatterns = [
    # ... other includes
    path("", include("django_suppliers.urls")),
]
```

Mounts admin API under `/api/suppliers/v2/admin/...`. No public endpoints (all admin-only).

## Auth

All admin endpoints: `JWTAuthentication` + `IsAdminUser` (subclass requiring `is_staff or is_superuser`). Storefront customers (authenticated regular users) get 403.

## Test Settings

Module repo `tests/settings.py` is standalone Django config. Set `SUPPLIER_TEST_DB_HOST=db` inside Docker, `localhost` on host. INSTALLED_APPS in tests: `django_regional`, `django_pim`, `django_suppliers`. NOT included: `django_qms`, `django_pricemanager` (soft / out-of-scope per D5/D6).
