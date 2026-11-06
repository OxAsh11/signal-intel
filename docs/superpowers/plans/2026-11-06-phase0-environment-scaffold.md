# Phase 0a — Environment Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A working monorepo: web and worker services that build, test and deploy to Railway against Supabase, with the first foundation migrations (tenancy, RLS helpers, audit, outbox) and CI.

**Architecture:** pnpm + Turborepo for TypeScript (`apps/web`, `packages/ui`, `packages/db-types`, `packages/contracts`), and a uv workspace for Python (`packages/signal_core`, `services/worker`). The schema lives in plain SQL Supabase migrations. Shared enums and event payloads live as JSON Schema in `packages/contracts`, generated to zod and Pydantic.

**Tech Stack:** Node 24, pnpm 10, Turborepo, Next.js 16 (App Router, standalone), Tailwind v4, `@supabase/ssr`; Python 3.13 via uv, Procrastinate, psycopg 3, Pydantic v2, structlog, pytest, ruff, mypy; Supabase CLI; PGlite (local migration smoke test); GitHub Actions; Railway.

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md`

## Global Constraints

- Canonical values come from PRD Appendix A exactly (case and spelling). `packages/contracts/schema/enums.json` is their only source in code.
- The `authenticated` role gets only `SELECT` on tenant tables. All writes go through `app.*` SECURITY DEFINER functions or the worker's `service_role`.
- Every write function writes `audit_log` in the same transaction; when the change maps to an event in the closed catalogue (A.4) it also writes `outbox` there. No new event types are invented for Phase 0.
- `person` entities and contact points are always `scope = 'tenant'` (CHECK constraint, not a setting).
- Automation gate defaults: confidence ≥ 90 and reversible → `applied`; 40–89, or ≥ 90 and irreversible → `review queue`; < 40 → `digest`.
- No secret in the repo. `.env.example` lists names only.
- Python 3.13 (`.python-version`); Node ≥ 24.

## Review Focus

1. A user in org A querying any tenant table returns zero rows of org B. Pinned by the pgTAP `rls_isolation` test.
2. A Viewer calling `app.create_workspace` is refused. Pinned by the pgTAP `write_paths` test.
3. A replayed outbox event never enqueues twice. Pinned by `test_relay_idempotent` (queueing lock = event id).
4. A disabled job type does not run. Pinned by `test_job_kill_switch`.
5. Generated zod and Pydantic enums drift from `enums.json`. Pinned by the `codegen:check` CI step and by `test_enums_match_appendix`.

---

### Task 1: Root tooling
**Files:** `package.json`, `pnpm-workspace.yaml`, `turbo.json`, `pyproject.toml`, `.python-version`, `.gitignore`, `.gitattributes`, `.editorconfig`, `.env.example`, `README.md`
- [ ] Root `package.json` with `packageManager: pnpm@10.x`, scripts `dev | build | lint | typecheck | test | codegen | codegen:check | db:check`.
- [ ] `pnpm-workspace.yaml`: `apps/*`, `packages/*` (TS only), `onlyBuiltDependencies: [supabase, esbuild, sharp]`.
- [ ] `pyproject.toml` uv workspace with members `packages/signal_core` and `services/worker`; dev group ruff, mypy, pytest.
- [ ] `.gitattributes` `* text=auto eol=lf`.
- [ ] Verify: `pnpm install` and `uv sync` both succeed.

### Task 2: Contracts + codegen
**Files:** `packages/contracts/schema/{enums,events}.json`, `packages/contracts/scripts/gen.mjs`, `packages/contracts/gen/ts/index.ts`, `packages/signal_core/src/signal_core/contracts/__init__.py` (generated)
**Produces:** TS `Verdict`, `Band`, `Stage`, … as zod enums plus types; Python `Verdict(StrEnum)` and the rest; `EVENT_TYPES` tuple.
- [ ] Test `test_enums_match_appendix`: `Verdict` values == `["Confirmed","Likely","Unverified","Contradicted"]`, the event list has 20 entries, `Stage` has 9.
- [ ] `gen.mjs` writes both outputs deterministically. `--check` exits 1 if the files would change.

### Task 3: Supabase foundation migrations
**Files:** `supabase/config.toml`, `supabase/migrations/2026110600000{1..5}_*.sql`, `supabase/tests/*.test.sql`, `supabase/seed.sql`, `scripts/check-migrations.mjs`
- [ ] 0001 extensions: `vector`, `pg_trgm`, `unaccent`, `pgcrypto`; schema `app`.
- [ ] 0002 enums: every Appendix A list and the event catalogue as Postgres enums.
- [ ] 0003 tenancy: `organisation`, `workspace`, `member`, `team`, `role_assignment`, `invitation`; helpers `app.current_member_id()`, `app.has_org_role(org, min)`, `app.has_workspace_role(ws, min)` (STABLE SECURITY DEFINER); SELECT-only RLS policies.
- [ ] 0004 `audit_log` (append-only: UPDATE/DELETE revoked plus a trigger), `outbox`, `app.record(...)` helper that writes both; `app.create_organisation`, `app.create_workspace` as the first audited write paths.
- [ ] 0005 `job_type_config` (kill switch), `workspace_settings` (gate thresholds 90/40, dormancy 45, attribution 90).
- [ ] pgTAP: `rls_isolation`, `write_paths`, `audit_append_only` (run in CI).
- [ ] `scripts/check-migrations.mjs` applies all migrations in PGlite with a Supabase stub prelude (auth schema, roles). Verify locally: `pnpm db:check` passes.

### Task 4: Python core + worker
**Files:** `packages/signal_core/src/signal_core/{config,logging,db,gate,jobs,events}.py`, `services/worker/src/worker/{app,main,health}.py`, tests under each
**Produces:** `gate(confidence: int, reversible: bool, thresholds=Thresholds()) -> GateOutcome`; `@job(type: str, ...)` decorator; `relay_once(conn) -> int`; `Settings` (pydantic-settings).
- [ ] `test_gate_*`: 95/rev → applied; 95/irrev → review_queue; 60 → review_queue; 39 → digest; 40 boundary → review_queue; 90 boundary/rev → applied.
- [ ] `test_job_kill_switch`: a job whose type is disabled returns a skipped result without calling the body.
- [ ] `test_relay_idempotent`: two relays of the same event defer one job (fake connector, Procrastinate `InMemoryConnector`).
- [ ] Worker: Procrastinate app on `DATABASE_URL_SESSION`, a `heartbeat` periodic task (every minute) writing to the log, `/healthz` on `$PORT`, SIGTERM-safe.
- [ ] Verify: `uv run pytest`, `uv run ruff check`, `uv run mypy`.

### Task 5: UI package + web app
**Files:** `packages/ui/{scripts/build-tokens.mjs,src/tokens.css,src/index.ts,src/components/*}`, `apps/web/**`
- [ ] `build-tokens.mjs` turns `design/signal-intel-ds/project/tokens.json` into `tokens.css` (light `:root`, dark media-query + `[data-theme]`) and a Tailwind v4 `@theme` mapping. Test: the output contains `--signal: #1c4d86` and a dark block with `--signal: #86b4ee`.
- [ ] `@signal/ui` exports `Button`, `VerdictBadge`, `BandChip`, `DerivedValue`, `EvidenceQuote`, `MetricTile`, `HealthIndicator` (typed ports of the design-system bundle). Test: `VerdictBadge` renders the label plus a glyph for each verdict.
- [ ] `apps/web`: Next.js 16 standalone; app shell with the 11 screens in nav; `/design` renders tokens and components; `/login` (Google, Microsoft via Supabase); `/api/health`; `lib/supabase/{server,client,middleware}.ts`.
- [ ] Verify: `pnpm build` and `pnpm test` pass.

### Task 6: Deploy + CI
**Files:** `apps/web/railway.json`, `services/worker/railway.json`, `.github/workflows/{ci,migrate-dev,migrate-prod}.yml`, `docs/deploy.md`
- [ ] Railway: web `pnpm --filter web start`, healthcheck `/api/health`; worker `uv run --package worker worker`, healthcheck `/healthz`; watch paths per service.
- [ ] CI: install → codegen:check → lint → typecheck → test (TS + Py) → `supabase start` + `supabase test db`.
- [ ] migrate-dev on push to `main`; migrate-prod on release with the `production` environment (requires approval).
- [ ] `docs/deploy.md` lists every variable per service and the click-path for GitHub, Supabase and Railway.

### Task 7: Verification
- [ ] A clean `pnpm install && uv sync && pnpm codegen:check && pnpm lint && pnpm typecheck && pnpm test && pnpm db:check && pnpm build` passes, and all Python checks pass.
