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

### Recommended: one TypeScript monorepo on Railway + Supabase

| Concern | Choice | Why |
|---|---|---|
| Language | **TypeScript everywhere** (Node 24) | One type system from the DB schema to the UI. Shared Zod schemas validate AI output against the predicate registry (S3). |
| Monorepo | **pnpm workspaces + Turborepo** | Shared packages and cached builds. Railway builds each service from its own path. |
| Web app | **Next.js (App Router, latest stable)**, `output: "standalone"` | Server components read through RLS. Server actions are the "audited server functions" (S8). Runs as a normal Node server on Railway. |
| Worker | **Node service running pg-boss** | Job orchestrator (S1), scheduler, outbox relay (S4), derivation engine (S5). |
| Public API + MCP | **Hono service** (added in Phase 3) | `/v1` REST with OpenAPI (M13) and the MCP server share one codebase. YAGNI until Phase 3. |
| Database | **Supabase Postgres** + `pgvector`, `pg_trgm`, `unaccent`, `pgcrypto` | F8: one DB holds structured data, vectors (M15) and fuzzy name matching (M4). |
| Migrations | **Plain SQL via Supabase CLI** (`supabase/migrations`) | RLS policies, triggers and functions are SQL anyway. SQL is the easiest form to review ("foundation changes are migrations, reviewed"). |
| Typed queries | **Kysely** + `kysely-codegen` (types generated from the DB) | Type-safe SQL with no ORM magic. The schema is generated from migrations, never hand-maintained. |
| Auth | **Supabase Auth**: Google + Microsoft (Azure) OAuth, allowed domain per org | Exactly the M14 V1 scope. SAML comes in V2 via Supabase SSO. |
| Object storage | **Supabase Storage** (private buckets) | Raw payloads, uploads, transcripts, research files (F3/F8). |
| Secrets | **Railway variables** (encrypted, per environment) + **Supabase Vault** for integration credentials and field-level encryption of contact points | S8. Nothing secret ever goes in the repo. |
| AI | **AI SDK** (provider-agnostic) behind our own `ai-gateway` package | Modules call `extract / judge / summarise / embed / write`, never a vendor (S3). Bring-your-own-key fits naturally. |
| UI | **Tailwind CSS v4 + shadcn/ui (Radix)** + our own `@signal/ui` package | The design system (§7). |
| Logs and errors | **pino** structured JSON (Railway log explorer) + **Sentry** | S7. A redaction list keeps personal data out of logs. |
| Tests | **Vitest** (unit), **pgTAP** via `supabase test db` (RLS + SQL), **Playwright** (e2e, from Phase 1) | RLS is the security boundary, so it gets direct tests. |
| CI/CD | **GitHub Actions** (lint, typecheck, test, migration check) → **Railway GitHub deploys** | Railway deploys services. Actions apply migrations to Supabase. |

### Alternatives considered

- **TS web + Python workers (FastAPI/Celery).** Python has stronger data and NLP tooling. The cost: two type systems, duplicated predicate validation, Redis needed for Celery, and the transactional outbox becomes harder. Not worth it at this team size. Python stays an option later as a separate extraction service behind the AI gateway.
- **Managed orchestrator (Trigger.dev / Inngest) instead of pg-boss.** Great developer experience, but it's another vendor holding tenant job payloads. It also can't enqueue inside the same Postgres transaction as the change, and the PRD needs exactly that for the outbox (S4: "no change is ever visible without its event"). **pg-boss lives in the same Supabase database**, so a write, its outbox event and its job can share one transaction.
- **BullMQ + Railway Redis.** Fast, but it's a second datastore, and an enqueue isn't transactional with Postgres.

---

## 3. System architecture

```
                         ┌──────────────── Railway project ────────────────┐
 Browser ──HTTPS──▶  [web]  Next.js  ── server actions (audited writes) ──┐ │
                       │   RSC reads with user JWT (RLS enforced)         │ │
                       │                                                  ▼ │
 Sequencers/CRMs ──▶ [api]  Hono /v1 + webhooks + MCP  (Phase 3) ──▶  Supabase
                                                                     Postgres
                     [worker] pg-boss: jobs · cron · outbox relay ◀──▶ (RLS, pgvector,
                       │      derivation engine · connectors          pg-boss schema,
                       ▼                                              outbox, audit)
                  [egress]  fetch guard (allowlist, no private IPs)    Storage · Auth
                       │                                              Vault
                       ▼
                  Public web / sources        AI providers (via ai-gateway)
                         └──────────────────────────────────────────────────┘
```

