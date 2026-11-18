# What we learned from Clay: the whole study in one file

*2026-11-18. A plain-language summary of the Clay study. The detail and the evidence (screenshots, captured API
responses) are in the phase files linked at the end. Nothing here has been built yet.*

---

## 1. Why we did this, and how

**Why.** Our product felt "very basic". Before redesigning it, we wanted to understand properly how the best tool
in this space works, and why users get value from it. That tool is Clay.

**How.** I used your own Clay workspace through your logged-in Chrome, and did four things:
1. Opened every screen.
2. Ran real jobs on our FlytBase test case (drone-inspection buyers in Indian energy).
3. Read the data Clay's own app loads behind each screen.
4. Took screenshots as evidence.

Cost: about 66 Clay credits and 95 actions, out of 1,000 and 5,000. No payment details were touched. Nothing was
sent to anyone.

**The stages, and how each one feeds the next:**

```
Stage 1  WHAT does Clay have?            → feature inventory   (the parts)
   │
Stage 2  HOW does a user use it?         → journeys            (the parts in motion)
   │
Stage 3  WHY does it behave that way?    → data model          (the machinery underneath)
   │
Stage 4  WHAT does it mean for us?       → mapping             (adopt / adapt / reject)
   │
Stage 5  WHAT do we build?               → new nav + page spec (the design)
```

Each stage explains the one before it:
- Stage 1 showed us a "Waterfall" button.
- Stage 2 showed it finding 9 of 10 emails while paying only where needed.
- Stage 3 showed *why*: each provider column only runs if all earlier ones came back empty.
- Stage 4 decided we build the same thing on our own data.
- Stage 5 placed it in the "Add column" panel.

---

## 2. Clay in one paragraph

Clay is **a spreadsheet where every column can do work**:
- A row is a company or a person.
- A column can be plain data, or it can *fetch* something (call a data provider, ask an AI, run a formula).
- Columns read other columns. So when one fills, the ones that depend on it can fill automatically.
- Rows arrive from searches, CSVs or scheduled "signals".
- At the end, an email is assembled from the columns and sent through a sequence.

That's the whole product. Everything else is a convenience around this loop:

```
 Find rows ──► Add columns that fill themselves ──► Filter to the good rows ──► Write email from columns ──► Send
   ▲                                                                                                       │
   └──────────────────── signals keep adding new rows on a schedule ◄──────────────────────────────────────┘
```

---

## 3. Stage 1: what Clay has (the parts)

| Part | What it does, in plain words |
|---|---|
| **Home** | An "ask me anything" box and five start cards (find leads, import, build a segment, start outreach, template) |
| **Find leads** | Search 48.6M companies or 300.8M people. You type in English; the AI turns it into filters you can see and edit |
| **Tables (in workbooks)** | The spreadsheet. Workbooks are folders that hold several linked tables |
| **Add column** | Pick from 1,576 ready "actions" from 264 providers, or: AI, Formula, Waterfall, Message, plain types |
| **Use AI column** | Describe a task; Clay writes the prompt. It can search the web. Output can be structured fields |
| **Waterfall** | One outcome ("work email") tried across several providers in order, stopping at the first hit |
| **Message column** | Write an email where `/` inserts any column, an AI-written snippet, or an if/else |
| **Claygents** | Reusable AI agents with a prompt, tools, an output format, test cases and versions |
| **Signals** | Scheduled sources (news, job posts, job changes…) that keep adding rows to a table |
| **Audiences** | Clay's newer "one master record per company / person" store, with segments and a field coverage view |
| **Sequencer** | Built-in email sequences; actually sent by Smartlead behind the scenes |
| **Workflows (beta)** | A drag-and-drop pipeline builder; Clay's newest direction |
| **Business context** | One place for your company, ICP and personas. Every AI feature reads it |
| **Sandbox, history, credits** | Test on a copy; see what was spent per column and per run; a credit meter everywhere |

**Main insight from Stage 1:** Clay is half-way through a change. It's moving from "many spreadsheets" to "one
master record, a field catalogue, and pipelines". In its own sequencer, tables are already labelled *Legacy*.

---

## 4. Stage 2: how a user moves through it (the parts in motion)

