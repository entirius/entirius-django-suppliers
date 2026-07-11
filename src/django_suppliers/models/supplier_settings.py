# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

from django.db import models
from django_utils.models.base_model import BaseModel


class SupplierSettings(BaseModel):
    auto_push_enabled = models.BooleanField(default=True)
    scraper_dispatch_enabled = models.BooleanField(default=True)
    delta_sync_enabled = models.BooleanField(default=True)
    integration_event_retention_days = models.PositiveIntegerField(default=90)
    change_log_retention_days = models.PositiveIntegerField(default=90)

    class Meta:
        verbose_name = "Supplier Settings"
        verbose_name_plural = "Supplier Settings"

    def save(self, *args, **kwargs) -> None:
        self.pk = 1
        if self.created_at is None:
            existing = type(self).objects.filter(pk=1).only("created_at").first()
            if existing is not None:
                self.created_at = existing.created_at
        super().save(*args, **kwargs)

    @classmethod
    def load(cls) -> "SupplierSettings":
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        return "Supplier Settings"
