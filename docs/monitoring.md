---
title: Monitoring
description: IntegrationEvent taxonomy, recommended Grafana queries, alert rules.
sidebar:
  label: Monitoring
---

`IntegrationEvent` is the operational telemetry table. Every meaningful state transition emits a row. Operators read it via CMS Events mode; ops reads it via Grafana over PostgreSQL datasource.

## Event Types

| event_type | Default severity | When emitted | `details` schema |
|---|---|---|---|
| `feed_failed` | critical | `import_service` finalize after exception | `{exception, traceback}` |
| `feed_no_run` | warning | `supplier_status` command detects feed without recent run | `{feed_id, days_since_last}` |
| `import_completed` | info | Successful feed run finalize | `{run_id, totals, mode}` |
| `mass_delisting` | warning | >50% of pushed SPs delisted in single full sync | `{delisted_count, pushed_count, ratio}` |
| `pushed_product_delisted` | info | Single pushed SP delisted | `{external_id}` |
| `unknown_external_id_in_delta` | info | Delta sync references SP not in staging | `{external_id, run_id}` |
| `ean_collision_detected` | warning | EAN-match heuristic finds existing SP with different external_id | `{old_external_id, new_external_id, ean}` |
| `cost_updated` | info | `pricemanager_writer.log_cost` per channel | `{channel_idx, old_cost, new_cost, currency}` |
| `cost_missing` | warning | Delta sync cost missing for pushed SP | `{external_id}` |
| `currency_missing` | warning | Delta sync currency missing | `{external_id}` |
| `stock_updated` | info | `qms_writer.write_stock` succeeded | `{channel_idx, new_stock}` |
| `qms_not_installed_skipped` | info | `qms_writer` soft skip (no `django_qms` import) | `{}` |
| `qms_warehouse_not_configured` | warning | Supplier missing `target_warehouse_code` | `{supplier_idx}` |
| `qms_no_real_product` | warning | Stock write attempted for SP without `real_product` | `{external_id}` |
| `qms_write_failed` | warning | QMS service raised; soft contract — not re-raised | `{exception}` |
| `image_failed` | warning | `download_supplier_images_task` HTTP failure per URL | `{url, status_code, exception}` |
| `image_download_started`, `image_download_completed` | info | Per-task lifecycle | `{sp_id, channel_idx, count}` |
| `push_succeeded` | info | INIT push completed for channel | `{channel_idx, sku, real_product_created}` |
| `push_failed` | critical | Push transaction rollback | `{exception, channel_idx}` |
| `force_repush_executed` | info | Operator-triggered force re-push | `{user_id, channels}` |
| `physical_update_applied` | info | Delta sync updated RealProduct physical fields | `{sku, fields}` |
| `multi_supplier_overlap` | info | Push detects existing ProductSupplierLink with different supplier | `{existing_supplier_idxs, new_supplier_idx, sku}` |
| `pim_feature_missing` / `pim_attribute_missing` / `pim_category_missing` / `pim_feature_set_missing` / `pim_channel_missing` | warning | Mapping resolve fails per row | `{idx, source_value?}` |
| `attribute_value_missing` | warning | Source field empty for required mapping | `{source_field, profile_idx}` |
| `channel_removed_from_profile` | warning | Profile updated, channel dropped from target_channel_idxs | `{profile_idx, removed_channel_idxs, affected_skus_count}` |
| `supplier_deleted` | warning | Hard delete cascade (D37) | `{supplier_idx, affected_links_count, affected_pushed_skus}` |
| `scraper_dispatch_skipped` | info | Killswitch skipped scraper RabbitMQ dispatch | `{feed_id}` |
| `scraper_banned` | warning | Scraper-workers reported ban event | `{domain}` |
| `scraper_results_validation_errors` | warning | Scraper callback Pydantic-validate fail | `{run_id, errors}` |
| `scraper_unknown_run_id` | warning | Scraper callback for unknown run | `{run_id}` |
| `scraper_callback_signature_invalid` | warning | HMAC signature mismatch on scraper callback | `{run_id}` |
| `supplier_credentials_viewed` | info | Operator opened `suppliers/{idx}/credentials/` | `{viewer_user_id, supplier_idx}` |
| `language_fallback` | warning | etap-08: push fell back from supplier default to channel default | `{channel_idx, supplier_lang, used_lang}` |
| `mapping_transform_failed` | warning | etap-09: `value_transformer` returned `applied=False` | `{modifier, source_field, error}` |
| `auto_linked_to_existing_realproduct` | info | etap-13a: SP attached to existing RealProduct via EAN match | `{sku, ean, supplier_idx}` |
| `physical_tolerance_violation` | warning | etap-13a: EAN match found, but physical fields exceeded tolerance | `{sku, ean, deltas}` |
| `manual_unlink_from_realproduct` | info | etap-13a: operator-triggered unlink via `unlink-from-realproduct/` | `{old_sku, new_sku, reason}` |
| `preferred_supplier_switched` | info | etap-13b: auto-preferred flipped `is_preferred` via cron / inline | `{sku, from_supplier_idx, to_supplier_idx}` |
| `preferred_supplier_emergency_switch` | warning | etap-13b: emergency switch (preferred stock=0, bypasses cooldown + hysteresis) | `{sku, from, to, stock_was}` |
| `preferred_supplier_forced` | warning | etap-13b: operator manual override via `set-preferred-supplier/` | `{sku, from, to, reason, actor}` |
| `preferred_switch_skipped_cooldown` | info | etap-13b: candidate switch blocked by cooldown gate | `{sku, since_last_switch_hours}` |
| `preferred_switch_skipped_hysteresis` | info | etap-13b: candidate switch blocked by hysteresis threshold | `{sku, improvement_pct, threshold_pct}` |
| `preferred_switch_skipped_manual_override` | info | etap-13b: candidate switch blocked by sticky `manual_override` | `{sku, locked_supplier_idx}` |
| `realproduct_manually_merged` | info | etap-07b: operator triggered `realproducts/merge-by-ean/` | `{winner_sku, loser_sku, reason, actor}` |
| `physical_update_skipped_non_preferred` | info | etap-10: non-preferred supplier blocked from physical write | `{sku, supplier_idx, skipped_fields}` |
| `physical_update_overwrite` | warning | etap-10: opt-in non-preferred physical write applied (audited) | `{sku, supplier_idx, fields, previous_writer}` |

