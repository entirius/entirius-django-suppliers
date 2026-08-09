---
title: Data Model
description: Entities, fields, constraints, relationships for django-suppliers.
sidebar:
  label: Data Model
---

11 models. All inherit `BaseModel` (`created_at`, `modified_at`). Source of truth: `src/django_suppliers/models/`. The `SupplierProductChangeLog` model was added in etap-04; the post-roadmap full inventory is captured in `repos/django-apps/django-suppliers/AGENTS.md` § Data Model.

## Supplier

Global registry. Multi-channel targeting via `MappingProfile`, not via per-supplier channel field.

| Field | Type | Notes |
|---|---|---|
| `idx` | SlugField, unique, max=64 | Stable identifier (sku_prefix candidate) |
| `name` | CharField max=128 | Display name |
| `supplier_role` | choices(trade/data/monitoring) | MVP accepts only `trade`. Other → 400 |
| `supplier_type` | choices(feed/manual/dropship) | MVP accepts `feed` and `manual`. Dropship → 400 |
| `review_mode` | choices(manual/auto) | `auto` enables signal-driven push after import |
| `is_active` | BooleanField | Soft-delete flag |
| `default_language` | FK django_regional.Language | PROTECT on_delete |
| `default_currency` | FK django_regional.Currency | PROTECT on_delete |
| `country` | FK django_regional.Country | nullable |
| `sku_prefix` | CharField max=10 | Used for derived PIM SKUs (`AMZ`, `IKE`) |
| `default_feature_set_idx` | CharField | FK-string to `pim.FeatureSet.idx`. Resolved at push time |
| `target_warehouse_code` | CharField | FK-string to `qms.Warehouse.code`. Soft (no QMS = silent skip) |
| `qty_subtract` / `qty_minimum` | int, MinValue=0 | Stock modifiers (D6) |
| `default_product_class` | int, default=0 | etap-12: PIM ProductClass on INIT push (`0`=ProductBase, `1`=ProductSimple) |
| `realproduct_match_tolerance_pct` | int, default=10 | etap-13a: max % diff on physical fields for EAN auto-link |
| `disable_ean_auto_link` | bool, default=False | etap-13a: per-supplier opt-out for EAN matcher |
| `allow_physical_writes_from_non_preferred` | bool, default=False | etap-10: opt-in legacy last-write-wins on shared RealProducts |
| `preferred_strategy` | CharField, default=`lowest_cost_with_stock` | etap-13b: auto-preferred picker. Other: `highest_stock`, `manual_only` |
| `preferred_switch_cooldown_hours` | int, default=24 | etap-13b: minimum hours between consecutive switches |
| `preferred_switch_hysteresis_pct` | int, default=2 | etap-13b: minimum cost-improvement % before swap |
| `eval_frequency` | CharField, default=`daily` | etap-13b: cron cadence — `daily` / `hourly` / `manual` |
| `company_name`, `contact_email`, `contact_phone`, `contact_person`, `notes`, `lead_time_days` | optional metadata | |
| `credentials` | JSONField | Per-connector API keys (TODO: encrypt in Phase 2) |

Indices: `idx`, `is_active`, `supplier_role`.

## SupplierFeed

Per-supplier, per-source-of-data. One supplier can have many feeds (e.g. main catalog + clearance).

| Field | Type | Notes |
|---|---|---|
| `supplier` | FK CASCADE | `related_name='feeds'` |
| `idx` | SlugField max=64 | UniqueConstraint with supplier |
| `connector_kind` | CharField max=64 | Resolved via entry_points (`xml_feed`, `scraper`, future plugins) |
| `feed_config` | JSONField | Connector-specific (URL, xpath, field_mapping, image_xpath) |
| `schedule_cron` | CharField max=64 | Empty = manual-only |
| `sync_mode` | choices(full/delta) | `full` rebuilds staging; `delta` updates cost/qty/physical only |
| `language`, `currency` | FK regional, nullable | Override supplier defaults |
| `feature_set_idx` | CharField | FK-string PIM FeatureSet override per feed |
| `is_active` | bool | Soft-disable |
| `last_sync_at`, `last_sync_status`, `last_run_id` | run history | populated by import_service |

UniqueConstraint: `(supplier, idx)`.

## SupplierProduct

Staging table — every imported product before it reaches PIM.

