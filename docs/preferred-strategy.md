---
title: Auto-Preferred Selection
description: How django-suppliers picks the preferred supplier per RealProduct — strategies, safety mechanisms, triggers, cron setup, manual override, audit events, operator playbook.
sidebar:
  label: Preferred Strategy
---

When a single PIM RealProduct has more than one `ProductSupplierLink`, exactly one of them is `is_preferred=True`. That flag drives the storefront: the cost subscriber writes prices from the preferred supplier only, stock displays from the preferred supplier only, and any single-source-of-truth read keys off it. Auto-preferred selection is the system that keeps that flag pointed at the right link without an operator touching it every time costs or stock move.

This page is the operator playbook. For backend wiring see `django-suppliers/AGENTS.md` and the implementation in `services/preferred_strategy_service.py`.

## Overview

Auto-preferred runs in three places:

- **Inline after `init_push`** — when a multi-supplier link is created via EAN auto-match (etap-13a), the evaluator picks the best candidate immediately.
- **Inline emergency** — when delta sync drops the preferred supplier's stock to zero, the evaluator flips to whoever still has stock (cooldown + hysteresis bypassed).
- **Daily cron** — `evaluate_preferred_suppliers_task` walks every multi-supplier RealProduct at 03:00 UTC and applies switches where safety guards allow.

What blocks auto-preferred from doing anything:

- The RealProduct has only one active link (nothing to evaluate).
- The currently preferred link has `manual_override=True` (operator forced it).
- The winner is the same link that's already preferred (no flap, no audit spam).
- The cost improvement is below the supplier's hysteresis threshold.
- A switch already happened on this RealProduct within the cooldown window.
- No candidate has stock greater than zero.

## Strategy Types

Configured per supplier via `Supplier.preferred_strategy`. Only `lowest_cost_with_stock` is implemented today.

| Strategy | Picker logic | Status |
|----------|--------------|--------|
| `lowest_cost_with_stock` | Active link with the lowest `SupplierProduct.cost` and `stock > 0`. Deterministic tie-breaker on `supplier_id` ASC. | Default. Implemented. |
| `highest_stock` | Pick the link with the largest `stock`. | Reserved. Not yet implemented in etap-13b. |
| `manual_only` | Auto-strategy never selects a winner; operator decides. | Reserved. Same eval skip semantics as `manual_override`. |

A supplier whose strategy is `manual_only` opts out of auto-eval entirely — same as `eval_frequency=manual`.

## Safety Mechanisms

Three guards keep storefront prices from flapping when costs and stock wobble.

### Hysteresis

Default **2%**, per-supplier override via `Supplier.preferred_switch_hysteresis_pct`. The candidate must be cheaper than the current preferred by at least this percentage before a switch fires.

```
current preferred cost = 0.140
candidate cost = 0.139  → improvement 0.71% < 2% → SKIP, emit
                          preferred_switch_skipped_hysteresis (info).

candidate cost = 0.130  → improvement 7.14% > 2% → cooldown check next.
```

The check protects against a competitor whose feed updates costs every 15 minutes with sub-percent jitter — without hysteresis, the storefront would re-deploy prices a hundred times per day.

### Cooldown

Default **24 hours**, per-supplier override via `Supplier.preferred_switch_cooldown_hours`. After a switch fires, the same RealProduct cannot flip again for this many hours.

```
T+0:    switch ft → kh  (kh.preferred_changed_at = now)
T+1h:   ft cost drops 30%  → candidate beats kh on hysteresis,
                              BUT 1h < 24h cooldown → SKIP, emit
                              preferred_switch_skipped_cooldown (info).
T+25h:  cooldown expired → re-eval → switch fires if conditions hold.
```

The cooldown is read from the *current preferred's* supplier setting, not the candidate's. The supplier that "won" the storefront owns the cooldown.

### Emergency Bypass

