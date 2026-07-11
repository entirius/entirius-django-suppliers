# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Celery task: nightly auto-preferred batch evaluation.

Iterates every RealProduct with >1 active ProductSupplierLink and runs
`evaluate_and_apply` per RP. Hysteresis + cooldown gate every switch so the
batch does NOT bypass safety guards (only the emergency inline trigger does).

Per-supplier `eval_frequency = manual` opts the supplier OUT of cron — links
whose supplier has `eval_frequency=manual` are excluded from the iteration set.
A RealProduct linked to mixed-frequency suppliers (some daily, some hourly,
some manual) is still evaluated when ANY non-manual supplier participates —
manual-only RPs require the operator's explicit `evaluate_preferred_suppliers`
management command.

Returns a summary dict keyed by skip reason so a celery flower / log scraper
can chart what happened: {evaluated, switched, skipped_cooldown, ...}.
"""

from __future__ import annotations

import logging

from celery import shared_task

from django_suppliers.enums import EvalFrequency, PreferredSkipReason
from django_suppliers.models import ProductSupplierLink
from django_suppliers.services import preferred_strategy_service
from django_suppliers.settings import QUEUE_DEFAULT

logger = logging.getLogger(__name__)


def _eligible_real_product_skus() -> list[str]:
    """Distinct SKUs with >=2 active links AND at least one non-manual-frequency supplier."""
    skus = preferred_strategy_service.iter_multi_supplier_real_product_skus()
    if not skus:
        return []
    non_manual_skus = set(
        ProductSupplierLink.objects.filter(real_product_sku__in=skus, is_active=True)
        .exclude(supplier__eval_frequency=EvalFrequency.MANUAL.value)
        .values_list("real_product_sku", flat=True)
        .distinct()
    )
    return [s for s in skus if s in non_manual_skus]


def evaluate_all(*, bypass_safety: bool = False) -> dict[str, int]:
    """Synchronous core — exposed so management command and tests can call it directly."""
    summary: dict[str, int] = {
        "evaluated": 0,
        "switched": 0,
        "skipped_no_change": 0,
        "skipped_cooldown": 0,
        "skipped_hysteresis": 0,
        "skipped_manual_override": 0,
        "skipped_no_candidates": 0,
        "errors": 0,
    }
    for sku in _eligible_real_product_skus():
        summary["evaluated"] += 1
        try:
            result = preferred_strategy_service.evaluate_and_apply(sku, bypass_safety=bypass_safety)
        except Exception:  # noqa: BLE001 — single RP failure must not break the batch
            summary["errors"] += 1
            logger.warning("evaluate_and_apply failed for sku=%s", sku, exc_info=True)
            continue
        if result.should_switch:
            summary["switched"] += 1
        else:
            key = f"skipped_{result.skip_reason.value}"
            # Defensive: PreferredSkipReason.NONE should never reach here (should_switch=True).
            if result.skip_reason == PreferredSkipReason.NONE:
                continue
            summary[key] = summary.get(key, 0) + 1
    return summary


@shared_task(name="django_suppliers.evaluate_preferred_suppliers", queue=QUEUE_DEFAULT)
def evaluate_preferred_suppliers_task() -> dict[str, int]:
    """Celery beat target. Scheduled daily 03:00 UTC by default (see docker/settings_local.py)."""
    return evaluate_all(bypass_safety=False)