| Field | Type | Notes |
|---|---|---|
| `supplier` | FK CASCADE | |
| `feed` | FK SET_NULL | Track origin feed; survives feed deletion |
| `external_id` | CharField max=128, indexed | Supplier's SKU |
| `external_id_history` | JSONField default list | EAN-rename FIFO cap=20 (D38) |
| `name` | CharField max=512, indexed | |
| `cost`, `currency` | Decimal + 3-char | `currency` matches ISO 4217 |
| `stock` | int, nullable | |
| `ean` | CharField max=14, indexed | EAN-13 + check |
| `url` | URLField | Supplier's product URL |
| `image_urls` | JSONField | List of strings |
| `data` | JSONField | Custom attributes (mapped to PIM features at push) |
| `data_hash` | CharField max=40, indexed | SHA1 of data — diff detection |
| `status` | choices, max=30, indexed | new/queued/approved/rejected/pushed_pending_images/pushed |
| `feature_set_idx_override` | CharField | Per-SP override for resolve hierarchy |
| `real_product` | FK pim.RealProduct, SET_NULL | Set after first push |
| `pushed_to_channel_idxs` | JSONField default list | Idempotency guard |
| `images_complete_channel_idxs` | JSONField default list | Status flips to `pushed` when set matches `pushed_to_channel_idxs` |
| `last_synced_at`, `data_changed_at`, `physical_changed_at` | timestamps | |
| `reviewed_by`, `reviewed_at`, `pushed_by`, `pushed_at` | audit | |

UniqueConstraint: `(supplier, external_id)`.

## SupplierMappingProfile + AttributeMapping + CategoryMapping

Multi-channel targeting + value mapping.

**SupplierMappingProfile:**

| Field | Notes |
|---|---|
| `supplier` | FK CASCADE related_name='mapping_profiles' |
| `idx` | UniqueConstraint with supplier |
| `target_channel_idxs` | JSONField list of `pim.Channel.idx` strings — D24 active profiles must be disjoint |
| `import_language` | FK regional, nullable — overrides supplier default |
| `feature_set_idx` | FK-string FeatureSet override |
| `is_active` | bool — disjoint constraint applies only to active |

**SupplierAttributeMapping:**

| Field | Notes |
|---|---|
| `profile` | FK CASCADE related_name='attribute_mappings' |
| `source_field` | Special tokens: `__name__`, `__cost__`, `__ean__` — others → key in `SupplierProduct.data` |
| `target_type` | choices(feature/real_product/skip) |
| `target_identifier` | PIM Feature.idx OR RealProduct field name (whitelisted: weight/ean/width/height/deep/kind_of_product) |
| `is_required` | bool — fail import if missing |

UniqueConstraint: `(profile, source_field)`.

**SupplierCategoryMapping:**

| Field | Notes |
|---|---|
| `profile` | FK CASCADE related_name='category_mappings' |
| `source_field` | Field path in SP.data (e.g. `category_path`) |
| `source_value` | Literal value to match |
| `target_category_idx` | FK-string `pim.ProductCategory.idx` |

UniqueConstraint: `(profile, source_field, source_value)`.

## ProductSupplierLink

Multi-supplier-per-product. M2M-style. Operator owns the preferred flag.

| Field | Notes |
|---|---|
| `real_product_sku` | CharField max=128, indexed (FK-string to PIM RealProduct) |
| `supplier` | FK CASCADE related_name='product_links' |
| `external_id` | Supplier's SKU for this product |
| `priority` | int, default=0 — operator-set |
| `is_preferred` | bool, default=false — D25: `upsert_for_push` never touches this; `set_preferred` unsets siblings. etap-13a refinement: keyword-only `set_preferred_if_first=False` lets `_write_qms_cost_link` mark the first link `True` at creation so the cost subscriber treats single-supplier links as preferred |
| `is_active` | bool |
| `notes` | TextField — operator notes |
| `manual_override` | bool, default=False — etap-13b sticky bit: True blocks auto-preferred (cron + inline + emergency) until `reset_to_auto` clears it |
| `preferred_changed_at` | DateTimeField, nullable — etap-13b: stamped on every preferred flip; used by cooldown gate |

UniqueConstraint: `(real_product_sku, supplier)`.

## ImportLog

Per-feed-run summary.

| Field | Notes |
|---|---|
| `feed` | FK CASCADE |
| `run_id` | UUIDField, unique |
| `mode` | choices(full/delta/test) |
| `status` | choices(running/success/partial/failed) |
| `started_at`, `finished_at` | DateTime |
| `total_count`, `new_count`, `updated_count`, `unchanged_count`, `delisted_count`, `error_count` | counters. `updated_count` semantics tightened to cost/qty-only in etap-10 (physical changes moved to dedicated counters) |
| `pushed_delisted_count` | int, default=0 — etap-12: pushed-but-delisted split out from `delisted_count` for accurate operator dashboards |
| `physical_updated_count` | int, default=0 — etap-10 |
| `physical_skipped_non_preferred_count` | int, default=0 — etap-10: race-skipped attempts by non-preferred suppliers |
| `physical_overwrite_count` | int, default=0 — etap-10: opt-in overwrites by non-preferred suppliers (with warning event) |
| `error_summary` | JSONField cap=50 |
| `triggered_by` | FK auth.User SET_NULL |
| `source` | choices(cli/api/scheduler/signal) |

