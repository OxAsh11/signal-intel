# Clay study — Phase 5: Information architecture and page spec

*2026-11-18. Builds on [04-mapping](04-mapping.md). Decisions taken before this phase:*
- *Waterfalls: 2–3 email providers (LeadMagic, Findymail, Prospeo) plus one firmographic provider. No marketplace.*
- *Finding people: a per-account lookup through one provider in V1.*
- *Formulas: a small, safe expression language, not JavaScript.*

*This is the spec for review. Once approved, it becomes the design spec and then the implementation plan. No code
until then.*

## 1. What the redesign has to fix

**The problem today:**
- The app is a list of module screens (Brief Studio, Discovery, Accounts, Review…).
- Each screen shows its own slice. None of them answers the user's actual question: *which companies should I sell
  to, why, who do I contact, and what do I say?*

**What Clay gets right:** one surface (the table) where rows arrive, columns fill, and email gets written from the
columns.

**What Clay gets wrong:** that surface is a pile of copied tables, with no evidence and no memory of why.

**The new IA in short:**
- One operating table over one graph, scoped by Brief.
- Everything else is either a way to *feed* the table (Brief, Sources), a way to *act* on it (People, Sequences),
  or a place for *decisions and results* (Review, Inbox, Meetings, Reports).

### Rules every page follows
1. **One graph, many lenses.** A view is a filter plus columns over the same accounts. Never a copy.
2. **Every value opens to its receipt.** Source, quote, confidence, run history, cost.
3. **Cost before action.** Every run, rerun or enrichment shows "est. $X for N accounts" before it starts.
4. **AI drafts, you edit.** Briefs, columns, filters and messages start from a plain-English request. The AI returns
   an editable structure and says where it approximated.
5. **Honest state.** Running shows progress and an ETA. Stale says stale. Errors say why and offer a retry. Nothing
   looks finished when it isn't.
6. **Start small.** First runs use a sample (10 accounts) behind a visible banner with "Run the rest (est. $X)".

## 2. Navigation

```
┌──────────────────────────────┐
│ ◧ Signal Intel   [Workspace▾]│  workspace switcher
│ [Brief: All Briefs        ▾] │  global lens (§2.2)
│ ⌕ Search or ask…   Ctrl K    │  search + AI command bar
├──────────────────────────────┤
│ ⌂ Home                       │
│                              │
│ FIND                         │
│   Briefs                     │
│   Sources                    │
│                              │
│ WORK                         │
│   Accounts                   │
│     · Ready to contact   12  │  ← saved views, pinned
│     · Needs evidence     31  │
│     · Stale               4  │
│     + New view               │
│   People                     │
│   Review                  7 ● │  mustard count = needs you
│                              │
│ ENGAGE                       │
│   Sequences                  │
│   Inbox                   3 ● │
│   Meetings & deals           │
│                              │
│ MEASURE                      │
│   Reports                    │
├──────────────────────────────┤
│ ⚙ Settings     $12.40 / $50 ▮│  budget meter always visible
└──────────────────────────────┘
```

### 2.1 Groups follow the journey
The group order is the order of the work: **Find → Work → Engage → Measure**. A new user reads the sidebar top to
bottom as the journey. Empty groups further down show a short "unlocks after…" hint instead of an empty page (§5).

### 2.2 The Brief lens replaces Clay's workbook
- A Brief is the hypothesis ("drone inspection buyers in Indian energy").
- The lens at the top of the sidebar scopes Home, Accounts, People, Review, Sequences and Reports to one Brief or
  to *All Briefs*.
- It's a filter, not a folder. An account matched by two Briefs appears in both lenses as **one row**, with two
  verdicts.
- The URL carries it (`?brief=…`), so links share the same lens.

