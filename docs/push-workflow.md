---
title: Push Workflow
description: INIT push, force re-push, image async flow, error handling.
sidebar:
  label: Push Workflow
---

Push is the moment a SupplierProduct becomes a real PIM Product. INIT push is one-time per channel; force re-push is operator-triggered on demand.

## INIT Push

`push_supplier_product(sp_id, user_id)` is the entry point. Service is wrapped in `transaction.atomic()` with `select_for_update(of=("self",))` (D33) — concurrent push of same SP is blocked.

### Steps

1. **Pre-flight per-supplier:**
   - At least one active `MappingProfile` exists.
   - All active profiles have non-empty `target_channel_idxs`.
   - Each target Channel exists in PIM (`pim_channel_missing:{idx}` event otherwise).
   - Each mapped Feature exists (`pim_feature_missing:{idx}` event).
   - Each mapped Category exists in at least one target channel (else `pim_category_missing_all_channels:{idx}` event).
2. **Per channel from union of all active profiles' `target_channel_idxs`:**
   - Skip if channel already in `sp.pushed_to_channel_idxs` (D24 layer 2 — idempotent re-run).
   - Resolve `feature_set_idx` per hierarchy: SP override → Feed → Profile → Supplier default.
   - Resolve `language` per `pim_writer.resolve_language_for_channel(profile, supplier, channel)` (etap-08): `profile.import_language` → `supplier.default_language ∈ channel.languages` → `channel.default_language` (with `used_fallback=True`). Fallback emits `language_fallback` warning event + `LANGUAGE_FALLBACK` audit row.
   - **EAN auto-link (etap-13a):** `realproduct_match_service.find_match_by_ean(sp, supplier)` runs BEFORE `RealProduct.get_or_create`. When SP shares an EAN with an existing RealProduct AND `physical_tolerance_check` passes (within `Supplier.realproduct_match_tolerance_pct`, default 10%), the SP attaches via `ProductSupplierLink` and emits `auto_linked_to_existing_realproduct`. Tolerance fail falls back to fresh RealProduct + `physical_tolerance_violation` warning. Per-supplier opt-out via `Supplier.disable_ean_auto_link=True`.
   - `RealProduct.objects.get_or_create(sku=derived_sku)` (multi-channel SKU shared via `normalize_sku`).
   - `Product.objects.get_or_create(real_product=, shop=channel)` with defaults `is_enabled=False, visibility=NOT_VISIBLE_INDIVIDUALLY, product_class=Supplier.default_product_class` (etap-12: was hard-coded ProductBase).
   - `apply_attribute_mappings()` writes `ProductAttribute` rows (D34: `Manager.create()` per row, NOT `bulk_create` — preserves PIM `post_save` signals). **Value modifiers (etap-09):** `services/value_transformer.transform(value, modifier)` runs between value lookup and write. Numeric modifiers (`grams_to_kg`, `mm_to_cm`, `currency_minor_to_major`, ...) on `Decimal`; string modifiers (`string_trim/lowercase/uppercase`) require `str`. Failures soft — raw value used + `mapping_transform_failed` warning event. Successful transforms emit `mapping_transform` audit row.
   - `ProductInCategory` per `target_category_idx` if category exists in this channel.
   - `pushed_to_channel_idxs.append(channel_idx)`.
3. **After all channels:**
   - `_write_qms_cost_link()` — runs `qms_writer.write_stock` + `pricemanager_writer.log_cost` + `product_link_service.upsert_for_push`.
   - Status flips to `pushed_pending_images`.
   - `supplier_product_pushed_signal` fires.
4. **Image dispatch (signal handler, D32):**
   - `on_supplier_product_pushed` uses `transaction.on_commit(lambda: download_supplier_images_task.delay(sp_id, channel_idx))` for each channel.
   - Without `on_commit`: DB rollback after fail does NOT cancel Celery — task runs against non-existent Products → `image_failed` event lawiną.

### Image Async Flow

`download_supplier_images_task` (queue=`supplier_images`, max_retries=2, acks_late=True):

1. Per URL in `sp.image_urls` → HTTP GET → SimpleUploadedFile.
2. `product_picture_service.upload_picture(file_bytes, file_name)` (PIM SHA1 dedup at Picture level).
3. `link_picture_to_product` per channel.
4. Per-URL failure → `image_failed` event + continue.
5. After all images for channel: append channel_idx to `sp.images_complete_channel_idxs`.
6. When `set(pushed_to_channel_idxs) == set(images_complete_channel_idxs)` → status flips to `pushed`.