We ran the full journey for real:

1. **Search:** "drone inspection buyers in Indian energy, 201–5,000 staff" gave about 45 companies. The AI said
   plainly where it approximated ("used a 'similar to' match because the exact phrase found nothing").
2. **Save to a table:** a short form let us tick outcomes (company data, AI research, news, job openings), each
   with its cost per row. Clay built all the columns for us.
   - **Only 10 rows ran.** This is a deliberate cheap test run, but the table doesn't explain it, so the rest look
     broken.
3. **Find people at these companies:** 76 people, saved as a second table, linked back to the company table.
   - Problem: matching by web domain alone pulled in Fugro staff from Cairo, Oslo and Brazil for "Fugro India".
4. **Work email waterfall:** 7 providers in order. 9 of 10 found, and later providers didn't run once one had
   succeeded.
5. **Score with an AI agent:** every company came out "Moderate Fit", even after we gave it our ICP. The reasoning
   improved; the score didn't. **A score with no clear rubric doesn't separate good from bad.**
6. **Write the email** from columns, with a live preview of 5 real rows.
7. **Push to a sequence:** imported into Audiences, then a draft sequence. We stopped before sending (that needs
   your mailbox sign-in).

**What makes Clay sticky (we should copy the idea):**
- You pick **outcomes**, not plumbing.
- **Cost is shown before every run.**
- The **AI writes inputs and admits its guesses.**
- **Live previews** everywhere.
- Tables **chain** automatically.
- You **only pay until the first hit.**

**Where users get stuck (our opening):**
- The silent 10-row limit.
- AI columns running for minutes with no progress shown.
- Wrong-country people.
- Flat AI scores.
- **Old results that look current after you change the agent or the ICP.**
- Field auto-mapping that picked the wrong email column.
- Two different email composers.

---

## 5. Stage 3: why it behaves that way (the machinery)

Everything above comes from a few simple rules underneath.

**Rule 1: a column is a node in a dependency graph.**
- Each column lists the columns it reads (`{{column}}` references).
- When an input changes, or a new row arrives, dependent columns can run automatically.
- AI prompts and email subjects are just text with references. So **one mechanism** powers dependencies, AI and
  email.

**Rule 2: a column can have a run condition.** "Only run if this is true." If false, the cell says *"Run condition
not met"* and costs nothing.

**Rule 3: a waterfall is just Rules 1 + 2.**
- Provider 2's condition is "provider 1 came back empty". Provider 3's is "1 and 2 came back empty", and so on.
- A final column picks the first non-empty answer.
- Each finder has a validator that checks the email.

**Rule 4: the cell is the unit of work, cost and audit.** Every cell remembers:
- its **status**: success, no data, condition not met, waiting, error, stale…;
- **when** it ran, and a **fingerprint of its inputs**;
- the **full result**, not just what's displayed;
- for AI cells, the **whole conversation**;
- **what it cost**: charged up front, corrected after, and refunded when there's no data.

**Rule 5: views are just filters,** and filters can test a cell's status. That's how "Errored rows" or "Stale rows"
views work.

**Rule 6: sources feed tables.** A search, CSV, webhook or signal is a "source" that writes rows into one or more
tables. It remembers what it already pulled, so it doesn't pull the same rows twice. A signal is a source on a
schedule.

**Rule 7: agents are versioned, but tables don't pin a version.** A table always uses the agent's latest version.
Old answers are never marked out of date. **This is a design flaw we avoid.**

**Rule 8: Audiences are a separate "master record" world.**
- Records hold field values, identity keys (email, LinkedIn…) and segments.
- You get there by *importing* a table, which is a copy.
- Merges can't be undone. Our imported people were not linked to their companies.

**Rule 9: money.**
- Five separate credit balances, plus per-workbook budgets.
- Plan limits (rows, columns, chained tables) are stored as data, not code.

**How the stages connect, shown on a few features:**

