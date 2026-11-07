# Phase 1a — AI Gateway and Metering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every module asks for an AI *capability*, never a vendor. Each call is budget-checked before it runs, validated against a typed schema, and written to the usage ledger with its cost. Admins set budgets and approve paused work in Settings.

**Architecture:**
- **Migration 0012** adds `model_price`, `usage_ledger` (append-only), `budget`, `budget_approval`, `workspace_settings.editor_approval_limit_usd` and audited `public.*` functions.
- **`signal_core.ai_gateway`** (Python):
  - holds the capability API, provider adapters (Anthropic, Voyage, Fake) and a file-based prompt registry recorded as `extractor_version`;
  - `signal_core.metering` holds the ledger, budget guard and cost preview.
- **Worker:** cron jobs re-queue approved work and report whether AI is configured.
- **Web:** Settings gains a "Usage and budgets" section.

**Tech Stack:** psycopg 3, Pydantic v2, `anthropic` and `voyageai` Python SDKs, Procrastinate, Next.js server actions, PGlite checks, pytest.

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md` (§2 AI row, §3 rule 6, §10 "AI cost spikes") · PRD S3, S6 · map: `2026-11-07-phase1-overview.md`

## Global Constraints

- Modules never import a vendor SDK. Only `signal_core/ai_gateway/providers/*` may import `anthropic` or `voyageai`; a test enforces this.
- Capabilities and default models (from `Settings`):
  - `extract`, `judge` and `write` use `claude-sonnet-5-5`;
  - `prejudge` uses `claude-haiku-5-5`;
  - `summarise` uses `claude-sonnet-5-5`;
  - `embed` uses `voyage-3.5` at 1024 dimensions, matching `memory_chunk.embedding vector(1024)`.
- **Budget check comes before the call** (spec §10). A call that would push spend past a day or month cap does not run: it raises `BudgetPaused` and opens a `budget_approval`.
- Caps are USD, per workspace and per Brief, `day` and `month`, in UTC calendar periods. No budget row means no cap. Spend is the sum of `usage_ledger.cost_usd` in the period.
- Approvals:
  - Owner or Admin can approve any amount.
  - An Editor can approve up to `workspace_settings.editor_approval_limit_usd` (default 25).
  - Operator and Viewer cannot approve.
- **Typed outputs:** invalid model output is rejected (`AiOutputInvalid`), never coerced. The rejected call is still ledgered, because its tokens were spent.
- **Prompt versions:**
  - each version is an immutable file `prompts/<name>/v<N>.md`;
  - its SHA-256 is stored as `extractor_version.prompt_hash`;
  - editing a released version's file fails a test.
- **No keys means "Not set up yet"** (standing rule for OAuth and credentials). With no key, real capabilities raise `AiNotConfigured`, the worker records health `ai_gateway` as `degraded` with `{"configured": false}`, and Settings shows `NotConfigured`. Tests always use `FakeProvider`.
- **Logs:** never log prompt text or completions. Log only capability, prompt name and version, model, tokens, cost, latency and status (S7 redaction).
- **Copy:** sentence case, plain, no exclamation marks (design system README).

## Review Focus

1. Two jobs check the same budget at the same moment, and both fit alone but not together. Expected: only one runs. The guard reserves the estimate under `pg_advisory_xact_lock` on the budget scope. Tests `concurrent calls cannot both overspend` (Task 1, sequential) and `test_concurrent_reserves_cannot_both_overspend` (Task 5, two real sessions).
2. The model returns JSON that parses but breaks the schema, or text wrapped in a code fence. Expected: a fenced JSON block is unwrapped; schema-invalid output is rejected and ledgered. Tests `fenced json accepted` and `schema-invalid output rejected and ledgered`.
3. A provider times out or returns 429 or 5xx. Expected: up to 2 retries with backoff, then `AiProviderError`. Nothing is ledgered if no tokens came back. Test `provider 429 retried then fails without ledger row`.
4. A Viewer or Operator calls `decide_budget_approval`, or an Editor approves above the limit. Expected: refused. Tests `viewer cannot approve` and `editor limit enforced`.
5. A model with no `model_price` row. Expected: the call is refused before it runs (`AiNotConfigured("no price for model")`), never metered as free. Test `unknown model refused`.

---

### Task 1: Migration 0012 — metering schema and functions

**Files:**
- Create: `supabase/migrations/20261107000012_metering.sql`
- Modify: `scripts/check-migrations.mjs` (new checks)
- Create: `supabase/tests/database/06_metering.test.sql`
- Modify: `packages/contracts/schema/enums.json`, adding:
  - `UsageUnit` (`usage_unit`: `ai_input_tokens`, `ai_output_tokens`, `embed_tokens`, `fetch`, `enrichment_lookup`, `research_doc`)
  - `BudgetPeriod` (`budget_period`: `day`, `month`)
  - `ApprovalStatus` (`approval_status`: `pending`, `approved`, `rejected`, `expired`)

  Then run `pnpm codegen`.

**Interfaces:**

Tables:
- `model_price(model text pk, provider text, unit public.usage_unit, usd_per_million numeric(12,4), updated_at)`, with the primary key on `(model, unit)`.
  - Seeded for the five default models.
  - Prices come from the current published rates: look them up with the `claude-api` skill and Voyage's pricing page at implementation time, and cite the date in a comment.
- `usage_ledger(id bigint identity, org_id, workspace_id, brief_id null, job_id text null, capability text, provider text, model text, unit usage_unit, quantity numeric, cost_usd numeric(14,6), latency_ms int, status text check in ('ok','rejected','reserved','released'), reservation_id uuid null, created_at)`.
  - Insert-only: UPDATE and DELETE are revoked, and a trigger raises.
  - Index on `(workspace_id, created_at)` and `(brief_id, created_at)`.
- `budget(id, org_id, workspace_id, brief_id null, period budget_period, cap_usd numeric(12,2) check > 0, updated_by, updated_at)`, with `unique nulls not distinct (workspace_id, brief_id, period)`.
- `budget_approval(id, org_id, workspace_id, brief_id null, job_type text, job_args jsonb, estimate_usd numeric(12,4), reason text, status approval_status default 'pending', requested_at, decided_by null, decided_at null, resumed_at null)`.

SQL functions:
- `app.period_spend(p_ws uuid, p_brief uuid, p_period budget_period) returns numeric`
  - Sums `cost_usd`, counting a `reserved` row only until its `released` twin exists.
- `app.reserve_budget(p_ws uuid, p_brief uuid, p_estimate numeric, p_job_type text, p_job_args jsonb, p_approval uuid default null) returns jsonb`
  - Returns `{"ok": true, "reservation_id": …}` or `{"ok": false, "approval_id": …, "period": …, "cap": …, "spent": …}`.
  - An `approved` `p_approval` for the same workspace pre-approves spend up to its `estimate_usd`, so resumed work is not paused again.
  - Takes `pg_advisory_xact_lock(hashtext(ws||brief))`.
  - Checks every applicable cap: workspace day/month, then Brief day/month.
  - On success, inserts a `reserved` ledger row.
  - On refusal, inserts `budget_approval` and audits it.
  - Service-role only.
- `app.settle_reservation(p_reservation uuid, p_actual jsonb)`
  - Writes a `released` row cancelling the reservation, then the real `ok`/`rejected` rows.
  - Service-role only.
- `public.set_budget(p_ws uuid, p_brief uuid, p_period budget_period, p_cap numeric)`
  - Admin+.
  - A null cap deletes the budget.
  - Audited.
- `public.decide_budget_approval(p_id uuid, p_approve boolean)`
  - Owner/Admin may decide any amount; Editor only if `estimate_usd <= editor_approval_limit_usd`.
  - Only `pending` approvals can be decided.
  - Audited.
- `workspace_settings.editor_approval_limit_usd numeric(10,2) not null default 25`.

Access:
- RLS SELECT on `usage_ledger` and `budget` for workspace members.
- RLS SELECT on `budget_approval` for Editor+.
- `model_price` is readable by `authenticated`.

- [ ] **Step 1: Write the failing checks** in `check-migrations.mjs`:
  - `ledger is insert-only`: update or delete raises.
  - `reserve within cap ok`: workspace day cap 10, spent 4, estimate 5 returns `ok: true`.
  - `reserve over cap pauses`: estimate 7 returns `ok: false` with one `pending` approval.
  - `brief cap applies under workspace cap`.
  - `concurrent calls cannot both overspend`: two reserves of 5 against cap 10 with spent 1, done sequentially in one session; the second returns `ok: false`. The lock is asserted in pgTAP.
  - `viewer cannot approve`.
  - `editor limit enforced`: Editor approving 30 when the limit is 25 raises.
  - `admin can approve any`.
  - `set_budget requires Admin`.
  - `other org cannot read ledger`.
- [ ] **Step 2:** Run `pnpm db:check`. Expected: the new checks fail.
- [ ] **Step 3:** Write the migration, the enum additions and `06_metering.test.sql`, which mirrors the checks. True two-session concurrency can't run in pgTAP, so it is proven in Task 5 against the dev database: two worker tasks reserve at once and only one succeeds.
- [ ] **Step 4:** Run `pnpm codegen && pnpm db:check`. Expected: all checks pass, including the new ones.
- [ ] **Step 5:** Commit `feat(metering): usage ledger, budgets and approvals (S6)`.

### Task 2: `signal_core.metering` — pricing, ledger, budget guard, cost preview

**Files:**
- Create: `packages/signal_core/src/signal_core/metering.py`
- Test: `packages/signal_core/tests/test_metering.py`

**Interfaces (Produces):**
- `@dataclass(frozen=True) class CallContext`:
  - `org_id: UUID`, `workspace_id: UUID`, `brief_id: UUID | None`, `job_id: str | None`, `job_type: str`, `job_args: dict[str, Any]`.
- `class PriceBook(Protocol)`:
  - `async def price(self, model: str, unit: UsageUnit) -> Decimal | None`
- `def cost_usd(per_million: Decimal, quantity: int) -> Decimal`
  - Rounds half-up to 6 places.
- `@dataclass class UsageLine`:
  - `unit: UsageUnit`, `quantity: int`, `cost_usd: Decimal`.
- `class BudgetGuard(Protocol)`:
  - `async def reserve(self, ctx: CallContext, estimate_usd: Decimal) -> UUID`
    - Returns the reservation id, or raises `BudgetPaused(approval_id, period, cap, spent)`.
  - `async def settle(self, reservation_id: UUID, ctx: CallContext, capability: str, provider: str, model: str, lines: list[UsageLine], latency_ms: int, status: Literal["ok", "rejected"]) -> None`
  - `async def release(self, reservation_id: UUID) -> None`
    - Used when the call failed with no tokens.
- Implementations:
  - `PgBudgetGuard(pool)` calls `app.reserve_budget` and `app.settle_reservation`.
  - `PgPriceBook(pool)` caches prices for 5 minutes.
  - `MemoryBudgetGuard` and `MemoryPriceBook` are test doubles with the same semantics, including caps.
- `def preview(items: int, avg_input_tokens: int, avg_output_tokens: int, model_prices: tuple[Decimal, Decimal], seconds_per_item: float, concurrency: int) -> CostEstimate`
  - `CostEstimate(volume: int, cost_usd: Decimal, duration_seconds: int)`.
  - This is the S6 preview that M1 calls in 1b and 1e.

- [ ] **Step 1: Write failing tests:**
  - `test_cost_rounding`: `cost_usd(Decimal("3"), 1500) == Decimal("0.004500")`.
  - `test_memory_guard_pauses_over_cap`
  - `test_settle_records_rejected_status`
  - `test_preview_duration_uses_concurrency`: 100 items at 2 s with concurrency 4 gives 50 s.
  - `test_unknown_model_refused`: `price()` returns None and the gateway raises `AiNotConfigured`, asserted in Task 3.
- [ ] **Step 2:** Run `uv run pytest packages/signal_core/tests/test_metering.py -v`. Expected: the tests fail.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run the tests again. Expected: they pass.
- [ ] **Step 5:** Commit `feat(metering): budget guard, price book and cost preview`.

### Task 3: `signal_core.ai_gateway` — providers, prompts, capabilities, validation

**Files:**
- Create in `packages/signal_core/src/signal_core/ai_gateway/`:
  - `__init__.py`, `errors.py`, `prompts.py`, `gateway.py`, `registry.py`
  - `providers/__init__.py`, `providers/base.py`, `providers/anthropic.py`, `providers/voyage.py`, `providers/fake.py`
  - `prompts/canary_extract/v1.md`: a real minimal prompt, used by the canary in 1d.
- Create: `packages/signal_core/tests/test_ai_gateway.py`, `packages/signal_core/tests/test_ai_boundaries.py`
- Modify: `packages/signal_core/pyproject.toml`, adding `anthropic` and `voyageai` at the latest versions at least 2 weeks old.

**Interfaces (Produces):**
- `errors.py`: `AiNotConfigured`, `AiProviderError`, `AiOutputInvalid(errors: list[str])`, re-export `BudgetPaused`.
- `providers/base.py`:
  - `Completion(text: str, input_tokens: int, output_tokens: int, model: str)`
  - `class ChatProvider(Protocol)`:
    - `name: str`
    - `async def complete(self, *, model: str, system: str, user: str, max_tokens: int, json_schema: dict[str, Any] | None) -> Completion`
  - `Embeddings(vectors: list[list[float]], tokens: int, model: str)`
  - `class EmbedProvider(Protocol)`:
    - `name: str`
    - `async def embed(self, *, model: str, texts: list[str], input_type: Literal["document","query"]) -> Embeddings`
- Adapters:
  - `AnthropicProvider(api_key: SecretStr)`
  - `VoyageProvider(api_key: SecretStr)` with `output_dimension=1024`
  - Both retry up to 2 times on 429 and 5xx with exponential backoff (0.5 s, then 1 s, with jitter), then raise `AiProviderError`.
- `FakeProvider(responses: dict[str, str] | Callable[[str], str], tokens: tuple[int, int] = (100, 20))` implements both protocols. Embeddings are deterministic: a 1024-dimension vector from a SHA-256 seed.
- `prompts.py`:
  - `Prompt(name: str, version: int, capability: Capability, system: str, user_template: str, sha256: str)`
  - `def load_prompt(name: str, version: int) -> Prompt`
    - The file has front matter (`capability:`), then a `---` line, then the system text, then `--- user ---`, then the user template with `{variables}`.
  - `def render(prompt: Prompt, variables: Mapping[str, str]) -> str`
    - Raises `KeyError` on a missing variable.
- `class Capability(StrEnum)`: `extract`, `judge`, `prejudge`, `summarise`, `embed`, `write`.
- `gateway.py`, `class AiGateway`:
  - `__init__(self, *, settings: Settings, chat: ChatProvider | None, embedder: EmbedProvider | None, prices: PriceBook, budget: BudgetGuard, versions: ExtractorVersions)`
  - `async def run(self, capability: Capability, prompt: Prompt, variables: Mapping[str, str], output: type[T], ctx: CallContext, *, max_tokens: int = 2048) -> Result[T]`
    - `Result[T]` has `value: T`, `extractor_version_id: UUID`, `cost_usd: Decimal`.
    - Order of operations:
      1. Resolve the model from the capability.
      2. Check the price (no price raises `AiNotConfigured`).
      3. Estimate as rendered characters ÷ 4 for input plus `max_tokens` for output.
      4. `budget.reserve`.
      5. `chat.complete` with `json_schema=output.model_json_schema()`.
      6. Strip one surrounding code fence.
      7. `output.model_validate_json`.
      8. Settle `ok`, or settle `rejected` and raise `AiOutputInvalid`.
    - Provider failure with no completion: `budget.release`, then re-raise.
  - `async def embed(self, texts: list[str], ctx: CallContext, *, input_type: Literal["document","query"]) -> list[list[float]]`
    - Same budget, ledger and price steps, using unit `embed_tokens`.
  - `def configured(self) -> dict[str, bool]` returns `{"chat": …, "embed": …}`.
- `class ExtractorVersions(Protocol)`:
  - `async def ensure(self, capability: str, prompt: Prompt, model: str, parser_version: str) -> UUID`
  - `PgExtractorVersions` upserts into `extractor_version` and fills `prompt_hash`.
- `registry.py` validates against the predicate registry, used by M3 in 1d:
  - `Predicate(key: str, value_type: str, allowed_values: list[str] | None, unit: str | None)`
  - `def validate_claim_value(p: Predicate, value: object) -> str | None`
    - Returns an error message or None.
    - Mirrors `app.validate_claim`: boolean, number, enum membership, text, ISO date, and UUID for `entity_ref`.

Building and checking it:
- [ ] **Step 1: Write failing tests** with `FakeProvider`, `MemoryBudgetGuard` and `MemoryPriceBook`:
  - `test_run_returns_typed_value_and_ledgers_ok`: asserts two ledger lines (input, output) with the expected `cost_usd`.
  - `test_fenced_json_accepted`
  - `test_schema_invalid_output_rejected_and_ledgered`
  - `test_budget_paused_before_provider_called`: the fake records zero calls.
  - `test_provider_429_retried_then_fails_without_ledger_row`: uses an `httpx.MockTransport` on `AnthropicProvider`; asserts 3 attempts and the reservation released.
  - `test_unknown_model_refused`
  - `test_no_key_raises_not_configured`: `chat=None`.
  - `test_embed_is_1024_dims`
  - `test_prompt_hash_stable_and_missing_variable_raises`
  - `test_validate_claim_value` covers each value type plus an enum value outside the list.
- [ ] **Step 2: Write `test_ai_boundaries.py`:**
  - `test_only_providers_import_vendor_sdks`: walks `packages/` and `services/` for `import anthropic`/`voyageai` outside `ai_gateway/providers`.
  - `test_released_prompts_unchanged`: compares each `prompts/**/v*.md` SHA-256 against `prompts/LOCK.json`.
- [ ] **Step 3:** Run `uv run pytest packages/signal_core -v`. Expected: the new tests fail.
- [ ] **Step 4:** Implement the module and add `prompts/LOCK.json`.
- [ ] **Step 5:** Run `uv run pytest packages/signal_core -v && uv run mypy packages services && uv run ruff check .`. Expected: all pass.
- [ ] **Step 6:** Commit `feat(ai): capability gateway with typed outputs, prompt registry and budget checks (S3)`.

### Task 4: Eval hook (S3, M18)

**Files:**
- Create:
  - `packages/signal_core/src/signal_core/ai_gateway/evals.py`
  - `packages/signal_core/evals/canary_extract/v1.jsonl` (5 cases)
  - `packages/signal_core/tests/test_evals.py`
- Modify: root `pyproject.toml`, adding the script `ai-eval = "signal_core.ai_gateway.evals:main"`

**Interfaces:**
- `EvalCase(id: str, variables: dict[str, str], expected: dict[str, Any])`
- `EvalReport(prompt: str, version: int, passed: int, failed: int, failures: list[tuple[str, str]])`
- `async def run_eval(gateway: AiGateway, prompt: Prompt, output: type[BaseModel], cases: list[EvalCase]) -> EvalReport`
  - A case passes when every key in `expected` equals the same key in the output.
- `main()`:
  - CLI: `uv run ai-eval canary_extract@1 [--fake]`.
  - Exits 1 on any failure.
  - Spends real budget under a platform `CallContext` unless `--fake` is passed.

Building and checking it:
- [ ] **Step 1:** Write the failing test: `test_eval_counts_pass_and_fail` runs with a `FakeProvider` that gets 4 of 5 cases right and asserts `passed == 4` and that the failure names the case id.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run `uv run pytest packages/signal_core/tests/test_evals.py -v && uv run ai-eval canary_extract@1 --fake`. Expected: the test passes and the CLI prints `5/5 passed` (the fake echoes the expected values).
- [ ] **Step 4:** Commit `feat(ai): evaluation hook for prompt versions`.

### Task 5: Worker wiring — gateway factory, health, resume approved work

**Files:**
- Create: `packages/signal_core/src/signal_core/ai_gateway/factory.py`
- Modify:
  - `services/worker/src/worker/app.py`
  - `services/worker/src/worker/main.py`
  - `services/worker/tests/test_app.py` (or the existing worker test file)

**Interfaces:**
- `def build_gateway(settings: Settings, pool: AsyncConnectionPool) -> AiGateway`
  - Passes `chat=None` when `anthropic_api_key` is unset, and `embedder=None` when `voyage_api_key` is unset.
- Worker jobs (all behind `job_type_config` kill switches; seed the rows in a migration):
  - **`system.ai_status`**, cron every 10 minutes:
    - writes `app.record_health('ai_gateway', status, detail)`;
    - status is `ok` when both are configured, otherwise `degraded`;
    - detail is `{"chat": bool, "embed": bool}`.
  - **`budget.resume`**, cron every minute:
    - selects `approved` approvals with `resumed_at is null`;
    - defers `job_type` with `job_args` plus `{"approval_id": id}` and sets `resumed_at` in the same transaction;
    - `queueing_lock = approval_id`.
- `PgBudgetGuard.reserve` passes `ctx.job_args.get("approval_id")` as `p_approval`.

Building and checking it:
- [ ] **Step 1: Write failing tests:**
  - `test_ai_status_degraded_without_keys`
  - `test_budget_resume_defers_once`: two cron ticks still give one deferral.
  - `test_approved_job_not_paused_again`
  - `test_concurrent_reserves_cannot_both_overspend`: marked `@pytest.mark.db` and skipped without `DATABASE_URL_SESSION`. It uses a throwaway workspace with a cap of 10, fires two `asyncio.gather` reserves of 6, asserts exactly one `ok`, then cleans up.
- [ ] **Step 2:** Implement.
- [ ] **Step 3:** Run `uv run pytest services/worker -v`. Expected: the tests pass.
- [ ] **Step 4:** Commit `feat(worker): AI status health and budget approval resume`.

### Task 6: Settings → Usage and budgets (web)

**Files:**
- Create:
  - `apps/web/src/app/(app)/settings/usage.tsx`
  - `apps/web/src/app/actions/budgets.ts`
- Modify: `apps/web/src/app/(app)/settings/page.tsx`, adding a second `<Suspense>` section under Members.

**Interfaces:**
- Server actions, following the `ActionState` pattern in `app/actions/access.ts`:
  - `setBudget(prev, formData)` calls `rpc('set_budget')`.
  - `decideApproval(prev, formData)` calls `rpc('decide_budget_approval')`.
- Sections, using `@signal/ui` components and the `NotConfigured` card:
  1. **AI providers:** reads the latest `health_check` where `component = 'ai_gateway'`. When `chat`/`embed` is false it shows `<NotConfigured name="Claude (extract, judge, write)" setup={["Add ANTHROPIC_API_KEY to the environment and restart the worker."]}/>`, and the same for Voyage.
  2. **Spend:** today's and this month's total per workspace from `usage_ledger`, against the caps. Every figure links to the ledger rows behind it (a simple table filtered by period).
  3. **Budgets:** workspace day/month caps form, Admin+ only, read-only for others.
  4. **Waiting for approval:** pending approvals showing job type, estimate, reason and the cap that stopped it, with Approve and Reject buttons. The buttons appear only for roles that may decide that amount, so they mirror the SQL rule; the SQL remains the authority.

Building and checking it:
- [ ] **Step 1:** Implement the screens, then run `pnpm --filter web typecheck && pnpm --filter web lint && pnpm --filter web build`. Expected: all pass.
- [ ] **Step 2:** Commit `feat(web): Settings usage, budgets and approvals`.

### Task 7: Verify and apply

- [ ] **Step 1:** Run the full check suite: `pnpm codegen:check && pnpm lint && pnpm typecheck && pnpm test && pnpm db:check && pnpm build && pnpm py:check`. Expected: all green.
- [ ] **Step 2:** Apply migration 0012 to the dev project: run `supabase db push --db-url "$DATABASE_URL_SESSION" --dry-run`, then the push itself. Verify:
  - `model_price` has the 5 rows;
  - RLS is on for all 4 new tables;
  - clients have no write grants.
- [ ] **Step 3:** Run the worker locally for 2 minutes. Expected:
  - a `health_check` row `ai_gateway` that is `degraded` with `{"chat": false, "embed": false}` (no keys yet);
  - `budget.resume` runs without error.
- [ ] **Step 4:** Browser at `/login` and `/design`: no regressions. Settings stays untestable until sign-in exists; say so in the report.