## IntegrationEvent

Per-event severity log. Read by CMS Events mode and Grafana.

| Field | Notes |
|---|---|
| `event_type` | CharField, indexed — full taxonomy in [Monitoring](/volkanos/modules/suppliers/monitoring/) |
| `severity` | choices(critical/warning/info), indexed |
| `supplier`, `feed`, `supplier_product` | FKs SET_NULL |
| `message` | TextField |
| `details` | JSONField (structured payload per event_type) |
| `acknowledged_at`, `acknowledged_by` | nullable — operator ack flow |

Indices: `(event_type, severity)`, `created_at desc`, `acknowledged_at`.
Retention: configurable via `SupplierSettings.integration_event_retention_days` (default 90). Daily Celery task `prune_supplier_events_task`.

## SupplierProductChangeLog (etap-04)

Per-field audit log. Source of truth for "who wrote what when". Powers the CMS PIM + Suppliers panel bridges (etap-05/06/07).

| Field | Notes |
|---|---|
| `supplier_product` | FK SET_NULL |
| `real_product_sku` | CharField — denormalised for cross-supplier queries when SP deleted |
| `source` | CharField, 17 active variants — see below |
| `field_path` | CharField — e.g. `real_product.weight`, `feature.42.value_txt_t9n.en`, `physical_skipped` |
| `before` | JSONField — value before the write |
| `after` | JSONField — value after the write |
| `applied_to_pim` | bool — distinguishes "we acknowledged the change" from "we wrote it to PIM" (etap-10 physical_skipped uses False) |
| `actor` | FK auth.User SET_NULL — nullable for system-driven writes |
| `acknowledged_at`, `acknowledged_by` | nullable — operator ack flow via `POST /pim-sku/{sku}/acknowledge/` |

**Source variants (17 across 4 cohorts):**

| Cohort | Sources |
|---|---|
| Base (etap-04) | `init_push`, `force_repush`, `operator_sp_edit`, `operator_acknowledge`, `language_fallback`, `mapping_transform`, `auto_link`, `manual_unlink` |
| Cost subscriber (etap-11) | `cost_signal_received`, `cost_ignored_non_preferred`, `cost_ignored_no_link`, `cost_skipped_admin_override`, `cost_skipped_resolution_failed` |
| Auto-preferred (etap-13b) | `auto_preferred_switch`, `manual_override`, `emergency_switch` |
| Operator merge (etap-07b) | `manual_merge` |
| Physical race (etap-10) | `physical_skipped`, `physical_overwrite` |

Retention: `SupplierSettings.change_log_retention_days` (default 180). Daily Celery task `prune_supplier_change_logs_task` (etap-04).

Indices: `(real_product_sku, source)`, `created_at desc`, `acknowledged_at`.

## SupplierSettings (singleton)

`pk=1` enforced in `save()`. Pattern: `pim.PimSettings.load()`.

| Field | Default |
|---|---|
| `auto_push_enabled` | true — kill switch for `on_supplier_products_imported` handler |
| `scraper_dispatch_enabled` | true — kill switch for `ScraperConnector.dispatch_fetch*` |
| `delta_sync_enabled` | true — kill switch for delta-context QMS / Pricemanager writes |
| `integration_event_retention_days` | 90 |

Cache: `auto_push_enabled` cached 60s under `supplier:auto_push_enabled` (Redis). Invalidated on save via `transaction.on_commit` (D32).

## Status Workflow (SupplierProduct)

```
new        → queued     │ new → approved (bulk)         │ new → rejected (bulk)
queued     → approved   │ queued → rejected             │ queued → new (skip)
rejected   → queued     │ approved → rejected           │ approved → pushed_pending_images
pushed_pending_images → pushed                          │ pushed → approved (force re-push)
```

Forbidden: `new → pushed`, `rejected → pushed`. Every push goes through `approved`.

## Resolve Hierarchy

**feature_set:** SP override → Feed → MappingProfile → Supplier default. First non-NULL wins.
**language:** `MappingProfile.import_language` (if set) → `supplier.default_language`.

## FeatureType → ProductAttribute mapping

| PIM FeatureType | Storage field |
|---|---|
| BOOL (1) | `value_int` (0/1) |
| DECIMAL (2), TEMPERATURE (12), LENGTH (13), MASS (14) | `value_dec` |
| VARCHAR255 (3) | `value_txt` |
| VARCHAR255_T9N (4) | `value_txt_t9n[lang]` |
| TEXT (5) | `value_txt` |
| TEXT_T9N (6) | `value_txt_t9n[lang]` |
| SELECT (7), MULTISELECT (8) | FK `attribute` (lookup by `(feature, idx)`) |
| JSON (9) | `value_json` |
| JSON_T9N (11) | `value_json_t9n[lang]` |
| DATETIME (10) | `value_dt` |
