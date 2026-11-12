# Phase 3 — Activation & GA

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the activation layer: M9 campaigns + push, M10 inbox + reply handling, M12 meetings/tasks/next steps, M13 integrations hub + public REST API /v1 + webhooks + MCP server, M16 canonical metrics, and M18 compliance additions (DSR, lawful basis, evaluation sets, suppression list) — completing V1 so every discovered account can be activated, every outcome recorded, and every surface reports the same metric with a traceable path.

**Architecture:** New tables in migration 0023 follow the same guard-trigger + SECURITY DEFINER pattern as Phases 1–2. Python modules m09–m18 write only through `app.*` RPCs. A new `services/api` FastAPI service shares `signal_core` and exposes /v1 REST + inbound webhook ingestion + an MCP server. The worker gains 7 new job types. Web pages (inbox, campaigns, meetings/tasks, reports) are read-only RSC.

**Tech Stack:** PostgreSQL RPC + RLS (guard pattern), psycopg3 async, Procrastinate, Pydantic v2, FastAPI + Uvicorn, `mcp[cli]` (official MCP Python SDK), Next.js 16 App Router.

**Spec:** `docs/PRD.md` §M9, §M10, §M12, §M13, §M16, §M18, §Part 8.1; `docs/superpowers/specs/2026-11-06-platform-approach-design.md` §6 Phase 3

## Global Constraints

- S8 write discipline: all INSERT/UPDATE/DELETE through `app.*` SECURITY DEFINER RPCs; `authenticated` gets SELECT only on public tables; no direct-INSERT from Python or TypeScript
- Every migration: REVOKE EXECUTE from public, anon, authenticated BEFORE every GRANT block
- Campaign write-path RPCs (`create_campaign`, `add_campaign_member`, `pause_campaign`, `set_campaign_push_mode`, `check_and_pause_if_bouncing`) have NO GRANT to authenticated — called via service_role only
- Meeting/task/next_step RPCs have NO GRANT to authenticated
- Webhook delivery signing: HMAC-SHA256 of JSON payload bytes with the per-subscription secret; signature in `X-Signal-Signature: sha256=<hex>`
- API auth: `Authorization: Bearer <token>` validated against `api_token.token_hash = sha256(token)`; check `revoked_at IS NULL` and `expires_at > now()`; scope checked per route
- Outbound webhook delivery: exponential backoff; max 10 attempts over 24 h; disable subscription (`enabled = false`) after 10 consecutive failures
- Push mode gate: a push job must check `push_mode = 'live'` atomically in SQL before writing any `campaign_member`; dry_run records the member without calling any external system
- Bounce guard: `app.check_and_pause_if_bouncing` row-locks campaign, checks `bounce_count / NULLIF(bounce_messages_sent, 0)`: emit `campaign.paused` and set `status = 'paused'` when ≥ 10%; never two separate statements
- psycopg3 placeholder syntax: `%s` (not `$1`/`$2`)
- `push_mode` enum values: 'off', 'dry run', 'live' (space, not underscore — already in canonical_enums)
- All Phase 3 enums already exist: `motion`, `push_mode`, `reply_class`, `token_scope`, `next_step_mode`, `access_mode`, `field_owner` — do NOT re-create them
- `audit_log` and `app.audit(...)` already exist from Phase 0 — do NOT re-create; use `app.audit(p_org, p_ws, action, target_type, target_id)` for all writes
- API service sets `SET app.channel = 'api'` on each DB connection; MCP server sets `SET app.channel = 'mcp'`
- `metric_definition` rows are insert-only once published — revoke UPDATE/DELETE after the seed

## Review Focus

1. **Bounce guard race:** two concurrent push jobs could both read bounce_rate < 10% and together cross the threshold. `app.check_and_pause_if_bouncing` must use `SELECT … FOR UPDATE` on the campaign row; never two separate `SELECT` + `UPDATE` statements.
2. **Webhook replay safety:** replayed deliveries must resend the SAME `webhook_delivery.payload` already stored (never re-sign with a new timestamp); replaying a `status = 'delivered'` delivery must be idempotent — no second INSERT into activity.
3. **API token scope enforcement:** a Read-scoped token must not reach any POST/PATCH/DELETE route; this check lives in a FastAPI dependency before any DB call, not only in SQL.
4. **DSR deletion completeness:** `app.process_dsr_deletion` must delete `contact_point`, null personal fields in `entity` (`display_name → '[deleted]'`), and add to `suppression_list` in one transaction; partial execution leaves PII behind.
5. **Metric consistency:** `app.compute_metric(p_workspace_id, p_brief_id, p_metric_key, p_filters)` must be the single function used by the web page, the API endpoint, and the scheduled report — never re-implemented per surface; the SQL body is stored in `metric_definition.sql_body` and executed with `EXECUTE`.

---

## File Map

| File | Action |
|---|---|
| `supabase/migrations/20261110000023_phase3_activation.sql` | Create |
| `supabase/tests/database/15_campaigns.test.sql` | Create |
| `supabase/tests/database/16_meetings_tasks.test.sql` | Create |
| `supabase/tests/database/17_webhooks_api.test.sql` | Create |
| `supabase/tests/database/18_compliance_metrics.test.sql` | Create |
| `packages/signal_core/src/signal_core/modules/m09_activation.py` | Create |
| `packages/signal_core/src/signal_core/modules/m10_inbox.py` | Create |
| `packages/signal_core/src/signal_core/modules/m12_meetings.py` | Create |
| `packages/signal_core/src/signal_core/modules/m13_integrations.py` | Create |
| `packages/signal_core/src/signal_core/modules/m16_reporting.py` | Create |
| `packages/signal_core/src/signal_core/modules/m18_quality.py` | Create |
| `packages/signal_core/tests/test_m09_activation.py` | Create |
| `packages/signal_core/tests/test_m10_inbox.py` | Create |
| `packages/signal_core/tests/test_m12_meetings.py` | Create |
| `packages/signal_core/tests/test_m13_integrations.py` | Create |
| `packages/signal_core/tests/test_m16_reporting.py` | Create |
| `packages/signal_core/tests/test_m18_quality.py` | Create |
| `services/api/pyproject.toml` | Create |
| `services/api/railway.json` | Create |
| `services/api/Dockerfile` | Create |
| `services/api/src/api/__init__.py` | Create |
| `services/api/src/api/main.py` | Create |
| `services/api/src/api/auth.py` | Create |
| `services/api/src/api/routes/__init__.py` | Create |
| `services/api/src/api/routes/entities.py` | Create |
| `services/api/src/api/routes/accounts.py` | Create |
| `services/api/src/api/routes/claims.py` | Create |
| `services/api/src/api/routes/briefs.py` | Create |
| `services/api/src/api/routes/campaigns.py` | Create |
| `services/api/src/api/routes/activity.py` | Create |
| `services/api/src/api/routes/tasks.py` | Create |
| `services/api/src/api/routes/meetings.py` | Create |
| `services/api/src/api/webhooks_inbound.py` | Create |
| `services/api/src/api/mcp_server.py` | Create |
| `services/api/tests/__init__.py` | Create |
| `services/api/tests/test_api_auth.py` | Create |
| `services/api/tests/test_webhooks.py` | Create |
| `services/worker/src/worker/app.py` | Modify (add 7 new job types + 2 new crons) |
| `apps/web/src/app/(app)/workspace/[wsid]/inbox/page.tsx` | Create |
| `apps/web/src/app/(app)/workspace/[wsid]/campaigns/page.tsx` | Create |
| `apps/web/src/app/(app)/workspace/[wsid]/meetings/page.tsx` | Create |
| `apps/web/src/app/(app)/workspace/[wsid]/reports/page.tsx` | Create |

