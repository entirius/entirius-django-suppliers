# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Killswitches for django_suppliers signals.

Two layers:

1. Thread-local suppression — `suppress_supplier_signals()` context manager.
   Used to wrap a block of code that should NOT fire side-effect handlers
   (e.g. data migrations, bulk fixtures load, test setup). Inspect via
   `is_suppressed()` from inside a handler.

2. DB-level auto-push toggle — `is_auto_push_enabled()` reads
   `SupplierSettings.auto_push_enabled` with a 60s Redis/LocMem cache.
   Cache is invalidated on `SupplierSettings` post_save via a handler
   wired in `apps.py` (uses `transaction.on_commit` per decision #32).
"""

import threading
from contextlib import contextmanager

from django.core.cache import cache

_AUTO_PUSH_CACHE_KEY = "supplier:auto_push_enabled"
_AUTO_PUSH_CACHE_TTL = 60

_local = threading.local()


@contextmanager
def suppress_supplier_signals():
    previous = getattr(_local, "suppressed", False)
    _local.suppressed = True
    try:
        yield
    finally:
        _local.suppressed = previous


def is_suppressed() -> bool:
    return getattr(_local, "suppressed", False)


def is_auto_push_enabled() -> bool:
    cached = cache.get(_AUTO_PUSH_CACHE_KEY)
    if cached is not None:
        return bool(cached)

    from django_suppliers.models import SupplierSettings

    value = SupplierSettings.load().auto_push_enabled
    cache.set(_AUTO_PUSH_CACHE_KEY, value, _AUTO_PUSH_CACHE_TTL)
    return value


def invalidate_auto_push_cache() -> None:
    cache.delete(_AUTO_PUSH_CACHE_KEY)
