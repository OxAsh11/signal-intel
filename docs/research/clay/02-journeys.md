# Clay study — Phase 2: Journeys

*Run 2026-11-18 in our Clay workspace with the FlytBase test case (drone inspection buyers in India). Raw evidence:
[raw/phase2-notes.md](raw/phase2-notes.md). Screenshots: [img/phase2/](img/phase2/).
**[V]** = verified by running it and reading Clay's API. **[I]** = inferred. Unmarked statements in this doc are [V].*

**Cost of this phase:** about 65.5 data credits and 94 actions. Balance: 934 / 1,000 credits and 4,905 / 5,000
actions.

## The end-to-end journey we ran

```
Saved search (45 cos) ─► Save to table + pick enrichments ─► 10-row test run ─► company table (54 rows)
        │                                                                     │ waterfalls: news, jobs
        │                                                                     │ AI column: ICP research
        ▼                                                                     ▼
Find people "at these companies" (AI chat) ─► people table (76) ─► work-email waterfall (7 providers)
        │                                         │
        │      company row auto-triggers people   │ lookup back to company row
        ▼                                         ▼
Claygent "Score Account Fit" as a column ─► fit tier + confidence + reasoning
        │
        ▼
Message column: subject/body from columns ─► Export ─► import into Audiences (match + map) ─► sequence draft
                                                                                      (stop: mailbox needed)
```

## J1 — Search → table → enrich
Screens: [01](img/phase2/01-save-search-enrich-picker.jpg), [02](img/phase2/02-company-table-created.jpg),
[03](img/phase2/03-waterfall-cells-ai-running.jpg).

1. Open the saved search ("View saved searches" → pick it). The filters and preview come back exactly as they were.
2. **Continue** opens a short in-place form:
   - how many rows to save;
   - what to do with them (save to a new table);
   - **"Enrich to add more data?"**, a pre-ticked shortlist with the cost per row of each item: Enrich company 0.5
     (locked on), Research company ICP with AI 0.1 (ticked by default), News, Latest Funding, Job Openings,
     Competitors.
3. **Save** makes two calls:
   - `search-dsl/send-to-table` creates the workbook and table;
   - a server-side **"wizard"** turns each ticked enrichment into one or more columns.
4. The workbook and table are **named automatically from the search** ("Indian Energy Companies, Drone Inspections,
   201-5,000 Employees").
5. **Only the first 10 rows run.** This is deliberate: a test run that keeps spending low. The other rows wait until
   you run them. The preview said ~45 companies; the table actually has 54.

**Friction we hit:**
- The preview count didn't match the rows saved.
- The 10-row limit isn't explained anywhere on the table. Rows 11+ just look empty. In the people wizard the button
  says it plainly ("Save and run 10 rows").
- The AI column ran for about 8 minutes, showing only rotating words ("Refining…", "Sculpting…") and no progress
  or ETA.

**What made it work well:** you're never asked to build columns yourself. Picking outcomes ("News", "Job Openings")
creates the whole provider chain for you.

## J2 — Companies → people
Screens: [05](img/phase2/05-find-people-scoped.jpg)–[09](img/phase2/09-people-table.jpg).

1. Tools → Import → "Find people at these companies" opens People search **already scoped to this table**: "50 valid
   rows (out of 54), match companies using Company domain".
2. The plain-English request ("senior people who own inspection / maintenance / operations / HSE / drone programs")
   became a seniority filter plus a semantic job-title match. That gave **68 people**.
   - The query references the live table: `clay.filter_to_companies(@table("t_…:gv_…:f_domain:domain"))`.
3. The enrich-people wizard shows a running total as you tick items ("~1.2 credits / ~3 actions per row"), then
   "Save and run 10 rows".
4. The people table lands in the **same workbook**, as a second tab, with 76 rows.

**Chaining (two links are created automatically):**
- **Company → people:** the company table gets a `trigger-find-people-source` column with auto-update on. A new
  company row automatically runs the people search for that company.
- **People → company:** each person row has a `lookup-company-in-other-table` column back to its company. In the
  email editor, the "/" menu then offers "Company Table Data — 15 items".

**Quality problem:** matching people by domain pulled in **global Fugro staff** (Cairo, Oslo, Brazil…) for "Fugro
India". The company's country isn't carried over to its people. For us, the account is the unit (site and
country), not just the domain.

