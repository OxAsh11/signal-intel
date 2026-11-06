# Signal Intel — Build Approach & Architecture

Status: **Draft for review** · 2026-11-06 · Source: `docs/PRD.md` (v1.0, 5 Nov 2026)

This document turns the PRD into a build plan: the stack, how the code is laid out, how each PRD layer maps onto that stack, the order we build in, and what "environment setup" includes. Each phase gets its own detailed spec and implementation plan when we reach it. This document covers the whole platform at the architecture level, plus Phase 0 (the foundation) in more detail.

---

## 1. What I understood

**From you / the PRD (given):**
- An industry-agnostic engine. The user describes a hypothesis. The platform collects public signals, resolves them into one record per company, proves or disproves the hypothesis with quoted evidence, then researches, enriches, scores and activates the accounts.
- Data moves one way: **observed → resolved → derived**. Raw data is immutable. Derived values are never edited.
- Multi-tenant (Organisation → Workspace → Brief) with **Postgres RLS on every tenant table**.
- Hosting: **Railway** (compute), **Supabase** (Postgres, auth, storage), **GitHub** (your account, your repo).
- V1 = M1–M18 in four phases with gates. V2 = learning (M19).
- A design system is needed.

**My assumptions (please correct):**
- A1. A small team (you plus AI-assisted development), so one language and one repo beat a multi-service polyglot setup.
- A2. The working product name is **Signal Intel** (the folder name) until PRD open decision #8 closes.
- A3. Part 9's build-sequence chart did not come through in the markdown export (it shows as `[embedded content]`). I rebuilt the phases in §6 from the PRD's cross-references (Gate B = verdict accuracy, M14 in full in Phase 0, Phase 3 = GA). **Please check §6 against the original chart.**
- A4. Phase 0 must work end to end with **no external sources connected**, using a synthetic source record (the M17 canary). Real connectors start in Phase 1.

---

## 2. Stack decision

### Decided (2026-11-06): TypeScript web + Python workers, on Railway + Supabase

Decisions taken: **TS web + Python workers**, **hosted Supabase dev project (no local Docker)**, a **Postgres-native job queue inside Supabase**, and **Claude + Voyage** as default AI providers.

> **Job-queue note.** pg-boss was the pick, but it's a Node-only library, and the workers are now Python. The Python equivalent is **Procrastinate**: a Postgres-native task queue with retries and backoff, cron, queueing locks (idempotency), LISTEN/NOTIFY and per-queue concurrency, living in the same Supabase database. It keeps the reason for the choice: no extra infrastructure, and a change, its outbox event and its follow-on job are committed transactionally.