---

### Task 1: DB migration 0023 — Phase 3 tables + RPCs + kill-switch seeds

**Files:**
- Create: `supabase/migrations/20261110000023_phase3_activation.sql`

**Interfaces:**
- Produces: `public.integration(id uuid PK, org_id, workspace_id, name, category text, access_mode public.access_mode DEFAULT 'Read', enabled bool DEFAULT true, last_success_at, last_error text, credentials_vault_key text, config jsonb DEFAULT '{}', created_at)` — RLS: Viewer SELECT
- Produces: `public.field_ownership(id uuid PK, integration_id, object_type text, field_name text, owner public.field_owner, created_at)` — unique(integration_id, object_type, field_name); RLS: Admin SELECT
- Produces: `public.campaign(id uuid PK, org_id, workspace_id, brief_id uuid REFERENCES public.brief(id), segment_id uuid, name text NOT NULL, motion public.motion NOT NULL, destination_integration_id uuid REFERENCES public.integration(id), external_campaign_id text, push_mode public.push_mode DEFAULT 'off', status text DEFAULT 'draft' CHECK IN ('draft','active','paused','archived'), bounce_messages_sent int DEFAULT 0, bounce_count int DEFAULT 0, created_at)` — RLS: Viewer SELECT
- Produces: `public.campaign_member(id uuid PK, org_id, workspace_id, campaign_id uuid REFERENCES public.campaign(id), account_id uuid REFERENCES public.account(id), person_entity_id uuid, contact_point_id uuid, variables jsonb DEFAULT '{}', pushed_at timestamptz, push_mode_at_send public.push_mode, external_id text, created_at)` — unique(campaign_id, person_entity_id); RLS: Viewer SELECT
- Produces: `public.meeting(id uuid PK, org_id, workspace_id, account_id uuid REFERENCES public.account(id), person_entity_id uuid, external_id text, source_integration_id uuid, scheduled_at timestamptz NOT NULL, held_at timestamptz, proof_status text DEFAULT 'unconfirmed' CHECK IN ('unconfirmed','confirmed','has_transcript'), transcript_source_record_id uuid, attribution_campaign_id uuid, attribution_tier text, created_at)` — unique(source_integration_id, external_id) NULLS NOT DISTINCT; RLS: Viewer SELECT
- Produces: `public.task(id uuid PK, org_id, workspace_id, account_id uuid REFERENCES public.account(id), owner_member_id uuid, title text NOT NULL, due_date date, origin text NOT NULL CHECK IN ('manual','reply','meeting','next_step'), status text DEFAULT 'open' CHECK IN ('open','done'), created_at)` — RLS: Viewer SELECT
- Produces: `public.next_step(id uuid PK, org_id, workspace_id, account_id uuid REFERENCES public.account(id) UNIQUE, suggested_text text NOT NULL, mode public.next_step_mode DEFAULT 'Suggest', created_at, updated_at)` — unique(account_id); RLS: Viewer SELECT
- Produces: `public.suppression_list(id uuid PK, org_id, person_entity_id uuid, reason text, added_at timestamptz DEFAULT now())` — unique(org_id, person_entity_id); RLS: Admin SELECT
- Produces: `public.webhook_subscription(id uuid PK, org_id, workspace_id, event_types text[] NOT NULL, url text NOT NULL, secret_vault_key text NOT NULL, enabled bool DEFAULT true, fail_count int DEFAULT 0, created_at)` — RLS: Admin SELECT
- Produces: `public.webhook_delivery(id uuid PK, subscription_id uuid REFERENCES public.webhook_subscription(id), event_id uuid, payload jsonb NOT NULL, signature text NOT NULL, status text DEFAULT 'pending' CHECK IN ('pending','delivered','failed'), attempt_count int DEFAULT 0, last_attempted_at timestamptz, delivered_at timestamptz, created_at)` — unique(subscription_id, event_id) ON CONFLICT DO NOTHING; RLS: Admin SELECT
- Produces: `public.api_token(id uuid PK, org_id, workspace_id, member_id uuid, scope public.token_scope NOT NULL, name text NOT NULL, token_hash text NOT NULL UNIQUE, last_used_at timestamptz, expires_at timestamptz, rate_limit_rpm int DEFAULT 100, revoked_at timestamptz, created_at)` — RLS: authenticated user sees their own tokens (member_id check)
- Produces: `public.external_write_log(id uuid PK, org_id, workspace_id, integration_id uuid, action_type text, target_type text, target_id uuid, external_id text, idempotency_key text NOT NULL UNIQUE, dry_run bool DEFAULT false, status text DEFAULT 'pending' CHECK IN ('pending','ok','error'), error text, executed_at timestamptz, created_at)` — RLS: Admin SELECT
- Produces: `public.evaluation_set(id uuid PK, org_id, workspace_id, brief_version_id uuid REFERENCES public.brief_version(id), examples jsonb NOT NULL DEFAULT '[]', created_at, updated_at)` — unique(brief_version_id); RLS: Editor SELECT
- Produces: `public.data_subject_request(id uuid PK, org_id, type text NOT NULL CHECK IN ('access','deletion'), person_entity_id uuid, requested_by text NOT NULL, completed_at timestamptz, created_at)` — RLS: Admin SELECT
- Produces: `public.lawful_basis_record(id uuid PK, org_id, source_type text NOT NULL, basis text NOT NULL, recorded_at timestamptz DEFAULT now(), created_at)` — RLS: Admin SELECT
- Produces: `public.metric_definition(id uuid PK, org_id, key text NOT NULL, version int NOT NULL DEFAULT 1, sql_body text NOT NULL, published_at timestamptz, created_at)` — unique(org_id, key, version); INSERT-only after published_at set; RLS: Admin SELECT
- Produces: `app.create_campaign(p_org_id uuid, p_workspace_id uuid, p_brief_id uuid, p_name text, p_motion public.motion, p_destination_integration_id uuid DEFAULT NULL) RETURNS uuid` — SECURITY DEFINER; inserts campaign; calls app.audit
- Produces: `app.add_campaign_member(p_campaign_id uuid, p_account_id uuid, p_person_entity_id uuid, p_contact_point_id uuid DEFAULT NULL, p_variables jsonb DEFAULT '{}') RETURNS uuid` — SECURITY DEFINER; inserts campaign_member; emits `campaign.member_added`; idempotent on (campaign_id, person_entity_id)
- Produces: `app.check_and_pause_if_bouncing(p_campaign_id uuid) RETURNS bool` — SECURITY DEFINER; SELECT FOR UPDATE on campaign; computes bounce_count/bounce_messages_sent; sets status='paused' and emits `campaign.paused` if ≥ 10%; returns true if paused
- Produces: `app.set_campaign_push_mode(p_campaign_id uuid, p_push_mode public.push_mode) RETURNS void` — SECURITY DEFINER; updates push_mode; calls app.audit
- Produces: `app.create_meeting(p_org_id uuid, p_workspace_id uuid, p_account_id uuid, p_person_entity_id uuid, p_external_id text, p_source_integration_id uuid, p_scheduled_at timestamptz) RETURNS uuid` — SECURITY DEFINER; inserts meeting; idempotent on (source_integration_id, external_id)
- Produces: `app.confirm_meeting(p_meeting_id uuid, p_held_at timestamptz, p_proof_status text, p_attribution_campaign_id uuid DEFAULT NULL) RETURNS void` — SECURITY DEFINER; updates meeting; emits `meeting.held` when proof_status IN ('confirmed','has_transcript')
- Produces: `app.create_task(p_org_id uuid, p_workspace_id uuid, p_account_id uuid, p_owner_member_id uuid DEFAULT NULL, p_title text, p_due_date date DEFAULT NULL, p_origin text) RETURNS uuid` — SECURITY DEFINER; inserts task; emits `task.created`
- Produces: `app.upsert_next_step(p_account_id uuid, p_suggested_text text, p_mode public.next_step_mode) RETURNS uuid` — SECURITY DEFINER; ON CONFLICT (account_id) DO UPDATE
- Produces: `app.create_webhook_subscription(p_org_id uuid, p_workspace_id uuid, p_event_types text[], p_url text, p_secret_vault_key text) RETURNS uuid` — SECURITY DEFINER; inserts subscription; calls app.audit
- Produces: `app.record_webhook_delivery(p_subscription_id uuid, p_event_id uuid, p_payload jsonb, p_signature text, p_status text, p_attempt_count int, p_delivered_at timestamptz DEFAULT NULL) RETURNS uuid` — SECURITY DEFINER; upserts webhook_delivery by (subscription_id, event_id); updates fail_count on subscription
- Produces: `app.create_api_token(p_org_id uuid, p_workspace_id uuid, p_member_id uuid, p_scope public.token_scope, p_name text, p_token_hash text, p_expires_at timestamptz DEFAULT NULL) RETURNS uuid` — SECURITY DEFINER; inserts api_token; calls app.audit
- Produces: `app.record_external_write(p_org_id uuid, p_workspace_id uuid, p_integration_id uuid, p_action_type text, p_target_type text, p_target_id uuid, p_external_id text, p_idempotency_key text, p_dry_run bool, p_status text, p_error text DEFAULT NULL) RETURNS uuid` — SECURITY DEFINER; inserts/updates external_write_log; idempotent on idempotency_key
- Produces: `app.process_dsr_deletion(p_org_id uuid, p_person_entity_id uuid) RETURNS void` — SECURITY DEFINER; in one transaction: (1) delete from contact_point where org_id = p_org_id and entity_id = p_person_entity_id; (2) update entity set display_name = '[deleted]', status = 'retired' where id = p_person_entity_id and org_id = p_org_id; (3) insert into suppression_list (org_id, person_entity_id, reason) values (p_org_id, p_person_entity_id, 'dsr_deletion') ON CONFLICT DO NOTHING; (4) update data_subject_request set completed_at = now() where person_entity_id = p_person_entity_id and completed_at IS NULL
- Produces: `app.compute_metric(p_workspace_id uuid, p_brief_id uuid, p_metric_key text, p_filters jsonb DEFAULT '{}') RETURNS jsonb` — SECURITY DEFINER; looks up metric_definition.sql_body for the given key (latest published version); EXECUTEs the body with p_workspace_id, p_brief_id, p_filters as $1/$2/$3; returns the result as jsonb
- Produces: kill-switch seeds for 'campaign.push', 'reply.classify', 'inbox.reconcile', 'meeting.sync', 'next_step.suggest', 'webhook.deliver', 'metric.snapshot' in `job_type_config`
- Produces: 15 canonical metric_definition rows (see Task 9) seeded at the end of migration with published_at = now()

