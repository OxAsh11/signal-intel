# Deploying Signal Intel

Three systems, wired once: **GitHub** (code and CI), **Supabase** (database, auth, storage), **Railway** (the `web` and `worker` services). Secrets live only in Railway variables, GitHub secrets and your local `.env` files. Never put them in the repo or in chat.

## 1. GitHub

1. Create an empty **private** repo (for example `signal-intel`). Don't add a README or licence.
2. From this folder:
   ```bash
   git remote add origin https://github.com/<you>/signal-intel.git
   ```
   ```bash
   git push -u origin main
   ```
3. Settings → Environments: create `development` and `production`. On `production`, add yourself as a **required reviewer** so prod migrations wait for approval.
4. Settings → Secrets and variables → Actions → add:

| Secret | Where to find it |
|---|---|
| `SUPABASE_ACCESS_TOKEN` | supabase.com → Account → Access Tokens |
| `SUPABASE_PROJECT_REF_DEV` / `SUPABASE_PROJECT_REF_PROD` | Project Settings → General → Reference ID |
| `SUPABASE_DB_PASSWORD_DEV` / `SUPABASE_DB_PASSWORD_PROD` | The password chosen when the project was created |

## 2. Supabase (two projects)

1. Create `signal-intel-dev` and `signal-intel-prod` **in the same region you'll pick on Railway** (decide data residency first).
2. Authentication → Sign In / Providers:
   - **Google:** create an OAuth client in Google Cloud Console (Web application). Authorised redirect URI: `https://<project-ref>.supabase.co/auth/v1/callback`. Paste the client ID and secret into Supabase.
   - **Azure (Microsoft):** register an app in Microsoft Entra ID with the same redirect URI. Create a client secret and paste both into Supabase. Leave the tenant URL as `https://login.microsoftonline.com/common/v2.0` unless you restrict to one tenant.
3. Authentication → URL Configuration: set **Site URL** to the Railway web URL. Add `https://<web-domain>/auth/callback` and `http://localhost:3000/auth/callback` (dev project only) to **Redirect URLs**.
4. Migrations are applied by GitHub Actions (`migrate-dev` on every push to `main` that touches `supabase/migrations`, `migrate-prod` on a published release). For the very first run, trigger **Migrate dev** manually from the Actions tab.

## 3. Railway

1. New project → **Deploy from GitHub repo** → pick the repo. Create two services from the same repo. Railway no longer accepts a config file path (`railway.json` is deprecated), so set these on each service directly (Settings, or the `serviceInstanceUpdate` API). The values mirror `apps/web/railway.json` and `services/worker/railway.json`, which stay in the repo as the reference:

| Service | Dockerfile path | Health check | Restart policy | Region |
|---|---|---|---|---|
| `web` | `apps/web/Dockerfile` | `/api/health` (60 s) | on failure, 5 retries | same as Supabase or nearest |
| `worker` | `services/worker/Dockerfile` | `/healthz` (90 s) | always, 1 replica | same as `web` |

Copy the watch patterns from each `railway.json` so a change to one service doesn't rebuild the other. Don't add `--mount=type=cache` lines to the Dockerfiles: Railway rejects cache mounts without its own `s/<service-id>-` id prefix.

2. Create environments `staging` (deploys `main`) and `production` (deploys from release tags or a manual promote).
3. Variables per service (the names match `.env.example`):

| Variable | web | worker | Notes |
|---|:-:|:-:|---|
| `NEXT_PUBLIC_SUPABASE_URL` | ✓ | | Also used at build time |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | ✓ | | Also used at build time |
| `NEXT_PUBLIC_SITE_URL` | ✓ | | `https://<web-domain>` |
| `DATABASE_URL_SESSION` | | ✓ | Supabase → Connect → **Session pooler** (port 5432) |
| `ANTHROPIC_API_KEY` | | ✓ | Phase 1 onwards |
| `VOYAGE_API_KEY` | | ✓ | Phase 1 onwards |
| `APP_ENV` | ✓ | ✓ | `staging` or `production` |
| `LOG_LEVEL` | | ✓ | `info` |
| `SENTRY_DSN` | ✓ | ✓ | Optional |

Railway sets `PORT` itself. Health checks: web `GET /api/health`, worker `GET /healthz`. The worker runs **one replica**: the outbox relay is safe with more (it uses `SKIP LOCKED`), but one is enough for Phase 0.

4. Settings → Networking: generate a domain for `web` only. The worker needs no public domain.

## 4. Local development (no Docker)

```bash
pnpm install
```
```bash
uv sync
```
Both the web app and the worker read **one file: `.env` at the repo root** (git-ignored). Fill it by hand from `.env.example`, or pull the URL and keys with the script below, which never prints them:

```bash
pnpm exec supabase login
```
```bash
pnpm env:pull <dev-project-ref>
```
Then add `DATABASE_URL_SESSION` to `.env` by hand (Supabase → Connect → Session pooler; it contains your database password), and run:

```bash
pnpm dev
```
```bash
uv run worker
```

Checks you can run any time, all offline:

```bash
pnpm codegen:check && pnpm lint && pnpm typecheck && pnpm test && pnpm db:check && pnpm build
```
```bash
pnpm py:check
```

`pnpm db:check` applies every migration to an in-memory Postgres (PGlite) and checks tenant isolation, write paths, audit immutability and enum drift. The full pgTAP suite runs against real Supabase in CI.

## AI gateway, evals and budgets

- **Keys and models (worker):** `ANTHROPIC_API_KEY` (Claude), `VOYAGE_API_KEY` (embeddings), and the model names `AI_MODEL_EXTRACT`, `AI_MODEL_JUDGE`, `AI_MODEL_PREJUDGE`, `AI_MODEL_WRITE`, `AI_MODEL_EMBED` (defaults are in `.env.example`). Without a key the matching capability is off and Settings shows "Not set up yet"; jobs that need it end without retrying.
- **Eval CLI:** `uv run ai-eval <name>@<version> [--fake] [--workspace <uuid>]`. `--fake` uses a deterministic provider and spends nothing. Without it, `--workspace` names the workspace to charge.
- **Database tests:** `uv run pytest -m db` runs against `DATABASE_URL_SESSION` (the shared dev database) and leaves its test rows there.
- **Job and approval contract:** when a call would pass a budget cap, the job pauses (it ends cleanly and is not retried) and an approval is raised. A paused job resumes only when `CallContext.job_type` is a registered task name, `job_args` are exactly that task's keyword arguments, and the task accepts an `approval_id` keyword argument. An approval covers one call's estimate, not a whole job. Eval CLI pauses are not resumable.
- **Housekeeping:** the `budget.release_stale` job (every 5 minutes) releases reservations older than 30 minutes and expires approvals pending over 7 days.

## Rules

- Schema changes only through new files in `supabase/migrations/`, merged through a PR. CI applies them. Never edit an applied migration.
- Changing a canonical value list means editing `packages/contracts/schema/enums.json`, running `pnpm codegen`, and adding a migration (`alter type … add value`).
- Upgrading Procrastinate means adding its version migrations from `procrastinate/sql/migrations/` as new Supabase migrations in the `procrastinate` schema.