### 2.3 Old routes to new
| Today | New | Change |
|---|---|---|
| `/` Today | **Home** | Rebuilt around the journey checklist and "what changed" (§4.1) |
| `/briefs`, `/briefs/[id]`, `/pilot`, `/versions`, `/radars` | **Briefs** + Brief detail tabs | Pilot and versions become tabs; radars move to Sources |
| `/discovery` | **Sources** | Renamed. Shows every radar with a schedule, yield and health |
| `/accounts` + `/workspace/[wsid]/accounts` | **Accounts** (operating table) | One route set (workspace-scoped). Duplicates removed |
| `/workspace/[wsid]/verdicts` | Accounts preset view "By verdict" | Folded in |
| — | **People** | New: a table of contacts linked to accounts |
| `/review` + `/workspace/[wsid]/review` | **Review** | One queue (already the intent) |
| `/campaigns` | **Sequences** | Renamed to the user's word. Gains the composer and approvals |
| `/inbox`, `/meetings` | **Inbox**, **Meetings & deals** | Meetings gains the built-in CRM stages |
| `/reports` | **Reports** | Adds coverage, cost and funnel |
| `/settings` | **Settings** | Adds Integrations, Mailboxes, Providers, Budget, Health |

## 3. Shared building blocks
These appear on many pages. They're specified once here.

### 3.1 The cell
A cell is the latest claim plus the latest run (04 §4.2). It has one of nine visible states:

| State | Looks like | Hover / click |
|---|---|---|
| Empty, never run | blank, faint `—` | "Not run. Run this cell (est. $0.02)" |
| Queued | grey dot | position in queue |
| Running | animated bar + "12 / 54" in the column header, ETA | live step ("Searching news…") instead of rotating words |
| Value | the value + small confidence chip (H / M / L) | opens the cell panel |
| Derived (formula) | value in the derived style (design-system `DerivedValue`) | shows the expression and its inputs |
| No data | grey "No data" | which providers or sources were tried, cost spent |
| Skipped | grey "Skipped" | the reason: "an earlier provider found it" / "input Email is blank" |
| Error | lava dot + short reason | full error + **Retry** |
| Stale | value + mustard corner mark | what changed (Brief v3 → v4, input re-filled) + **Re-run (est. $X)** |

### 3.2 The cell panel (right-side drawer)
Opens from any cell. Sections:
1. **Value**, with confidence and the fill method (Extracted / Provider: Findymail / AI research / Formula /
   Entered by you).
2. **Evidence:** the quote (`EvidenceQuote`), source link and date. For an AI cell: the claims it used, each with
   its receipt.
3. **History:** past runs with status, cost, time and the Brief version used. Old values stay visible, marked
   superseded.
4. **Actions:** Re-run · Edit (becomes a manual value; the old value is kept) · Mark wrong (feeds Review and
   calibration).

### 3.3 The column catalogue ("Add column")
Opens from the `+` at the end of the table header.

**Outcome first:**
- Search or ask: "work email", "do they use drones?", "headcount in India".
- Results are grouped by kind:
  - **Contact:** work email, phone, LinkedIn;
  - **Company facts:** headcount, revenue, HQ, sites;
  - **Signals:** tenders, hiring, news, deployments;
  - **AI research:** a custom question;
  - **Formula**;
  - **Manual**.
- Each item shows its fill method and **est. cost per account**.

**Picking an item:**
- It creates the predicate with a sensible fill method:
  - Work email = a provider waterfall (LeadMagic → Findymail → Prospeo, validated);
  - "Do they use drones?" = extract first, then AI research only where extraction found nothing.
- A **5-row live preview** fills in as you configure.
- **Advanced** (collapsed) holds the provider order, the run condition, inputs, auto-run, and the prompt for AI
  columns.

**Save** offers three choices, each with its cost:
- **Run on 10 (est. $0.40)**;
- **Run on all 54 in view (est. $2.10)**;
- **Save without running**.

An `extract` column also backfills from evidence already collected, at no cost.

**AI research columns:**
- The user writes a question in plain words.
- The AI drafts the prompt, its inputs (as `{{column}}` chips) and the output type (yes/no, number, text, list).
- It also writes one line saying what it can and can't check.

**Formula columns:**
- An expression box with autocomplete for columns and functions (`coalesce`, `concat`, `if`, `contains`, `>`,
  `round`…).
- The AI can draft the expression from a sentence.

