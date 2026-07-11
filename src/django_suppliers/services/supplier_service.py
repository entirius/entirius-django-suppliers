# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from typing import Any

from django.apps import apps
from django.db import transaction
from django.db.models import Q, QuerySet

from django_suppliers.enums import PUSHED_STATUSES, SupplierRole, SupplierType
from django_suppliers.models import Supplier
from django_suppliers.services import event_service

_ALLOWED_ROLES = {SupplierRole.TRADE.value, SupplierRole.MONITORING.value}
_ALLOWED_TYPES = {SupplierType.FEED.value, SupplierType.MANUAL.value}

# Whitelist of fields editable via update_supplier (D-mass-assignment).
_EDITABLE_FIELDS = frozenset(
    {
        "name",
        "supplier_role",
        "supplier_type",
        "review_mode",
        "is_active",
        "default_language",
        "default_currency",
        "country",
        "sku_prefix",
        "default_feature_set_idx",
        "target_warehouse_code",
        "qty_subtract",
        "qty_minimum",
        "company_name",
        "contact_email",
        "contact_phone",
        "contact_person",
        "notes",
        "lead_time_days",
        "credentials",
        # preferred-only physical writes opt-in.
        "allow_physical_writes_from_non_preferred",
    }
)


def _validate_role_type(*, supplier_role: str | None, supplier_type: str | None) -> None:
    if supplier_role is not None and supplier_role not in _ALLOWED_ROLES:
        raise NotImplementedError(f"supplier_role '{supplier_role}' not implemented. Allowed: 'trade', 'monitoring'.")
    if supplier_type is not None and supplier_type not in _ALLOWED_TYPES:
        raise NotImplementedError(f"supplier_type '{supplier_type}' not implemented in MVP. Allowed: 'feed', 'manual'.")


def list_suppliers(
    *, role: str | None = None, type: str | None = None, is_active: bool | None = None, search: str | None = None
) -> QuerySet[Supplier]:
    qs = Supplier.objects.all()
    if role is not None:
        qs = qs.filter(supplier_role=role)
    if type is not None:
        qs = qs.filter(supplier_type=type)
    if is_active is not None:
        qs = qs.filter(is_active=is_active)
    if search:
        qs = qs.filter(Q(idx__icontains=search) | Q(name__icontains=search))
    return qs


def get_supplier(idx: str) -> Supplier:
    try:
        return Supplier.objects.get(idx=idx)
    except Supplier.DoesNotExist as exc:
        raise ValueError(f"Supplier '{idx}' not found") from exc


def supplier_exists(idx: str) -> bool:
    return Supplier.objects.filter(idx=idx).exists()


def _resolve_fk(model_label: str, model_class: type, pk: int | None):
    if pk is None:
        return None
    try:
        return model_class.objects.get(pk=pk)
    except model_class.DoesNotExist as exc:
        raise ValueError(f"{model_label} with id={pk} not found") from exc


def _resolve_regional_fks(
    *, default_language_id: int | None, default_currency_id: int | None, country_id: int | None
) -> dict:
    """C4: views pass `_id` ints, service resolves FK objects internally."""
    from django_regional.models import Country, Currency, Language

    resolved: dict = {}
    if default_language_id is not None:
        resolved["default_language"] = _resolve_fk("Language", Language, default_language_id)
    if default_currency_id is not None:
        resolved["default_currency"] = _resolve_fk("Currency", Currency, default_currency_id)
    if country_id is not None:
        resolved["country"] = _resolve_fk("Country", Country, country_id)
    return resolved


def list_active_suppliers() -> list[Supplier]:
    """Return all suppliers with is_active=True. Used by bulk-push for cross-supplier flows."""
    return list(Supplier.objects.filter(is_active=True))


def resolve_id_by_idx(idx: str) -> int | None:
    """Returns Supplier PK for the given idx, or None when not found.

    Used by view layer to translate URL-side `idx` slugs into FK queries
    without leaking ORM into the view (C4 layer enforcement).
    """
    return Supplier.objects.filter(idx=idx).values_list("id", flat=True).first()