- [ ] **Step 1: Write the migration**

New tables in this order: `integration`, `field_ownership`, `campaign`, `campaign_member`, `meeting`, `task`, `next_step`, `suppression_list`, `webhook_subscription`, `webhook_delivery`, `api_token`, `external_write_log`, `evaluation_set`, `data_subject_request`, `lawful_basis_record`, `metric_definition`.

For every new table: `enable row level security`, write RLS policies, `grant select to authenticated` only. No INSERT/DELETE grants to authenticated.

Exception: `evaluation_set` gets `grant select on public.evaluation_set to authenticated` with an Editor SELECT policy.

For `metric_definition`: revoke UPDATE and DELETE after adding INSERT-only constraint.

RPCs follow the pattern from migration 0022 (`upsert_score`): `SECURITY DEFINER; set search_path = ''`; REVOKE EXECUTE FROM public, anon, authenticated BEFORE GRANT block; `app.check_and_pause_if_bouncing` gets NO GRANT to authenticated.

Each RPC that emits an event calls `app.emit(event_type, payload)` (the existing outbox writer).

- [ ] **Step 2: Run pgTAP smoke test to verify tables exist**

Run: `supabase test db --local` — confirm no migration errors; count of new tables = 16.

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/20261110000023_phase3_activation.sql
git commit -m "feat(db): Phase 3 migration — campaigns, meetings, tasks, webhooks, API tokens, compliance, metrics"
```

---

### Task 2: M9 Activation

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m09_activation.py`
- Create: `packages/signal_core/tests/test_m09_activation.py`

**Interfaces:**
- Consumes: `app.create_campaign(...)`, `app.add_campaign_member(...)`, `app.check_and_pause_if_bouncing(...)`, `app.set_campaign_push_mode(...)`, `app.record_external_write(...)` (from T1)
- Consumes: `public.contact_point` (verify status = 'valid' before push)
- Consumes: `public.suppression_list` (check before adding member)
- Produces: `create_campaign(pool, ctx, name, motion, brief_id, destination_integration_id=None) -> UUID`
- Produces: `push_campaign_batch(pool, gateway, campaign_id, ctx) -> dict` — returns `{"pushed": int, "skipped": int, "paused": bool}`

**Rules:**
- `push_campaign_batch` loads the campaign row; if `push_mode = 'off'` raises `KillSwitchError`; if `push_mode = 'dry run'` records external_write_log with `dry_run=True` but does NOT create campaign_member rows
- Never push a person in the suppression_list
- Never push a person with no `contact_point.status = 'valid'`
- After every 100 messages in push mode 'live', call `app.check_and_pause_if_bouncing`; stop pushing if returns True
- One active campaign per person by default: skip person already in another active campaign in this workspace

- [ ] **Step 1: Write the failing tests**

```python
def test_create_campaign_returns_uuid(mock_pool):
    result = create_campaign(mock_pool, ctx, "Q4 Target", "Vertical", brief_id)
    assert isinstance(result, UUID)

def test_push_dry_run_records_no_members(mock_pool):
    # Campaign with push_mode = 'dry run'
    result = push_campaign_batch(mock_pool, mock_gateway, campaign_id, ctx)
    assert result["pushed"] == 0
    # external_write_log has dry_run=True entries

def test_push_skips_suppressed_person(mock_pool):
    # person_entity_id in suppression_list
    result = push_campaign_batch(mock_pool, mock_gateway, campaign_id, ctx)
    assert result["skipped"] >= 1

def test_push_pauses_at_bounce_threshold(mock_pool):
    # bounce_count/bounce_messages_sent >= 0.10 for campaign
    result = push_campaign_batch(mock_pool, mock_gateway, campaign_id, ctx)
    assert result["paused"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m09_activation.py -v
```

- [ ] **Step 3: Implement `m09_activation.py`**

`create_campaign(pool, ctx, name, motion, brief_id, destination_integration_id=None) -> UUID`: calls `app.create_campaign` RPC with psycopg3.

