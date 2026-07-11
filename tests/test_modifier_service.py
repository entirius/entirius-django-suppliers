# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import pytest

from django_suppliers.services import modifier_service
from tests.factories import SupplierFactory

pytestmark = pytest.mark.django_db


def _supplier(qs: int = 0, qm: int = 0):
    return SupplierFactory(qty_subtract=qs, qty_minimum=qm)


def test_qty_subtract_basic():
    assert modifier_service.apply_qty_modifiers(10, _supplier(qs=1, qm=0)) == 9


def test_zero_in_zero_out():
    assert modifier_service.apply_qty_modifiers(0, _supplier(qs=0, qm=0)) == 0


def test_subtract_clamps_to_zero():
    assert modifier_service.apply_qty_modifiers(1, _supplier(qs=2, qm=0)) == 0


def test_qty_minimum_floors_result():
    assert modifier_service.apply_qty_modifiers(5, _supplier(qs=2, qm=10)) == 10


def test_none_treated_as_zero():
    assert modifier_service.apply_qty_modifiers(None, _supplier(qs=0, qm=0)) == 0


def test_passthrough_no_modifiers():
    assert modifier_service.apply_qty_modifiers(100, _supplier(qs=0, qm=0)) == 100


def test_subtract_more_than_value_clamps_to_zero():
    assert modifier_service.apply_qty_modifiers(2, _supplier(qs=5, qm=0)) == 0


def test_negative_raw_clamped_to_zero():
    """Defensive — model rejects qty_subtract<0, but raw_qty from delta sync could be negative."""
    assert modifier_service.apply_qty_modifiers(-5, _supplier(qs=0, qm=0)) == 0
