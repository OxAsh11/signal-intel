# Clay study — Phase 1: Feature inventory

*Captured 2026-11-18 from our own Clay workspace (1485570, Trial plan). Raw evidence:
[raw/phase1-notes.md](raw/phase1-notes.md) (every screen's text plus the API calls it makes). Screenshots:
[img/phase1/](img/phase1/). We record behaviour and data shapes only; no Clay code, copy or assets go into our product.*

## How this was captured
- I used Claude in Chrome in your own logged-in browser. REA's `npx` install was blocked by the auto-mode safety check.
- On each screen I recorded the visible text and the `api.clay.com/v3` calls behind it, using an in-page recorder
  and the browser's resource timings. For the core objects I ran read-only GETs.
- Credits spent: 0.5 data credits and 1 action. Balance is now 999.5 / 1,000 credits and 4,999 / 5,000 actions.
  The Claygent test run and the AI search drew from the same balances.
- Objects the study created in Clay: a saved search "Study - India drone inspection targets", a Custom signal
  workbook (Google News, monthly), the Claygent "Score Account Fit", a draft "Untitled sequence" and a draft
  "Untitled workflow". All can be deleted after the study.

## 1. Navigation (information architecture)

| Group | Item | What it is |
|---|---|---|
| — | **Home** | Greeting, an "Ask me anything / describe what you'd like to do" AI box, 5 start cards (Find leads, Import data, Build a segment, Start outreach, Try a template), and the **All Files** list of workbooks |
| — | **Find leads** (flyout) | Search People / Companies / Jobs / Local businesses / Company lookalikes; "Create a workflow" (People, Companies) |
| Audiences | **People**, **Companies** | Canonical records: All · Segments · **Data hub** · Reporting |
| Orchestration | **Signals** | Scheduled monitors (Topic intent, Job change, New hire, Job posting, Promotion, News & fundraising, Web intent, Custom) |
| | Ads | Paid add-on (Upgrade) |
| | **Sequencer** | Built-in email sequences (Smartlead underneath) |
| | **Claygents** | Reusable AI agents |
| | **Functions** | Reusable column logic ("subroutines" in the API) |
| | **Workflows** (Beta) | Node-graph pipelines (the newer model) |
| | Agent users (Beta) | MCP access for ChatGPT / Claude / Cursor, with credit limits per user |
| | API and CLI | Keys, webhooks, routine runs, search quota (0 / 10,000) |
| — | Exports, Trash, Settings, **AI context** | AI context = "My business context": company description, ICP, buyer personas, with "Generate from domain" |

Global top bar on every page: Upgrade · tools · **credit meter** · help · notifications.
Screens: [01-table](img/phase1/01-table.jpg), [12-find-leads-menu](img/phase1/12-find-leads-menu.jpg).

**Reading:** the nav follows the journey: *find → hold records (Audiences) → automate (Signals, Workflows, Claygents)
→ send (Sequencer)*. Business context lives in one place, and every AI feature reads from it.

## 2. The table (workbook) — the core surface
A **workbook** contains one or more **tables**. Its **Overview** is a canvas that shows tables as nodes, each with
rows, columns, cost per row and a run button, plus a Create palette of sources and signals
([08](img/phase1/08-workbook-overview.jpg)).

Inside a table ([01](img/phase1/01-table.jpg)):
- **Toolbar:** Auto-run toggle, view picker, "3/5 columns", "10/10 rows", filter, sort, search, Sandbox Mode,
  Sculptor, Tools.
- **Under each header:** a fill row showing completion per column (✓ 100%).
- **Footer:** "+ Add [10] more rows"; tabs Overview | tables | + Add; and a status bar ("100% of table completed",
  Stop, History).
- **Action columns:** a ▷ run button sits in the header. Cells show a logo plus a short preview. Opening a cell
  shows the full result as typed chips, and any key can be pulled out into its own column
  ([07](img/phase1/07-cell-details.jpg)).
  *Gotcha:* pressing Enter on a selected action cell **re-runs it**.

**Add column** menu ([02](img/phase1/02-add-column-menu.jpg)), in this order:
- Built-in AI and logic: Add enrichment · Use AI · Functions · Message · Waterfall · Formula · Merge columns.
- Plain types: Text · Number · Currency · Date · URL · Email · Image from URL · Checkbox · Select · Multi-select ·
  Assigned to.

**Column header menu** ([06](img/phase1/06-column-menu.jpg)):
- Edit, insert, rename, description and colour.
- **Go to parent column**, **Used in…** (dependency view), **Run column** ▸ (choose N rows / run empty or out-of-date
  / force all).
- Duplicate, **Save as function**, Filter, Pin, Hide, Delete.

**History** ([09](img/phase1/09-usage-history.jpg)):
- Usage history grouped by time, column or run. Costs are split into **data credits** and **actions**.
- Row deduplication.
- Change log.

**Sandbox Mode** ([11](img/phase1/11-sandbox.jpg)) makes a shadow table. You pick test rows, build there, use
"Review changes", then publish to the full table.

**Sculptor** ([10](img/phase1/10-sculptor-table.jpg)) is an AI assistant docked in the table.
- It suggests next steps from the table's own data (add tech stack, analyse distribution, find decision makers).
- You can right-click an AI cell to add it to the chat's context.

## 3. The catalogue (Tools panel)
"Add enrichment" opens the **Tools** panel ([03](img/phase1/03-tools-panel.jpg)), with tabs
*For you · Import · Signals · Enrich · Functions · Export*. Every item shows its **cost per row** and its kind:
Enrichment, Waterfall ("+6 providers, ~3.3/row"), Table function, Transformation.

| Tab | Content |
|---|---|
| Import | Find more companies (into this table) · Find people / jobs at these companies (new linked table) · CSV · Webhook |
| Signals | The signal types, as table sources |
| Enrich | Most popular · Browse by provider · Connections · Favorites · Suggested; waterfalls for revenue, headcount, techstack, funding |
| Functions | Clay-managed functions, e.g. Work Email 1.1/row, Person Job Title 2/row |
| Export | Download CSV · Send table data · Invoke workflow · Export to CRMs · Clay sequence · External sequencer · Databases |

The full catalogue comes from `GET /v3/actions?workspaceId=…` (36 MB):
- **1,576 actions from 264 providers.** By type: Enrich Data 1,111, Send Data 170, Add Data 117. By entity:
  People 709, Company 697.
- 419 actions run on Clay-managed keys; the rest need your own account with the provider.
- Each action declares an input schema, an output schema with semantic types, an auth type, rate limits and
  **pricing** (data credits + 1 action, with **refund when there's no data**).
- **Connections** (Settings): 132 provider accounts, each showing credit spend over 30 days and how many resources
  use it.

## 4. AI column ("Use AI")
Screens: [04](img/phase1/04-use-ai-generate.jpg), [05](img/phase1/05-use-ai-configure.jpg).
- **Generate mode:** "What would you like AI to do?" You describe the task in plain words; Clay writes the prompt
  and picks the settings.
- **Configure mode:**
  - Use case: Web research (Claygent, has internet) · Image generation · Create or modify content (no internet).
  - Model: Auto ~1.9/row, plus Compare models.
  - Prompt with `/` to insert columns (`{{Domain}}`).
  - **Define outputs:** Fields | JSON Schema | Text.
  - **Run settings:** Auto-run, *run condition* ("only run if this formula resolves to true"), and a delay of up
    to 600 s.
- Inline guard: "This column doesn't reference any other columns, so it will never run automatically."
- The cost per row shows next to Save. "Create Claygent" turns the column into a reusable agent.

## 5. Finding leads (search)
Screens: [13](img/phase1/13-find-companies.jpg), [14](img/phase1/14-find-companies-ai.jpg),
[15](img/phase1/15-find-companies-dsl.jpg), [16](img/phase1/16-find-people.jpg).
- **Companies:** a 48.6M index. **People:** a 300.8M index. Each has a live 50-row preview.
- A search session **is an AI chat** (`/chats/cc_…`). You type plain English and Sculptor builds a **visible filter
  tree**. It **names its approximations and asks you to confirm**. For example, "run drone inspections" became a
  semantic "similar to" match because the exact query returned nothing.
- **Query mode** shows the same search as a SQL-like DSL:
  `select from companies where locations.any(is_headquarters = true and country_name = "India") and company_size in
  (…) and industry in (…) and (products_and_services is_similar_to ("drone inspections") or description contains (…))`.
  It can be exported as an "Agent skill".
  - Test result: **45 Indian mining, oil & gas and renewables companies** for the FlytBase-style query.
- **62 filter keys per entity**, including semantic description, derived industries and revenue streams,
  technographics, lookalikes (KNN, similarity tier) and "previous entities" bitmaps (so you don't re-pull rows
  you've already pulled).
- Continue ▾: Save to Companies (Audience, up to 1M) · Save to new table (1,000 on our plan) · Save to existing
  table · Save to workflow. Searches can be saved and re-run.

## 6. Signals (the recurring loop)
Screens: [17](img/phase1/17-signal-step1.jpg)–[20](img/phase1/20-signal-table.jpg).

The Custom signal wizard has four steps:
1. **Source.** About 30 options:
   - social listening: posts, Reddit, YouTube, GitHub;
   - first-party data: Snowflake, Databricks, Gong, Mixpanel;
   - company sourcing: TrustRadius intent, Google Maps, Store Leads, Pitchbook;
   - generic: RSS, Google Search, Apify, Phantombuster, Airtable.

   Each shows its cost.
2. **Inputs**, with a **live preview of real results** (10 recent drone-inspection news articles).
3. **Frequency:** daily to quarterly.
4. **Optional enrichments**, e.g. Slack message, Salesforce/HubSpot lookup or update.

**Saving creates a workbook.** Its first column is the scheduled source ("Waiting for events…", next run date,
cost per result). New rows append, and the table's columns then run on each new row.
**A signal is a table that refills itself.**

## 7. Claygents (reusable agents)
Screens: [21](img/phase1/21-claygent-built.jpg), [22](img/phase1/22-claygent-output-deploy.jpg).
- **Picking a template doesn't open a form.** It hands Sculptor a written brief, and Sculptor builds the agent
  itself in about 40 s:
  - prompt (Context / Objective / Business Context);
  - input variables;
  - JSON output schema;
  - tools: web search, find contacts 0.5/run, business context;
  - **three generated test cases**;
  - then it auto-saves and lists "One manual step required".
- **Test** runs show the agent's steps live ("Calling Search Google — Query: …") and then the output as fields
  (`fitTier: Moderate Fit`, `reasoning`).
- **Deploy** goes to Tables (as a column) or Workflows (as a step).
- Agents are **versioned** (`currentVersion{variables, modelSettings, outputFormat, skills}`).

## 8. Sequencer (email)
Screens: [23](img/phase1/23-sequence-editor.jpg)–[25](img/phase1/25-sequence-ai-snippet.jpg).
- **Sending accounts:** buy managed mailboxes (Growth+), or bring your own Gmail or Outlook (OAuth) or SMTP. Your
  mailbox credentials are shared with **Smartlead**.
- **Audience:** a People segment, a table (labelled *Legacy*) or a workflow.
- **Editor:**
  - left: Lead setup · **AI context** (sequence goals, documents, workspace context) · Spam check · Sample data;
  - middle: steps with Subject and body;
  - right: Sculptor ("Suggest improvements to my email copy", "Why are leads not progressing").
- In the body, **`/` inserts lead fields or an "AI snippet"**. An AI snippet is an inline prompt that writes text
  per lead, with preview / accept.
  - The header shows the running cost per lead: one AI snippet took it to 2 actions + ~0.40 credits.
- A/B testing and spintax are built in.

## 9. Workflows (Beta) and Audiences — where Clay is heading
- **Workflows** ([26](img/phase1/26-workflow-builder.jpg)):
  - starts from a prompt ("What should we build today?") or manual building;
  - trigger types: Segment, Find leads, Webhook, Signal, Activity, CSV;
  - step types: Enrich · People and Companies · Flow · Transform · AI · Send and export · Code · Conditional ·
    Function · Claygent · Write to Audiences;
  - Graph | Runs, with a Table view of the same data.
- **Audiences Data hub** ([27](img/phase1/27-data-hub.jpg)):
  - one canonical Company / Person record;
  - a **field catalogue** showing each field's *fill rate*, *records covered* and *which enrichments fill it*;
  - "+ Add field"; tabs Fields · Workflows · Signals · Claygents.
- "Save enrichment data in Audiences" stores bought values on the record and reuses them until they expire,
  with an expiry window per action.
- In the Sequencer, tables are already labelled *Legacy*. **Clay is moving from "many spreadsheets" to "one
  record + field catalogue + pipelines".**

## 10. Mechanics worth copying (behaviour, not code)
1. **Columns are typed dependency nodes.** Inputs reference other columns (`{{field_id}}`). Each column stores
   the columns it depends on. A column with no dependency never auto-runs, and the UI warns about it.
2. **Every cell has a status:** has-not-run, queued, running, awaiting callback, retry, error, no results,
   run-condition-not-met, stale. Each one is filterable. Preset views ("Errored rows", "Fully enriched rows")
   are just filters on those statuses.
3. **Big result, small cell.** An action cell stores a whole JSON result and shows a preview. Any key becomes a
   derived column (`{{action}}.url`).
4. **Cost is visible before every run** (per row, per lead, per result, per run). Usage is split into data
   credits vs actions, and you get a refund when there's no data.
5. **AI-first input everywhere:** search, AI column, Claygent, workflow and sequence each start from a plain-English
   box, produce an editable structure (filter tree, prompt and schema, graph), and **say where they approximated**.
6. **Safe experimentation:** Sandbox shadow tables, test cases on agents, a 50-row search preview, a 10-row signal
   preview.
7. **One business context** (company, ICP, personas), referenced with `@` by every AI feature.

## 11. Early read for Signal Intel (details in Phase 4)
- Our `entity` / `predicate` / `claim` already maps to Clay's newest model (Audience record / Data-hub field /
  saved value), not just to their table. **Our Brief = their AI context**, but ours drives discovery as well.
- **Where Clay is thin, and we're strong:**
  - Clay cells keep provenance (run history) but no **evidence or confidence per value**.
  - Clay has no verdict or stage on an account.
  - Clay's signals bring in raw events; they don't judge relevance against a Brief.
- **Gaps on our side this exposes:**
  - no cell-status model or column dependencies;
  - no cost-before-run;
  - no AI-built filters or columns with stated approximations;
  - no sandbox / test rows;
  - no `/`-insert email composer;
  - no per-field coverage view.

## 12. Not yet captured (moves to Phases 2–3)
- **Phase 2 journeys** (needs runs):
  - save the 45 drone-inspection companies to a table;
  - add Find People;
  - add work email, an AI research column and the Score Account Fit Claygent;
  - compose the email from columns.
- **Mailbox:** connecting a mailbox to send needs your OAuth sign-in. I'll stop there.
- **Phase 3:** full payloads for workbook, run/job, function (subroutine), workflow, segment and the credit ledger.
- Not studied: Ads (paid add-on), template gallery, CRM import.