`push_campaign_batch(pool, gateway, campaign_id, ctx) -> dict`: (1) load campaign with `SELECT … FOR UPDATE` on the campaign row to prevent double-push; (2) check push_mode; (3) load eligible account_ids from segment; (4) for each person: check suppression_list, check contact_point validity, check for existing active campaign membership; (5) in 'live' mode call `app.add_campaign_member` and `app.record_external_write`; in 'dry run' mode call `app.record_external_write` with `dry_run=True` only; (6) every 100 messages call `app.check_and_pause_if_bouncing`.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m09_activation.py -v
```

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m09_activation.py packages/signal_core/tests/test_m09_activation.py
git commit -m "feat(py): M9 activation — campaign creation and push with bounce guard"
```

---

### Task 3: M10 Inbox

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m10_inbox.py`
- Create: `packages/signal_core/tests/test_m10_inbox.py`

**Interfaces:**
- Consumes: `public.activity` (WHERE kind = 'reply_received'); `public.campaign_member` (resolve attribution)
- Consumes: `app.create_activity(...)` (existing RPC from Phase 0 for updating activity.payload with reply_class)
- Produces: `classify_reply(pool, activity_id, reply_class, member_id=None, source="ai") -> None`
  - Signature: `classify_reply(pool: AsyncConnectionPool, activity_id: UUID, reply_class: str, member_id: UUID | None, source: Literal["human","ai","sequencer"]) -> None`
  - Stores class in `activity.payload["reply_class"]`; if source == "human" stores `feedback` row via `app.create_feedback`
  - Emits `reply.classified` via outbox
- Produces: `get_inbox(pool, workspace_id, filters) -> list[dict]`
  - Returns activity rows WHERE kind = 'reply_received' ordered by occurred_at DESC; filters: unclassified_only, min_age_days

**Rules:**
- Human mark always wins: `payload["reply_class_source"] = "human"` once human sets it; subsequent AI classification is a no-op for that activity row
- `Unsubscribe` reply class: add person_entity_id to suppression_list via `INSERT INTO public.suppression_list ... ON CONFLICT DO NOTHING`; never push that person again
- Activity older than 2 working days without classification returns `is_overdue = True` in `get_inbox`

- [ ] **Step 1: Write the failing tests**

```python
def test_classify_reply_stores_class(mock_pool):
    classify_reply(mock_pool, activity_id, "Interested", None, "ai")
    # activity.payload["reply_class"] == "Interested"

def test_human_mark_wins_over_ai(mock_pool):
    classify_reply(mock_pool, activity_id, "Not interested", None, "ai")
    classify_reply(mock_pool, activity_id, "Interested", member_id, "human")
    # payload["reply_class"] == "Interested", source == "human"

def test_ai_cannot_override_human(mock_pool):
    classify_reply(mock_pool, activity_id, "Interested", member_id, "human")
    classify_reply(mock_pool, activity_id, "Not interested", None, "ai")
    # payload["reply_class"] == "Interested" (unchanged)

def test_unsubscribe_adds_to_suppression(mock_pool):
    classify_reply(mock_pool, activity_id, "Unsubscribe", None, "sequencer")
    # suppression_list has row for person_entity_id
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m10_inbox.py -v
```

- [ ] **Step 3: Implement `m10_inbox.py`**

`classify_reply`: load activity row; check `payload.get("reply_class_source") == "human"` — if so, skip for non-human callers; UPDATE activity payload (use psycopg3 `jsonb_set`); if source == "human" insert feedback row; if reply_class == "Unsubscribe" upsert suppression_list; emit `reply.classified`.

`get_inbox`: SELECT from activity WHERE workspace_id and kind = 'reply_received' with optional filters; compute `is_overdue` from occurred_at.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m10_inbox.py -v
```

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m10_inbox.py packages/signal_core/tests/test_m10_inbox.py
git commit -m "feat(py): M10 inbox — reply classification with human-wins rule and unsubscribe handling"
```

---

### Task 4: M12 Meetings + Tasks

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m12_meetings.py`
- Create: `packages/signal_core/tests/test_m12_meetings.py`

**Interfaces:**
- Consumes: `app.create_meeting(...)`, `app.confirm_meeting(...)`, `app.create_task(...)`, `app.upsert_next_step(...)` (from T1)
- Produces: `sync_meeting(pool, org_id, workspace_id, integration_id, external_id, scheduled_at, attendee_domains) -> UUID`
  - Matches attendee_domains against entity_alias.alias_type='domain' to find account_id; creates meeting row
- Produces: `confirm_meeting(pool, meeting_id, held_at, proof_status, attribution_campaign_id=None) -> None`
  - `proof_status` in ('confirmed', 'has_transcript') required; calls `app.confirm_meeting`
- Produces: `create_task(pool, org_id, workspace_id, account_id, title, due_date, origin, owner_member_id=None) -> UUID`
- Produces: `suggest_next_step(pool, gateway, account_id, workspace_id) -> str`
  - Calls AI gateway WRITE capability with account context summary; writes suggested text to `next_step` via `app.upsert_next_step`

**Rules:**
- Meeting held counts only when `proof_status IN ('confirmed','has_transcript')`; PRD proof rule (§M12)
- Attribution: set `attribution_campaign_id` only if a campaign touch reached the same person within 90 days (attribution window)
- `suggest_next_step` uses the prompt capability `write`; model defaults to Haiku for cost

- [ ] **Step 1: Write the failing tests**

```python
def test_sync_meeting_matches_domain_to_account(mock_pool):
    meeting_id = sync_meeting(mock_pool, org_id, ws_id, integ_id, "ext1",
                              now(), ["acme.com"])
    assert isinstance(meeting_id, UUID)

def test_confirm_meeting_emits_held(mock_pool):
    confirm_meeting(mock_pool, meeting_id, now(), "confirmed")
    # outbox has meeting.held event

def test_unconfirmed_meeting_does_not_emit_held(mock_pool):
    confirm_meeting(mock_pool, meeting_id, now(), "unconfirmed")
    # no meeting.held in outbox

def test_create_task_returns_uuid(mock_pool):
    task_id = create_task(mock_pool, org_id, ws_id, acct_id, "Follow up", None, "reply")
    assert isinstance(task_id, UUID)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m12_meetings.py -v
```

- [ ] **Step 3: Implement `m12_meetings.py`**

`sync_meeting`: query entity_alias for domain matches; call `app.create_meeting` RPC (idempotent on integration_id+external_id).

`confirm_meeting`: call `app.confirm_meeting` RPC; check attribution window query before passing campaign_id.

`create_task`: call `app.create_task` RPC.

`suggest_next_step`: build context string from account stage + recent activity; call `gateway.write()` with `prompts/next_step/v1.md`; call `app.upsert_next_step`.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m12_meetings.py -v
```

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m12_meetings.py packages/signal_core/tests/test_m12_meetings.py
git commit -m "feat(py): M12 meetings and tasks — meeting proof rule, attribution, task creation, next-step suggestion"
```

---