**Waterfall columns:**
- Shown as one column with a collapsible group header ("Work email ‹3 providers›").
- Expanded, there is one sub-column per provider with ✓ / ✗ / Skipped, so it's visible which provider paid off.

### 3.4 Run controls
The header menu of a column, and the toolbar's selection, both offer:
- Run empty;
- Run stale;
- Run errored;
- Force re-run selected;
- Pause auto-run.

Each shows "N accounts · est. $X" before confirming.

**Auto-run** (default on for `extract` and `formula`, off for paid columns): new accounts that arrive from Sources
fill automatically. A paid column with auto-run on shows a monthly estimate from recent arrival volume.

### 3.5 Filter builder (shared by views, segments and sequence audiences)
- **Plain-English box first:** "confirmed or likely, has work email, not contacted in 90 days".
- It becomes editable chips: `Verdict is Confirmed, Likely` · `Work email is not empty` · `Last contacted > 90d
  ago`.
- Filters can test values, verdict, score band, stage, label, run status (errored / stale / skipped) and Brief.
- One filter syntax everywhere (04 §4.3).

### 3.6 Cost preview and budget
- The `CostPreview` component sits on every run button.
- The sidebar shows the budget meter.
- If a run would cross the budget, the button says so and offers "Run the first N within budget".
- No hidden spend: auto-run spend counts against the same budget and appears in Reports.

## 4. Pages

### 4.1 Home
**Purpose:** "What needs me today, and what changed?" For a new user: "What's my next step?"
**User:** everyone, daily.