| Concern | Choice | Why |
|---|---|---|
| Languages | **TypeScript** (Node 24) for the web app; **Python 3.13** for workers and the public API | Python gets the data, NLP and scraping ecosystem for the pipelines (M2–M8). TS gets the best UI stack. |
| Repo | **One monorepo**: pnpm workspaces + Turborepo (TS side), **uv workspace** (Python side) | One PR can change schema, worker and UI together. Railway builds each service from its own root. |
| Web app | **Next.js (App Router, latest stable)**, `output: "standalone"` | Server components read through RLS. Server actions are the "audited server functions" (S8) for user-initiated writes (M1 edits, feedback, review decisions, comments). |
| Worker | **Python service: Procrastinate + psycopg 3** | Job orchestrator (S1), cron schedules, outbox relay (S4), derivation engine (S5), connectors (S2), AI gateway (S3), indexing (S9), health (M17), and the M2–M8 pipelines. |
| Public API + MCP | **FastAPI service** (Phase 3) sharing the Python core package | `/v1` REST with OpenAPI generated for free (M13), plus webhooks ingress and the official MCP Python SDK. |
| Database | **Supabase Postgres** + `pgvector`, `pg_trgm`, `unaccent`, `pgcrypto` | F8: one DB holds structured data, vectors (M15) and fuzzy name matching (M4). |
| Migrations | **Plain SQL via Supabase CLI** (`supabase/migrations`), applied only by CI | Language-neutral, so both TS and Python read the same schema. SQL is the easiest form to review. |
| Cross-language types | **The DB schema + `packages/contracts` (JSON Schema)** are the single source of truth. Codegen: `supabase gen types` → TS; JSON Schema → **zod** (TS) and **Pydantic v2** (Python) | The price of two languages. Event payloads, predicate value types and Appendix A enums are defined once and generated for both sides. CI fails if generated code is stale. |
| TS data access | `@supabase/ssr` + `supabase-js` with the user's JWT (RLS enforced) for reads; server actions call Postgres RPC functions for writes | Writes stay inside audited SQL functions. |
| Python data access | **psycopg 3** + SQL (SQLAlchemy Core where composition helps), Pydantic models | Explicit SQL, no ORM magic in pipelines that must be reproducible. |
| Auth | **Supabase Auth**: Google + Microsoft (Azure) OAuth, allowed domain per org | Exactly the M14 V1 scope. SAML comes in V2 via Supabase SSO. |
| Object storage | **Supabase Storage** (private buckets) | Raw payloads, uploads, transcripts, research files (F3/F8). |
| Secrets | **Railway variables** (encrypted, per environment) + **Supabase Vault** for integration credentials and field-level encryption of contact points | S8. Nothing secret ever goes in the repo. |
| AI | Own **`ai_gateway`** Python module: capabilities `extract / judge / summarise / embed / write` over provider adapters. Defaults: **Claude Sonnet 5.5** (extract, judge, write), **Claude Haiku 5.5** (cheap relevance pre-judge), **Voyage** (embeddings) | S3: modules never call a vendor. Outputs are validated by Pydantic against the predicate registry. Every call is written to the usage ledger. Workspaces can bring their own keys. |
| UI | **Tailwind CSS v4 + shadcn/ui (Radix)** + our own `@signal/ui` package | The design system (§7). |
| Logs and errors | **structlog** (Python) / **pino** (TS) as JSON to Railway logs, plus **Sentry** for both | S7. A shared redaction list keeps personal data out of logs. |
| Tests | **pytest** (worker), **Vitest** (web), **pgTAP** via `supabase test db` in CI (RLS + SQL), **Playwright** (e2e, from Phase 1) | No local Docker, so the DB test suite runs in GitHub Actions, where Docker is available. |
| CI/CD | **GitHub Actions** (lint, typecheck, tests, codegen-fresh check, migration check) → **Railway GitHub deploys** | Railway deploys services. Actions apply migrations to Supabase. |

### Why the transactional outbox still works across two languages

The web app never enqueues jobs directly. A server action writes `change + audit_log + outbox` in one Postgres transaction. The Python relay (LISTEN/NOTIFY + polling fallback) reads the outbox and defers Procrastinate jobs keyed by `event_id`, so a replay never duplicates. Jobs that the Python side starts itself are deferred on the same psycopg connection, inside the transaction that made the change. Either way, no change is ever visible without its event (S4).

### Alternatives considered

- **All-TypeScript monorepo.** One type system and simpler. Not chosen: you prefer Python for the pipelines.
- **Celery + Redis.** The usual Python default, but it adds a second datastore, and an enqueue isn't transactional with Postgres.
- **Trigger.dev / Inngest.** Another vendor holding tenant job payloads, and its enqueue isn't transactional with the DB.
- **Supabase Queues (pgmq).** Postgres-native and language-neutral, but it has no cron, retry policy or locks. We would rebuild what Procrastinate already provides.

---

## 3. System architecture

```
                         ┌──────────────── Railway project ────────────────┐
 Browser ──HTTPS──▶  [web]  Next.js (TS) ── server actions (audited writes)┐ │
                       │   RSC reads with user JWT (RLS enforced)         │ │
                       │                                                  ▼ │
 Sequencers/CRMs ──▶ [api]  FastAPI /v1 + webhooks + MCP (Py, Phase 3)▶ Supabase
                                                                     Postgres
                     [worker] Python · Procrastinate: jobs · cron ◀──▶ (RLS, pgvector,
                       │   outbox relay · derivation · connectors     procrastinate
                       │   ai_gateway · pipelines M2–M8               schema, outbox,
                       ▼                                              audit)
                  [egress]  fetch guard (allowlist, no private IPs)    Storage · Auth
                       │                                              Vault
                       ▼
                  Public web / sources        AI providers (via ai-gateway)
                         └──────────────────────────────────────────────────┘
```