### Task 5: M13 Integrations Hub (Python)

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m13_integrations.py`
- Create: `packages/signal_core/tests/test_m13_integrations.py`

**Interfaces:**
- Consumes: `public.integration`, `public.field_ownership`, `public.webhook_subscription`, `public.webhook_delivery` (from T1)
- Consumes: `app.create_webhook_subscription(...)`, `app.record_webhook_delivery(...)`, `app.record_external_write(...)` (from T1)
- Produces: `create_integration(pool, ctx, name, category, access_mode) -> UUID`
- Produces: `deliver_webhook(pool, subscription_id, event_id, event_type, payload_dict) -> str`
  - Signs payload with HMAC-SHA256 using subscription's secret from Vault
  - Posts to subscription.url with retry logic; calls `app.record_webhook_delivery`
  - Returns status: 'delivered' | 'failed'
- Produces: `replay_webhook_delivery(pool, delivery_id) -> str`
  - Resends stored `webhook_delivery.payload` without re-signing; idempotent for delivered deliveries

**Rules:**
- `deliver_webhook` retries with exponential backoff: delays = [10s, 30s, 90s, 270s, 810s, 1800s, 3600s, 7200s, 14400s, 28800s]; max 10 attempts
- After 10 consecutive failures: call `UPDATE public.webhook_subscription SET enabled=false WHERE id=…` via service_role; this is NOT an RPC write (it is an operational update from the worker's service_role, not from authenticated)
- `replay_webhook_delivery` must load the STORED payload and signature; must NOT re-sign

- [ ] **Step 1: Write the failing tests**

```python
def test_create_integration_returns_uuid(mock_pool):
    int_id = create_integration(mock_pool, ctx, "HubSpot", "CRMs", "Read")
    assert isinstance(int_id, UUID)

def test_deliver_webhook_calls_url(mock_pool, httpx_mock):
    httpx_mock.add_response(url="https://example.com/hook", status_code=200)
    status = deliver_webhook(mock_pool, sub_id, event_id, "score.band_changed", {})
    assert status == "delivered"

def test_replay_uses_stored_payload(mock_pool, httpx_mock):
    httpx_mock.add_response(url="https://example.com/hook", status_code=200)
    status = replay_webhook_delivery(mock_pool, delivery_id)
    assert status == "delivered"
    # sent body matches stored webhook_delivery.payload bytes exactly

def test_replay_idempotent_for_delivered(mock_pool):
    # delivery already has status='delivered'
    status = replay_webhook_delivery(mock_pool, delivery_id)
    # returns 'delivered' without making an HTTP call (idempotent check)
    assert status == "delivered"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m13_integrations.py -v
```

- [ ] **Step 3: Implement `m13_integrations.py`**

Use `httpx.AsyncClient` for outbound HTTP. Sign with `hmac.new(secret.encode(), payload_bytes, hashlib.sha256).hexdigest()`. Use `signal_core.vault` module (or `os.environ`) for secret retrieval.

`replay_webhook_delivery`: check `delivery.status == 'delivered'`; return early if so; otherwise resend `delivery.payload` as JSON with stored `delivery.signature` in header.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m13_integrations.py -v
```

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m13_integrations.py packages/signal_core/tests/test_m13_integrations.py
git commit -m "feat(py): M13 integrations — integration CRUD, webhook delivery with retry and replay"
```

---

### Task 6: API service foundation

**Files:**
- Create: `services/api/pyproject.toml`
- Create: `services/api/railway.json`
- Create: `services/api/Dockerfile`
- Create: `services/api/src/api/__init__.py`
- Create: `services/api/src/api/main.py`
- Create: `services/api/src/api/auth.py`
- Create: `services/api/src/api/routes/__init__.py`
- Create: `services/api/src/api/routes/entities.py`
- Create: `services/api/src/api/routes/accounts.py`
- Create: `services/api/src/api/routes/claims.py`
- Create: `services/api/src/api/routes/briefs.py`
- Create: `services/api/src/api/routes/campaigns.py`
- Create: `services/api/src/api/routes/activity.py`
- Create: `services/api/src/api/routes/tasks.py`
- Create: `services/api/src/api/routes/meetings.py`
- Create: `services/api/tests/__init__.py`
- Create: `services/api/tests/test_api_auth.py`

**Interfaces:**
- Consumes: `signal_core` (workspace dep via `packages/signal_core`)
- Consumes: `public.api_token` via service_role for token validation
- Produces: FastAPI app at `services/api/src/api/main.py` mounted at `/v1`
- Produces: `get_token(request) -> ApiToken` FastAPI dependency in `auth.py`
  - Extracts Bearer token from header; hashes with SHA-256; queries `api_token` WHERE `token_hash = sha256(raw)` AND `revoked_at IS NULL` AND (`expires_at IS NULL OR expires_at > now()`); raises 401 on failure; updates `last_used_at`
- Produces: `require_scope(scope: TokenScope)` FastAPI dependency factory — raises 403 if token.scope < required
- Produces: cursor-paginated list endpoints on all resources; each endpoint: `GET /v1/{resource}?workspace_id=...&after=<cursor>&limit=50`

**Route shape (all read-only unless noted):**
- `GET /v1/entities` — filter by workspace_id, entity_type, updated_since
- `GET /v1/entities/{id}`
- `GET /v1/accounts` — filter by workspace_id, brief_id, band, stage, updated_since
- `GET /v1/accounts/{id}`
- `GET /v1/claims` — filter by entity_id, predicate_key, status, updated_since
- `GET /v1/briefs` — filter by workspace_id
- `GET /v1/briefs/{id}/versions`
- `GET /v1/campaigns` — filter by workspace_id; Write scope required for POST
- `POST /v1/campaigns` (Write scope) — calls `app.create_campaign` via service_role
- `GET /v1/campaigns/{id}/members`
- `GET /v1/activity` — filter by workspace_id, account_id, kind, since
- `GET /v1/tasks` — filter by workspace_id, account_id, status
- `GET /v1/meetings` — filter by workspace_id, account_id, proof_status
- `GET /v1/health` — returns 200 (Railway health check)
- `GET /v1/openapi.json` — auto-generated by FastAPI

**pyproject.toml dependencies:**
```toml
[project]
name = "api"
requires-python = ">=3.13"
dependencies = [
  "fastapi>=0.115",
  "uvicorn[standard]>=0.30",
  "psycopg[binary,pool]>=3.1",
  "pydantic>=2.7",
  "httpx>=0.27",
  "signal_core",
]
```

- [ ] **Step 1: Write the failing auth tests**

```python
async def test_valid_token_resolves(client):
    # Insert api_token with token_hash = sha256("test-token")
    resp = await client.get("/v1/entities", headers={"Authorization": "Bearer test-token"},
                            params={"workspace_id": str(ws_id)})
    assert resp.status_code == 200

async def test_missing_token_returns_401(client):
    resp = await client.get("/v1/entities", params={"workspace_id": str(ws_id)})
    assert resp.status_code == 401

async def test_revoked_token_returns_401(client):
    # token with revoked_at = now()
    resp = await client.get("/v1/entities", headers={"Authorization": "Bearer revoked-token"},
                            params={"workspace_id": str(ws_id)})
    assert resp.status_code == 401

async def test_read_token_cannot_post(client):
    # Read-scoped token
    resp = await client.post("/v1/campaigns", headers={"Authorization": "Bearer read-token"},
                             json={"name": "x", "motion": "Vertical", "brief_id": str(brief_id), "workspace_id": str(ws_id)})
    assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/api && uv run pytest tests/test_api_auth.py -v
```

- [ ] **Step 3: Implement the API service**

`auth.py`: SHA-256 token hash; async psycopg SELECT with token hash lookup.

`main.py`: FastAPI app; include all routers; `/v1` prefix; CORS middleware; request-id middleware; `SET app.channel = 'api'` on each DB connection via connection pool setup hook.

Each route module uses cursor pagination: `after` param is a base64url-encoded last row's `id`; `SELECT … WHERE id > decode(after) ORDER BY id LIMIT {limit}`.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd services/api && uv run pytest tests/test_api_auth.py -v
```

- [ ] **Step 5: Commit**

```bash
git add services/api/
git commit -m "feat(api): API service foundation — FastAPI /v1, token auth, scope enforcement, all read endpoints"
```

---

### Task 7: Webhooks (inbound ingestion + outbound delivery job)