def create_supplier(
    *,
    idx: str,
    name: str,
    default_language=None,
    default_currency=None,
    supplier_role: str = SupplierRole.TRADE.value,
    supplier_type: str = SupplierType.FEED.value,
    review_mode: str = "manual",
    is_active: bool = True,
    country=None,
    sku_prefix: str = "",
    default_feature_set_idx: str | None = None,
    target_warehouse_code: str | None = None,
    qty_subtract: int = 0,
    qty_minimum: int = 0,
    default_language_id: int | None = None,
    default_currency_id: int | None = None,
    country_id: int | None = None,
    **optional: Any,
) -> Supplier:
    # C4: accept either resolved FK objects (legacy callers) or `_id` ints.
    if default_language is None or default_currency is None or country is None:
        resolved = _resolve_regional_fks(
            default_language_id=default_language_id if default_language is None else None,
            default_currency_id=default_currency_id if default_currency is None else None,
            country_id=country_id if country is None else None,
        )
        default_language = default_language or resolved.get("default_language")
        default_currency = default_currency or resolved.get("default_currency")
        country = country or resolved.get("country")
    if default_language is None:
        raise ValueError("default_language is required (pass default_language or default_language_id)")
    if default_currency is None:
        raise ValueError("default_currency is required (pass default_currency or default_currency_id)")
    _validate_role_type(supplier_role=supplier_role, supplier_type=supplier_type)
    if Supplier.objects.filter(idx=idx).exists():
        raise ValueError(f"Supplier with idx '{idx}' already exists.")
    return Supplier.objects.create(
        idx=idx,
        name=name,
        supplier_role=supplier_role,
        supplier_type=supplier_type,
        review_mode=review_mode,
        is_active=is_active,
        default_language=default_language,
        default_currency=default_currency,
        country=country,
        sku_prefix=sku_prefix,
        default_feature_set_idx=default_feature_set_idx,
        target_warehouse_code=target_warehouse_code,
        qty_subtract=qty_subtract,
        qty_minimum=qty_minimum,
        **optional,
    )


def update_supplier(idx: str, **fields: Any) -> Supplier:
    # C4: accept `_id` ints from API layer, resolve FK objects internally.
    fk_kwargs: dict = {}
    for fk_id_key in ("default_language_id", "default_currency_id", "country_id"):
        if fk_id_key in fields:
            fk_kwargs[fk_id_key] = fields.pop(fk_id_key)
    if fk_kwargs:
        fields.update(_resolve_regional_fks(**{k: fk_kwargs.get(k) for k in fk_kwargs}))
    invalid = set(fields) - _EDITABLE_FIELDS
    if invalid:
        raise ValueError(f"Fields not editable via update_supplier: {sorted(invalid)}")
    supplier = get_supplier(idx)
    _validate_role_type(supplier_role=fields.get("supplier_role"), supplier_type=fields.get("supplier_type"))
    for field, value in fields.items():
        setattr(supplier, field, value)
    supplier.save()
    return supplier


def _count_affected_links(supplier: Supplier) -> int:
    try:
        link_model = apps.get_model("django_suppliers", "ProductSupplierLink")
    except LookupError:
        return 0
    return link_model.objects.filter(supplier=supplier).count()


def _list_affected_pushed_skus(supplier: Supplier, *, cap: int = 100) -> list[str]:
    try:
        sp_model = apps.get_model("django_suppliers", "SupplierProduct")
    except LookupError:
        return []
    qs = sp_model.objects.filter(supplier=supplier, status__in=PUSHED_STATUSES).select_related("real_product")
    skus = list(qs.values_list("real_product__sku", flat=True)[:cap])
    return [sku for sku in skus if sku]


def delete_supplier(idx: str, *, force: bool = False) -> dict:
    if not force:
        update_supplier(idx, is_active=False)
        return {"mode": "soft", "supplier_idx": idx}

    with transaction.atomic():
        supplier = get_supplier(idx)
        if hasattr(supplier, "feeds"):
            supplier.feeds.update(is_active=False)

        affected_links_count = _count_affected_links(supplier)
        affected_pushed_skus = _list_affected_pushed_skus(supplier)

        event_service.record(
            event_type="supplier_deleted",
            severity="warning",
            supplier=None,
            message=(
                f"Supplier '{idx}' hard-deleted. "
                f"{affected_links_count} links removed, "
                f"{len(affected_pushed_skus)} pushed SKUs orphaned in PIM (manual cleanup required)."
            ),
            details={
                "supplier_idx": idx,
                "affected_links_count": affected_links_count,
                "affected_pushed_skus": affected_pushed_skus,
            },
        )

        supplier.delete()

    return {
        "mode": "hard",
        "supplier_idx": idx,
        "affected_links_count": affected_links_count,
        "affected_pushed_skus_count": len(affected_pushed_skus),
    }