### Core rules and how the code enforces them

1. **Tenancy (F1).** Every tenant table has `org_id`, `workspace_id` and `scope`, plus an RLS policy built from one SQL helper (`auth_has_workspace_role(workspace_id, min_role)`). The JWT carries only the user id. Membership is looked up from `member`/`role_assignment` in a `STABLE SECURITY DEFINER` function, so a role change takes effect immediately with no token refresh. The `person` entity type and `contact_point` are forced to `scope='tenant'` by a CHECK constraint, so the rule needs no setting.
2. **Writes go through one door (S8, M18).** The browser never writes to tables directly. Only `SELECT` RLS policies are granted to `authenticated`. Every user write is a server action → a Postgres RPC function (`app.*`, `SECURITY DEFINER`, checks the caller's role) → one transaction that writes the change, its `audit_log` row and its `outbox` event together. System writes from Python go through `signal_core.db.unit_of_work()`, which does the same three writes in one psycopg transaction. A lint rule and a test assert that no `insert/update/delete` grant exists for `authenticated`.
3. **Derived tables are read-only (F7).** `verdict`, `score`, `label`, `account_card` and `account.stage` are written only by the worker's derivation role. A DB trigger rejects writes from any other role. The UI shows them with an "Explain" affordance and no edit control. Disagreement becomes `feedback`.
4. **Versioning (F5).** `brief_version`, `extractor_version` and `rubric_version` are insert-only (UPDATE/DELETE revoked). Every derived row carries the version FKs that produced it, enforced by `NOT NULL`.
5. **The outbox is the event bus (S4).** Inside the transaction: `INSERT INTO outbox(event, payload)`. The relay (worker) uses `LISTEN/NOTIFY` with a polling fallback and fans out to consumers (derivation, webhooks, notifications, indexer, health) as Procrastinate jobs with `queueing_lock = event_id`, so delivery is idempotent. The event catalogue (Appendix A.4) is a Postgres enum: adding an event takes a migration, which makes it the "reviewed change" the PRD asks for.
6. **Jobs (S1).** A `@job(type=..., input=PydanticModel, idempotency_key=..., timeout=..., retry=..., concurrency=...)` decorator over Procrastinate adds what it lacks: a kill switch per type (`job_type_config.enabled`, checked before every run), per-workspace concurrency (Postgres advisory locks), cost metering hooks (S6) and the **automation gate** (`gate(confidence, reversible)` → `applied | review_queue | digest`, thresholds read from workspace settings).
7. **Loud failure (M17).** Every job type and connector declares an expected volume window. A cron job writes `health_check` rows and opens `health_incident`s for silence, failures, lag and stuck jobs. The daily canary pushes a synthetic `source_record` through extract → resolve → verdict → score in every environment.

### Monorepo layout

```
signal-intel/
├─ apps/
│  └─ web/                     Next.js (TS) — all 11 screens (PRD §6.1), server actions
├─ services/
│  ├─ worker/                  Python — Procrastinate app, cron, outbox relay entrypoint
│  └─ api/                     Python — (Phase 3) FastAPI /v1 + webhooks + MCP
├─ packages/
│  ├─ ui/                      TS — design system: tokens, primitives, domain components
│  ├─ db-types/                TS — generated Supabase types (never hand-edited)
│  ├─ contracts/               JSON Schema: events, predicate value types, enums (Appendix A)
│  │   ├─ gen/ts/              → zod (generated)
│  │   └─ gen/py/              → Pydantic (generated)
│  ├─ config/                  TS — eslint, tsconfig, tailwind preset
│  └─ signal_core/             Python — the shared core used by worker and api:
│      ├─ db/                  psycopg pool, RLS-scoped sessions, ID-set helpers
│      ├─ jobs/                @job decorator, automation gate, kill switches, metering
│      ├─ events/              outbox writer, relay, consumers registry
│      ├─ ai_gateway/          capabilities, provider adapters, prompt registry, cost ledger
│      ├─ connectors/          S2 framework + fetch guard (allowlist, no private IPs)
│      ├─ derivation/          S5 dependency graph, shadow mode
│      └─ modules/             m02_radar … m18_quality (one package per PRD module ID)
├─ supabase/
│  ├─ migrations/              SQL — the only source of truth for the schema
│  ├─ tests/                   pgTAP: RLS, constraints, triggers (run in CI)
│  ├─ seed.sql                 two demo orgs × two unrelated Briefs (PRD Part 9 rule)
│  └─ config.toml
├─ docs/                       PRD, specs, plans, ADRs
├─ .github/workflows/          ci.yml, migrate-dev.yml, migrate-prod.yml
├─ pyproject.toml              uv workspace root
├─ pnpm-workspace.yaml         pnpm workspace root
└─ railway.json per service    build/start/healthcheck/watch paths
```

`signal_core/modules` is organised by PRD module ID. Each module exports only what it owns and talks to others through events, which mirrors the PRD's rule "a module writes only to the tables it owns". Module logic that the UI needs synchronously (for example the Narrow-tier recompute preview) lives in SQL functions or is called through a worker job, so it is never re-implemented in TS.

---

## 4. Environments

| Env | Supabase | Railway | Deploys from | Migrations |
|---|---|---|---|---|
| **local** | Hosted project `signal-intel-dev` (no Docker) | `pnpm dev` (web) + `uv run worker` on your machine | — | Never applied from a laptop. Push a branch, and CI applies it to dev after merge. |
| **dev / staging** | Same `signal-intel-dev` project | Environment `staging` | `main` branch | GitHub Action on push to `main` |
| **production** | Project `signal-intel-prod` | Environment `production` | Git tag `v*` / manual promote | GitHub Action on release, requires approval |
| **CI** | Throwaway local Supabase inside GitHub Actions (Docker available there) | — | Every PR | `supabase db reset` + pgTAP on every PR |

Notes:
- Supabase's free plan allows two active projects, which covers dev + prod. A separate staging project can be added later without code changes.
- Because local dev shares the hosted dev DB, **each developer works in their own seeded demo organisation**. RLS keeps them apart, which also exercises tenancy every day.
- Every environment has the canary and health monitor switched on (M17).

---

## 5. How the PRD maps onto the system

| PRD element | Where it lives |
|---|---|
| F1 tenancy | `organisation`, `workspace`, `member`, `role_assignment`; RLS helper functions; `scope` CHECKs |
| F2 identity | `entity`, `entity_alias` (unique on `(alias_type, value_normalised, org scope)`), `entity_current` view, merge/unmerge functions |
| F3 provenance | `source_record` (+ Storage `payload_ref`, `content_hash` unique), `mention`, `predicate`, `claim` (typed by `value_type`, validated by trigger plus zod) |
| F4 relationships | `relationship` + `relation_type` registry |
| F5 versioning | `brief_version`, `extractor_version`, insert-only |
| F6 activity/feedback | `activity` (unique `(integration_id, external_id)`), `feedback`; `activity_kind` enum = A.3 |
| F7 derived | `verdict`, `score`, `label`, `account_stage_event`, `account_card`, `memory_chunk`; written only by the derivation role |
| F8 storage | Supabase Postgres + pgvector + Storage; ID-set RPC functions; materialised projections per heavy view |
| S1–S9 | `signal_core/jobs`, `connectors`, `ai_gateway`, `events`, `derivation`; `usage_ledger` + `budget` tables; structlog/pino/Sentry; Vault; FTS + `memory_chunk` |
| M1–M18 | Pipelines in `signal_core/modules/mXX_*` (Python); screens and user-initiated writes in `apps/web/app/(app)/*` (TS) |

---

## 6. Build sequence (reconstructed — please confirm against the original Part 9 chart)

Every phase is tested on **two unrelated Briefs** from day one. Example seeds: (a) engineering firms that design in a specific CAD tool and are hiring; (b) operators of cold-storage warehouses. Both go into `seed.sql`.

| Phase | Builds | Gate (release criteria) |
|---|---|---|
| **0 — Foundation** | Environment, CI/CD, F1–F8 schema + RLS, M14 in full (orgs, workspaces, members, roles, invitations, Google/Microsoft sign-in), S1 jobs + gate + kill switches, S4 outbox, S7 logging, S8 secrets, M18 audit log, M17 skeleton + canary, design system + app shell | **Gate A:** pgTAP suite proves cross-tenant isolation for every table; a Viewer cannot reach any write path; canary passes end-to-end with stub extract/resolve; every write produces an audit row and an outbox event. |
| **1 — Intelligence core** | S2 connectors (CSV upload, company websites, news, job posts first), S3 AI gateway + prompt registry, S5 derivation engine, S6 metering/budgets/cost preview, S9 search; M1 Brief Studio (intake → pilot → calibrate → publish), M2 radars, M3 extraction, M4 identity resolution + review queue, M5 verdicts | **Gate B:** on both test Briefs the pilot hits the agreed verdict accuracy target (open decision #6); every verdict clicks through to quotes and sources; re-fetch never duplicates; Narrow/Redefine/Expand previews match actual results. |
| **2 — Accounts** | M8 scoring + segments, M6 research, M7 enrichment (waterfall, verification gate, suppression), M11 account workspace + stage machine, M15 memory + account cards, M17 full health | **Gate C:** every score is explainable down to claims; enrichment spend never exceeds caps; account page complete; derived fields not editable anywhere. |
| **3 — Activation & GA** | M9 campaigns (dry run → live, bounce guard), M10 inbox + reply classes, M12 meetings/tasks/next steps, M13 integrations hub + public API + webhooks + MCP, M16 reporting + canonical metrics, M18 compliance (DSR, lawful basis, retention) | **Gate D (GA):** Part 8.1 capture checklist all ticked; dry run equals live push; every webhook traceable and replayable; same metric gives the same value on every surface; pen test. |
| **V2** | M19 learning loops, AI-answer and review-site radars, SAML/SCIM | Per PRD Part 8 |

---

## 7. Design system approach

**Character.** An evidence instrument, not a marketing dashboard: calm, dense, precise. It should feel closer to a research terminal than a CRM. Three ideas from the PRD drive it:

1. **Evidence is first-class.** Quotes, sources and dates have their own visual language (an `EvidenceQuote` block with source favicon, type, age and a link to the stored record).
2. **Derived values look derived.** Verdicts, scores, bands, stages and labels use a distinct "computed" treatment: tabular numerals, a small ⓘ *Explain* trigger, never an input border. Editable fields look clearly editable.
3. **Cost before action.** Any action that spends money renders through a `CostPreview` (volume, cost, duration, approval needed) before it can run.

**Deliverables (in `packages/ui`):**
- **Tokens** (CSS variables + Tailwind v4 `@theme`): color (neutral scale, one accent, semantic success/warning/danger/info), plus **domain palettes**: Verdict (Confirmed, Likely, Unverified, Contradicted), Band (High, Medium, Low, Excluded), Stage (9 stages), Reply class (6). Also type scale (Inter for UI, JetBrains Mono for IDs, values and quotes metadata), spacing (4px grid), radius, elevation, motion. Light and dark themes from day one.
- **Primitives:** shadcn/ui components themed with the tokens (Button, Input, Select, Dialog, Sheet, Tabs, Table, Tooltip, Popover, Command, Toast, Badge, Card, Skeleton).
- **Domain components:** `VerdictBadge`, `BandChip`, `StageBadge`, `ReplyClassTag`, `ScoreBreakdown` (5 dimensions + gates), `EvidenceQuote`, `ExplainPopover`, `DerivedValue`, `ClaimRow`, `EntityHeader`, `CostPreview`, `ReviewQueueItem` (side-by-side candidates), `HealthIndicator`, `PushModeSwitch` (off, dry run, live), `MetricTile` (always links to its rows), `EmptyState` (with a stated reason).
- **App shell:** sidebar with the 11 screens, workspace switcher, Brief context switcher, command palette (⌘K), review-queue counter.
- **A living reference:** a `/design` route in the web app that renders every token and component, plus a published Artifact page you can share.

---

## 8. Environment setup: what gets done

**I do (in this folder):**
1. Monorepo scaffold (§3 layout): pnpm via corepack, Turborepo, TypeScript, ESLint and Prettier; uv workspace with ruff, mypy and pytest; `.editorconfig`, `.gitignore`, `.env.example` (no secrets).
2. `apps/web`: Next.js + Tailwind v4 + shadcn/ui, `@signal/ui` wired in, app shell, `/design` page, Supabase SSR auth helpers.
3. `services/worker`: Procrastinate app, `@job` decorator with kill switch + automation gate stubs, outbox relay, health heartbeat, `/healthz` endpoint for Railway, graceful shutdown.
4. `packages/contracts` + codegen scripts (JSON Schema → zod and Pydantic) seeded with the Appendix A enums and the event catalogue.
5. `supabase/` init with config, the first migrations (extensions, tenancy tables, RLS helpers, audit log, outbox, enums from Appendix A), pgTAP smoke tests and the two-industry seed.
6. `railway.json` per service (start command, health check path, watch paths), GitHub Actions `ci.yml` (TS + Python + pgTAP in Docker) and the migration workflows.
7. `README.md` covering setup, environment variables and deployment.

**You do (accounts and credentials, which only you can create):**
1. Install **uv** (`winget install astral-sh.uv`), the **GitHub CLI** (`winget install GitHub.cli`) and the **Railway CLI** (`npm i -g @railway/cli`). The **Supabase CLI** comes in as a project dev dependency.
2. Create an empty GitHub repo (e.g. `signal-intel`, private). I'll add the remote and push once you give me the name.
3. Create two Supabase projects (`signal-intel-dev`, `signal-intel-prod`) in the same region as Railway, and enable the Google and Azure providers in Auth. Put keys and connection strings into Railway variables and your local `.env.local`. I'll give the exact list; never paste secrets into chat.
4. Create a Railway project, connect the GitHub repo, and add the `web` and `worker` services (I'll provide the root directories and commands).
5. Add GitHub Actions secrets: `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD_DEV/PROD`, `SUPABASE_PROJECT_REF_DEV/PROD`.
6. Create API keys for Anthropic and Voyage AI, stored only in Railway variables and `.env.local`.

---

## 9. Decisions

**Taken 2026-11-06:** TS web + Python workers · hosted Supabase dev project, no local Docker · Postgres-native queue in Supabase (Procrastinate, since pg-boss is Node-only) · Claude + Voyage as default AI providers.

**Still open, but they can wait**: the test industries (I'll seed the two examples above), product name, first sequencers and CRMs (Phase 3), pricing unit, data residency (this one affects the Supabase region choice, so tell me if EU or India residency is needed before creating the prod project).

---

## 10. Risks specific to this stack

| Risk | Mitigation |
|---|---|
| Supabase connection limits with Procrastinate + Next.js | Web uses the transaction pooler (port 6543). Worker uses the session pooler / direct connection (needed for LISTEN/NOTIFY), with a small pool. |
| Type drift between TS and Python | One source of truth (DB + `packages/contracts`). Codegen runs in CI, which fails if the generated files are stale. Contract tests on event payloads. |
| Shared hosted dev DB gets polluted or broken | Migrations only via CI. One demo org per developer. `seed.sql` is re-runnable. pgTAP runs on a throwaway DB in CI, never on dev. |
| RLS performance on a generic schema at scale | `(select auth.uid())` initplan pattern, membership functions marked `STABLE`, composite indexes led by `workspace_id`, materialised projections per heavy view (F8). |
| Railway region vs Supabase region latency | Put both in the same region. Pick it once residency is known. |
| AI cost spikes | Every AI call goes through the gateway → usage ledger → budget check before the call, not after. |
| Schema drift between environments | Migrations are applied only by CI. The CI job diffs `supabase db diff` against the migrations and fails if they differ. |
