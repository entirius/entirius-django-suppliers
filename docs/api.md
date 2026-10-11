---
title: API Surface
description: URL prefix, authentication, throttling and resources of the django-suppliers admin API.
---

URL prefix: `/api/suppliers/v2/admin/`. Auth: `JWTAuthentication` + `IsAdminUser` (custom subclass:
`is_staff or is_superuser`). Throttling: `suppliers_change_log_bulk` 120/min on `pim-sku/has-changes/`.

| Resource | Path |
|---|---|
| Suppliers | `suppliers/` (GET/POST), `suppliers/{idx}/` (GET/PATCH/DELETE), `suppliers/{idx}/delete-impact/`, `suppliers/{idx}/data-keys/` (flattened `SupplierProduct.data` keys + reserved tokens; cached 5 min, invalidated by `execute_feed_task`), `suppliers/{idx}/data-values/?source_field=X&limit=N`, `suppliers/{idx}/credentials/` (audited) |
| Feeds | `suppliers/{idx}/feeds/...` (CRUD + `/trigger/` + `/test/`); `/test/` fetches the feed with the stored credentials, so it needs the write level of `suppliers.sources`, and its `limit` is 1 to 10 (default 10, 400 outside) |
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
