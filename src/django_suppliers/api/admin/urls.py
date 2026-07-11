# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

"""Admin API URL routing — manual `path()` per Volkanos convention."""

from django.urls import path

from django_suppliers.api.admin.views.change_log_views import PimSkuChangeLogViewSet
from django_suppliers.api.admin.views.connector_views import ConnectorListView
from django_suppliers.api.admin.views.feed_views import SupplierFeedViewSet
from django_suppliers.api.admin.views.log_views import ImportLogViewSet, IntegrationEventViewSet
from django_suppliers.api.admin.views.mapping_views import (
    SupplierAttributeMappingViewSet,
    SupplierCategoryMappingViewSet,
    SupplierMappingProfileViewSet,
)
from django_suppliers.api.admin.views.product_link_views import ProductSupplierLinkViewSet
from django_suppliers.api.admin.views.product_views import SupplierProductViewSet
from django_suppliers.api.admin.views.push_views import BulkPushView
from django_suppliers.api.admin.views.realproduct_views import RealProductCrossSupplierViewSet
from django_suppliers.api.admin.views.settings_views import SupplierSettingsView
from django_suppliers.api.admin.views.supplier_views import SupplierCredentialsView, SupplierViewSet

urlpatterns = [
    # Suppliers
    path("suppliers/", SupplierViewSet.as_view({"get": "list", "post": "create"}), name="admin-suppliers-list"),
    path(
        "suppliers/<slug:idx>/",
        SupplierViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="admin-suppliers-detail",
    ),
    path(
        "suppliers/<slug:idx>/delete-impact/",
        SupplierViewSet.as_view({"get": "delete_impact"}),
        name="admin-suppliers-delete-impact",
    ),
    path(
        "suppliers/<slug:idx>/data-keys/",
        SupplierViewSet.as_view({"get": "data_keys"}),
        name="admin-suppliers-data-keys",
    ),
    path(
        "suppliers/<slug:idx>/data-values/",
        SupplierViewSet.as_view({"get": "data_values"}),
        name="admin-suppliers-data-values",
    ),
    path(
        "suppliers/<slug:idx>/credentials/",
        SupplierCredentialsView.as_view({"get": "retrieve"}),
        name="admin-suppliers-credentials",
    ),
    # Feeds (nested under supplier)
    path(
        "suppliers/<slug:supplier_idx>/feeds/",
        SupplierFeedViewSet.as_view({"get": "list", "post": "create"}),
        name="admin-feeds-list",
    ),
    path(
        "suppliers/<slug:supplier_idx>/feeds/<slug:idx>/",
        SupplierFeedViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="admin-feeds-detail",
    ),
    path(
        "suppliers/<slug:supplier_idx>/feeds/<slug:idx>/trigger/",
        SupplierFeedViewSet.as_view({"post": "trigger"}),
        name="admin-feeds-trigger",
    ),
    path(
        "suppliers/<slug:supplier_idx>/feeds/<slug:idx>/test/",
        SupplierFeedViewSet.as_view({"post": "test_feed"}),
        name="admin-feeds-test",
    ),
    # Mapping profiles (nested under supplier)
    path(
        "suppliers/<slug:supplier_idx>/mapping-profiles/",
        SupplierMappingProfileViewSet.as_view({"get": "list", "post": "create"}),
        name="admin-mapping-profiles-list",
    ),
    path(
        "suppliers/<slug:supplier_idx>/mapping-profiles/<slug:idx>/",
        SupplierMappingProfileViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="admin-mapping-profiles-detail",
    ),
    path(
        "suppliers/<slug:supplier_idx>/mapping-profiles/<slug:idx>/validate/",
        SupplierMappingProfileViewSet.as_view({"post": "validate"}),
        name="admin-mapping-profiles-validate",
    ),
    # Attribute mappings (nested under profile by PK)
    path(
        "mapping-profiles/<int:profile_pk>/attribute-mappings/",
        SupplierAttributeMappingViewSet.as_view({"get": "list", "post": "create"}),
        name="admin-attribute-mappings-list",
    ),
    path(
        "mapping-profiles/<int:profile_pk>/attribute-mappings/<int:pk>/",
        SupplierAttributeMappingViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="admin-attribute-mappings-detail",
    ),
    # Category mappings (nested under profile by PK)
    path(
        "mapping-profiles/<int:profile_pk>/category-mappings/",
        SupplierCategoryMappingViewSet.as_view({"get": "list", "post": "create"}),
        name="admin-category-mappings-list",
    ),
    path(
        "mapping-profiles/<int:profile_pk>/category-mappings/<int:pk>/",
        SupplierCategoryMappingViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="admin-category-mappings-detail",
    ),
    # Products (cross-supplier)
    path("products/", SupplierProductViewSet.as_view({"get": "list"}), name="admin-products-list"),
    path(
        "products/bulk-approve/",
        SupplierProductViewSet.as_view({"post": "bulk_approve"}),
        name="admin-products-bulk-approve",
    ),
    path(
        "products/bulk-reject/",
        SupplierProductViewSet.as_view({"post": "bulk_reject"}),
        name="admin-products-bulk-reject",
    ),
    path(
        "products/bulk-requeue/",
        SupplierProductViewSet.as_view({"post": "bulk_requeue"}),
        name="admin-products-bulk-requeue",
    ),
    path(
        "products/<int:pk>/",
        SupplierProductViewSet.as_view({"get": "retrieve", "patch": "partial_update"}),
        name="admin-products-detail",
    ),
    path(
        "products/<int:pk>/approve/", SupplierProductViewSet.as_view({"post": "approve"}), name="admin-products-approve"
    ),
    path("products/<int:pk>/reject/", SupplierProductViewSet.as_view({"post": "reject"}), name="admin-products-reject"),
    path("products/<int:pk>/skip/", SupplierProductViewSet.as_view({"post": "skip"}), name="admin-products-skip"),
    path("products/<int:pk>/queue/", SupplierProductViewSet.as_view({"post": "queue"}), name="admin-products-queue"),
    path("products/<int:pk>/push/", SupplierProductViewSet.as_view({"post": "push"}), name="admin-products-push"),
    path(
        "products/<int:pk>/force-repush/",
        SupplierProductViewSet.as_view({"post": "force_repush"}),
        name="admin-products-force-repush",
    ),
    path(
        "products/<int:pk>/unlink-from-realproduct/",
        SupplierProductViewSet.as_view({"post": "unlink_from_realproduct"}),
        name="admin-products-unlink-from-realproduct",
    ),
    # Product Supplier Links (cross-supplier)
    path(
        "product-links/",
        ProductSupplierLinkViewSet.as_view({"get": "list", "post": "create"}),
        name="admin-product-links-list",
    ),
    path(
        "product-links/<int:pk>/",
        ProductSupplierLinkViewSet.as_view({"get": "retrieve", "patch": "partial_update", "delete": "destroy"}),
        name="admin-product-links-detail",
    ),
    path(
        "product-links/<int:pk>/set-preferred/",
        ProductSupplierLinkViewSet.as_view({"post": "set_preferred"}),
        name="admin-product-links-set-preferred",
    ),
    # Logs + Events
    path("import-logs/", ImportLogViewSet.as_view({"get": "list"}), name="admin-import-logs-list"),
    path("import-logs/<int:pk>/", ImportLogViewSet.as_view({"get": "retrieve"}), name="admin-import-logs-detail"),
    path("events/", IntegrationEventViewSet.as_view({"get": "list"}), name="admin-events-list"),
    path("events/<int:pk>/", IntegrationEventViewSet.as_view({"get": "retrieve"}), name="admin-events-detail"),
    path(
        "events/<int:pk>/acknowledge/",
        IntegrationEventViewSet.as_view({"post": "acknowledge"}),
        name="admin-events-acknowledge",
    ),
    # PIM SKU bridge — audit log read API
    # NOTE: `has-changes/` MUST precede `<path:sku>/...` so the literal slug
    # is not swallowed by the SKU converter.
    path(
        "pim-sku/has-changes/",
        PimSkuChangeLogViewSet.as_view({"get": "has_changes_bulk"}),
        name="admin-pim-sku-has-changes",
    ),
    path(
        "pim-sku/<path:sku>/changes/",
        PimSkuChangeLogViewSet.as_view({"get": "changes_for_sku"}),
        name="admin-pim-sku-changes",
    ),
    path(
        "pim-sku/<path:sku>/acknowledge/",
        PimSkuChangeLogViewSet.as_view({"post": "acknowledge"}),
        name="admin-pim-sku-acknowledge",
    ),
    path(
        "pim-sku/<path:sku>/force-repush/",
        PimSkuChangeLogViewSet.as_view({"post": "force_repush_by_sku"}),
        name="admin-pim-sku-force-repush",
    ),
    # auto-preferred selection manual override / reset
    path(
        "pim-sku/<path:sku>/set-preferred-supplier/",
        PimSkuChangeLogViewSet.as_view({"post": "set_preferred_supplier"}),
        name="admin-pim-sku-set-preferred-supplier",
    ),
    path(
        "pim-sku/<path:sku>/reset-preferred-to-auto/",
        PimSkuChangeLogViewSet.as_view({"post": "reset_preferred_to_auto"}),
        name="admin-pim-sku-reset-preferred-to-auto",
    ),
    # RealProducts (cross-supplier dashboards)
    path(
        "realproducts/merge-by-ean/",
        RealProductCrossSupplierViewSet.as_view({"post": "merge_by_ean"}),
        name="admin-realproducts-merge-by-ean",
    ),
    path(
        "auto-matched/",
        RealProductCrossSupplierViewSet.as_view({"get": "auto_matched_list"}),
        name="admin-auto-matched-list",
    ),
    path(
        "duplicates/", RealProductCrossSupplierViewSet.as_view({"get": "duplicates_list"}), name="admin-duplicates-list"
    ),
    # Push, Settings, Connectors
    path("push/", BulkPushView.as_view(), name="admin-push"),
    path("settings/", SupplierSettingsView.as_view(), name="admin-settings"),
    path("connectors/", ConnectorListView.as_view(), name="admin-connectors"),
]