### Core rules and how the code enforces them

1. **Tenancy (F1).** Every tenant table has `org_id`, `workspace_id` and `scope`, plus an RLS policy built from one SQL helper (`auth_has_workspace_role(workspace_id, min_role)`). The JWT carries only the user id. Membership is looked up from `member`/`role_assignment` in a `STABLE SECURITY DEFINER` function, so a role change takes effect immediately with no token refresh. The `person` entity type and `contact_point` are forced to `scope='tenant'` by a CHECK constraint, so the rule needs no setting.
2. **Writes go through one door (S8, M18).** The browser never writes to tables directly. Only `SELECT` RLS policies are granted to `authenticated`. Every write is a server action → a `domain` package function → one Postgres transaction that writes the change, its `audit_log` row and its `outbox` event together. A lint rule and a test assert that no `insert/update/delete` grant exists for `authenticated`.
3. **Derived tables are read-only (F7).** `verdict`, `score`, `label`, `account_card` and `account.stage` are written only by the worker's derivation role. A DB trigger rejects writes from any other role. The UI shows them with an "Explain" affordance and no edit control. Disagreement becomes `feedback`.
4. **Versioning (F5).** `brief_version`, `extractor_version` and `rubric_version` are insert-only (UPDATE/DELETE revoked). Every derived row carries the version FKs that produced it, enforced by `NOT NULL`.
5. **The outbox is the event bus (S4).** Inside the transaction: `INSERT INTO outbox(event, payload)`. The relay (worker) uses `LISTEN/NOTIFY` with a polling fallback and fans out to consumers (derivation, webhooks, notifications, indexer, health) as pg-boss jobs keyed by `event_id`, so delivery is idempotent. The event catalogue (Appendix A.4) is a Postgres enum: adding an event takes a migration, which makes it the "reviewed change" the PRD asks for.
6. **Jobs (S1).** A `defineJob({ type, input: zod, idempotencyKey, timeout, retry, concurrency })` wrapper over pg-boss adds what pg-boss lacks: a kill switch per type (`job_type_config.enabled`, checked before every run), per-workspace concurrency (Postgres advisory locks), cost metering hooks (S6) and the **automation gate** (`gate({ confidence, reversible })` → `applied | review_queue | digest`, thresholds read from workspace settings).
7. **Loud failure (M17).** Every job type and connector declares an expected volume window. A cron job writes `health_check` rows and opens `health_incident`s for silence, failures, lag and stuck jobs. The daily canary pushes a synthetic `source_record` through extract → resolve → verdict → score in every environment.

### Monorepo layout

```
signal-intel/
├─ apps/
│  ├─ web/                 Next.js — all 11 screens (PRD §6.1)
│  ├─ worker/              pg-boss runner, cron, outbox relay, derivation
│  └─ api/                 (Phase 3) Hono /v1 + webhooks + MCP
├─ packages/
│  ├─ db/                  Kysely client, generated types, RLS-aware session helpers
│  ├─ domain/              Module logic M1–M18 (pure TS, one folder per module ID)
│  ├─ jobs/                defineJob, automation gate, kill switches, metering
│  ├─ events/              outbox writer, event catalogue types, relay
│  ├─ ai-gateway/          capabilities, prompt registry, typed outputs, cost ledger
│  ├─ connectors/          S2 framework + source/destination connectors
│  ├─ ui/                  design system: tokens, primitives, domain components
│  ├─ config/              eslint, tsconfig, tailwind preset
│  └─ shared/              canonical enums (Appendix A), zod schemas, utils
├─ supabase/
│  ├─ migrations/          SQL — the only source of truth for the schema
│  ├─ tests/               pgTAP: RLS, constraints, triggers
│  ├─ seed.sql             two demo orgs × two unrelated Briefs (PRD Part 9 rule)
│  └─ config.toml
├─ docs/                   PRD, specs, plans, ADRs
├─ .github/workflows/      ci.yml, migrate-staging.yml, migrate-prod.yml
└─ railway.json per app    build/start/watch paths per service
```

`packages/domain` is organised by PRD module ID (`m01-brief/`, `m04-identity/`, …). Each module exports only what it owns and talks to others through events, which mirrors the PRD's rule "a module writes only to the tables it owns".

---

## 4. Environments

