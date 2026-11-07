# Phase 1 — Intelligence Core: sub-phase map

**Spec:** `docs/superpowers/specs/2026-11-06-platform-approach-design.md` §6 (Phase 1) · PRD S2, S3, S5, S6, S9, M1–M5

Phase 1 is too large for one plan: five subsystems, each with its own tests. It is split like Phase 0 (0 / 0b / 0c). Each sub-phase ships working, tested software on its own, and gets a detailed plan when we reach it. Only **1a** is planned in detail now.

| Sub-phase | Builds | Done when | Depends on |
|---|---|---|---|
| **1a — AI gateway and metering** | S3 gateway (capabilities, Anthropic + Voyage + fake adapters, prompt registry, typed validation, predicate-registry validator, eval hook), S6 usage ledger, budgets, approvals, cost preview; Settings → Usage and budgets | Every AI call is budget-checked before it runs and ledgered after; invalid output is rejected; the fake adapter makes all of it testable with no keys | Phase 0 |
| **1b — Brief Studio (M1, without pilot)** | Intake (AI drafts a Brief from plain language), editable chips, required-field check, workspace predicates and "add a column", exclusion list, states Draft → Active ⇄ Paused → Archived, version compare | A Brief can't leave Draft without required fields; any two versions can be diffed | 1a |
| **1c — Connectors and radars (S2, M2)** | Connector framework + fetch guard (allowlist, no private IPs, robots), Storage payloads, CSV upload, company websites, news, job posts; radars, query generation, pre-filter, cursors, yield; Discovery screen | Re-fetch never duplicates; every record shows its status; a silent radar opens a health incident | 1a, 1b |
| **1d — Extraction and identity (M3, M4)** | Relevance judge, structured extraction with quotes, backfill mode; alias-priority + fuzzy + AI tie-break matching, duplicate sweep, merge/unmerge; Review screen (identity queue) | No claim without a source record or outside its type; every merge can be undone; candidates side by side | 1a, 1c |
| **1e — Derivation, verdicts, pilot (S5, M5, S9, M1 pilot)** | Derivation graph + shadow mode, verdict rubric engine, verdict review queue and weekly sample, pilot/calibrate, Narrow/Redefine/Expand previews, full-text + embedding indexer | **Gate B** (below) | 1b, 1d |

**Gate B:** on both test Briefs the pilot hits the verdict accuracy target; every verdict clicks through to quotes and sources; re-fetch never duplicates; Narrow/Redefine/Expand previews match actual results.

## Decisions this map takes (change any of them before its sub-phase starts)

1. **AI keys are optional until you add them.** Same rule as OAuth: with no Anthropic/Voyage key the gateway runs the **fake adapter** in tests and reports "Not set up yet" in Settings. Real calls start the moment a key is in the environment.
2. **Bring-your-own-key:** the gateway resolves keys per workspace from day one (1a), but storing a workspace key in Vault arrives with Settings → Integrations in 1c. Until then, everyone uses the platform key.
3. **News source (1c):** GDELT DOC API plus RSS feeds. Both are free and need no account.
4. **Job-post source (1c):** the public Greenhouse, Lever and Ashby job-board APIs (free, no login), plus careers pages through the website connector.
5. **Gate B accuracy target (PRD open decision #6):** proposed **≥ 85% of pilot verdicts marked right on calibration, per Brief**. Confirm or change it before 1e.
6. **Test industries (open decision #7):** the two seeded ones: CAD-tool engineering firms that are hiring, and cold-storage warehouse operators.
