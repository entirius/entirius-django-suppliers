---
title: Testing
description: PostgreSQL requirement of the django-suppliers test suite and how to run it.
---

Tests run on postgres via `DATABASE_URL` (CI provides a postgres service; locally point it at any
postgres 15+).

```bash
make install
make test
```

INSTALLED_APPS in tests: `django_regional`, `django_pim`, `django_suppliers`.
**NOT** `django_qms` / `django_pricemanager` (soft deps, out of scope).