| Feature (Stage 1) | What we saw (Stage 2) | The rule behind it (Stage 3) |
|---|---|---|
| Waterfall | 9/10 emails, later providers skipped | Run conditions "all earlier empty" + first-non-empty merge |
| Auto-run | New company rows triggered people search | Dependency graph + a trigger column |
| 10-row test | Rows 11+ looked empty | Save-and-run on N rows; the rest stay "not run" |
| Agent score | Flat "Moderate Fit" | Agent prompt + business context; no rubric, no version pin |
| Stale results | Old scores looked current after the ICP changed | Fingerprint only covers input *columns*, not the agent or context |
| Message column | Live 5-row preview, free | Email is a formula over columns: computed instantly, no credits |
| People import | Names 8% filled, not linked to companies | Import is a copy with field auto-mapping; no company link |

---

## 6. Stage 4: what it means for us

**The big finding:** our data model is already shaped like Clay's newest model, and in some ways better.

| Clay | Signal Intel | |
|---|---|---|
| row | `entity` (company / site / person) | same idea |
| column | `predicate` | same idea |
| cell value | `claim` | **ours also stores the evidence:** source, quote, confidence |
| business context | Brief | **ours also drives discovery,** the rubric and scoring |
| source / signal | radar | same idea |
| workbook / table copies | (none) | **we deliberately don't copy data** |

**What we lack is the working surface on top:**
- columns you can add and run;
- a status and cost per cell;
- waterfalls;
- saved views;
- email built from columns.

**So the plan is to add two thin layers, not rebuild:**
1. **A run layer** (`predicate_run`): Clay's per-cell status, input fingerprint, cost and AI transcript. A cell
   becomes **stale** when its inputs, its column definition *or the Brief* change. This is better than Clay.
2. **A view layer** (`saved_view`): filter + columns + sort + layout (table, board, map).

**Plus:**
- **Columns gain a fill method.** Every method writes a claim with a receipt.
  - extract (free, from evidence we already have);
  - provider (waterfall);
  - AI research;
  - formula;
  - manual.
- **Message templates and drafts.** Typed pieces, and every sent email records the facts it used.

**Your three decisions:**
- Waterfall with 2–3 email providers (LeadMagic, Findymail, Prospeo) plus one company-data provider.
- People found per account through one provider.
- A small safe formula language, not JavaScript.

