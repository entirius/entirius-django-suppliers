# django-suppliers

Suppliers Django module for the Volkanos ecommerce platform — supplier registry, feed-driven
product import pipeline, review/approval workflow, one-time push to PIM and cyclic delta refresh
of cost / qty / physical attributes. Cross-supplier features: auto EAN-match, preferred-supplier
selection, per-field audit log and a CMS bridge for "supplier touched this SKU" badges.

## Installation

```shell
pip install entirius-django-suppliers
```

Add the app to your project:

```python
INSTALLED_APPS = [
    ...
    "django_suppliers",
]
```

Optional QMS stock integration (soft dependency — degrades gracefully when absent):

```shell
pip install "entirius-django-suppliers[qms]"
```

## Development

```shell
make install     # sync dependencies (uv)
make check       # lint + format check (ruff)
make test        # test suite (pytest + pytest-django, postgres via DATABASE_URL)
```

Architecture, API and model reference: [AGENTS.md](AGENTS.md), [docs/](docs/).

## License

Mozilla Public License 2.0 — see [LICENSE](LICENSE).
