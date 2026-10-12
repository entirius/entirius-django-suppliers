---
title: "Suppliers: Database Diagrams"
description: "Auto-generated ER diagrams for the Suppliers module."
sidebar:
  badge:
    text: "Auto-gen"
    variant: "note"
---

:::caution[Auto-generated]
These diagrams are auto-generated from Django model introspection.
Do not edit. Run `make erd` in entirius-docker to regenerate.
:::

## Core Registry

```d2 layout=elk
Supplier: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  idx: varchar {constraint: unique}
  default_language_id: int {constraint: foreign_key}
  default_currency_id: int {constraint: foreign_key}
  country_id: int {constraint: foreign_key}
  name: varchar
  supplier_role: varchar
  supplier_type: varchar
}

SupplierSettings: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  auto_push_enabled: bool
  scraper_dispatch_enabled: bool
  delta_sync_enabled: bool
  integration_event_retention_days: int
  change_log_retention_days: int
}

ProductSupplierLink: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  supplier_id: int {constraint: foreign_key}
  real_product_sku: varchar
  external_id: varchar
  priority: int
  is_preferred: bool
  is_active: bool
  notes: text
}

Country: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Country (External: django_regional)"
}

Currency: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Currency (External: django_regional)"
}

Language: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Language (External: django_regional)"
}



Supplier.default_language_id -> Language.id: {style.stroke: "#484B57"}

Supplier.default_currency_id -> Currency.id: {style.stroke: "#484B57"}

Supplier.country_id -> Country.id: {style.stroke: "#484B57"}

ProductSupplierLink.supplier_id -> Supplier.id: {style.stroke: "#00ACC1"}
```

## Feeds and Import

```d2 layout=elk
SupplierFeed: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  supplier_id: int {constraint: foreign_key}
  language_id: int {constraint: foreign_key}
  currency_id: int {constraint: foreign_key}
  idx: varchar
  connector_kind: varchar
  feed_config: jsonb
  schedule_cron: varchar
}

SupplierProduct: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  supplier_id: int {constraint: foreign_key}
  feed_id: int {constraint: foreign_key}
  real_product_id: int {constraint: foreign_key}
  reviewed_by_id: int {constraint: foreign_key}
  pushed_by_id: int {constraint: foreign_key}
  external_id: varchar
  external_id_history: jsonb
}

ImportLog: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  feed_id: int {constraint: foreign_key}
  run_id: uuid {constraint: unique}
  triggered_by_id: int {constraint: foreign_key}
  mode: varchar
  status: varchar
  started_at: timestamp
  finished_at: timestamp
}

Currency: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Currency (External: django_regional)"
}

Language: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Language (External: django_regional)"
}

RealProduct: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "RealProduct (External: django_pim)"
}

Supplier: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Supplier (See core-registry diagram)"
}

User: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "User (External: auth)"
}



SupplierFeed.supplier_id -> Supplier.id: {style.stroke: "#484B57"}

SupplierFeed.language_id -> Language.id: {style.stroke: "#484B57"}

SupplierFeed.currency_id -> Currency.id: {style.stroke: "#484B57"}

SupplierProduct.supplier_id -> Supplier.id: {style.stroke: "#484B57"}

SupplierProduct.feed_id -> SupplierFeed.id: {style.stroke: "#00ACC1"}

SupplierProduct.real_product_id -> RealProduct.id: {style.stroke: "#484B57"}

SupplierProduct.reviewed_by_id -> User.id: {style.stroke: "#484B57"}

SupplierProduct.pushed_by_id -> User.id: {style.stroke: "#484B57"}

ImportLog.feed_id -> SupplierFeed.id: {style.stroke: "#00ACC1"}

ImportLog.triggered_by_id -> User.id: {style.stroke: "#484B57"}
```

## Mapping

```d2 layout=elk
SupplierMappingProfile: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  supplier_id: int {constraint: foreign_key}
  import_language_id: int {constraint: foreign_key}
  idx: varchar
  name: varchar
  target_channel_idxs: jsonb
  feature_set_idx: varchar
  is_active: bool
}

SupplierAttributeMapping: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  profile_id: int {constraint: foreign_key}
  source_field: varchar
  target_type: varchar
  target_identifier: varchar
  is_required: bool
  modifier: varchar
}

SupplierCategoryMapping: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  profile_id: int {constraint: foreign_key}
  source_field: varchar
  source_value: varchar
  target_category_idx: varchar
}

Language: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Language (External: django_regional)"
}

Supplier: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Supplier (See core-registry diagram)"
}



SupplierMappingProfile.supplier_id -> Supplier.id: {style.stroke: "#484B57"}

SupplierMappingProfile.import_language_id -> Language.id: {style.stroke: "#484B57"}

SupplierAttributeMapping.profile_id -> SupplierMappingProfile.id: {style.stroke: "#00ACC1"}

SupplierCategoryMapping.profile_id -> SupplierMappingProfile.id: {style.stroke: "#00ACC1"}
```

## Monitoring

```d2 layout=elk
IntegrationEvent: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  supplier_id: int {constraint: foreign_key}
  feed_id: int {constraint: foreign_key}
  supplier_product_id: int {constraint: foreign_key}
  acknowledged_by_id: int {constraint: foreign_key}
  event_type: varchar
  severity: varchar
  message: text
}

SupplierProductChangeLog: {
  shape: sql_table
  style.fill: "#00ACC1"
  style.stroke: "#12141A"
  style.font-color: "#EBEDF2"
  id: int {constraint: primary_key}
  supplier_product_id: int {constraint: foreign_key}
  triggered_by_id: int {constraint: foreign_key}
  real_product_sku: varchar
  source: varchar
  field_path: varchar
  before: jsonb
  after: jsonb
}

Supplier: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "Supplier (See core-registry diagram)"
}

SupplierFeed: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "SupplierFeed (See feeds-and-import diagram)"
}

SupplierProduct: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "SupplierProduct (See feeds-and-import diagram)"
}

User: {
  shape: sql_table
  style.fill: "#484B57"
  style.stroke: "#1A1C25"
  style.stroke-dash: 3
  style.font-color: "#9A9CAA"
  id: int {constraint: primary_key}
  label: "User (External: auth)"
}



IntegrationEvent.supplier_id -> Supplier.id: {style.stroke: "#484B57"}

IntegrationEvent.feed_id -> SupplierFeed.id: {style.stroke: "#484B57"}

IntegrationEvent.supplier_product_id -> SupplierProduct.id: {style.stroke: "#484B57"}

IntegrationEvent.acknowledged_by_id -> User.id: {style.stroke: "#484B57"}

SupplierProductChangeLog.supplier_product_id -> SupplierProduct.id: {style.stroke: "#484B57"}

SupplierProductChangeLog.triggered_by_id -> User.id: {style.stroke: "#484B57"}
```
