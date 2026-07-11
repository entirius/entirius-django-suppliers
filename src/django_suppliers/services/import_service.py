# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import hashlib
import json
import logging
import traceback
from collections.abc import Iterator
from datetime import datetime
from decimal import Decimal
from itertools import islice
from typing import Any
from uuid import UUID

from django.utils import timezone

from django_suppliers.connectors.base import BaseConnector
from django_suppliers.enums import PUSHED_STATUSES, ChangeLogSource, EventSeverity, EventType, LogStatus, ProductStatus
from django_suppliers.models import ImportLog, Supplier, SupplierFeed, SupplierProduct
from django_suppliers.schemas.contract import PriceStockUpdate, RawProduct
from django_suppliers.services import audit_service, connector_registry, event_service, log_service
from django_suppliers.signals import supplier_products_imported_signal

logger = logging.getLogger(__name__)

_BATCH_SIZE = 500
_HISTORY_CAP = 20
_PHYSICAL_KEYS = {"weight", "ean", "width", "height", "deep"}
_NON_PUSHED_DELIST_TARGETS = {ProductStatus.NEW.value, ProductStatus.QUEUED.value, ProductStatus.APPROVED.value}
_AUDIT_TRACKED_SCALAR_FIELDS = ("name", "cost", "currency", "stock", "ean")

# physical race detection outcomes — returned by _apply_physical_update_to_real_product
# so process_delta_sync can bucket counts without re-querying.
PHYSICAL_OUTCOME_NOOP = "noop"
PHYSICAL_OUTCOME_APPLIED = "applied"
PHYSICAL_OUTCOME_SKIPPED_NON_PREFERRED = "skipped_non_preferred"
PHYSICAL_OUTCOME_OVERWRITTEN = "overwritten"


