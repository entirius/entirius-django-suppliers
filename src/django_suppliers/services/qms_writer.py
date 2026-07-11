# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""QMS stock writer — soft dependency on django_qms.

Decision #6: SOFT — `try/except ImportError`. Without QMS installed, stock writes
are skipped (info event), supplier flow continues. Stock write target is
`Supplier.target_warehouse_code` (FK-string to qms.Warehouse.code).
"""

import functools
import logging

from django_suppliers.enums import EventSeverity, EventType
from django_suppliers.models import Supplier, SupplierProduct, SupplierSettings
from django_suppliers.services import event_service, modifier_service

logger = logging.getLogger(__name__)


@functools.cache
def _qms_available() -> bool:
    """Cached check — django_qms.services.warehouse_service importable.

    Logs ONE warning per process lifetime when QMS is absent (perf #9).
    """
    try:
        from django_qms.services import warehouse_service  # noqa: F401

        return True
    except (ImportError, RuntimeError):
        # side-fix: RuntimeError is raised when django_qms
        # is on PYTHONPATH but NOT in INSTALLED_APPS — common in dev setups where every
        # repo is mounted but the test config only installs a subset. ImportError alone
        # leaks 58 baseline failures into the suppliers test suite.
        logger.warning("django_qms not installed — supplier stock writes will be skipped for this process lifetime")
        return False


def _resolve_dispatch_hours(sp: SupplierProduct, supplier: Supplier) -> int | None:
    """Dispatch promise in hours: per-product `shipping_time_h` (SupplierProduct.data) else
    `Supplier.lead_time_days * 24`. None when neither is set (QMS keeps any existing override).
    """
    raw = sp.data.get("shipping_time_h") if sp.data else None
    if raw is not None:
        try:
            return max(0, int(raw))  # clamp — a bad/negative feed value must not hit a PositiveIntegerField
        except (TypeError, ValueError):
            pass
    if supplier.lead_time_days:
        return max(0, supplier.lead_time_days * 24)
    return None


def write_stock(sp: SupplierProduct, supplier: Supplier, channel_idxs: list[str], context: str = "push") -> None:
    """Write stock for an SP to QMS Warehouse via warehouse_service.bulk_upsert_stock.

    Soft path — never re-raises when QMS is missing or fails. Supplier flow continues.

    Args:
        sp: SupplierProduct (must have real_product FK to derive SKU).
        supplier: Supplier owning the stock (target_warehouse_code source).
        channel_idxs: included for symmetry with pricemanager_writer; QMS warehouse
            is per-supplier (not per-channel), so this is informational only.
        context: 'push' (always writes) or 'delta' (respects delta_sync_enabled killswitch).
    """
    if not _qms_available():
        # Perf #9: silently skip per-SP. The startup log warning (in _qms_available)
        # is the operator signal; emitting one event per SP × delta-run = noise.
        return
    if not supplier.target_warehouse_code:
        event_service.record(
            event_type=EventType.QMS_WAREHOUSE_NOT_CONFIGURED.value,
            severity=EventSeverity.WARNING.value,
            supplier=supplier,
            supplier_product=sp,
            message=f"Supplier '{supplier.idx}' has no target_warehouse_code, stock write skipped",
        )
        return
    if context == "delta" and not SupplierSettings.load().delta_sync_enabled:
        return  # silent skip — operator-toggled killswitch
    if sp.real_product_id is None:
        event_service.record(
            event_type=EventType.QMS_NO_REAL_PRODUCT.value,
            severity=EventSeverity.WARNING.value,
            supplier=supplier,
            supplier_product=sp,
            message=f"SP {sp.id} has no real_product FK, cannot write stock",
        )
        return

    quantity = modifier_service.apply_qty_modifiers(sp.stock, supplier)
    dispatch_time_h = _resolve_dispatch_hours(sp, supplier)

    try:
        from django_qms.models import Warehouse
        from django_qms.services import warehouse_service

        warehouse = Warehouse.objects.get(code=supplier.target_warehouse_code)
        item = {"sku": sp.real_product.sku, "quantity": quantity}
        if dispatch_time_h is not None:
            item["dispatch_time_h"] = dispatch_time_h
        warehouse_service.bulk_upsert_stock(warehouse=warehouse, items=[item], allow_integration=True)
        event_service.record(
            event_type=EventType.STOCK_UPDATED.value,
            severity=EventSeverity.INFO.value,
            supplier=supplier,
            supplier_product=sp,
            message=f"Stock updated for sku={sp.real_product.sku} qty={quantity}",
            details={
                "sku": sp.real_product.sku,
                "warehouse_code": supplier.target_warehouse_code,
                "quantity": quantity,
                "raw_stock": sp.stock,
                "dispatch_time_h": dispatch_time_h,
                "context": context,
                "channel_idxs": list(channel_idxs),
            },
        )
    except Exception as exc:  # noqa: BLE001 — soft dep, never re-raise
        event_service.record(
            event_type=EventType.QMS_WRITE_FAILED.value,
            severity=EventSeverity.WARNING.value,
            supplier=supplier,
            supplier_product=sp,
            message=f"QMS write failed: {exc}",
            details={"context": context, "error": str(exc)},
        )