## Waterfalls — the full logic
Screen: [17](img/phase2/17-email-waterfall-result.jpg).

A waterfall is made of three things:
1. **Provider columns**, one action column per provider, in a fixed order.
2. **Run conditions.** Provider *k* has an automatic run condition: *"every earlier provider came back empty"*,
   written as `!!([null,'',false].includes((pX && pX?.count) ?? null))` joined with AND.
3. **A merge column** with `formulaType: "waterfall"`: an ordered list of formulas where the first non-empty one wins.

**Work email** has 7 finders: LeadMagic → Findymail → Prospeo → Dropcontact → Clay → Datagma → Wiza.
- Each finder is paired with a **validator column** that runs only if that finder returned an email that isn't
  already verified.
- On the 10 test rows:
  - LeadMagic found 6;
  - Findymail found 1;
  - Wiza, the last provider, found 2 more;
  - providers after a hit showed "Run condition not met", so they cost nothing.
- **Result: 9/10 rows have an email, and spend happened only where it was needed.**

The UI groups the providers under a collapsible "Waterfall «" header. Each cell shows the provider icon,
✓ "10 Results Found" or ✗ "No email found".

## J3/J4 — Score accounts with an agent
Screens: [10](img/phase2/10-add-agent-to-table.jpg)–[12](img/phase2/12-agent-column-config.jpg),
[23](img/phase2/23-claygent-business-context.jpg).

**Adding the agent:**
1. Search "Score Account Fit" in Tools, then "Add agent to table".
2. **All 4 inputs were auto-mapped** to columns by name and type. There's also an "AI suggest mappings" button.
3. Each output (fitTier, confidence, reasoning) can become its own column.
4. **Save ▾ shows the cost of each choice:** "run 10 rows (~10 actions, ~19 credits)", "run 50 in view (~50 / ~95)"
   or "don't run".

**First run, with no business context:** all 10 companies scored "**Moderate Fit**". The agent had no ICP to judge
against.

**Second run, after "Generate business context from flytbase.com"** (about 60 s, accurate) and switching the agent
to "Access all business context":
- The reasoning now cites FlytBase's ICP ("meets the scale threshold (2,415 employees)") and confidence went from
  Low to High/Medium.
- **But the tier stayed "Moderate Fit" for every row.**

**Logic verified:**
- Saving an agent creates a **draft version**; Deploy publishes it. The table column points at the agent by id with
  **no version pinned**, so new runs use the latest saved draft.
- Changing the agent or the business context **does not mark old results stale**. Old and new answers sit side by
  side with nothing to tell them apart.
- AI cells carry their own `confidence` in metadata.

## J5 — Write the email from columns (Message column)
Screens: [13](img/phase2/13-message-drafting.jpg)–[16](img/phase2/16-message-preview.jpg).
- Add column → **Message**. The editor sits next to a **live preview of 5 real rows** that updates as you type.
- The **`/` menu** in Subject or Body offers:
  - **Clean variable**: a column, plus a case format, plus removing emojis, plus a **fallback** text;
  - **AI snippet**;
  - **Conditional snippet**;
  - **Spintax**;
  - any column, including nested JSON paths and the linked company's fields. The picker shows each row's actual
    value.
- **Storage:** a *field group* "New email" with Subject and Body columns. Each is a list of segments: `Formula`
  (text + `{{column}}`), `CleanVariable`, etc. These compute instantly for every row at no credit cost.
- **Not observed:** the AI snippet editor here. The Grammarly browser extension blocked it. It does work in the
  Sequencer (Phase 1).

## J6 — Push to a sequence
Screens: [18](img/phase2/18-export-to-sequencer.jpg)–[22](img/phase2/22-sequence-from-table.jpg).
1. **Export → "Send to external sequencer"**:
   - **17 targets**: Clay, Smartlead, Instantly, Lemlist, Outreach, Salesloft, Apollo, HeyReach, …
   - the external ones need your own account.