When delta sync drops the current preferred's stock to zero, both hysteresis and cooldown are bypassed and the evaluator immediately picks any link with stock available. `manual_override=True` is still respected — operator intent always wins.

```
T+0:  preferred = kh, stock = 1500  → storefront 0.13 EUR, available.
T+1h: delta sync, kh.stock = 0
      → emergency trigger fires
      → ft (stock=2400, cost=0.14) becomes preferred
      → emit preferred_supplier_emergency_switch (warning).
```

The storefront recovers within one delta cycle, not within one cron tick.

## Triggers

| Trigger | Where | Safety |
|---------|-------|--------|
| Inline after init_push | `pim_writer._write_qms_cost_link` → `maybe_trigger_inline_after_link` | Full (hysteresis + cooldown) |
| Inline emergency on stock=0 | `import_service.process_delta_sync` → `maybe_trigger_emergency_on_stock_drop` | Bypass hysteresis + cooldown |
| Cron daily 03:00 UTC | `tasks.preferred_strategy.evaluate_preferred_suppliers_task` | Full |
| Manual management command | `python manage.py evaluate_preferred_suppliers` | Configurable (`--bypass-safety`) |

The cron task only iterates RealProducts where at least one linked supplier has `eval_frequency != manual`. If every supplier on a SKU is opted out, that SKU is operator-driven only.

## Cron Setup

