---
title: Management Commands
description: Feed runs, push, retention, preferred-supplier evaluation and triage commands of django-suppliers.
---

| Command | Purpose |
|---|---|
| `execute_supplier_feed --feed-id N \| --supplier-idx X --feed-idx Y [--mode full\|delta] [--async]` | Trigger feed run from CLI |
| `push_supplier_approved --supplier-idx X [--async]` | Push all approved SPs for supplier |
| `prune_supplier_events` | Apply IntegrationEvent retention policy (Celery beat target) |
| `prune_supplier_change_logs [--days N]` | Apply SupplierProductChangeLog retention (Celery beat target) |
| `evaluate_preferred_suppliers [--sku SKU] [--bypass-safety]` | Synchronous auto-preferred batch. Equivalent to the celery beat task |
| `find_duplicate_realproducts --by ean [--tolerance-pct N]` | Read-only EAN-collision triage — shared with the REST endpoint |
| `supplier_status` | Health summary: settings + counts + top 5 logs + unack events |
