# Operating-table redesign: roadmap of sub-plans

**Spec:** [2026-11-18-operating-table-redesign-design.md](../specs/2026-11-18-operating-table-redesign-design.md)
(approved 2026-11-18).

The spec covers several independent subsystems. Each one below gets its own plan, and each plan ships working,
tested software as one PR. The order follows spec §11: the table is only as good as the data in its cells.

| # | Plan | Delivers | Engine gaps | Depends on |
|---|---|---|---|---|
| P1 | [Engine integrity](2026-11-18-p1-engine-integrity.md) | Typed claim values; one transaction per record and idempotent claims; a Brief-aware relevance judge; exact-name repeat sightings attach; verdicts recompute on `claim.created` / `entity.merged`; Review loads; live Discovery yield; no "def v1" | #5 #6 #4 #2 #3 #12 #13 #14 | — |
| P2 | Brief → columns and rubric | Intake drafts a short name, predicates with roles, ICP lists and evidence rules; deterministic rubric verdict with the AI only for open cases (reasoning stored); scoring by predicate role; backfill when a predicate is added | #1 #15 #8 #11 #7 | P1 |
| P3 | Column runtime | `predicate` fill-method fields, `predicate_run`, `saved_view`, shared filter syntax, a formula language (safe subset), a run executor for `extract` and `formula`, staleness by `inputs_hash`, cost estimates | — | P2 |
| P4 | Shell and Home | New sidebar (Find / Work / Engage / Measure), Brief lens (defaults to the last Brief), route consolidation, **Email engine and CRM placeholder pages**, the new Home (see the mockup) | — | P1 (P3 for stale/errored counts) |
| P5 | Operating table UI | Cell states, cell panel, Add column catalogue (extract, formula, AI research), run controls with cost, views and presets, board and map layouts, export | — | P3, P4 |
| P6 | AI research columns, waterfalls, People | `research` fill method; provider waterfall (LeadMagic → Findymail → Prospeo, validated; one firmographic provider); People menu and table; **every person linked to an account** (unlinked → Review); find people per account; location-mismatch flag | #9 #10 (provider-side only) | P5 |
| P7 | Brief flow | Generate from domain, pilot calibration (Right / Wrong), version impact preview (what goes stale, re-run cost) | — | P2, P3 |
| P8 | Reports and Settings | Coverage, cost by Brief / column / provider, source yield; Settings sections Providers, Budget & usage, Health, Data | — | P3, P6 |

**Out of this roadmap until their study round:** the Email engine (spec §6) and the CRM (spec §7). P4 ships their
menu items as placeholders only.

Each plan is written when the one before it is merged, so it starts from the real code rather than from guesses.