In Docker, `docker/settings_local.py` registers the schedule:

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "suppliers.evaluate_preferred_daily": {
        "task": "django_suppliers.evaluate_preferred_suppliers",
        "schedule": crontab(hour=3, minute=0),
        "options": {"queue": "supplier_default"},
    },
}
```

`celery-beat` runs as its own container (`docker-compose.yml` `celery-beat` service). The worker that picks up the task is the `celery` container, which already listens on the `supplier_default` queue.

Per-instance overrides go in the service settings (`service/{client}-volkanos/main/settings/*.py`):

```python
from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    **CELERY_BEAT_SCHEDULE,
    # High-velocity catalogue — bump to hourly for this client.
    "suppliers.evaluate_preferred_daily": {
        "task": "django_suppliers.evaluate_preferred_suppliers",
        "schedule": crontab(minute=0),  # top of every hour
        "options": {"queue": "supplier_default"},
    },
}
```

### Troubleshooting "cron nie odpala"

1. Is the `celery-beat` container up? `docker compose ps celery-beat` should show `Up`.
2. Is the schedule visible? `docker compose logs celery-beat | grep evaluate_preferred` — beat prints the schedule on startup.
3. Is the worker consuming the queue? `docker compose logs celery | grep supplier_default` — worker logs every task pickup.
4. Did the evaluator find any candidates? Run `python manage.py evaluate_preferred_suppliers` manually — the printed summary shows `evaluated=0` when no multi-supplier RP exists, or `skipped_manual_override=N` when every multi-supplier SKU is operator-locked.

The management command is the synchronous fallback when beat is down or you want immediate results without waiting for the next tick.

## Manual Override

`POST /api/suppliers/v2/admin/pim-sku/{sku}/set-preferred-supplier/` flips the preferred link and sets `manual_override=True` on that link. The auto-strategy then skips this RealProduct on every cron run, every inline trigger, and every emergency check. The override is sticky until cleared.

```json
POST /api/suppliers/v2/admin/pim-sku/FT-ce5b9e3089ff/set-preferred-supplier/
{
  "supplier_idx": "fortrade",
  "reason": "Strategic partner — promised exclusivity Q2."
}
```

The `reason` is required (minimum 3 characters) and audited. CMS Supplier tab renders the "Force preferred" modal which captures it.

### Reset to Auto

`POST /api/suppliers/v2/admin/pim-sku/{sku}/reset-preferred-to-auto/` clears `manual_override=True` on every link of the RealProduct and immediately runs `evaluate_and_apply` with `bypass_safety=True`. The cooldown is bypassed on reset because the operator just asked for the auto-strategy *now*.

The response tells the operator what happened: `switched: true` means a flip fired, `switched: false` plus a `skip_reason` explains why the current preferred stayed (most often `no_change` — auto-strategy agreed with the previous manual pick).

## Audit Events

Six `IntegrationEvent` types land in the events log:

| Event type | Severity | Fires when |
|------------|----------|------------|
| `preferred_supplier_switched` | info | Auto-strategy picked a different winner and the cron / inline trigger applied it. |
| `preferred_supplier_emergency_switch` | warning | Preferred lost its stock; emergency bypass flipped to a new supplier. |
| `preferred_supplier_forced` | warning | Operator called `set-preferred-supplier`; `manual_override=True` is now sticky. |
| `preferred_switch_skipped_cooldown` | info | Candidate beat the current preferred but cooldown hadn't elapsed. |
| `preferred_switch_skipped_hysteresis` | info | Candidate cost improvement under the supplier's hysteresis threshold. |
| `preferred_switch_skipped_manual_override` | info | A link on this RealProduct has `manual_override=True`; eval skipped. |

Every applied switch also writes one `SupplierProductChangeLog` row keyed by the new preferred SP, `field_path=product_supplier_link.is_preferred`, with `source` set to `auto_preferred_switch`, `manual_override`, or `emergency_switch` — match it against the timeline in the PIM Supplier tab.

Skip events do NOT write audit rows. The skip itself is observability data, not a state change.

## Operator Playbook

### "Storefront price is wrong"

1. Open the PIM product. Supplier tab → check who is currently preferred.
2. Open the timeline. Find the most recent `product_supplier_link.is_preferred` audit row — that tells you which trigger flipped it and when.
3. Check the events log filtered by SKU. The last `preferred_supplier_*` event has the decision audit (cost diff, hysteresis pct, cooldown remaining).
4. If the wrong supplier is preferred and auto-strategy would pick correctly, click **Reset to auto**. If you want to override the auto pick, click **Force preferred**.

### "Switch should have fired but didn't"

1. Run `python manage.py evaluate_preferred_suppliers --sku {SKU}` for that one SKU. The output prints the skip reason verbatim.
2. Possible skip reasons:
   - `cooldown` — wait, or override.
   - `hysteresis` — competitor's price improvement is genuinely small; auto-strategy is doing its job.
   - `manual_override` — someone forced it. Reset to auto if that's stale.
   - `no_candidates` — every linked supplier has stock zero. Storefront is showing the last preferred regardless.
   - `no_change` — auto-strategy agrees with the current preferred.

### "I want to change the strategy"

Open Suppliers panel → supplier detail → Overview tab. The fields are:

- **Preferred strategy** — only `lowest_cost_with_stock` works today. The other choices reserve enum space for future work.
- **Switch cooldown (hours)** — bump up to 168 (a week) for slow-moving catalogues; drop to 1 for high-velocity electronics.
- **Switch hysteresis (%)** — 0 makes it bang-bang. 10 ignores everything but big competitive moves.
- **Auto-eval frequency** — `daily` runs the cron, `hourly` runs it more often (requires per-instance beat schedule), `manual` opts out (only inline triggers fire).

## Related Code

- `services/preferred_strategy_service.py` — eval + switch + force + reset.
- `tasks/preferred_strategy.py` — celery beat target + `evaluate_all` synchronous core.
- `management/commands/evaluate_preferred_suppliers.py` — operator CLI.
- `api/admin/views/change_log_views.py` — `set_preferred_supplier` + `reset_preferred_to_auto` ViewSet actions.
- `signals/definitions.py` — `preferred_switched_signal` (broadcast hook; no in-tree subscribers).

Auto EAN-match (etap-13a) is the upstream half of this story. See [Auto EAN-match](/volkanos/modules/suppliers/) (docs/auto-ean-match.md inside the suppliers repo).