## Recommended Grafana Queries

PostgreSQL datasource. Adjust schema/table prefix per service.

### Failed feeds last 24h

```sql
SELECT s.idx AS supplier, f.idx AS feed, e.message, e.created_at
FROM django_suppliers_integrationevent e
JOIN django_suppliers_supplierfeed f ON f.id = e.feed_id
JOIN django_suppliers_supplier s ON s.id = f.supplier_id
WHERE e.event_type = 'feed_failed'
  AND e.created_at > NOW() - INTERVAL '24 hours'
ORDER BY e.created_at DESC;
```

### Image download success rate per supplier (last 7d)

```sql
SELECT
  s.idx AS supplier,
  COUNT(*) FILTER (WHERE e.event_type = 'image_download_completed') AS completed,
  COUNT(*) FILTER (WHERE e.event_type = 'image_failed') AS failed,
  ROUND(
    100.0 * COUNT(*) FILTER (WHERE e.event_type = 'image_download_completed')
    / NULLIF(COUNT(*), 0),
  2) AS success_pct
FROM django_suppliers_integrationevent e
JOIN django_suppliers_supplier s ON s.id = e.supplier_id
WHERE e.event_type IN ('image_download_completed', 'image_failed')
  AND e.created_at > NOW() - INTERVAL '7 days'
GROUP BY s.idx
ORDER BY success_pct ASC NULLS LAST;
```

### Mass delisting alerts (last 30d)

```sql
SELECT s.idx AS supplier, e.details->>'ratio' AS ratio,
       e.details->>'delisted_count' AS delisted, e.created_at
FROM django_suppliers_integrationevent e
JOIN django_suppliers_supplier s ON s.id = e.supplier_id
WHERE e.event_type = 'mass_delisting'
  AND e.created_at > NOW() - INTERVAL '30 days'
ORDER BY e.created_at DESC;
```

### Pushed products with stale data (>7d since last sync)

```sql
SELECT s.idx AS supplier, sp.external_id, sp.last_synced_at
FROM django_suppliers_supplierproduct sp
JOIN django_suppliers_supplier s ON s.id = sp.supplier_id
WHERE sp.status IN ('pushed', 'pushed_pending_images')
  AND sp.last_synced_at < NOW() - INTERVAL '7 days'
ORDER BY sp.last_synced_at ASC
LIMIT 100;
```

## Recommended Dashboards

- **Supplier Health Overview** — counts per status (new/queued/approved/rejected/pushed), feed success rate last 24h, top 5 unacknowledged criticals.
- **Feed Execution Timeline** — `import_completed` + `feed_failed` events as time series per supplier, with totals annotation.
- **Data Quality** — `pim_*_missing` + `attribute_value_missing` + `cost_missing` + `currency_missing` rates per supplier.
- **Image Download Health** — success_pct + average download time + retry count per supplier.

## Alert Rules (Grafana Alertmanager)

Suggested thresholds — tune per environment.

| Alert | Condition | Severity | Channel |
|---|---|---|---|
| Feed fails repeatedly | `feed_failed` count > 2 in 1h for same feed | critical | Slack on-call |
| Mass delisting | `mass_delisting` event in last 5min | warning | Email ops |
| Image fail spike | `image_failed` rate > 50% over last 1h for supplier | warning | Email supplier owner |
| Unacknowledged criticals | `severity='critical' AND acknowledged_at IS NULL` count > 0 for 30min | warning | Slack on-call |
| Scraper ban | `scraper_banned` event in last 5min | critical | Slack scraper-workers team |

## Retention

`prune_supplier_events_task` runs daily 03:00 (configurable in service Celery beat). Deletes events older than `SupplierSettings.integration_event_retention_days` (default 90). Set higher for compliance-heavy environments.
