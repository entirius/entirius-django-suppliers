# Changelog

## [Unreleased]

- Access: the module declares its own access areas on its AppConfig and its admin views (copied from the
  entirius-django-access defaults; behaviour unchanged).
- Docs: neutral supplier name in examples.

### Security

- `POST suppliers/<idx>/feeds/<idx>/test/` needs the write level of `suppliers.sources` (it was mapped to read, so
  a read-only role could fetch a supplier feed with the stored credentials), and its `limit` is capped at 10
  (1 to 10, default 10; a value outside answers 400).

## 2.0.0 — 2026-07-11

- Initial public release: supplier registry, feed-driven import pipeline
  with mapping value modifiers, review workflow with per-field audit log,
  auto EAN matching, preferred-supplier strategy, and one-time push to PIM
  with cyclic delta refresh.
- Connector contract via `supplier_connectors` entry points.
- Optional QMS integration behind the `[qms]` extra.
- Migrations squashed into a single initial migration for the Entirius epoch.