**Shows:**
- **Journey strip** (until it's done): Brief ✓ → Sources running ✓ → First accounts ✓ → First Confirmed →
  Contacts found → First sequence sent. Each step links to where you do it.
- **Needs you** (mustard): Review items, drafts awaiting approval, replies to handle, errored runs. Each count links
  to a filtered page.
- **What changed since your last visit:**
  - new Confirmed / Likely accounts;
  - verdict changes (with the claim that caused them);
  - new signals on tracked accounts;
  - replies.
- **Health** (only when something is wrong): a source failing, budget at 80%, a mailbox disconnected.

**Primary actions:** open the top item; "Ask" box ("which accounts moved to Confirmed this week?").

**States:**
- **Empty** (new workspace): the journey strip plus one card, "Describe who you sell to", which starts Brief
  creation.
- **Loading:** skeleton tiles.
- **Error:** the tile that failed says so, and the others still load.

### 4.2 Briefs
**List:**
- Purpose: see every hypothesis and how it's doing.
- Shows:
  - name (written by the AI, editable; fixes #15);
  - state (Draft / Pilot / Live / Paused);
  - accounts found, Confirmed / Likely counts;
  - spend this month;
  - last run.
- Primary action: **New Brief**.

**New Brief (AI-first):**
1. Domain or a sentence: "We sell drone inspection software to energy and infrastructure companies in India."
2. One click, **Generate from flytbase.com** (about 60 s): the AI reads the site and drafts the whole Brief:
   - offer;
   - ICP (industries, size, geography, buyer roles);
   - **columns (predicates) with fill methods** (fixes #1);
   - the verdict rubric;
   - score weights by predicate role (fixes #11);
   - the sources to watch, with estimated volume and cost.
3. The draft is shown as editable sections, with a short "What I assumed" list ("Assumed 'energy' includes
   renewables and transmission. Assumed 200+ employees.").
4. **Start pilot** runs on a small sample: the Pilot state, our sandbox.

**Brief detail tabs:**

| Tab | Contents |
|---|---|
| Overview | Hypothesis, state, the funnel for this Brief (found → matched → Confirmed → contacted → replied → meeting), spend |
| ICP & rubric | Who fits, and the rubric that decides Confirmed / Likely / Unverified / Contradicted, in plain rules citing columns |
| Columns | This Brief's predicates: fill method, fill rate, cost, role in scoring. Same catalogue as §3.3 |
| Sources | This Brief's radars (links to Sources filtered) |
| Pilot | Sample accounts with verdicts; for each, "Right / Wrong" feedback; **Publish** makes it Live |
| Versions | Every version with a diff. Publishing a version marks dependent cells and verdicts **stale**, with an estimate to re-run |

**Editing a Live Brief:**
- Shows the impact before saving: "Changes the rubric. 54 verdicts will be recomputed (free). 12 AI cells will be
  stale (est. $1.20 to re-run)."

**States:**
- **Empty list:** the same card as Home.
- **Generating:** a progress list of steps (reading site → ICP → columns → rubric → sources), not a spinner.
- **Error:** "Couldn't read flytbase.com (blocked). Paste a description instead."

### 4.3 Sources
**Purpose:** where accounts come from, and whether each source is working.
**User:** the person running the Brief.

**Shows:** a table of radars with:
- type (news, tenders, job posts, company search, people at accounts, CSV, webhook, website watch);
- Brief;
- schedule and next run;
- last run result: fetched → relevant → new accounts → repeat sightings attached;
- yield trend;
- cost;
- health.

**Primary actions:**
- **Add source** (AI suggests sources from the Brief);
- Run now (with cost);
- Pause;
- Upload CSV (with field mapping and sample values; §7 rejects silent auto-map).

**Source detail:**
- Its queries.
- Recent fetched records, with why each was kept or dropped (relevance reasons; makes #4 visible).
- The accounts it produced.

**States:**
- **Empty:** "Your Brief suggests 5 sources" with one-click add.
- **Running:** per-source progress.
- **Error:** loud. "News source failing: rate limited by GDELT since 09:10. Retrying at 10:00." This makes #9 and
  #10 visible instead of swallowed.

### 4.4 Accounts: the operating table
**Purpose:** the main working surface. See matched companies, how sure we are, the evidence, and how to reach them.
Add columns, run them, filter, and act.
**User:** everyone. This is where most time is spent.

```
┌ Accounts · Brief: Drone inspection – India ▾ ──────────────────────────────────────────────────────┐
│ Views: [All] [Ready to contact 12] [Needs evidence 31] [Stale 4] [Errored 2]  + New view          │
│ ⌕ Filter: "confirmed or likely, has email"  [Verdict: Confirmed, Likely ×] [Work email: not empty ×]│
│ Layout: ▦ Table  ▤ Board  ◎ Map        Columns ▾   Sort ▾   Export ▾        54 accounts            │
├────────────────────┬───────────┬───────┬──────────┬─────────────┬───────────────┬──────────┬───┤
│ Account            │ Verdict   │ Score │ Stage    │ Uses drones?│ Headcount (IN)│ Tenders  │ + │
├────────────────────┼───────────┼───────┼──────────┼─────────────┼───────────────┼──────────┼───┤
│ ▢ NTPC (Delhi)     │ Confirmed │ 86 A  │ New      │ Yes  H      │ 15,000        │ 3        │   │
│ ▢ Fugro India      │ Likely    │ 71 B  │ New      │ Yes  M ◤    │ 420           │ No data  │   │
│ ▢ Adani Green      │ Unverified│ 58 C  │ New      │ ▬▬▬ 12/54   │ 2,415         │ —        │   │
│ ▢ Tata Power DDL   │ Likely    │ 69 B  │ Contacted│ Error ● …   │ 3,100         │ 1        │   │
└────────────────────┴───────────┴───────┴──────────┴─────────────┴───────────────┴──────────┴───┘
  ◤ = stale (mustard)   H/M/L = confidence   ▬ = running   ● = error (lava)
  ┌ Banner ─────────────────────────────────────────────────────────────────────────────┐
  │ "Uses drones?" ran on 10 of 54 accounts. Run the other 44 (est. $1.76) · Dismiss     │
  └──────────────────────────────────────────────────────────────────────────────────────┘
```

**System columns (always available, pinned left):**
- Account (name + site / country);
- Verdict (`VerdictBadge`);
- Score with band (`BandChip`);
- Stage;
- Owner;
- Last signal.

Predicate columns follow.

**Interactions:**
- **Select rows** shows a bulk bar:
  - Run columns (cost);
  - Find people (cost);
  - Add to sequence;
  - Label;
  - Set stage;
  - Export;
  - Merge (reversible).
- **Click a cell** opens the cell panel (§3.2). **Click the account name** opens the account page (§4.5).
- **Keyboard:** arrows move, Space selects. **Enter opens the panel. It never runs anything** (lesson from Clay,
  where Enter force-runs a cell).
- **Views:**
  - saving the current filter, columns and sort creates a view (personal or workspace);
  - **presets** ship by default: Ready to contact, Needs evidence, Stale, Errored, By verdict;
  - pinned views appear in the sidebar with live counts.

**Layouts:**
- **Board:** grouped by stage (or verdict), with drag to change stage. This is the CRM pipeline view.
- **Map:** sites plotted, coloured by verdict. This fills the missing map/sites gap.

**Export:** CSV of the current view (columns as shown). Optionally include evidence links.

**States:**
- **Empty (Brief has no accounts yet):** "Sources are running: 3 of 5 finished, 18 records fetched, first accounts
  in about 5 min". It links to Sources. Never a blank grid.
- **Empty (filter matches nothing):** "No accounts match" + Clear filter + the nearest match ("31 accounts match
  if you drop 'has work email'").
- **Loading:** skeleton rows, with the header visible immediately.
- **Error:** an inline banner with a retry. Cell errors stay in cells and never break the table.

**Adaptive density:**
- **Wide screens:** show up to all columns, with the first column sticky.
- **Under 1024 px:** system columns plus two predicate columns, and the rest in a "+N columns" drawer.
- **Phone:** cards (name, verdict, score, top 2 facts, next action).

### 4.5 Account page
**Purpose:** everything about one company in one place, and the case for contacting it.

**Header:**
- name, site / country, domain;
- verdict + score band;
- stage;
- owner;
- **Why this account** (3 lines generated from the rubric, each citing a claim).

**Tabs:**
1. **Evidence:** every claim grouped by predicate, each with its quote and source. Timeline of signals (tenders,
   hiring, news), newest first. Contradicting evidence is shown, not hidden.
2. **Fields:** the same columns as the table, vertically, with cell states and run controls.
3. **People:** contacts at this account (and this site), with role, email status, and sequence status. **Find
   people** (per-account lookup, with cost).
4. **Activity:** emails sent and received, meetings, notes, stage changes (the built-in CRM).
5. **Sites & relations:** other sites of the same company, and parent / subsidiary.

**Primary actions:**
- Add to sequence;
- Find people;
- Change stage;
- Mark verdict wrong (goes to Review with the reason);
- Merge / unmerge.

**States:**
- **No evidence yet:** "Found by News on 17 Nov. No facts extracted yet. Run extraction (free)."
- **Merged:** banner, "Merged from 2 records · Undo".

### 4.6 People
**Purpose:** contacts across accounts, ready for sequences.

**Shows:** the same table component with person columns:
- name, role, seniority;
- account (+ site);
- work email (waterfall cell with ✓ validated / ✗);
- LinkedIn;
- sequence status;
- last touch.

**Where people come from:**
- **Find people at accounts** (one provider, per account; decision 2);
- CSV;
- replies and meetings (auto-created);
- manual.

**Matching rule:** people link through the account entity (company **and** site / country). A person whose
location doesn't match the account's site is flagged "Possibly a different office", not silently added (the Fugro
lesson from Phase 2).

**Primary actions:** Find work emails (cost) · Add to sequence · Export.

**States:**
- **Empty:** "No people yet. Pick accounts and choose Find people." With a shortcut to "Ready to contact" accounts.

### 4.7 Review
**Purpose:** one queue for every human decision, ordered by how much the answer matters.

**Item types:**
- possible duplicates (merge / keep apart);
- verdicts to confirm (low confidence or contradicted);
- "Mark wrong" reports;
- people with location mismatch;
- meetings to match to an account;
- drafts to approve (also reachable from Sequences).

Each item shows the evidence needed to decide and a one-key answer. Keyboard-first: J/K to move, 1/2/3 to answer.

**Change from today:** repeat sightings of a known company **no longer come here**. They attach to the account
automatically (fixes #2), so the queue only holds real decisions.

**States:**
- **Empty:** "Nothing needs you."
- **Error:** fixes #12 (the page errored on every load); one failing item type must not break the queue.

### 4.8 Sequences
**List:**
- Name, Brief, audience (segment), steps, state (Draft / Live / Paused).
- Sent, replies, positive replies, meetings, bounce rate (bounce guard pauses at a threshold).

**Sequence editor (one page, three panes):**
1. **Audience:**
   - a segment built with the filter builder (§3.5);
   - default "Ready to contact" + people with a validated work email;
   - exclusions: other live sequences, contacted in last N days, blocklist, replied;
   - a count and a sample.
2. **Steps:**
   - each step has a delay, subject and body;
   - the composer has typed pieces:
     - text;
     - **field** (any column, including the account's, with a fallback and case);
     - **AI snippet** (a prompt that may only use claims with receipts, with a max length);
     - **if / else** on a filter;
   - type `/` to insert. A/B variants per step.
3. **Live preview of 5 real people** next to the composer, updating as you type:
   - each preview shows which claims were used;
   - empty fields show their fallback, highlighted.

**Sending:**
- From the user's connected Gmail or Outlook (Settings → Mailboxes).
- Daily limits per mailbox; sending window by recipient time zone.

**Approvals (V1 is human-approved):**
- **Generate drafts** creates one draft per person, with an est. cost for AI snippets.
- The **Approve** queue offers approve / edit / reject, with bulk approve for the ones with no warnings.
- Only approved drafts send.
- Each sent message keeps its variables and claim ids (PRD Part 8).

**States:**
- **No mailbox:** "Connect Gmail or Outlook to send." Drafting and approval still work before connecting (this
  covers the OAuth-deferred period; see the placeholders rule).
- **Audience empty:** shows why (e.g. "18 accounts are Confirmed but none have a validated email → Find work
  emails, est. $0.90").
- **Sending error:** per-message reason (bounced, mailbox auth expired), and the sequence pauses on a mailbox
  failure.

### 4.9 Inbox
**Purpose:** every reply, classified, with a one-click next step.

**Shows:**
- the thread;
- its class (Interested, Not now, Wrong person + referral, Unsubscribe, Out of office);
- the account and its verdict;
- a suggested reply drafted from the thread and the account's evidence.

**Primary actions:**
- Reply;
- Book meeting (calendar);
- Mark class;
- Set stage;
- Add referred person.

**States:**
- **Empty:** "No replies yet" + the sequences currently sending.
- **Disconnected mailbox:** a loud banner.

### 4.10 Meetings & deals
**Purpose:** the built-in CRM after a reply.

**Shows:**
- upcoming and past meetings, each matched to an account (unmatched ones go to Review);
- notes, next steps and tasks;
- deals on a board by stage, with value.

**Primary actions:** Log meeting · Add task · Move stage · Mark won/lost (with reason, which feeds calibration).

**States:**
- **Empty:** "Meetings appear here when booked from Inbox or synced from your calendar."

### 4.11 Reports
**Purpose:** is the hypothesis working, and what does it cost?

**Sections:**
1. **Funnel by Brief:** found → matched → Confirmed → contacted → replied → meeting → won. Every number links to
   the filtered table.
2. **Coverage:** per column, the fill rate, how it was filled (extracted / provider / AI / manual), stale and error
   counts, cost. Replaces the old "data completeness".
3. **Cost:** spend by Brief, column, provider and month. Cost per Confirmed account, per meeting.
4. **Sources:** yield and cost per source. Which sources produce Confirmed accounts.
5. **Calibration:** verdicts marked wrong, won/lost by verdict, for tuning the rubric.

**States:**
- **Not enough data:** "Reports fill in after your first sequence sends." The sections that do have data still show.

### 4.12 Settings
| Section | Contents |
|---|---|
| Workspace | Name, members & roles, invites |
| Business context | Company, offer, proof points. Used by every AI feature. "Generate from domain" |
| Mailboxes | Gmail / Outlook connections, daily limits, signatures, warm-up status |
| Integrations | Calendar, Slack + Resend notifications, CRM export (later). Not-connected items say "Not set up yet" |
| Providers | Email waterfall order, firmographic provider, people lookup provider; status of each key; cost per call |
| Budget & usage | Monthly budget, alerts at 50/80/100%, per-Brief caps, usage ledger |
| Data | Labels, segments, predicate library (reusable column templates), blocklist |
| Health | Source runs, worker queue, provider errors, mailbox status. Loud when something fails |
| API & tokens | M13 tokens |

## 5. The end-to-end journey: signup to first sent email
Target: a new user gets to a first Confirmed account in **under 30 minutes** and a first approved email the same
day, spending **under $5**.

| # | Step | Where | What the user does | What the system does | Cost |
|---|---|---|---|---|---|
| 1 | Sign up | `/login` → onboarding | Email sign-in, workspace name | Creates workspace and default budget ($50) | — |
| 2 | Business context | Onboarding | Enters domain `flytbase.com` | Reads the site, drafts the business context (~60 s) | ~$0.05 |
| 3 | First Brief | Onboarding → Brief | "Drone inspection buyers in Indian energy and infrastructure" | Drafts ICP, columns with fill methods, rubric, weights, sources; lists assumptions | ~$0.10 |
| 4 | Review the draft | Brief | Edits two assumptions, clicks **Start pilot** | Starts sources on a sample | — |
| 5 | Sources run | Sources (or watch from Accounts) | Waits; sees progress per source | Fetches, filters by Brief relevance, resolves entities, extracts claims | ~$0.50 |
| 6 | First accounts | Accounts | Sees accounts arrive with verdicts; opens evidence | `extract` columns fill free; verdicts computed from rubric | — |
| 7 | Calibrate | Brief → Pilot | Marks 5 accounts Right / Wrong, clicks **Publish** | Brief goes Live, rubric version saved | — |
| 8 | First Confirmed | Accounts → Ready to contact view | Opens the top Confirmed account; reads "Why this account" | — | — |
| 9 | Fill gaps | Accounts | Adds "Uses drones?" (AI research) on 10 accounts | Runs only where extraction found nothing | ~$0.40 |
| 10 | Find people | Bulk bar → Find people | Selects 12 Ready accounts | Per-account lookup; flags location mismatches | ~$1.20 |
| 11 | Work emails | People | Runs Work email on 30 people | Waterfall LeadMagic → Findymail → Prospeo, validated | ~$1.50 |
| 12 | Write the sequence | Sequences → New | Describes the angle; AI drafts 3 steps from columns | 5-person live preview with cited claims | ~$0.05 |
| 13 | Approve | Sequences → Approve | Reviews 30 drafts, edits 3, bulk-approves | Locks variables + claim ids per draft | AI snippets ~$0.30 |
| 14 | Send | Settings → Mailboxes | Connects Gmail (OAuth), goes Live | Sends within limits and window | — |

**Where the journey can stall, and what the page does about it:**
- **Sources return nothing:** Sources shows the health reason and suggests other sources. Home's strip stays on
  step 5 with a link.
- **No Confirmed after the pilot:** Accounts' empty "Ready to contact" view explains the nearest miss ("31 Likely:
  most lack 'Uses drones?' evidence → add the AI research column, est. $1.20").
- **No mailbox yet:** steps 12–13 still work. Step 14 waits on OAuth.

## 6. Visual direction
Follows the agreed system (Mixpanel-guideline light system, design-system tokens and components in `packages/ui`).

**Page and type:**
- **Page:** white `#FFFFFF`, insets and table headers `#FAFAFA`, hairlines `#EAEAEA`. Light by default; never
  auto-switch to dark.
- **Type:** DM Sans for UI, DM Mono for values, IDs and costs. Headlines `#1F2023`, body black at 70%.
- **Spacing:** an 8 px grid; 4/8 px radii; page margins 48/40/32/16 px by width; container max 1440 px, except the
  table, which uses full width.

**Controls:** near-black pill primary buttons. The accent is used only for links, tags and selection. **No
purple.**

**Meaning colours (gradient families, used the same way everywhere):**

| Family | Meaning |
|---|---|
| pool | discovery: Sources, new accounts |
| mint | judgement: verdict, evidence, score |
| grass | engagement: people, sequences, replies, deals |
| mustard | needs you: Review, approvals, stale |
| lava | cost and errors |

**The table:**
- **Dense:** 32 px rows, 13 px text, a sticky header and first column.
- **Hairline grid** with no zebra striping. Cells use colour only for state, never decoration.

**Every number links** to the rows behind it.

**Checks before calling any page done:** desktop and phone screenshots, all states (empty, loading, error,
populated) looked at, not just a passing build.

## 7. Walk against the Part A gap list
Every core gap and half-built module has a home in this IA.

| Gap | Where it lands |
|---|---|
| #1 AI draft creates no predicates | New Brief (§4.2) drafts columns with fill methods |
| #2 repeat sightings go to Review | Sources attach them to the account; Review (§4.7) no longer receives them |
| #3 verdicts don't recompute | Stale states (§3.1); Brief versions (§4.2) show the recompute |
| #4 relevance ignores the Brief | Source detail (§4.3) shows keep/drop reasons per record |
| #5 JSON-in-string values / #6 duplicate claims | Engine fixes; visible through the cell panel's evidence (§3.2) |
| #7 no backfill | `extract` columns backfill free on create (§3.3) |
| #8 verdict not rubric | ICP & rubric tab (§4.2); "Why this account" cites claims (§4.5) |
| #9 / #10 radars | Sources health and loud errors (§4.3); new source types |
| #11 hard-coded scoring | Columns have a scoring role in the Brief (§4.2) |
| #12 Review errors on load | Review: per-item-type failure isolation (§4.7) |
| #13 hourly yield / #14 "def v1" / #15 Brief names | Sources show live counts; tiles redesigned; AI-written Brief names |
| Accounts: fixed columns, no views / export / board | The operating table (§4.4) |
| Enrichment: Hunter only, never on demand | Provider waterfalls + run controls (§3.3, §3.4) |
| Research: no manual trigger | AI research columns + run controls |
| Labels / segments: no UI | Bulk label (§4.4); Settings → Data; filter builder (§3.5) |
| Reports: tiles only | Reports (§4.11) |
| Settings: no Integrations / Health | Settings (§4.12) |
| Campaign push never writes | Sequences with native send (§4.8) |
| Missing map / sites view | Map layout (§4.4) |

## 8. Not in V1
- A free-form workflow builder.
- Clay-style table copies and workbooks.
- A provider marketplace.
- A licensed people index.
- Arbitrary JavaScript formulas.
- External sequencer push (Smartlead, Instantly…). Native send comes first.
- Ads audiences.
- CRM sync out (export covers it for now).

## 9. Build order once approved
1. **Engine fixes first**, unchanged from Part A: #5, #6, #4 → #2, #3 → #1, #8, #11 → #7. The table is only as good
   as its cells.
2. **Schema deltas** (04 §4): `predicate` fill methods, `predicate_run`, `saved_view`, shared filter syntax.
3. **Shell:** the new sidebar, the Brief lens, the route consolidation (§2.3), Home.
4. **The operating table:**
   - cell states;
   - the cell panel;
   - the column catalogue (extract, formula and AI research first, then waterfalls);
   - run controls;
   - views;
   - board and map layouts.
5. **Brief flow:** generate from domain, Pilot calibration, versions with impact preview.
6. **People + Find people + email waterfall.**
7. **Sequences:** composer, preview, approvals, native send (waits on mailbox OAuth).
8. **Inbox, Meetings & deals, Reports, Settings sections.**

Each step ends with screenshots of every state on desktop and phone.

## 10. Two choices to confirm
1. **People as its own nav item, or only a tab inside Accounts?** I recommend **its own item**. Sequences work on
   people, and a people table needs its own views (has email, seniority, in a sequence).
2. **Brief lens default for a workspace with several Briefs:** *All Briefs* or *the last Brief used*? I recommend
   **the last Brief used**. Most work happens inside one hypothesis, and the lens is always one click away.
