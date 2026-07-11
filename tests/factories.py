# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at https://mozilla.org/MPL/2.0/.

import uuid
from decimal import Decimal

import factory
from django.utils import timezone
from django_regional.models.currency import Currency
from django_regional.models.language import Language

from django_suppliers.enums import LogMode, LogSource, LogStatus, ProductStatus, SupplierRole, SupplierType
from django_suppliers.models import (
    AttributeMappingTargetType,
    ImportLog,
    Supplier,
    SupplierAttributeMapping,
    SupplierCategoryMapping,
    SupplierFeed,
    SupplierMappingProfile,
    SupplierProduct,
)


class LanguageFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Language
        django_get_or_create = ("iso2",)

    iso2 = "en"
    iso3 = "eng"
    name_en = "English"
    name_pl = "angielski"


class CurrencyFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Currency
        django_get_or_create = ("iso3",)

    iso3 = "EUR"
    name_en = "Euro"
    name_pl = "Euro"
    symbol = "EUR"


class SupplierFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = Supplier
        django_get_or_create = ("idx",)

    idx = factory.Sequence(lambda n: f"test-supplier-{n}")
    name = factory.LazyAttribute(lambda obj: f"Test Supplier {obj.idx}")
    supplier_role = SupplierRole.TRADE.value
    supplier_type = SupplierType.FEED.value
    is_active = True
    default_language = factory.SubFactory(LanguageFactory)
    default_currency = factory.SubFactory(CurrencyFactory)


class FeedFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SupplierFeed
        django_get_or_create = ("supplier", "idx")

    supplier = factory.SubFactory(SupplierFactory)
    idx = factory.Sequence(lambda n: f"feed-{n}")
    connector_kind = "xml_feed"
    feed_config = factory.LazyFunction(
        lambda: {
            "feed_url": "https://example.com/feed.xml",
            "field_mapping": {
                "external_id": "./sku/text()",
                "name": "./name/text()",
                "cost": "./price/text()",
                "currency": "./price/@currency",
                "stock": "./stock/text()",
                "ean": "./ean/text()",
                "url": "./url/text()",
                "description": "./description/text()",
                "manufacturer": "./manufacturer/text()",
                "category": "./category/text()",
            },
        }
    )
    sync_mode = "full"
    is_active = True


class SupplierProductFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SupplierProduct

    supplier = factory.SubFactory(SupplierFactory)
    feed = factory.SubFactory(FeedFactory, supplier=factory.SelfAttribute("..supplier"))
    external_id = factory.Sequence(lambda n: f"SKU-{n:04d}")
    name = factory.LazyAttribute(lambda obj: f"Product {obj.external_id}")
    cost = Decimal("100.00")
    currency = "EUR"
    stock = 10
    ean = ""
    url = ""
    image_urls = factory.LazyFunction(list)
    data = factory.LazyFunction(dict)
    data_hash = ""
    status = ProductStatus.NEW.value
    last_synced_at = factory.LazyFunction(timezone.now)


class ImportLogFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = ImportLog

    feed = factory.SubFactory(FeedFactory)
    run_id = factory.LazyFunction(uuid.uuid4)
    mode = LogMode.FULL.value
    status = LogStatus.RUNNING.value
    source = LogSource.SCHEDULER.value


class MappingProfileFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SupplierMappingProfile
        django_get_or_create = ("supplier", "idx")

    supplier = factory.SubFactory(SupplierFactory)
    idx = factory.Sequence(lambda n: f"profile-{n}")
    name = factory.LazyAttribute(lambda obj: f"Profile {obj.idx}")
    target_channel_idxs = factory.LazyFunction(list)
    is_active = True


class AttributeMappingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SupplierAttributeMapping

    profile = factory.SubFactory(MappingProfileFactory)
    source_field = factory.Sequence(lambda n: f"source_field_{n}")
    target_type = AttributeMappingTargetType.SKIP.value
    target_identifier = ""
    is_required = False


class CategoryMappingFactory(factory.django.DjangoModelFactory):
    class Meta:
        model = SupplierCategoryMapping

    profile = factory.SubFactory(MappingProfileFactory)
    source_field = "category"
    source_value = factory.Sequence(lambda n: f"value-{n}")
    target_category_idx = factory.Sequence(lambda n: f"cat-{n}")
