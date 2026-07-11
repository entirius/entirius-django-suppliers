# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.contrib import admin

from django_suppliers.models import (
    ImportLog,
    IntegrationEvent,
    ProductSupplierLink,
    Supplier,
    SupplierAttributeMapping,
    SupplierCategoryMapping,
    SupplierFeed,
    SupplierMappingProfile,
    SupplierProduct,
    SupplierProductChangeLog,
    SupplierSettings,
)


@admin.register(Supplier)
class SupplierAdmin(admin.ModelAdmin):
    list_display = (
        "idx",
        "name",
        "supplier_role",
        "supplier_type",
        "is_active",
        "default_currency",
        "target_warehouse_code",
    )
    list_filter = ("supplier_role", "supplier_type", "is_active")
    search_fields = ("idx", "name")

    def get_exclude(self, request, obj=None):
        # C1: hide `credentials` from non-superusers — sensitive connector secrets.
        base = list(super().get_exclude(request, obj) or [])
        if not request.user.is_superuser:
            base.append("credentials")
        return base


@admin.register(SupplierSettings)
class SupplierSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "auto_push_enabled",
        "scraper_dispatch_enabled",
        "delta_sync_enabled",
        "integration_event_retention_days",
        "change_log_retention_days",
    )

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(IntegrationEvent)
class IntegrationEventAdmin(admin.ModelAdmin):
    list_display = ("event_type", "severity", "supplier", "feed", "created_at", "acknowledged_at")
    list_filter = ("severity", "event_type", "acknowledged_at")
    search_fields = ("message",)
    readonly_fields = (
        "event_type",
        "severity",
        "supplier",
        "feed",
        "message",
        "details",
        "acknowledged_at",
        "acknowledged_by",
        "created_at",
        "modified_at",
    )


@admin.register(SupplierFeed)
class SupplierFeedAdmin(admin.ModelAdmin):
    list_display = ("supplier", "idx", "connector_kind", "sync_mode", "is_active", "last_sync_at", "last_sync_status")
    list_filter = ("connector_kind", "sync_mode", "is_active")
    search_fields = ("idx", "supplier__idx", "supplier__name")


@admin.register(SupplierProduct)
class SupplierProductAdmin(admin.ModelAdmin):
    list_display = ("supplier", "external_id", "name", "status", "cost", "stock", "data_changed_at")
    list_filter = ("status", "supplier")
    search_fields = ("external_id", "name", "ean")
    readonly_fields = (
        "data_hash",
        "external_id_history",
        "pushed_to_channel_idxs",
        "images_complete_channel_idxs",
        "last_synced_at",
        "data_changed_at",
        "physical_changed_at",
        "pushed_at",
        "pushed_by",
        "reviewed_at",
        "reviewed_by",
        "created_at",
        "modified_at",
    )


class SupplierAttributeMappingInline(admin.TabularInline):
    model = SupplierAttributeMapping
    extra = 0
    fields = ("source_field", "target_type", "target_identifier", "is_required")


class SupplierCategoryMappingInline(admin.TabularInline):
    model = SupplierCategoryMapping
    extra = 0
    fields = ("source_field", "source_value", "target_category_idx")


@admin.register(SupplierMappingProfile)
class SupplierMappingProfileAdmin(admin.ModelAdmin):
    list_display = ("supplier", "idx", "name", "is_active", "target_channel_idxs")
    list_filter = ("is_active", "supplier")
    search_fields = ("idx", "name", "supplier__idx")
    inlines = [SupplierAttributeMappingInline, SupplierCategoryMappingInline]


@admin.register(ImportLog)
class ImportLogAdmin(admin.ModelAdmin):
    list_display = (
        "feed",
        "run_id",
        "mode",
        "status",
        "started_at",
        "finished_at",
        "total_count",
        "new_count",
        "updated_count",
        "delisted_count",
        "error_count",
    )
    list_filter = ("status", "mode", "source")
    search_fields = ("run_id",)
    readonly_fields = (
        "feed",
        "run_id",
        "mode",
        "status",
        "started_at",
        "finished_at",
        "total_count",
        "new_count",
        "updated_count",
        "unchanged_count",
        "delisted_count",
        "error_count",
        "error_summary",
        "triggered_by",
        "source",
        "created_at",
        "modified_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ProductSupplierLink)
class ProductSupplierLinkAdmin(admin.ModelAdmin):
    list_display = ("real_product_sku", "supplier", "priority", "is_preferred", "is_active", "external_id")
    list_filter = ("supplier", "is_active", "is_preferred")
    search_fields = ("real_product_sku", "external_id")


@admin.register(SupplierProductChangeLog)
class SupplierProductChangeLogAdmin(admin.ModelAdmin):
    list_display = (
        "supplier_product",
        "real_product_sku",
        "source",
        "field_path",
        "applied_to_pim",
        "triggered_by",
        "created_at",
    )
    list_filter = ("source", "applied_to_pim")
    search_fields = ("real_product_sku", "field_path")
    readonly_fields = (
        "supplier_product",
        "real_product_sku",
        "source",
        "field_path",
        "before",
        "after",
        "triggered_by",
        "applied_to_pim",
        "applied_to_pim_at",
        "created_at",
        "modified_at",
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