2. Choosing **Clay** asks "Use the new sequencer?" (A/B testing, Sculptor, spam check) → **Create new**.
3. The new path **imports the table into Audiences (People) first**:
   - **record matching**: "When [Email / LinkedIn URL / Phone / External ID / Twitter] in [column] equals → merge".
     It runs only on first import, and *"merges cannot be unmerged"*.
   - **field mapping** with Auto-map.
   - **Auto-map got Email wrong.** It picked one provider's raw output, not the merged "Work email". I fixed it by
     hand.
4. A sequence draft was created: lead segment with **18 leads**, plus exclusions (other audiences, cooldown,
   blocklist).
   - **The Message column did not carry over.** The new sequencer has its own composer, and the table Message
     column is the legacy route.
5. **I stopped here.** Sender accounts and Launch need your Gmail/Outlook sign-in.

## J7 — First run and the recurring loop
- **First run** (for a brand-new account) wasn't reproduced. The entry points we observed:
  - Home: greeting, an AI box and 5 start cards;
  - AI context: "Generate from domain";
  - empty states ("Get started with Audiences").

  **Generating the business context is the single most valuable first step.** Without it, AI scoring is flat
  (J3/J4).
- **The signal loop** ([Phase 1 §6](01-feature-inventory.md)):
  1. a scheduled source column re-polls and appends rows;
  2. Auto-run ("when new records are added") fills the columns on each new row;
  3. the company → people trigger fans out to contacts.

## What is sticky, and where users get stuck

| Sticky (worth adopting) | Stuck / weak (our opening) |
|---|---|
| Outcomes, not plumbing: tick "Work email" and a 7-provider chain appears | The 10-row test run isn't explained on the table; rows look broken |
| Cost shown before every choice (per row, per run, totals in Save ▾) | AI columns: minutes with no progress or ETA |
| AI turns plain English into editable filters or queries, and says what it approximated | Domain-based people matching ignores the country (global staff for "Fugro India") |
| Live previews everywhere (search, signal, email with 5 real rows) | Scores without an ICP are flat; even with an ICP, the 3-tier output hid the differences |
| Tables chain automatically (company → people → back-link) | Changing the agent or context leaves old results looking current (no stale flag) |
| Waterfalls only pay until the first hit, and validate emails | Auto-map picked the wrong email column; Grammarly broke the editor |
| One business context reused by every AI feature | Two composers (table Message vs new Sequencer) with no migration |

## Do I now have every feature's complete logic?
Not every feature. Here's the coverage after Phases 1–2:

| Area | Logic status |
|---|---|
| Columns, references, dependencies, formulas (JS expressions) | **Verified** |
| Cell statuses and how they change during a run | **Verified** (SUCCESS, NO_DATA, RUN_CONDITION_NOT_MET, AWAITING_CALLBACK, ERROR) |
| Waterfalls (order, conditions, merge, validation) | **Verified** |
| Run controls (10-row test, run N / view / force, auto-run, keep existing) | **Verified** (rules read from settings + observed) |
| Search query language, AI search, people-at-companies scoping | **Verified** |
| Table chaining (trigger people source, lookup back) | **Verified** |
| Claygent build, versioning, deploy, context use | **Verified**; stale behaviour verified (none) |
| Message composer storage | **Verified**; AI snippet editor in table not observed |
| Audience import (matching, mapping) → sequence | **Verified** up to sending |
| Signals | Setup **verified**; a scheduled re-poll firing is **[I]** (next run Dec 18) |
| Credits / actions accounting | Totals **verified**; per-run ledger **[I]** (Phase 3) |
| Sending, replies, sequence analytics, CRM sync, workflows execution, dedupe | **Not observed**. Needs a mailbox or CRM connection, or is Phase 3 |

## Leftovers created in Clay (all private, nothing sends)
- Workbook "Indian Energy Companies…" (company and people tables, agent / message columns).
- Saved search; Custom signal workbook.
- Claygent "Score Account Fit" (v2 draft).
- Draft sequences ×2; draft workflow.
- 18 people in Audiences, with a LinkedIn-URL matching rule.
- Business context for flytbase.com.
