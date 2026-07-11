# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django.utils import timezone
from django_utils.models.base_model import BaseModel

from django_suppliers.enums import ProductStatus


class SupplierProduct(BaseModel):
    supplier = models.ForeignKey("django_suppliers.Supplier", on_delete=models.CASCADE, related_name="products")
    feed = models.ForeignKey(
        "django_suppliers.SupplierFeed", on_delete=models.SET_NULL, null=True, blank=True, related_name="products"
    )

    external_id = models.CharField(max_length=128, db_index=True)
    external_id_history = models.JSONField(default=list, blank=True)

    name = models.CharField(max_length=512, db_index=True)
    cost = models.DecimalField(max_digits=12, decimal_places=4, null=True, blank=True)
    currency = models.CharField(max_length=3, blank=True, default="")
    stock = models.IntegerField(null=True, blank=True)
    ean = models.CharField(max_length=14, blank=True, default="", db_index=True)
    url = models.URLField(max_length=2048, blank=True, default="")

    image_urls = models.JSONField(default=list, blank=True)
    data = models.JSONField(default=dict, blank=True)
    data_hash = models.CharField(max_length=40, blank=True, default="", db_index=True)

    status = models.CharField(max_length=30, choices=ProductStatus.choices, default=ProductStatus.NEW, db_index=True)
    feature_set_idx_override = models.CharField(max_length=64, null=True, blank=True)  # noqa: DJ001 — FK-string

    real_product = models.ForeignKey(
        "django_pim.RealProduct", on_delete=models.SET_NULL, null=True, blank=True, related_name="supplier_products"
    )

    pushed_to_channel_idxs = models.JSONField(default=list, blank=True)
    images_complete_channel_idxs = models.JSONField(default=list, blank=True)

    last_synced_at = models.DateTimeField(default=timezone.now)
    data_changed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    physical_changed_at = models.DateTimeField(null=True, blank=True)

    reviewed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    reviewed_at = models.DateTimeField(null=True, blank=True)
    pushed_by = models.ForeignKey("auth.User", on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    pushed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["supplier", "external_id"], name="uq_supplierproduct_supplier_external")
        ]

    def __str__(self) -> str:
        return f"{self.supplier.idx}/{self.external_id}"