**What we copy** (the idea, not Clay's code or design). These are separate items, not paired with the refuse list:
- **Pick outcomes; the tool builds the column.** The user asks for "work email" and the product sets up the
  providers, order and validation behind it.
- **Cost shown before every run.** "Run on 10 accounts, est. $0.40" before anything is spent.
- **Small first run, with a clear banner** saying the rest haven't run yet.
- **AI drafts inputs and states its guesses.**
- **Live 5-row previews** while setting up a column or an email.
- **Waterfall display and a cell detail panel.**
- **Preset views:** Errored, Stale, Needs review.

**What we refuse:**
- **Table copies and workbooks.** In Clay, saving a search makes a new table holding its own copy of the rows.
  The same company can sit in five tables with five different values, and nothing links them.
- **Arbitrary JavaScript formulas.** Clay lets users type real program code into a formula column. That's powerful,
  but risky to run safely and hard to support.
- **Old results that look current** after the agent or ICP changes.
- **Matching people by web domain only.**
- **Merges that can't be undone.**
- **Five credit balances.** We use one budget in money.
- **A free-form workflow builder in V1.**

**Where we are stronger, and must make it visible:**
1. Every value opens to its evidence.
2. We *find* the companies from a hypothesis (tenders, hiring, deployments) instead of pulling a list.
3. Verdicts with a rubric (Confirmed / Likely / Unverified / Contradicted), not flat scores.
4. Honest staleness.
5. People stay linked to the right company and site.

---

## 7. Stage 5: what that turns into (in short)

- **New sidebar, in journey order:**
  - Home;
  - **Find** (Briefs, Sources);
  - **Work** (Accounts, People, Review);
  - **Engage** (Sequences, Inbox, Meetings & deals);
  - **Measure** (Reports);
  - Settings, with a budget meter.
- **A Brief picker** at the top replaces Clay's workbooks. It's a filter, not a folder.
- **Accounts becomes the operating table:**
  - nine clear cell states;
  - a cell panel showing evidence;
  - an "Add column" catalogue with cost;
  - views;
  - board and map layouts.
- **A 14-step journey** from signup to first sent email. Target: first Confirmed account in under 30 minutes, for
  under $5.

Full detail: [05-ia-and-pages.md](05-ia-and-pages.md). Two small choices are still open there.

---

## 8. Do I have all the logic? An honest answer

**Short answer:**
- **Yes** for everything that matters to the redesign: tables, columns, waterfalls, AI columns, runs, statuses,
  costs, views, search, agents, and email composition. These were run for real and confirmed against Clay's own
  data.
- **No** for sending, replies and CRM sync. Those need your mailbox or CRM sign-in, which I deliberately didn't do.

| Area | How well I understand it |
|---|---|
| Columns, references, dependencies, formulas | **Fully**: seen and run |
| Cell statuses and the per-cell cost / run record | **Fully**: seen in real data |
| Waterfalls (order, conditions, merge, validation) | **Fully**: run on real rows |
| Run controls (10-row test, run N, auto-run, force, keep existing) | **Fully** |
| Search, AI-built filters, people-at-companies | **Fully** |
| Table chaining (company → people → back-link) | **Fully** |
| AI agents: build, versions, deploy, business context | **Fully**, including the "no stale" flaw |
| Email composer (fields, fallback, if/else) | **Mostly**. The AI-snippet editor inside tables was blocked by the Grammarly extension; seen in the Sequencer instead |
| Audiences: import, identity matching, segments | **Mostly**. Hourly segment refresh is read from settings, not watched |
| Signals firing on schedule | **Setup fully; firing inferred.** The first scheduled run is 18 Dec |
| Exactly what makes a cell "stale" | **Partly**. Confirmed it ignores agent/context changes; that it tracks input columns is inferred |
| Credit reporting rollups | **Mostly**. Totals confirmed; that reports sum the cell records is inferred |
| Sending, reply handling, open/click tracking, CRM sync | **Not seen.** Needs your mailbox / CRM sign-in |
| Workflows actually running | **Not seen.** Only the builder (beta) |
| Ads, template gallery | **Not studied.** Out of scope |

**Does the gap matter?** Not for the redesign. Sending and replies are where we deliberately differ anyway: we send
from your own Gmail / Outlook and keep the CRM inside the product. If you want, the missing parts can be checked
later by connecting a test mailbox in Clay. That needs your sign-in.

---

## 9. The ten lessons that matter most

1. **The table is the product.** Rows arrive, columns fill, email is written from columns. Our redesign centres on
   this.
2. **Outcomes, not plumbing.** Users pick "work email"; the tool builds the provider chain.
3. **Cost before every action** builds trust and lets people spend.
4. **Start small, visibly.** A 10-row test run is smart only if the screen says so.
5. **One reference mechanism** (`{{column}}`) runs dependencies, AI prompts and email. Simple and powerful.
6. **The cell is the unit of work, cost and audit.** Status, fingerprint, cost and transcript per value.
7. **AI should draft and admit its guesses.** Never present a blank form.
8. **A score needs a rubric and evidence.** Clay's flat "Moderate Fit" shows what happens without one.
9. **Out-of-date must look out-of-date.** Clay's biggest quiet flaw, and an easy win for us.
10. **Don't copy data.** Clay's table copies are why its people weren't linked to companies. One graph, many views.

---

## 10. Where to find the detail

| File | What's in it |
|---|---|
| [01-feature-inventory.md](01-feature-inventory.md) | Every screen and feature, with 27 screenshots |
| [02-journeys.md](02-journeys.md) | The journeys we ran, waterfall logic, sticky vs stuck, 23 screenshots |
| [03-data-model.md](03-data-model.md) | Clay's objects as real data shapes, run and cost records |
| [04-mapping.md](04-mapping.md) | Adopt / adapt / reject, and our proposed database changes |
| [05-ia-and-pages.md](05-ia-and-pages.md) | New navigation, every page, the end-to-end journey |
| [raw/](raw/) | Raw notes captured during each phase |

**Left in your Clay workspace from the study** (private, nothing sends; can be deleted when you like):
- the "Indian Energy Companies…" workbook;
- a saved search and a signal workbook;
- the "Score Account Fit" agent;
- two draft sequences and a draft workflow;
- 18 people in Audiences;
- the FlytBase business context.