D36 status: PIM `upload_picture` is functionally idempotent (sequential 2× same URL = 1 Picture, 2 ProductPicture) but NOT race-safe under true concurrent access. Phase 2 backlog: harden PIM Picture creation with `IntegrityError` retry.

## Force Re-push

`force_repush_to_channel(sp_id, user_id)` — operator-triggered, bypasses standard STATUS_TRANSITIONS (`pushed → approved` is not whitelisted).

| What it does | What it preserves |
|---|---|
| Replace `ProductAttribute` rows for mapped features (filter mapped feature__idx, delete, recreate via `apply_attribute_mappings`) | `Product.is_enabled` — not touched |
| Full replace `ProductInCategory` | `RealProduct.sku` — not touched |
| Reset images per channel: `ProductPicture.filter(product=, is_inherited=False).delete()` + remove channel from `images_complete_channel_idxs` | Manual ProductSupplierLink fields (`is_preferred`, `priority`, `notes`) — D25 |
| Direct save with `reviewed_at=now()` | |

Trigger: image async flow re-runs to download fresh images.

D30 scope: per-SP, **all** `pushed_to_channel_idxs` together. Confirmation modal shows affected channels (read-only). Per-channel selection — Phase 2 backlog.

## Errors

| Error | Effect |
|---|---|
| Pre-flight fail | Push returns 400, no DB write, no event |
| `IntegrityError` during `Product` create | `transaction.atomic()` rollback; `push_failed` event with `{exception, channel_idx}` |
| Image download HTTP fail | Per-URL `image_failed` event; task continues to next URL; status stays `pushed_pending_images` |
| QMS write fail | `qms_write_failed` warning event; soft contract — push succeeds, stock missing |
| All images succeed for one channel but fail for another | Status stays `pushed_pending_images` until all channels complete |

## Multi-Supplier Overlap

If `_init_push_to_channel` finds existing `ProductSupplierLink` for SKU with different supplier (`rp_created=False`), emit `multi_supplier_overlap` info event with `{existing_supplier_idxs, new_supplier_idx, sku}`. Operator decides via UI which supplier should be `is_preferred` (D25).

Post etap-13b the auto-preferred service can also flip `is_preferred` without operator action — see [Preferred Strategy](./preferred-strategy/) for hysteresis, cooldown, and emergency switch semantics.

## Multi-Supplier Physical Race (etap-10)

When 2+ suppliers link to the same RealProduct, the `_apply_physical_update_to_real_product` choke point gates every physical write. Returns one of `PHYSICAL_OUTCOME_{NOOP, APPLIED, SKIPPED_NON_PREFERRED, OVERWRITTEN}`.

| Caller | Outcome | Side effects |
|---|---|---|
| Preferred supplier | `applied` | RealProduct field updated, `physical_update_applied` info event, audit `source=delta_sync` or `force_repush` with `applied_to_pim=True`. `ImportLog.physical_updated_count++` |
| Non-preferred supplier (default) | `skipped_non_preferred` | Field NOT updated, `physical_update_skipped_non_preferred` info event, audit `source=physical_skipped, field_path=physical_skipped, applied_to_pim=False`. `ImportLog.physical_skipped_non_preferred_count++` |
| Non-preferred with `Supplier.allow_physical_writes_from_non_preferred=True` | `overwritten` | Field updated (legacy last-write-wins), `physical_update_overwrite` **warning** event, audit `source=physical_overwrite, field_path=physical.{field}, applied_to_pim=True`. `ImportLog.physical_overwrite_count++` |
| Non-preferred with no `ProductSupplierLink` at all | treated as non-preferred → `skipped_non_preferred` | Same as non-preferred default |

Force re-push respects this gate too — a non-preferred operator-initiated force-repush still cannot overwrite physical fields unless the opt-in is on.

## Sequence (one channel push)

```
push_supplier_product
  pre_flight()                          [no DB write]
  with transaction.atomic():
    sp = SupplierProduct.select_for_update(of=("self",)).get(id=)
    for channel_idx in target_channels:
      if channel_idx in sp.pushed_to_channel_idxs: continue
      _init_push_to_channel(sp, channel_idx)
        rp, rp_created = RealProduct.get_or_create(sku=)
        product, _ = Product.get_or_create(real_product=, shop=channel)
        apply_attribute_mappings(product, sp, profile)
        sync_categories(product, sp, profile, channel)
      sp.pushed_to_channel_idxs.append(channel_idx)
    _write_qms_cost_link(sp, supplier)
    sp.status = "pushed_pending_images"
    sp.save()
    transaction.on_commit(lambda: signal.send(...))
  # After commit:
  on_supplier_product_pushed handler queues download_supplier_images_task
```