**Files:**
- Create: `services/api/src/api/webhooks_inbound.py`
- Create: `services/api/tests/test_webhooks.py`

**Interfaces:**
- Consumes: `m13_integrations.deliver_webhook(...)` (from T5)
- Consumes: `m10_inbox.classify_reply(...)` (from T3) for sequencer event ingestion
- Produces: `POST /v1/webhooks/{integration_id}` — inbound webhook ingestion endpoint; validates HMAC-SHA256 signature; writes activity rows via `app.create_activity`; deduplicates by `(integration_id, external_id)` using `activity.external_id`
- Produces: webhook delivery job in worker (wired in T11): `deliver_webhook_job(pool, subscription_id, event_id)` → calls `m13_integrations.deliver_webhook`

**Rules:**
- Inbound webhook validates signature: compute HMAC-SHA256(request body bytes, integration.credentials_vault_key); compare with `X-Signal-Signature` header; return 401 on mismatch
- Inbound event deduplication: check `activity.external_id = event["external_id"]` and `activity.integration_id = integration_id` before inserting; return 200 if already exists
- Inbound event mapping: sequencer `email_replied` → activity.kind = 'reply_received'; `email_bounced` → 'message_bounced'; `email_unsubscribed` → 'unsubscribed'; unknown events are stored with kind = 'note_added' and logged

- [ ] **Step 1: Write the failing tests**

```python
async def test_valid_inbound_webhook_stores_activity(client):
    payload = {"external_id": "evt1", "type": "email_replied", "account_external_id": "acct1"}
    sig = compute_hmac(payload_bytes, secret)
    resp = await client.post(f"/v1/webhooks/{integration_id}",
                              content=json.dumps(payload),
                              headers={"X-Signal-Signature": f"sha256={sig}"})
    assert resp.status_code == 200

async def test_invalid_signature_returns_401(client):
    resp = await client.post(f"/v1/webhooks/{integration_id}",
                              content=b'{"foo":"bar"}',
                              headers={"X-Signal-Signature": "sha256=bad"})
    assert resp.status_code == 401

async def test_duplicate_event_idempotent(client):
    # POST same external_id twice
    resp1 = await client.post(f"/v1/webhooks/{integration_id}", ...)
    resp2 = await client.post(f"/v1/webhooks/{integration_id}", ...)
    assert resp2.status_code == 200  # no error on duplicate
    # activity row count = 1
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd services/api && uv run pytest tests/test_webhooks.py -v
```

- [ ] **Step 3: Implement `webhooks_inbound.py`**

Mount route at `/v1/webhooks/{integration_id}` in `main.py`. Validate HMAC with `hmac.compare_digest`. Map event type to activity kind. Call `app.create_activity` with `external_id` for deduplication.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd services/api && uv run pytest tests/test_webhooks.py -v
```

- [ ] **Step 5: Commit**

```bash
git add services/api/src/api/webhooks_inbound.py services/api/tests/test_webhooks.py
git commit -m "feat(api): inbound webhook ingestion — HMAC validation, activity deduplication, event mapping"
```

---

### Task 8: MCP server

**Files:**
- Create: `services/api/src/api/mcp_server.py`

**Interfaces:**
- Consumes: `public.account`, `public.entity`, `public.verdict`, `public.score`, `public.account_card`, `public.claim` via service_role SELECT
- Consumes: `app.compute_metric(...)` (from T1) for read-only metric queries
- Produces: MCP server with tools:
  - `get_account_card(account_id: str, workspace_id: str) -> dict` — returns account_card.content + entity.display_name + verdict.value + score.band
  - `search_account(query: str, workspace_id: str, limit: int = 10) -> list[dict]` — FTS on entity.display_name + entity_alias.value_normalised
  - `get_claims(entity_id: str, predicate_key: str | None = None) -> list[dict]` — returns active claims with quotes
  - `find_accounts(workspace_id: str, band: str | None = None, stage: str | None = None, limit: int = 20) -> list[dict]`

**Rules:**
- MCP server mounts as a Starlette sub-app alongside FastAPI using SSE transport (`mcp[cli]` SDK)
- Tool calls run under service_role; scope checked via token header the same as the REST API
- Writes (from MCP tools) are attributed to `api_token.member_id`; shared service tokens (`member_id IS NULL`) are read-only
- SET `app.channel = 'mcp'` on each MCP tool call DB connection

- [ ] **Step 1: Implement `mcp_server.py`**

```python
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("signal-intel")

@mcp.tool()
async def get_account_card(account_id: str, workspace_id: str) -> dict: ...

@mcp.tool()
async def search_account(query: str, workspace_id: str, limit: int = 10) -> list[dict]: ...

@mcp.tool()
async def get_claims(entity_id: str, predicate_key: str | None = None) -> list[dict]: ...

@mcp.tool()
async def find_accounts(workspace_id: str, band: str | None = None, stage: str | None = None, limit: int = 20) -> list[dict]: ...
```

Mount in `main.py`:
```python
app.mount("/mcp", mcp.sse_app())
```

- [ ] **Step 2: Verify MCP server starts**

```bash
cd services/api && uv run python -c "from api.mcp_server import mcp; print('MCP ok')"
```

Expected: `MCP ok` with no import errors.

- [ ] **Step 3: Commit**

```bash
git add services/api/src/api/mcp_server.py
git commit -m "feat(api): MCP server — get_account_card, search_account, get_claims, find_accounts"
```

---

### Task 9: M16 Reporting

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m16_reporting.py`
- Create: `packages/signal_core/tests/test_m16_reporting.py`

**Interfaces:**
- Consumes: `app.compute_metric(p_workspace_id, p_brief_id, p_metric_key, p_filters)` (from T1)
- Consumes: `public.metric_definition` — 15 canonical metric SQL bodies seeded in migration 0023
- Produces: `compute_metric(pool, workspace_id, brief_id, metric_key, filters={}) -> dict`
  - Wraps `app.compute_metric`; returns `{"value": ..., "metric_key": ..., "computed_at": ..., "definition_version": int}`
- Produces: `get_funnel(pool, workspace_id, brief_id) -> list[dict]`
  - Returns stage counts + conversion rates + median_days_in_stage for the stage funnel

**Canonical metric keys to seed in migration 0023:**
1. `accounts_discovered` — `SELECT count(*) FROM public.account WHERE workspace_id=$1 AND EXISTS (SELECT 1 FROM public.verdict v JOIN public.entity e ON e.id = v.entity_id WHERE e.id = account.entity_id AND v.brief_version_id IN (SELECT id FROM public.brief_version WHERE brief_id = $2))`
2. `accounts_verified` — count WHERE verdict IN ('Confirmed','Likely')
3. `accounts_researched` — count WHERE research_doc exists
4. `accounts_reachable` — count with ≥1 valid contact_point
5. `accounts_contacted` — distinct accounts with ≥1 message_sent activity
6. `people_contacted` — distinct person_entity_id with message_sent
7. `messages_sent` — count activity WHERE kind='message_sent'
8. `genuine_replies_people` — distinct people whose latest reply_class IN ('Interested','Referral')
9. `genuine_replies_accounts` — distinct accounts with ≥1 genuine reply
10. `companies_met` — distinct accounts with first held attributed meeting
11. `meetings_held` — count meeting WHERE proof_status IN ('confirmed','has_transcript')
12. `reply_rate` — genuine_replies_people / NULLIF(messages_sent/1000, 0)
13. `bounce_rate` — count(message_bounced) / NULLIF(messages_sent, 0)
14. `high_band_coverage` — accounts_contacted / NULLIF(high_band_accounts, 0)
15. `stalled_accounts` — accounts in Contacted > stall_threshold with no next_step

