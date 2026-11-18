# Operating-table redesign: navigation, the account table, Email engine and CRM

**Status:** approved 2026-11-18. The user approved §3–5 and decided the following:
- **People** is its own menu item, and **every person must be connected to an account**. There are no orphan
  people; a person without an account goes to Review.
- **Email engine** and **CRM** stay in the menu as **placeholders** for now: pages that say what is coming, with no
  build. §6–7 wait for a later study round.
- The Brief lens defaults to the last Brief used.
- The user has more ideas to add later.

**Builds on:**
- the Clay study: [00-study-summary](../../research/clay/00-study-summary.md),
  [04-mapping](../../research/clay/04-mapping.md), [05-ia-and-pages](../../research/clay/05-ia-and-pages.md);
- the PRD;
- the engine test study (gaps #1–#16).

**Mockup:** [Home dashboard](../../research/clay/mockups/home-dashboard.html). Open it in a browser. It uses sample
data from the FlytBase test case.

## 1. Goal

Signal Intel is a **discovery engine with its own CRM and its own email engine**. A user should be able to go from
a hypothesis to a sent email and a booked meeting without leaving the product:

```
Brief ─► Sources find companies ─► Accounts table (evidence, verdicts, columns that fill themselves)
      ─► People + work emails ─► Email engine (write, approve, send, read replies) ─► CRM (pipeline, meetings, tasks)
```

**This spec defines:**
1. the navigation;
2. the operating table (Accounts) and its building blocks;
3. the **Email engine**;
4. the **CRM**;
5. the data changes behind them;
6. the order in which to build.

Section 6 sets out the Email engine and CRM as a proposal. It needs its own study and discussion round before
approval (§11).

## 2. Decisions already made

| # | Decision | Source |
|---|---|---|
| D1 | One graph, many views: no table copies, no workbooks. A "table" is a saved view over `entity` | Clay study, Phase 4 |
| D2 | Columns are `predicate`s with a fill method: extract / provider / research / formula / manual | Phase 4 §4.1 |
| D3 | Waterfalls: 2–3 email providers (LeadMagic, Findymail, Prospeo) + 1 firmographic provider. No marketplace | You, 2026-11-18 |
| D4 | Finding people: one provider, per account | You, 2026-11-18 |
| D5 | Formulas: a small safe expression language, not JavaScript | You, 2026-11-18 |
| D6 | Email is sent from the user's own Gmail / Outlook. The CRM lives inside the product. Calendar: both providers. Notifications: Resend + Slack | Earlier decision (native outreach) |
| D7 | **Email engine** and **CRM** are top-level menu items | You, 2026-11-18 |
| D8 | Light Mixpanel-guideline system and Signal Intel design-system components. No purple. Light by default | Design system |
| D9 | A feature that needs OAuth keys shows "Not set up yet" until the keys are added | Earlier decision |
| D10 | V1 email is human-approved before sending | PRD |

## 3. Navigation

```
Signal Intel                 [Brief lens ▾]   [Search or ask… Ctrl K]
Home
FIND      Briefs · Sources
WORK      Accounts (+ pinned views) · People · Review
ENGAGE    Email engine  ─ Sequences · Approvals · Inbox · Mailboxes
          CRM           ─ Pipeline · Meetings · Tasks
MEASURE   Reports
Settings                                      budget meter $12.40 / $50
```

**Rules:**
- The groups follow the order of the work.
- Counts that need the user (Review, Approvals, Inbox) use the mustard "needs you" tag. All other counts are plain.
- **Brief lens:** a filter over all pages, not a folder. It defaults to the last Brief used (recommended; to
  confirm).
- **On phones:** the menu becomes one scrolling row of top-level items, and sub-items move into each page's tabs.
- **Old routes:**
  - `/discovery` → Sources;
  - `/campaigns` + `/inbox` → Email engine;
  - `/meetings` → CRM;
  - `/workspace/[wsid]/verdicts` → the Accounts "By verdict" view;
  - the duplicate `/accounts` and `/workspace/[wsid]/accounts` route sets merge into one.

## 4. Home (see the mockup)

| Area | Shows | Links to |
|---|---|---|
| Header | Brief name, version, live since, sources running. Buttons: Open accounts, New sequence | Accounts, Email engine |
| Journey strip | 6 steps: Brief → Sources → First accounts → First Confirmed → Contacts found → First email sent. Hidden once done | The page for each step |
| Number tiles | Accounts found (pool), Confirmed (mint), Emails sent / Replies / Meetings (grass), Spend (lava). Every number is a link | The filtered rows |
| Needs you (mustard) | Drafts to approve, replies to handle, Review decisions, stale accounts with re-run cost, errored cells | Each queue |
| Accounts to act on | Top rows of "Ready to contact": verdict, band, why now, contact status, next action (with cost when it spends) | Accounts |
| What changed | Verdict changes with the claims behind them, replies, Brief versions and what they made stale, new accounts by source | Account, Inbox, Brief |
| Email engine card | Sending today vs cap, live sequences, mailboxes, next send, bounce rate, "Review N drafts" | Email engine |
| CRM card | Pipeline by stage, tasks due today | CRM |
| Sources card | Health per source; failures are loud | Sources |

**Empty workspace:** the journey strip and one card, "Describe who you sell to".

## 5. The operating table (Accounts) and its building blocks

The full detail is in [05 §3–4.6](../../research/clay/05-ia-and-pages.md). The decisions that bind:

| # | Decision |
|---|---|
| T1 | **A cell is the latest claim plus the latest run.** Nine visible states: empty, queued, running (with count and time left), value + confidence, derived, no data, skipped (with the reason), error (with retry), stale (with the re-run cost) |
| T2 | **Cell panel:** clicking any cell shows its value, evidence (quote, source, date), run history (status, cost, Brief version) and actions (Re-run · Edit · Mark wrong) |
| T3 | **Add column is outcome first.** It shows each outcome's cost per account and a 5-row live preview. Saving offers Run on 10 / Run on all in view / Save without running, each with its cost. `extract` columns backfill free |
| T4 | **Run controls:** run empty / stale / errored / force selected. Auto-run is on by default for free methods and off for paid ones |
| T5 | **Filters:** you type plain English and get editable filter tags. Views, segments and sequence audiences all use the same filter syntax. Filters can test run status |
| T6 | **Views:** saved per person or per workspace. Presets: Ready to contact, Needs evidence, Stale, Errored, By verdict. Layouts: table / board (by stage) / map (sites) |
| T7 | **Enter opens the panel. It never runs anything** |
| T8 | **Matching people:** people match through the account entity (company + site / country). A location mismatch is flagged, never silently added |
| T9 | **Cost before action:** every action that spends shows its estimate, and a run that would cross the budget offers "first N within budget" |

## 6. Email engine (proposal: to be discussed)

**Purpose:** write, approve, send and track email from the user's own mailboxes, and bring replies back to the
account.

### 6.1 Pages
| Sub-page | What it does |
|---|---|
| **Sequences** | List: name, Brief, audience, steps, state (Draft / Live / Paused), sent, replies, Interested, meetings, bounce rate. The editor has three panes: **Audience** (filter + exclusions + count), **Steps** (the composer), **Live preview of 5 real people** with the claims each one used |
| **Approvals** | One queue of drafts. Approve / edit / reject, and bulk-approve drafts with no warnings. Warnings: missing field (fallback used), AI snippet with low confidence, contact unverified, person in another country |
| **Inbox** | Replies in threads, sorted by class (Interested, Referral, Not now, Not interested, Out of office, Unsubscribe). A suggested reply drafted from the thread and the account's evidence. Actions: reply, book meeting, set stage, add the referred person |
| **Mailboxes** | Connect Gmail / Outlook. Per mailbox: daily cap, warm-up status, sending window, signature, health (authentication checks, bounce rate, connection) |

### 6.2 Composer
- **Typed pieces:**
  - text;
  - **field** (any column, including the account's, with a fallback and case);
  - **AI snippet** (a prompt that may use only claims with receipts, with a word limit);
  - **if / else** on a filter.
- Type `/` to insert. A/B variants per step.
- **AI-first:** describe the angle in one sentence, and the AI drafts all the steps from the columns.
- Every draft records the template version, the variables and the claim ids it used (PRD Part 8).

### 6.3 Sending: proposed rules
| # | Rule | Why |
|---|---|---|
| E1 | Send through the **Gmail API / Microsoft Graph** with the user's OAuth, not SMTP passwords and not a third-party sender | Native outreach (D6); no passwords stored; replies come back through the same connection |
| E2 | A scheduler job (Procrastinate) sends due messages. It respects the mailbox daily cap, the sending window in the recipient's time zone, random spacing between sends, and the minimum gap between steps | Deliverability; no bursts |
| E3 | **Stop rules:** a person stops on reply, bounce, unsubscribe or meeting booked. An account stops when *anyone* at it replies (on by default) | Don't email a company that already answered |
| E4 | **Bounce guard:** a sequence pauses automatically at a 5% bounce rate (caution shown from 3%) | Existing `campaign.bounce_*` fields |
| E5 | Every email carries an unsubscribe header and link, and the suppression list is checked before each send | Compliance; existing `suppression_list` |
| E6 | Open and click tracking are **off by default** | Tracking pixels hurt deliverability and are unreliable |
| E7 | **Reply detection:** watch each sending thread (Gmail push / Graph subscription, with polling as a fallback), classify the reply with AI, and write an `activity` row | Inbox and CRM read one timeline |
| E8 | Only approved drafts send. A "dry run" mode renders and schedules without sending | Existing `push_mode` |

### 6.4 Open questions for the email round
1. **Build vs buy for sending infrastructure:** our own scheduler on the Gmail / Graph APIs (recommended above), or
   a sending provider underneath?
2. **Warm-up:** build it in, rely on the provider, or leave it to the user?
3. **Several mailboxes per sequence (rotation) in V1, or one?**
4. **Multi-channel (LinkedIn steps, calls as tasks) in V1, or email only?**
5. **How much of reply handling is automatic?** E.g. auto-pause on Out of office until the return date.

## 7. CRM (proposal: to be discussed)

**Purpose:** what happens after a reply. Pipeline, meetings, tasks and one activity timeline per account. It's
built in, so nothing has to sync.

| Sub-page | What it does |
|---|---|
| **Pipeline** | A board by stage (Untouched → Queued → Contacted → Engaged → Qualified → Meeting → Handed off, plus Dormant and Disqualified). It's the same account entity, so it opens the same account page. Optional deal value per account |
| **Meetings** | Upcoming and past meetings, matched to accounts (unmatched ones go to Review). Calendar sync (Google + Microsoft). Proof status. Notes and next steps |
| **Tasks** | Tasks due by owner, created by hand, from a reply ("send deck"), or from meeting next steps. A tick closes the task and writes an activity |

**Account page, Activity tab:** one timeline of emails sent and received, meetings, notes, stage changes and tasks.
It reads the existing `activity` table, which is append-only.

**Open questions for the CRM round:**
1. Are deals separate from accounts (several deals per account), or is there one stage per account in V1?
2. Export to an external CRM (HubSpot / Salesforce): needed in V1, or later?
3. Team features: owners, assignment rules, round-robin?

## 8. Data changes (proposals; full SQL sketches in 04 §4)

| Change | Kind | For |
|---|---|---|
| `predicate` + `fill_method`, `fill_config`, `input_predicate_ids`, `run_condition`, `auto_run`, `version`, `config_hash`, `cost_estimate_usd` | alter | Columns that fill themselves |
| `predicate_run` | new | Cell status, staleness, cost and transcript per run |
| `saved_view` | new | Views and layouts |
| shared filter AST (`saved_view.filter`, `segment.filter`) | convention | One filter syntax everywhere |
| `message_template`, `message_draft` | new | Composer and approvals |
| `mailbox`: provider, address, OAuth vault key, daily cap, window, warm-up, health | new | Email engine (replaces the push-to-sequencer use of `integration` for sending) |
| `email_message`: draft, mailbox, provider message / thread id, sent_at, status (scheduled / sent / bounced / replied), step | new | Sending and reply matching |
| `campaign` → sequence: + audience filter, exclusions, schedule settings, stop rules | alter | Email engine |
| `deal` (only if CRM question 1 says yes) | new | CRM |
| `alias_type` + email, phone; `radar` + schedule / next_run_at / new types; typed `claim.value`; idempotent claims | alter | Engine gaps #2–#7 |

**Reused as-is:**
- `activity` (timeline);
- `meeting`;
- `task`;
- `next_step`;
- `suppression_list`;
- `contact_point` (emails, with verification status);
- `usage_ledger` (+ `predicate_run_id`).

## 9. Errors, cost and safety
- **Every failure is visible where it happens:**
  - a cell shows its error with Retry;
  - a source shows its health on Sources and Home;
  - a mailbox shows auth expired and the sequence pauses.
  Nothing is swallowed into a log.
- **Cost:** one money budget per workspace, with optional per-Brief caps and alerts at 50 / 80 / 100%. Auto-run
  spend counts too.
- **Secrets:** OAuth tokens are kept only in the vault (`credentials_vault_key`). The product never asks for a
  mailbox password.
- **Stale is honest:** a change to a Brief, a predicate or an input marks dependent cells and verdicts stale, and
  shows the re-run cost.

## 10. How we'll check it works
- **Data:** every new table has database checks and runs in the end-to-end PGlite tests against the real schema.
  Mocks and ledgers don't count as proof.
- **Screens:** every page, in every state (empty, loading, error, filled), screenshotted on desktop and phone
  before it's called done.
- **Email:** dry-run mode end-to-end on a test mailbox before any live send. Bounce guard and stop rules get their
  own tests.

## 11. Build order (after approval)
1. **Engine fixes** (#5, #6, #4 → #2, #3 → #1, #8, #11 → #7). The table is only as good as its cells.
2. **Columns:** `predicate` changes, `predicate_run`, `saved_view`, the shared filter.
3. **Shell:** new navigation, the Brief lens, route consolidation, Home.
4. **Operating table:**
   - cells and the cell panel;
   - Add column (extract → formula → AI research → waterfalls);
   - run controls;
   - views;
   - board and map.
5. **Brief flow:** generate from domain, pilot calibration, version impact.
6. **People:** find people and the work-email waterfall.
7. **Email engine:** composer and approvals first (they work without a mailbox), then mailboxes, sending, replies.
   **Needs the email study round first.**
8. **CRM:** pipeline, meetings with calendar sync, tasks, the activity timeline. **Needs the CRM questions
   answered.**
9. **Reports and Settings sections.**

## 12. Not in V1
- A free-form workflow builder.
- Table copies and workbooks.
- A provider marketplace.
- A licensed people index.
- JavaScript formulas.
- Pushing to external sequencers.
- Ads audiences.
- Two-way CRM sync (to be confirmed in §7).

## 13. What needs your answer
1. Approve §3–5 (navigation, Home, operating table) as they stand, or say what to change.
2. Two small choices:
   - People as its own menu item (recommended);
   - the Brief lens defaults to the last Brief used (recommended).
3. Agree to an **Email engine + CRM study round** before §6–7 are finalised: dedicated email and CRM tools, plus
   the open questions in §6.4 and §7.
