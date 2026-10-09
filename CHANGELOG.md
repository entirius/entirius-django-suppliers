# Changelog

## [Unreleased]

- Access: the module declares its own access areas on its AppConfig and its admin views (copied from the
  entirius-django-access defaults; behaviour unchanged).

## 2.0.0 — 2026-07-11

- Initial public release: supplier registry, feed-driven import pipeline
  with mapping value modifiers, review workflow with per-field audit log,
  auto EAN matching, preferred-supplier strategy, and one-time push to PIM
  with cyclic delta refresh.
- Connector contract via `supplier_connectors` entry points.
- Optional QMS integration behind the `[qms]` extra.
- Migrations squashed into a single initial migration for the Entirius epoch.