- [ ] **Step 1: Write the failing tests**

```python
def test_compute_metric_accounts_discovered(mock_pool):
    result = compute_metric(mock_pool, ws_id, brief_id, "accounts_discovered")
    assert "value" in result
    assert isinstance(result["value"], int)

def test_compute_metric_unknown_key_raises(mock_pool):
    with pytest.raises(ValueError, match="unknown metric"):
        compute_metric(mock_pool, ws_id, brief_id, "not_a_metric")

def test_get_funnel_returns_all_stages(mock_pool):
    funnel = get_funnel(mock_pool, ws_id, brief_id)
    stage_names = [row["stage"] for row in funnel]
    assert "Untouched" in stage_names
    assert "Contacted" in stage_names
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m16_reporting.py -v
```

- [ ] **Step 3: Implement `m16_reporting.py`**

`compute_metric`: call `app.compute_metric` RPC; raise `ValueError("unknown metric: {key}")` if the RPC returns NULL.

`get_funnel`: query `account.stage` grouped by stage; compute conversion between consecutive stages; use `account_stage_event` to compute median time.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m16_reporting.py -v
```

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m16_reporting.py packages/signal_core/tests/test_m16_reporting.py
git commit -m "feat(py): M16 reporting — compute_metric wrapper, funnel view, 15 canonical metrics"
```

---

### Task 10: M18 Compliance additions

**Files:**
- Create: `packages/signal_core/src/signal_core/modules/m18_quality.py`
- Create: `packages/signal_core/tests/test_m18_quality.py`

**Interfaces:**
- Consumes: `app.process_dsr_deletion(p_org_id, p_person_entity_id)` (from T1)
- Consumes: `public.evaluation_set`, `public.data_subject_request`, `public.lawful_basis_record`, `public.suppression_list` (from T1)
- Produces: `submit_dsr(pool, org_id, person_entity_id, type_, requested_by) -> UUID`
  - Inserts data_subject_request row; for type_='deletion' calls `app.process_dsr_deletion` immediately
- Produces: `record_lawful_basis(pool, org_id, source_type, basis) -> UUID`
- Produces: `add_evaluation_example(pool, org_id, workspace_id, brief_version_id, example) -> None`
  - Upserts evaluation_set for brief_version_id; appends example to examples array
- Produces: `is_suppressed(pool, org_id, person_entity_id) -> bool`

**Rules:**
- DSR deletion: delegate entirely to `app.process_dsr_deletion` — do not re-implement in Python; the RPC is the single atomic path
- Access DSR: return a JSON snapshot of all data held on the person — claims, activity, contact_points, mentions — built from service_role queries, never from cached state

- [ ] **Step 1: Write the failing tests**

```python
def test_submit_dsr_deletion_calls_rpc(mock_pool):
    dsr_id = submit_dsr(mock_pool, org_id, person_id, "deletion", "user@example.com")
    assert isinstance(dsr_id, UUID)
    # suppression_list has row for person_id (via process_dsr_deletion)
    # entity.display_name == '[deleted]'

def test_submit_dsr_access_returns_snapshot(mock_pool):
    result = submit_dsr(mock_pool, org_id, person_id, "access", "user@example.com")
    # returns dict with "claims", "activity", "contact_points" keys

def test_add_evaluation_example_appends(mock_pool):
    add_evaluation_example(mock_pool, org_id, ws_id, bv_id, {"verdict": "Confirmed", "correct": True})
    # evaluation_set.examples has 1 item

def test_is_suppressed_returns_true_for_suppressed(mock_pool):
    # person in suppression_list
    assert is_suppressed(mock_pool, org_id, person_id) is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd packages/signal_core && uv run pytest tests/test_m18_quality.py -v
```

- [ ] **Step 3: Implement `m18_quality.py`**

`submit_dsr`: insert `data_subject_request`; if type_ == 'deletion' call `app.process_dsr_deletion` RPC; if type_ == 'access' run queries for claims, activity, contact_points grouped by person_entity_id.

`add_evaluation_example`: check if evaluation_set row exists for brief_version_id; INSERT if not; UPDATE examples = examples || `[example]::jsonb`.

`is_suppressed`: SELECT exists from suppression_list.

- [ ] **Step 4: Run tests and verify they pass**

```bash
cd packages/signal_core && uv run pytest tests/test_m18_quality.py -v
```

- [ ] **Step 5: Commit**

```bash
git add packages/signal_core/src/signal_core/modules/m18_quality.py packages/signal_core/tests/test_m18_quality.py
git commit -m "feat(py): M18 compliance — DSR deletion/access, evaluation sets, lawful basis, suppression check"
```

---

### Task 11: Worker wiring (Phase 3 job types + crons)

**Files:**
- Modify: `services/worker/src/worker/app.py`

**Interfaces:**
- Consumes: `m09_activation.push_campaign_batch(...)` (from T2)
- Consumes: `m10_inbox.classify_reply(...)`, `m10_inbox.get_inbox(...)` (from T3)
- Consumes: `m12_meetings.sync_meeting(...)`, `m12_meetings.suggest_next_step(...)` (from T4)
- Consumes: `m13_integrations.deliver_webhook(...)` (from T5)
- Consumes: `m16_reporting.compute_metric(...)` (from T9)
- Produces: 7 new job handlers registered in `build_app()`:

```python
# campaign.push → push_campaign_batch (m09)
# reply.classify → classify_reply (m10)
# inbox.reconcile → periodic inbox reconciliation (m10)
# meeting.sync → sync_meeting for a given integration (m12)
# next_step.suggest → suggest_next_step (m12)
# webhook.deliver → deliver_webhook (m13)
# metric.snapshot → snapshot all canonical metrics for a workspace (m16)
```

- Produces: 2 new cron jobs:
  - `INBOX_RECONCILE_CRON = "*/15 * * * *"` (every 15 min) — calls inbox.reconcile for all active workspaces
  - `NEXT_STEP_CRON = "0 7 * * *"` (daily 07:00 UTC) — suggest_next_step for all Contacted/Engaged/Qualified accounts without a recent next_step

**Rules:**
- Each new job follows the existing `@job(type=..., input=PydanticModel, ...)` pattern
- Each job checks the kill switch before running
- Each job has an idempotency key
- `webhook.deliver` has `queue="default"` with high concurrency to handle delivery bursts

- [ ] **Step 1: Add import block and new job types**

Add at the top of `app.py`:
```python
from signal_core.modules.m09_activation import push_campaign_batch
from signal_core.modules.m10_inbox import classify_reply as _classify_reply, get_inbox as _get_inbox
from signal_core.modules.m12_meetings import sync_meeting as _sync_meeting, suggest_next_step as _suggest_next_step
from signal_core.modules.m13_integrations import deliver_webhook as _deliver_webhook
from signal_core.modules.m16_reporting import compute_metric as _compute_metric
```

- [ ] **Step 2: Register job handlers in `build_app()`**

Follow the existing pattern from Phase 2 worker tasks. Each handler:
1. Checks kill switch: `if not KillSwitch(pool).is_enabled(task_type): return`
2. Loads inputs from the job's Pydantic model
3. Calls the module function
4. Logs completion

- [ ] **Step 3: Register new cron jobs**

```python
app.periodic_defer(inbox_reconcile_job, cron=INBOX_RECONCILE_CRON)
app.periodic_defer(next_step_cron_job, cron=NEXT_STEP_CRON)
```

