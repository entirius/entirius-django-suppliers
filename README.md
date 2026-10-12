# entirius-django-suppliers

A Django module for supplier integrations of Entirius, the open-source modular ecommerce platform.
It keeps a supplier registry, imports supplier feeds into a staging table, lets an operator review the candidates,
pushes approved products to the catalog once, and then refreshes cost, quantity and physical attributes.

[![PyPI](https://img.shields.io/pypi/v/entirius-django-suppliers.svg)](https://pypi.org/project/entirius-django-suppliers/)
[![Python](https://img.shields.io/pypi/pyversions/entirius-django-suppliers.svg)](https://pypi.org/project/entirius-django-suppliers/)
[![License: MPL-2.0](https://img.shields.io/badge/license-MPL--2.0-blue.svg)](https://github.com/entirius/entirius-django-suppliers/blob/develop/LICENSE)

## What it does

- Imports supplier feeds (XML and scraper connectors, more through entry points) into a review queue.
- Maps supplier fields, attributes and categories to the catalog through mapping profiles with value modifiers.
- Pushes approved products to the catalog per sales channel and keeps the supplier attribution.
- Matches new supplier products to existing catalog products by EAN and selects a preferred supplier per product.
- Refreshes cost, stock and physical attributes on a delta schedule; content stays with the curator.
- Records every field change in an audit log and exposes an admin API (JWT, Pydantic schemas, OpenAPI).

## Installation

```shell
uv add entirius-django-suppliers
pip install entirius-django-suppliers
```

Optional extras: `entirius-django-suppliers[qms]` (stock writes to the warehouse module; without it they are skipped).

```python
INSTALLED_APPS = [
    ...
    "django_utils",
    "django_regional",
    "django_pim",
    "django_suppliers",
]

# urls.py
urlpatterns = [
    path("", include("django_suppliers.urls")),
]
```

Settings (`SUPPLIER_*`), the runtime settings singleton, Celery queues and the beat schedule:
https://github.com/entirius/entirius-django-suppliers/blob/develop/docs/configuration.md

## Quick start

```python
from django_suppliers.services.supplier_service import list_active_suppliers

for supplier in list_active_suppliers():
    print(supplier.idx)
```

```shell
python manage.py execute_supplier_feed --supplier-idx acme --feed-idx main --mode full
python manage.py supplier_status
```

## Requirements

- Python 3.11 or newer, Django 5.0 or newer, PostgreSQL.
- A Celery worker consuming the `supplier_default`, `supplier_images` and `supplier_results` queues.
- Entirius packages pulled in: `entirius-django-pim`, `entirius-django-regional`, `entirius-django-utils`.

## Documentation

- Module docs: https://github.com/entirius/entirius-django-suppliers/tree/develop/docs
- Entirius documentation: https://docs.entirius.com/
- Changelog: https://github.com/entirius/entirius-django-suppliers/blob/develop/CHANGELOG.md

## Part of Entirius

This module runs inside the Entirius backend service (Volkanos) and writes into the product catalog.

- Backend service: https://github.com/entirius/entirius-service-volkanos
- Docker harness: https://github.com/entirius/entirius-zeno
- Sibling modules: https://github.com/entirius/entirius-django-pim, https://github.com/entirius/entirius-django-atlas,
  https://github.com/entirius/entirius-django-regional

## Development

```shell
make install     # sync dependencies (uv, incl. extras)
make check       # lint + format check (ruff)
make test        # test suite (pytest + pytest-django, needs PostgreSQL via DATABASE_URL)
```

Development and agent instructions: https://github.com/entirius/entirius-django-suppliers/blob/develop/AGENTS.md

## License

Mozilla Public License 2.0 (MPL-2.0) — see https://github.com/entirius/entirius-django-suppliers/blob/develop/LICENSE.
