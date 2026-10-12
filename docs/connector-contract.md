---
title: Connector Contract
description: RawProduct + PriceStockUpdate schemas, RabbitMQ queues, entry_points registration for adding custom supplier connectors.
sidebar:
  label: Connector Contract
---

Connectors are the fetch-side abstraction. Two flavors: `SyncConnector` (in-process, used by `xml_feed`) and `AsyncConnector` (dispatches to external worker over RabbitMQ, used by `scraper`). Custom connectors register via entry_points.

## SyncConnector

```python
# src/django_suppliers/connectors/base.py
from abc import ABC, abstractmethod
from collections.abc import Iterable
from django_suppliers.schemas.contract import RawProduct

class SyncConnector(ABC):
    """In-process fetch. Returns iterable of validated RawProduct dicts."""

    def __init__(self, feed_config: dict):
        self.config = feed_config

    @abstractmethod
    def fetch(self, *, limit: int | None = None) -> Iterable[RawProduct]:
        """Fetch products from source. Yield RawProduct dicts."""
        ...

    @abstractmethod
    def validate_config(self) -> None:
        """Raise ValueError if feed_config is invalid for this connector."""
        ...
```

Used by:
- `import_service.process_full_sync(feed)` — full rebuild of staging from feed.
- `import_service.process_test(feed, limit=10)` — `Test feed` button in CMS, no DB write.

## AsyncConnector

```python
class AsyncConnector(ABC):
    """Dispatches fetch to external worker. Worker callbacks via process_scraper_results_task."""

    @abstractmethod
    def dispatch_fetch(self, *, run_id: uuid.UUID, mode: str = "full") -> None:
        """Send task to RabbitMQ scraping queue. Worker consumes and POSTs back to supplier_results queue."""
        ...

    @abstractmethod
    def dispatch_fetch_delta(self, *, run_id: uuid.UUID) -> None:
        """Same but mode='delta'."""
        ...
```

Used by:
- `import_service.process_full_sync(feed)` — calls `dispatch_fetch`, returns immediately. Run finalized when callback arrives.
- `process_scraper_results_task` (in `tasks/scraper_callback.py`) — Celery worker on `supplier_results` queue, validates incoming payload via Pydantic, finalizes ImportLog.

Killswitch: `SupplierSettings.scraper_dispatch_enabled = False` → `dispatch_fetch*` skip RabbitMQ, emit `scraper_dispatch_skipped` event.

## RawProduct Schema (contract)

```python
# src/django_suppliers/schemas/contract.py
from decimal import Decimal
from pydantic import BaseModel, Field

class RawProduct(BaseModel):
    """Single product as fetched from supplier source. Pre-mapping."""
    external_id: str = Field(..., max_length=128)
    name: str = Field(..., max_length=512)
    cost: Decimal | None = None
    currency: str | None = Field(None, max_length=3)
    stock: int | None = None
    ean: str | None = Field(None, max_length=14)
    url: str | None = Field(None, max_length=2048)
    image_urls: list[str] = Field(default_factory=list)
    data: dict = Field(default_factory=dict)  # custom attributes
```

## PriceStockUpdate Schema (delta)

```python
class PriceStockUpdate(BaseModel):
    """Single delta update — only cost/stock/physical changes."""
    external_id: str
    cost: Decimal | None = None
    currency: str | None = None
    stock: int | None = None
    physical: dict | None = None  # weight, ean, dimensions
```

## RabbitMQ Queues

| Queue | Producer | Consumer | Payload |
|---|---|---|---|
| `scraping` | `django_suppliers.AsyncConnector.dispatch_fetch*` | scraper-workers (separate repo) | `{run_id, supplier_idx, feed_idx, mode, feed_config}` |
| `supplier_results` | scraper-workers | `process_scraper_results_task` (django-suppliers) | `{run_id, mode, products: [RawProduct], errors: list}` |

Wire format: JSON payload, kwargs. Producer uses `current_app.send_task(name, kwargs={...}, queue='scraping')`. Worker `acks_late=True`, idempotent on `run_id`.

## Adding a New Connector

```python
# my_supplier_pkg/connectors.py
from django_suppliers.connectors.base import SyncConnector
from django_suppliers.schemas.contract import RawProduct

class MyRestApiConnector(SyncConnector):
    def validate_config(self):
        if "api_url" not in self.config:
            raise ValueError("api_url required")

    def fetch(self, *, limit=None):
        # Hit REST API, transform to RawProduct, yield
        for item in fetch_paginated(self.config["api_url"]):
            yield RawProduct(
                external_id=item["sku"],
                name=item["title"],
                cost=item["price"],
                currency=item["currency"],
                stock=item["qty"],
                data={"vendor_sku": item["vendor_sku"]},
            )
```

Register via entry_points in your package's `pyproject.toml`:

```toml
[project.entry-points."supplier_connectors"]
my_rest_api = "my_supplier_pkg.connectors:MyRestApiConnector"
```

Install the package into the service venv. Restart service. New connector appears at `/api/suppliers/v2/admin/connectors/` and is selectable in CMS feed creation.

## Scraper-Workers (Separate Repo)

Scraper-workers is an independent repo (TODO: GitLab path TBD). Owns:
- Scraper drivers (Playwright / Scrapy / aiohttp).
- Anti-ban infrastructure (proxy rotation, ban detection).
- Cookie-based session management per domain.
- Reads `scraping` queue, writes `supplier_results` queue.

Connector contract above is the only API surface between them.

**Phase 2 backlog:** HMAC-signed payloads on `supplier_results` queue (current MVP trusts shared RabbitMQ — fine for single-tenant deployment, NOT for multi-tenant).