- [ ] **Step 4: Run worker smoke test**

```bash
cd services/worker && uv run python -c "from worker.app import build_app; print('worker ok')"
```

Expected: `worker ok` with no import errors.

- [ ] **Step 5: Commit**

```bash
git add services/worker/src/worker/app.py
git commit -m "feat(worker): Phase 3 wiring — campaign push, reply classify, inbox reconcile, meeting sync, webhook deliver, next step, metric snapshot"
```

---

### Task 12: Web pages (inbox, campaigns, meetings, reports)

**Files:**
- Create: `apps/web/src/app/(app)/workspace/[wsid]/inbox/page.tsx`
- Create: `apps/web/src/app/(app)/workspace/[wsid]/campaigns/page.tsx`
- Create: `apps/web/src/app/(app)/workspace/[wsid]/meetings/page.tsx`
- Create: `apps/web/src/app/(app)/workspace/[wsid]/reports/page.tsx`

**Interfaces:**
- Consumes: `createClient()` from `@/lib/supabase/server` (RSC pattern — `params: Promise<{wsid: string}>`, `await params`)
- Produces: 4 RSC pages following the existing pattern from accounts pages (Phase 2)

**Page data queries:**

**Inbox page** (`/inbox`):
```ts
supabase.from("activity")
  .select("id, kind, occurred_at, payload, person:person_entity_id(id, display_name), account:account_id(id)")
  .eq("workspace_id", wsid)
  .eq("kind", "reply_received")
  .order("occurred_at", { ascending: false })
  .limit(100)
```
Render: table of replies with occurred_at, account name, reply_class from payload (if classified), overdue flag (> 2 working days old without class).

**Campaigns page** (`/campaigns`):
```ts
supabase.from("campaign")
  .select("id, name, motion, push_mode, status, bounce_messages_sent, bounce_count")
  .eq("workspace_id", wsid)
  .order("created_at", { ascending: false })
```
Render: table with motion badge, push_mode chip, status, bounce rate (bounce_count/bounce_messages_sent formatted as %).

**Meetings page** (`/meetings`):
```ts
supabase.from("meeting")
  .select("id, scheduled_at, held_at, proof_status, account:account_id(id, entity:entity_id(display_name))")
  .eq("workspace_id", wsid)
  .order("scheduled_at", { ascending: false })
  .limit(50)
```
Plus tasks:
```ts
supabase.from("task")
  .select("id, title, due_date, origin, status, account:account_id(id, entity:entity_id(display_name))")
  .eq("workspace_id", wsid)
  .eq("status", "open")
  .order("due_date")
```
Render: two tabs — Meetings (with proof_status badge) and Tasks (with origin chip, due date).

**Reports page** (`/reports`):
```ts
// Call RPC directly for the 5 headline metrics
const { data: metrics } = await supabase.rpc("compute_metric", {
  p_workspace_id: wsid, p_brief_id: briefId, p_metric_key: "accounts_discovered", p_filters: {}
})
```
Fetch: accounts_discovered, accounts_verified, accounts_contacted, genuine_replies_people, companies_met. Render as 5 `MetricTile` components. Include a funnel section showing stage counts.

**Rules:**
- All pages are RSC — no `'use client'` at the top level
- Normalize PostgREST array joins → single objects before rendering (same pattern as accounts pages)
- Never show an edit control for derived values (stage, verdict, score, band)
- Each MetricTile must link to the underlying rows (href to accounts filtered by relevant segment)

- [ ] **Step 1: Create all 4 page files following the RSC pattern**

Each page: import `createClient`; `await params`; `const supabase = await createClient(); if (!supabase) return null;`

- [ ] **Step 2: Run TypeScript check**

```bash
cd apps/web && pnpm tsc --noEmit
```

Expected: 0 errors.

- [ ] **Step 3: Commit**

```bash
git add apps/web/src/app/(app)/workspace/[wsid]/inbox/ apps/web/src/app/(app)/workspace/[wsid]/campaigns/ apps/web/src/app/(app)/workspace/[wsid]/meetings/ apps/web/src/app/(app)/workspace/[wsid]/reports/
git commit -m "feat(web): Phase 3 pages — inbox, campaigns, meetings/tasks, reports"
```

---

### Task 13: pgTAP suites for Phase 3

**Files:**
- Create: `supabase/tests/database/15_campaigns.test.sql`
- Create: `supabase/tests/database/16_meetings_tasks.test.sql`
- Create: `supabase/tests/database/17_webhooks_api.test.sql`
- Create: `supabase/tests/database/18_compliance_metrics.test.sql`

**Interfaces:**
- All suites follow the standard pgTAP pattern: `begin; create extension pgtap; select plan(N); fixture; assertions; select * from finish(); rollback;`
- Derivation RPCs called under `set local role service_role;`
- All SELECT calls, not PERFORM

**Suite 15 — Campaigns (8 assertions):**
1. `app.create_campaign` exists
2. `app.add_campaign_member` exists
3. Direct INSERT into campaign blocked (42501) for authenticated
4. `create_campaign` returns a UUID
5. `add_campaign_member` is idempotent on (campaign_id, person_entity_id)
6. `campaign.member_added` event emitted after `add_campaign_member`
7. `check_and_pause_if_bouncing` returns false when bounce_count=0
8. `set_campaign_push_mode` changes push_mode

**Suite 16 — Meetings + Tasks (7 assertions):**
1. `app.create_meeting` exists
2. `app.create_task` exists
3. Direct INSERT into meeting blocked (42501) for authenticated
4. `create_meeting` is idempotent on (source_integration_id, external_id)
5. `confirm_meeting` with proof_status='confirmed' emits `meeting.held`
6. `confirm_meeting` with proof_status='unconfirmed' does NOT emit `meeting.held`
7. `create_task` emits `task.created`

**Suite 17 — Webhooks + API tokens (7 assertions):**
1. `app.create_webhook_subscription` exists
2. `app.record_webhook_delivery` exists
3. `app.create_api_token` exists
4. Direct INSERT into api_token blocked (42501) for authenticated
5. `record_webhook_delivery` upserts (idempotent on subscription_id+event_id)
6. `record_webhook_delivery` increments fail_count on subscription on status='failed'
7. `create_api_token` stores token_hash (not raw token)

**Suite 18 — Compliance + Metrics (7 assertions):**
1. `app.process_dsr_deletion` exists
2. `app.compute_metric` exists
3. `process_dsr_deletion` deletes contact_points for the person
4. `process_dsr_deletion` adds person to suppression_list
5. `process_dsr_deletion` nulls entity display_name
6. `compute_metric` returns a jsonb result for 'accounts_discovered'
7. `compute_metric` returns NULL for unknown metric key (Python raises ValueError)

- [ ] **Step 1: Write all 4 test files**

Each file must use unique fixture IDs (Suite 15 uses `b500` prefix, Suite 16 `b501`, Suite 17 `b502`, Suite 18 `b503`) to avoid uuid collisions with other suites.

- [ ] **Step 2: Run all 4 suites**

```bash
supabase test db --local
```

Expected: all suites pass with plan(N) = ok count.

- [ ] **Step 3: Commit**

```bash
git add supabase/tests/database/15_campaigns.test.sql supabase/tests/database/16_meetings_tasks.test.sql supabase/tests/database/17_webhooks_api.test.sql supabase/tests/database/18_compliance_metrics.test.sql
git commit -m "test(db): pgTAP Phase 3 suites — campaigns, meetings/tasks, webhooks/API, compliance/metrics"
```
