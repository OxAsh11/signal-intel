# Signal Intel

Signal Intel turns scattered public signals into evidence-backed, researched, reachable accounts. It answers three questions: which companies match my hypothesis, how sure are we, and how do we reach them.

- Product: [docs/PRD.md](docs/PRD.md)
- Architecture and build approach: [docs/superpowers/specs/2026-11-06-platform-approach-design.md](docs/superpowers/specs/2026-11-06-platform-approach-design.md)
- Current plan: [docs/superpowers/plans/2026-11-06-phase0-environment-scaffold.md](docs/superpowers/plans/2026-11-06-phase0-environment-scaffold.md)
- Deploying (GitHub, Supabase, Railway) and local setup: [docs/deploy.md](docs/deploy.md)
- Design system: `design/signal-intel-ds/` (source of the tokens) and `/design` in the running app

## Layout

| Path | What |
|---|---|
| `apps/web` | Next.js 16 app: the screens, sign-in, user-initiated writes through audited SQL functions |
| `services/worker` | Python worker: Procrastinate jobs and schedules, outbox relay, `/healthz` |
| `packages/signal_core` | Python core: settings, logging with redaction, automation gate, job kill switches, outbox |
| `packages/ui` | Design-system tokens (generated) and typed React components |
| `packages/contracts` | Canonical enums (PRD Appendix A), generated into TypeScript and Python |
| `supabase/` | SQL migrations (the only source of the schema), pgTAP tests, seed |
| `scripts/check-migrations.mjs` | Offline migration and RLS checks on PGlite |

## Quick start

```bash
pnpm install
```
```bash
uv sync
```
```bash
pnpm dev
```

Full checks:

```bash
pnpm codegen:check && pnpm lint && pnpm typecheck && pnpm test && pnpm db:check && pnpm build && pnpm py:check
```

AI keys, `uv run ai-eval`, `pytest -m db` and the job/approval contract are described in [docs/deploy.md](docs/deploy.md#ai-gateway-evals-and-budgets).