| Env | Supabase | Railway | Deploys from | Migrations |
|---|---|---|---|---|
| **local** | Supabase CLI in Docker (`supabase start`) | `pnpm dev` (web + worker) | — | `supabase db reset` |
| **staging** | Project `signal-intel-staging` | Environment `staging` | `main` branch | GitHub Action on push to `main` |
| **production** | Project `signal-intel-prod` | Environment `production` | Git tag `v*` / manual promote | GitHub Action on release, requires approval |
| **PR previews** | Shared staging DB (no Supabase branching at first, to save cost) | Railway PR environments | Pull requests | not applied; PRs that change migrations run in CI only |

Every environment has the canary and health monitor switched on (M17).

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
| S1–S9 | `packages/jobs`, `connectors`, `ai-gateway`, `events`, worker `derivation/`, `usage_ledger` + `budget`, pino/Sentry, Vault, FTS + `memory_chunk` |
| M1–M18 | `packages/domain/mXX-*` + screens in `apps/web/app/(app)/*` |

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
1. `git init` and the monorepo scaffold (§3 layout), pnpm via corepack, Turborepo, TypeScript, ESLint and Prettier configs, `.editorconfig`, `.gitignore`, `.env.example` (no secrets).
2. `apps/web` (Next.js + Tailwind v4 + shadcn/ui, `@signal/ui` wired in, app shell, `/design` page, Supabase SSR auth helpers).
3. `apps/worker` (pg-boss bootstrap, `defineJob`, health heartbeat, graceful shutdown).
4. `supabase/` init with config, the first migrations (extensions, tenancy tables, RLS helpers, audit log, outbox, enums from Appendix A), pgTAP smoke tests and the two-industry seed.
5. `railway.json` per service (Nixpacks/Railpack build, start command, health check path, watch paths), plus GitHub Actions `ci.yml` and migration workflows.
6. `README.md` covering local setup, environment variables and deployment.

**You do (accounts and credentials, which only you can create):**
1. Install **Docker Desktop** (for local Supabase), plus the **GitHub CLI** (`winget install GitHub.cli`), the **Railway CLI** (`npm i -g @railway/cli`) and the **Supabase CLI** (added as a dev dependency, so no global install is needed).
2. Create an empty GitHub repo (e.g. `signal-intel`, private). I'll add the remote and push once you give me the name.
3. Create two Supabase projects (staging, prod) and enable the Google and Azure providers in Auth. Then paste the URL, anon key, service-role key and DB connection strings into Railway variables. I'll give the exact list; never paste secrets into chat.
4. Create a Railway project, connect the GitHub repo, and add the `web` and `worker` services (I'll provide the root directories and commands).
5. Add GitHub Actions secrets: `SUPABASE_ACCESS_TOKEN`, `SUPABASE_DB_PASSWORD_STAGING/PROD`, `SUPABASE_PROJECT_REF_STAGING/PROD`.

---

## 9. Decisions needed now (they shape the scaffold)

1. **Stack:** all-TypeScript monorepo (recommended), or TS web + Python workers?
2. **Local DB:** install Docker and run Supabase locally (recommended), or develop against a hosted dev project?
3. **Job orchestrator:** pg-boss inside Supabase Postgres (recommended), Trigger.dev, or BullMQ + Redis?
4. **Default AI provider** (PRD open decision #3): Anthropic Claude for extract/judge/write plus an embeddings provider (Voyage or OpenAI) (recommended), or OpenAI only?

Decisions that can wait: the test industries (I'll seed the two examples above), product name, first sequencers and CRMs (Phase 3), pricing unit, data residency (this one affects the Supabase region choice, so tell me if EU or India residency is needed before creating the prod project).

---

## 10. Risks specific to this stack

| Risk | Mitigation |
|---|---|
| Supabase connection limits with pg-boss + Next.js | Web uses the transaction pooler (port 6543). Worker uses the session pooler / direct connection (needed for LISTEN/NOTIFY and pg-boss), with a small pool. |
| RLS performance on a generic schema at scale | `(select auth.uid())` initplan pattern, membership functions marked `STABLE`, composite indexes led by `workspace_id`, materialised projections per heavy view (F8). |
| Railway region vs Supabase region latency | Put both in the same region. Pick it once residency is known. |
| AI cost spikes | Every AI call goes through the gateway → usage ledger → budget check before the call, not after. |
| Schema drift between environments | Migrations are applied only by CI. The CI job diffs `supabase db diff` against the migrations and fails if they differ. |
