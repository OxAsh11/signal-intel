# Phase 0b — Foundation Schema (F2–F8) + Health Skeleton Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every foundation table from PRD Part 1 exists with RLS, provenance and versioning rules enforced in the database. A daily canary pushes a synthetic record through source → mention → entity → claim → verdict and records health.

**Architecture:** Plain SQL migrations 0007–0010. Fact tables are organisation-level and carry `scope`. Working and derived tables are workspace-level. Derived tables accept writes only from a derivation transaction. A SQL `app.run_canary()` function, called daily by the worker, exercises the chain.

**Tech Stack:** Postgres 17 (Supabase), pgvector, pg_trgm; PGlite for offline checks; pgTAP in CI; Procrastinate worker.

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md` (PRD Part 1, M17)

## Global Constraints

- Canonical value lists come only from `packages/contracts/schema/enums.json`. Statuses the PRD defines outside Appendix A (entity status, predicate value type, feedback action, health status) are CHECK constraints, not new enums.
- Clients (`authenticated`) get SELECT only. Every table has RLS.
- **Fact tables** (`entity`, `entity_alias`, `source_record`, `mention`, `claim`, `relationship`) are organisation-level. A row is readable by org members, or by anyone when `scope = 'shared'` and the platform switch `shared_facts_enabled` is on. It is off in V1, and turning it on is a row update, not a migration (F1).
- `person` entities, and claims, relationships and aliases that touch a person, are always `scope = 'tenant'`, enforced by constraint or trigger.
- **Immutable:** `source_record` payload columns, `brief_version`, `extractor_version`, `activity`, `feedback`, `audit_log` (F3, F5, F6).
- **Derived tables** (`verdict`, `score`, `label`, `account_stage_event`, `account_card`, `memory_chunk`, and `account.stage`) reject writes unless the transaction has set `app.writer = 'derivation'` (F7).
- Every derived row has a NOT NULL `brief_version_id` where the PRD ties it to a Brief version (F5), plus an `inputs` record.
- Claims reference predicates by id (not key alone), because workspace predicates may reuse a global key.
- Claim expiry defaults to `observed_at + 4 × decay_half_life_days`, when weight falls below 6.25%. Null half-life means no expiry.
- Embeddings: `vector(1024)` (Voyage 3.5 default dimension), HNSW cosine index.

## Review Focus

1. A claim whose value violates its predicate's type or allowed values is rejected. Test: `claim rejects out-of-registry value`.
2. A person entity or person claim marked `shared` is rejected. Test: `person data is always tenant`.
3. Org B cannot read org A's facts, even with `scope = 'shared'`, while the switch is off. Test: `shared facts hidden while switch off`.
4. Writing a verdict, or changing `account.stage`, outside a derivation transaction fails. Test: `derived tables reject direct writes`.
5. The same fetched item inserted twice yields one `source_record`. Test: `source_record dedupes on content hash`.

---

### Task 1: Identity, provenance, relationships (migration 0007)
`platform_setting`, `entity` (+ `entity_current` view, `app.canonical_entity()`), `entity_alias`, `account`, `source_record`, `mention`, `predicate`, `extractor_version`, `claim` (+ value validation and expiry trigger), `relation_type` (seeded: works_at, operated_by, resells, belongs_to, subsidiary_of, competes_with, partner_of), `relationship` (+ endpoint type check).

### Task 2: Briefs, versions, activity, feedback (migration 0008)
`brief`, `brief_version` (insert-only), `activity` (append-only, unique `(integration_id, external_id)`), `feedback` (append-only).

### Task 3: Derived tables (migration 0009)
`app.guard_derived()` trigger function; `verdict`, `score`, `label`, `account_stage_event`, `account_card`, `memory_chunk` (vector + FTS); guard on `account.stage`.

### Task 4: Health + canary (migration 0010)
`health_check`, `health_incident`; platform canary org, workspace, brief, brief_version and predicate (fixed UUIDs); `app.run_canary()` returns the verdict id and writes `health_check`, the outbox events and audit; job types `system.canary` (expected window 26 h).

### Task 5: Tests
Extend `scripts/check-migrations.mjs` with the five Review Focus checks, immutability, canary end to end, and isolation across all new tables. Add the pgTAP file `04_foundation.test.sql`.

### Task 6: Worker
A `system.canary` periodic job (daily) calling `app.run_canary()` as the derivation writer. The heartbeat writes a `health_check` row. Unit tests use a fake executor.

### Task 7: Verify
`pnpm db:check`, `pnpm test`, `pnpm typecheck`, `pnpm build`, `pnpm py:check` all pass; commit and push.