def _json_safe(value: Any) -> Any:
    """Convert Decimals to strings so JSONField stores stable shape across drivers."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, list | tuple):
        return [_json_safe(v) for v in value]
    if isinstance(value, dict):
        return {k: _json_safe(v) for k, v in value.items()}
    return value


def _snapshot_for_audit(sp: SupplierProduct | None) -> dict[str, Any] | None:
    """Capture mutable fields BEFORE `_build_or_update_sp` rewrites them in place."""
    if sp is None:
        return None
    snap: dict[str, Any] = {field: getattr(sp, field) for field in _AUDIT_TRACKED_SCALAR_FIELDS}
    snap["image_urls"] = list(sp.image_urls or [])
    snap["data"] = dict(sp.data or {})
    return snap


def _build_full_sync_audit_drafts(prev: dict[str, Any], sp: SupplierProduct) -> list[dict[str, Any]]:
    """Diff pre-mutation snapshot against post-mutation SP — per-field drafts.

    Strategy:
    one draft per changed scalar, one draft per added/removed/changed `data.{key}`,
    one draft with full before/after lists for `image_urls`.
    """
    drafts: list[dict[str, Any]] = []
    common = {"supplier_product": sp, "source": ChangeLogSource.FULL_SYNC.value, "applied_to_pim": False}
    for field in _AUDIT_TRACKED_SCALAR_FIELDS:
        prev_val = prev.get(field)
        new_val = getattr(sp, field)
        if prev_val != new_val:
            drafts.append({**common, "field_path": field, "before": _json_safe(prev_val), "after": _json_safe(new_val)})
    prev_imgs = prev.get("image_urls") or []
    new_imgs = list(sp.image_urls or [])
    if prev_imgs != new_imgs:
        drafts.append({**common, "field_path": "image_urls", "before": prev_imgs, "after": new_imgs})
    prev_data = prev.get("data") or {}
    new_data = sp.data or {}
    for key in sorted(set(prev_data) | set(new_data)):
        before = prev_data.get(key)
        after = new_data.get(key)
        if before != after:
            drafts.append(
                {**common, "field_path": f"data.{key}", "before": _json_safe(before), "after": _json_safe(after)}
            )
    return drafts


def _flush_audit_drafts(drafts: list[dict[str, Any]], *, context: str) -> None:
    """Out-of-band: audit log failure NEVER crashes the data path."""
    if not drafts:
        return
    try:
        audit_service.log_changes_bulk(drafts)
    except Exception:  # noqa: BLE001 — audit must be best-effort
        logger.warning("audit_service.log_changes_bulk failed in %s", context, exc_info=True)


def _hash_attributes(attributes: dict[str, Any]) -> str:
    payload = json.dumps(attributes, sort_keys=True, default=str)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()  # noqa: S324 — non-crypto hash


def _detect_physical_change(raw_attributes: dict[str, Any], existing_data: dict[str, Any]) -> bool:
    for key in _PHYSICAL_KEYS:
        if key in raw_attributes and raw_attributes.get(key) != existing_data.get(key):
            return True
    return False


def _batched(iterator: Iterator, size: int) -> Iterator[list]:
    while True:
        chunk = list(islice(iterator, size))
        if not chunk:
            return
        yield chunk


def execute_feed(feed: SupplierFeed, *, mode: str = "full", source: str = "scheduler", triggered_by=None) -> ImportLog:
    log = log_service.start_import_log(feed, mode=mode, triggered_by=triggered_by, source=source)
    try:
        connector = connector_registry.get_connector(feed.connector_kind)
        if connector.is_async:
            if mode == "delta":
                connector.dispatch_fetch_delta(feed, log.run_id)
            else:
                connector.dispatch_fetch(feed, log.run_id)
            return log  # finalized in the task callback
        if mode == "delta":
            counts = process_delta_sync(feed, connector, log)
        else:
            counts = process_full_sync(feed, connector, log)
        status = LogStatus.PARTIAL.value if counts.get("error_count", 0) > 0 else LogStatus.SUCCESS.value
        finalized = log_service.finalize_import_log(log.run_id, status=status, **counts)
        if status == LogStatus.SUCCESS.value:
            supplier_products_imported_signal.send(sender=execute_feed, feed=feed, import_log=finalized)
        return finalized
    except Exception as exc:
        logger.exception("execute_feed failed for feed=%s", feed.id)
        log_service.finalize_import_log(
            log.run_id, status=LogStatus.FAILED.value, error_summary=[traceback.format_exc()[:5000]]
        )
        event_service.record(
            event_type=EventType.FEED_FAILED.value,
            severity=EventSeverity.CRITICAL.value,
            supplier=feed.supplier,
            feed=feed,
            message=str(exc) or exc.__class__.__name__,
        )
        return ImportLog.objects.get(run_id=log.run_id)


def _build_or_update_sp(
    *,
    supplier: Supplier,
    feed: SupplierFeed,
    raw: RawProduct,
    existing_by_external: dict[str, SupplierProduct],
    existing_by_ean: dict[str, SupplierProduct],
    started_at: datetime,
) -> tuple[SupplierProduct | None, str]:
    """Returns (instance, change_kind) where change_kind ∈ {'new','updated','unchanged'}."""
    new_hash = _hash_attributes(raw.attributes)
    sp = existing_by_external.get(raw.external_id)
    rename_from: str | None = None
    if sp is None and raw.ean:
        candidate = existing_by_ean.get(raw.ean)
        if candidate is not None and candidate.external_id != raw.external_id:
            sp = candidate
            rename_from = candidate.external_id

    if sp is None:
        return SupplierProduct(
            supplier=supplier,
            feed=feed,
            external_id=raw.external_id,
            external_id_history=[],
            name=raw.name,
            cost=raw.cost,
            currency=raw.currency or "",
            stock=raw.stock,
            ean=raw.ean or "",
            url=raw.url or "",
            image_urls=list(raw.images),
            data=dict(raw.attributes),
            data_hash=new_hash,
            status=ProductStatus.NEW.value,
            last_synced_at=started_at,
            data_changed_at=started_at,
        ), "new"

    physical_changed = _detect_physical_change(raw.attributes, sp.data)
    data_changed = sp.data_hash != new_hash

    if rename_from is not None:
        history = list(sp.external_id_history) + [rename_from]
        sp.external_id_history = history[-_HISTORY_CAP:]
        sp.external_id = raw.external_id

    sp.name = raw.name
    sp.cost = raw.cost
    sp.currency = raw.currency or ""
    sp.stock = raw.stock
    sp.ean = raw.ean or ""
    sp.url = raw.url or ""
    sp.image_urls = list(raw.images)
    sp.data = dict(raw.attributes)
    sp.last_synced_at = started_at

    if data_changed:
        sp.data_hash = new_hash
        sp.data_changed_at = started_at
    if physical_changed:
        sp.physical_changed_at = started_at

    if rename_from is not None or data_changed or physical_changed:
        return sp, "updated"
    return sp, "unchanged"


def process_full_sync(feed: SupplierFeed, connector: BaseConnector, log: ImportLog) -> dict[str, int]:
    started_at = log.started_at
    supplier = feed.supplier

    counts: dict[str, Any] = {
        "total_count": 0,
        "new_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "delisted_count": 0,
        "error_count": 0,
        # #34: split pushed-product delistings from non-pushed (`delisted_count` stays for backwards compat).
        "pushed_delisted_count": 0,
        "mass_delisting_triggered": False,
    }
    error_summary: list[str] = []

    raw_iter = iter(connector.fetch(feed))

    for batch in _batched(raw_iter, _BATCH_SIZE):
        external_ids = [r.external_id for r in batch]
        eans = [r.ean for r in batch if r.ean]
        existing_by_external = {
            sp.external_id: sp for sp in SupplierProduct.objects.filter(supplier=supplier, external_id__in=external_ids)
        }
        existing_by_ean: dict[str, SupplierProduct] = {}
        if eans:
            for sp in SupplierProduct.objects.filter(supplier=supplier, ean__in=eans):
                if sp.ean and sp.external_id not in existing_by_external:
                    existing_by_ean[sp.ean] = sp

        to_create: list[SupplierProduct] = []
        to_update: list[SupplierProduct] = []
        audit_drafts: list[dict[str, Any]] = []

        for raw in batch:
            prev_sp = existing_by_external.get(raw.external_id)
            if prev_sp is None and raw.ean:
                prev_sp = existing_by_ean.get(raw.ean)
            prev_snapshot = _snapshot_for_audit(prev_sp)
            try:
                sp, kind = _build_or_update_sp(
                    supplier=supplier,
                    feed=feed,
                    raw=raw,
                    existing_by_external=existing_by_external,
                    existing_by_ean=existing_by_ean,
                    started_at=started_at,
                )
            except Exception as exc:  # noqa: BLE001
                counts["error_count"] += 1
                if len(error_summary) < 50:
                    error_summary.append(f"{raw.external_id}: {exc}")
                continue
            counts["total_count"] += 1
            if kind == "new":
                to_create.append(sp)
                counts["new_count"] += 1
            elif kind == "updated":
                to_update.append(sp)
                counts["updated_count"] += 1
            else:
                # unchanged still updates last_synced_at
                to_update.append(sp)
                counts["unchanged_count"] += 1
            # Audit only post-push mutations (operator interest); pre-push staging churn is noise.
            if prev_snapshot is not None and kind == "updated" and sp.status in PUSHED_STATUSES:
                audit_drafts.extend(_build_full_sync_audit_drafts(prev_snapshot, sp))

        if to_create:
            SupplierProduct.objects.bulk_create(to_create, batch_size=_BATCH_SIZE)
        if to_update:
            SupplierProduct.objects.bulk_update(
                to_update,
                fields=[
                    "external_id",
                    "external_id_history",
                    "feed",
                    "name",
                    "cost",
                    "currency",
                    "stock",
                    "ean",
                    "url",
                    "image_urls",
                    "data",
                    "data_hash",
                    "last_synced_at",
                    "data_changed_at",
                    "physical_changed_at",
                ],
                batch_size=_BATCH_SIZE,
            )
        _flush_audit_drafts(audit_drafts, context="full_sync")

    # Delisting pass
    stale_qs = SupplierProduct.objects.filter(supplier=supplier).exclude(last_synced_at__gte=started_at)
    pushed_total = SupplierProduct.objects.filter(supplier=supplier, status__in=PUSHED_STATUSES).count()
    pushed_delisted = 0
    for sp in stale_qs:
        if sp.status in _NON_PUSHED_DELIST_TARGETS:
            sp.status = ProductStatus.REJECTED.value
            sp.save(update_fields=["status", "modified_at"])
            counts["delisted_count"] += 1
        elif sp.status in PUSHED_STATUSES:
            pushed_delisted += 1
            counts["pushed_delisted_count"] += 1
            event_service.record(
                event_type=EventType.PUSHED_PRODUCT_DELISTED.value,
                severity=EventSeverity.WARNING.value,
                supplier=supplier,
                feed=feed,
                message=f"Pushed SupplierProduct {sp.external_id} disappeared from feed",
                details={"external_id": sp.external_id, "supplier_product_id": sp.id},
            )

    if pushed_total and pushed_delisted * 2 > pushed_total:  # >50%
        counts["mass_delisting_triggered"] = True
        event_service.record(
            event_type=EventType.MASS_DELISTING.value,
            severity=EventSeverity.WARNING.value,
            supplier=supplier,
            feed=feed,
            message=f"Mass delisting detected: {pushed_delisted}/{pushed_total} pushed products gone",
            details={"pushed_total": pushed_total, "pushed_delisted": pushed_delisted},
        )

    if error_summary:
        log.error_summary = error_summary[:50]
        log.save(update_fields=["error_summary", "modified_at"])
    return counts


def process_delta_sync(feed: SupplierFeed, connector: BaseConnector, log: ImportLog) -> dict[str, int]:
    started_at = log.started_at
    supplier = feed.supplier

    counts: dict[str, Any] = {
        "total_count": 0,
        "new_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "delisted_count": 0,
        "error_count": 0,
        # #34: delta sync never delists (no list of all SKUs); always zero/False, kept for shape parity.
        "pushed_delisted_count": 0,
        "mass_delisting_triggered": False,
        # Physical updates were silently bucketed into updated_count (cost/qty only).
        # Split into 3 dedicated counters so operators can dashboard race-skip vs applied vs overwrite.
        "physical_updated_count": 0,
        "physical_skipped_non_preferred_count": 0,
        "physical_overwrite_count": 0,
    }

    update_iter = iter(connector.fetch_delta(feed))

    for batch in _batched(update_iter, _BATCH_SIZE):
        external_ids = [u.external_id for u in batch]
        # select_related("real_product") avoids N+1 in _apply_physical_update + _write_qms_cost.
        existing = {
            sp.external_id: sp
            for sp in SupplierProduct.objects.filter(supplier=supplier, external_id__in=external_ids).select_related(
                "real_product"
            )
        }
        to_update: list[SupplierProduct] = []
        audit_drafts: list[dict[str, Any]] = []
        for upd in batch:
            counts["total_count"] += 1
            sp = existing.get(upd.external_id)
            if sp is None:
                counts["error_count"] += 1
                event_service.record(
                    event_type=EventType.UNKNOWN_EXTERNAL_ID_IN_DELTA.value,
                    severity=EventSeverity.INFO.value,
                    supplier=supplier,
                    feed=feed,
                    message=f"Unknown external_id in delta feed: {upd.external_id}",
                    details={"external_id": upd.external_id},
                )
                continue
            new_hash = _hash_attributes(
                {"cost": str(upd.cost) if upd.cost is not None else None, "currency": upd.currency, "stock": upd.stock}
            )
            prev_cost, prev_currency, prev_stock = sp.cost, sp.currency, sp.stock
            changed = (
                (upd.cost is not None and upd.cost != sp.cost)
                or (upd.currency is not None and upd.currency != sp.currency)
                or (upd.stock is not None and upd.stock != sp.stock)
            )
            if upd.cost is not None:
                sp.cost = upd.cost
            if upd.currency is not None:
                sp.currency = upd.currency
            if upd.stock is not None:
                sp.stock = upd.stock
            sp.last_synced_at = started_at
            if changed:
                sp.data_hash = new_hash
                sp.data_changed_at = started_at
                counts["updated_count"] += 1
            else:
                counts["unchanged_count"] += 1
            to_update.append(sp)
            # Audit only for pushed SPs — pre-push delta churn is noise to PIM operators.
            # applied_to_pim=False: D5 cost defer means delta NOT yet propagated to pricing.
            if changed and sp.status in PUSHED_STATUSES:
                common = {"supplier_product": sp, "source": ChangeLogSource.DELTA_SYNC.value, "applied_to_pim": False}
                if upd.cost is not None and upd.cost != prev_cost:
                    audit_drafts.append(
                        {**common, "field_path": "cost", "before": _json_safe(prev_cost), "after": _json_safe(upd.cost)}
                    )
                if upd.currency is not None and upd.currency != prev_currency:
                    audit_drafts.append(
                        {**common, "field_path": "currency", "before": prev_currency, "after": upd.currency}
                    )
                if upd.stock is not None and upd.stock != prev_stock:
                    audit_drafts.append({**common, "field_path": "stock", "before": prev_stock, "after": upd.stock})
            physical_outcome = _apply_physical_update_to_real_product(sp, upd, started_at)
            if physical_outcome == PHYSICAL_OUTCOME_APPLIED:
                counts["physical_updated_count"] += 1
            elif physical_outcome == PHYSICAL_OUTCOME_SKIPPED_NON_PREFERRED:
                counts["physical_skipped_non_preferred_count"] += 1
            elif physical_outcome == PHYSICAL_OUTCOME_OVERWRITTEN:
                counts["physical_overwrite_count"] += 1
            _write_qms_cost_for_pushed_sp(sp, supplier)
            # emergency switch when preferred just lost its stock. Best-effort;
            # never blocks delta sync. Real-time recovery beats waiting for cron.
            if changed and upd.stock is not None and upd.stock != prev_stock:
                try:
                    from django_suppliers.services import preferred_strategy_service

                    preferred_strategy_service.maybe_trigger_emergency_on_stock_drop(
                        sp, previous_stock=prev_stock, new_stock=upd.stock
                    )
                except Exception:  # noqa: BLE001 — emergency eval must not break delta sync
                    logger.warning("maybe_trigger_emergency_on_stock_drop failed for sp_id=%s", sp.id, exc_info=True)
        if to_update:
            SupplierProduct.objects.bulk_update(
                to_update,
                fields=[
                    "cost",
                    "currency",
                    "stock",
                    "last_synced_at",
                    "data_hash",
                    "data_changed_at",
                    "physical_changed_at",
                ],
                batch_size=_BATCH_SIZE,
            )
        _flush_audit_drafts(audit_drafts, context="delta_sync")
    return counts


_PHYSICAL_FIELDS = ("weight", "ean", "width", "height", "deep")
_PHYSICAL_DECIMAL_FIELDS = frozenset({"weight", "width", "height", "deep"})


def _write_qms_cost_for_pushed_sp(sp: SupplierProduct, supplier: Supplier) -> None:
    """Stage 5: delta sync of pushed SP triggers QMS stock + pricemanager cost log.

    Lazy imports keep stage-4-only environments happy.
    """
    if sp.status not in PUSHED_STATUSES:
        return
    from django_suppliers.services import pricemanager_writer, qms_writer, role_guard

    # Monitoring suppliers: SP row + change log update on delta, never QMS/PM writes.
    if role_guard.is_monitoring(supplier):
        return

    channels = list(sp.pushed_to_channel_idxs or [])
    qms_writer.write_stock(sp, supplier, channels, context="delta")
    pricemanager_writer.log_cost(sp, supplier, channels, context="delta")


def _apply_physical_update_to_real_product(sp: SupplierProduct, upd: "PriceStockUpdate", started_at) -> str:
    """Apply physical fields (weight/ean/dims) to RealProduct (shared across channels).

    Only for already-pushed SPs (status pushed / pushed_pending_images) — INIT/manual SPs
    have no real_product yet. Physical changes update RealProduct directly (single source
    of truth across channels) and stamp `physical_changed_at` on SP.

    Preferred-only writes. Non-preferred suppliers skip by default;
    opt-in via Supplier.allow_physical_writes_from_non_preferred restores legacy
    last-write-wins with a warning audit. Returns one of the PHYSICAL_OUTCOME_* constants
    so the caller can bucket counts without re-querying.
    """
    from decimal import Decimal, InvalidOperation

    from django_suppliers.models import ProductSupplierLink
    from django_suppliers.services import role_guard

    if upd.physical is None:
        return PHYSICAL_OUTCOME_NOOP
    if sp.status not in (ProductStatus.PUSHED.value, ProductStatus.PUSHED_PENDING_IMAGES.value):
        return PHYSICAL_OUTCOME_NOOP
    # Monitoring suppliers never write RealProduct physical fields.
    if role_guard.is_monitoring(sp.supplier):
        return PHYSICAL_OUTCOME_NOOP
    if sp.real_product_id is None:
        return PHYSICAL_OUTCOME_NOOP

    # preferred-only physical writes gate.
    link = ProductSupplierLink.objects.filter(
        real_product_sku=sp.real_product.sku, supplier_id=sp.supplier_id, is_active=True
    ).first()
    overwrite_mode = False
    if link is None or not link.is_preferred:
        if not sp.supplier.allow_physical_writes_from_non_preferred:
            _emit_physical_race_skip(sp, upd)
            return PHYSICAL_OUTCOME_SKIPPED_NON_PREFERRED
        overwrite_mode = True

    rp = sp.real_product
    changed_fields: list[str] = []
    physical_diffs: list[tuple[str, Any, Any]] = []

    for field in _PHYSICAL_FIELDS:
        new_value = upd.physical.get(field)
        if new_value is None:
            continue
        if field in _PHYSICAL_DECIMAL_FIELDS:
            try:
                new_value = Decimal(str(new_value))
            except (InvalidOperation, ValueError, TypeError):
                continue
        current = getattr(rp, field)
        if current == new_value:
            continue
        physical_diffs.append((field, current, new_value))
        setattr(rp, field, new_value)
        changed_fields.append(field)

    if not changed_fields:
        # Race gate passed but every field matched current state — treat as noop for counts.
        return PHYSICAL_OUTCOME_NOOP

    if "ean" in changed_fields:
        rp.save(update_fields=changed_fields, ignore_validate_ean=True)
    else:
        rp.save(update_fields=changed_fields)
    sp.physical_changed_at = started_at

    if overwrite_mode:
        # Opt-in path: legacy last-write-wins, but loudly audited so the operator never loses sight.
        preferred_idx = (
            ProductSupplierLink.objects.filter(real_product_sku=rp.sku, is_active=True, is_preferred=True)
            .values_list("supplier__idx", flat=True)
            .first()
        )
        event_service.record(
            event_type=EventType.PHYSICAL_UPDATE_OVERWRITE.value,
            severity=EventSeverity.WARNING.value,
            supplier=sp.supplier,
            supplier_product=sp,
            message=(
                f"Non-preferred supplier {sp.supplier.idx} overwrote RealProduct physical "
                f"fields for sku={rp.sku}: {changed_fields} (opt-in)"
            ),
            details={
                "sku": rp.sku,
                "changed_fields": changed_fields,
                "non_preferred_supplier_idx": sp.supplier.idx,
                "preferred_supplier_idx": preferred_idx,
            },
        )
        audit_source = ChangeLogSource.PHYSICAL_OVERWRITE.value
        outcome = PHYSICAL_OUTCOME_OVERWRITTEN
    else:
        event_service.record(
            event_type=EventType.PHYSICAL_UPDATE_APPLIED.value,
            severity=EventSeverity.INFO.value,
            supplier=sp.supplier,
            supplier_product=sp,
            message=f"RealProduct physical updated for sku={rp.sku}: {changed_fields}",
            details={"changed_fields": changed_fields, "sku": rp.sku},
        )
        audit_source = ChangeLogSource.DELTA_SYNC.value
        outcome = PHYSICAL_OUTCOME_APPLIED

    # Audit: applied_to_pim=True — RealProduct is the single source of truth across channels.
    drafts = [
        {
            "supplier_product": sp,
            "real_product_sku": rp.sku,
            "source": audit_source,
            "field_path": f"physical.{field}",
            "before": _json_safe(prev),
            "after": _json_safe(new),
            "applied_to_pim": True,
        }
        for field, prev, new in physical_diffs
    ]
    _flush_audit_drafts(drafts, context="physical_update")
    return outcome


def _emit_physical_race_skip(sp: SupplierProduct, upd: "PriceStockUpdate") -> None:
    """Non-preferred supplier tried to write physical fields — drop, audit, event.

    Best-effort: event + audit failures are logged but never re-raised (matches the
    posture of `_flush_audit_drafts`). The data path is the source of truth; observability
    is opportunistic.
    """
    from django_suppliers.models import ProductSupplierLink

    rp = sp.real_product
    attempted = sorted(k for k, v in (upd.physical or {}).items() if v is not None)
    preferred_idx = (
        ProductSupplierLink.objects.filter(real_product_sku=rp.sku, is_active=True, is_preferred=True)
        .values_list("supplier__idx", flat=True)
        .first()
    )
    try:
        event_service.record(
            event_type=EventType.PHYSICAL_UPDATE_SKIPPED_NON_PREFERRED.value,
            severity=EventSeverity.INFO.value,
            supplier=sp.supplier,
            supplier_product=sp,
            message=(f"Skipped physical update from non-preferred supplier {sp.supplier.idx} on sku={rp.sku}"),
            details={
                "sku": rp.sku,
                "supplier_idx": sp.supplier.idx,
                "preferred_supplier_idx": preferred_idx,
                "attempted_fields": attempted,
            },
        )
    except Exception:  # noqa: BLE001 — observability must never block delta sync
        logger.warning("event_service.record failed for physical race skip sp=%s", sp.id, exc_info=True)

    drafts = [
        {
            "supplier_product": sp,
            "real_product_sku": rp.sku,
            "source": ChangeLogSource.PHYSICAL_SKIPPED.value,
            "field_path": "physical_skipped",
            "before": None,
            "after": _json_safe({k: v for k, v in (upd.physical or {}).items() if v is not None}),
            "applied_to_pim": False,
        }
    ]
    _flush_audit_drafts(drafts, context="physical_race_skip")


def process_feed_results(run_id: UUID, raw_products: list[RawProduct]) -> ImportLog:
    """Callback dla async: weź raw_products dostarczonych przez worker, zapisz, finalize."""
    log = ImportLog.objects.select_related("feed", "feed__supplier").get(run_id=run_id)
    feed = log.feed
    supplier = feed.supplier

    started_at = log.started_at
    counts: dict[str, Any] = {
        "total_count": 0,
        "new_count": 0,
        "updated_count": 0,
        "unchanged_count": 0,
        "delisted_count": 0,
        "error_count": 0,
        # #34: scraper-callback path never runs delisting pass; kept zero for shape parity.
        "pushed_delisted_count": 0,
        "mass_delisting_triggered": False,
    }

    if raw_products:
        external_ids = [r.external_id for r in raw_products]
        eans = [r.ean for r in raw_products if r.ean]
        existing_by_external = {
            sp.external_id: sp for sp in SupplierProduct.objects.filter(supplier=supplier, external_id__in=external_ids)
        }
        existing_by_ean: dict[str, SupplierProduct] = {}
        if eans:
            for sp in SupplierProduct.objects.filter(supplier=supplier, ean__in=eans):
                if sp.ean and sp.external_id not in existing_by_external:
                    existing_by_ean[sp.ean] = sp

        to_create: list[SupplierProduct] = []
        to_update: list[SupplierProduct] = []
        for raw in raw_products:
            sp, kind = _build_or_update_sp(
                supplier=supplier,
                feed=feed,
                raw=raw,
                existing_by_external=existing_by_external,
                existing_by_ean=existing_by_ean,
                started_at=started_at,
            )
            counts["total_count"] += 1
            if kind == "new":
                to_create.append(sp)
                counts["new_count"] += 1
            elif kind == "updated":
                to_update.append(sp)
                counts["updated_count"] += 1
            else:
                to_update.append(sp)
                counts["unchanged_count"] += 1

        if to_create:
            SupplierProduct.objects.bulk_create(to_create, batch_size=_BATCH_SIZE)
        if to_update:
            SupplierProduct.objects.bulk_update(
                to_update,
                fields=[
                    "external_id",
                    "external_id_history",
                    "feed",
                    "name",
                    "cost",
                    "currency",
                    "stock",
                    "ean",
                    "url",
                    "image_urls",
                    "data",
                    "data_hash",
                    "last_synced_at",
                    "data_changed_at",
                    "physical_changed_at",
                ],
                batch_size=_BATCH_SIZE,
            )

    status = LogStatus.PARTIAL.value if counts["error_count"] > 0 else LogStatus.SUCCESS.value
    finalized = log_service.finalize_import_log(log.run_id, status=status, **counts)
    if status == LogStatus.SUCCESS.value:
        supplier_products_imported_signal.send(sender=process_feed_results, feed=feed, import_log=finalized)
    return finalized


def update_or_create_supplier_product(
    supplier: Supplier, feed: SupplierFeed, raw_product: RawProduct
) -> tuple[SupplierProduct, bool]:
    """Helper for unit tests / single-item import. Returns (instance, created)."""
    started_at = timezone.now()
    existing_by_external = {}
    existing_by_ean = {}
    sp = SupplierProduct.objects.filter(supplier=supplier, external_id=raw_product.external_id).first()
    if sp is not None:
        existing_by_external[sp.external_id] = sp
    elif raw_product.ean:
        ean_match = SupplierProduct.objects.filter(supplier=supplier, ean=raw_product.ean).first()
        if ean_match is not None:
            existing_by_ean[ean_match.ean] = ean_match
    instance, kind = _build_or_update_sp(
        supplier=supplier,
        feed=feed,
        raw=raw_product,
        existing_by_external=existing_by_external,
        existing_by_ean=existing_by_ean,
        started_at=started_at,
    )
    if kind == "new":
        instance.save()
        return instance, True
    instance.save()
    return instance, False


__all__ = [
    "execute_feed",
    "process_delta_sync",
    "process_feed_results",
    "process_full_sync",
    "update_or_create_supplier_product",
]


# Avoid F401 for PriceStockUpdate (re-export for typing convenience downstream)
_ = PriceStockUpdate
