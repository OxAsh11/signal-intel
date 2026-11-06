# Signal-Driven Account Intelligence Platform — PRD

Nov 5, 2026 · @Ashish Mishra · Working draft v1.0

## How to read this document

This PRD specifies an industry-agnostic platform that turns fragmented public signals into evidence-backed, researched, reachable accounts, and then activates them. It is written the way a building is built: nothing in an upper floor may depend on something the floor below does not provide.

| Layer | Building term | What it holds | Changes how often |
| --- | --- | --- | --- |
| Part 0 | The site | Problem, vision, principles, non-goals | Rarely |
| Part 1 | Foundation | The data model, identity, provenance, tenancy, versioning | Almost never; changes are migrations |
| Part 2 | Scaffolding | Platform services every module uses: jobs, connectors, AI gateway, events, metering, observability, security | Occasionally |
| Part 3 | Structure | The core intelligence modules: Brief to Scoring | Per release |
| Part 4 | Systems | Activation, engagement, account workspace, meetings and tasks | Per release |
| Part 5 | Utilities | Integrations, organisations and access, memory and retrieval | Per release |
| Part 6 | Finishes | Screens and reporting | Often |
| Part 7 | Inspection | Health, quality, audit, compliance | Continuously |
| Part 8 | Extensions | Self-learning loops (V2) | Later phase |

**Conventions**

- **Module IDs** are stable: M1 to M19 for modules, S1 to S9 for scaffolding services, F1 to F8 for foundation elements. Cross-references use these IDs.
- **Canonical names** for every object, state, role and event are fixed in the Appendix. If any section disagrees with the Appendix, the Appendix wins and the section is a bug.
- **Phase tags:** `V1` must ship in the first release; `V2` is the learning release; `V3` is later. Anything untagged is V1.
- **"Must"** means a release gate. **"Should"** means expected but negotiable.
- **Configuration, not code.** Wherever this PRD says a behaviour varies by industry, it is varied through the Brief (M1) and the predicate registry (F3), never by changing the schema.

## Part 0 — The site

The platform answers one question for any industry: **which companies match my hypothesis, how sure are we, and how do we reach them?** It does this by weaving scattered signals into one evidence-backed record per company.

### 0.1 Problem

Go-to-market teams that sell into specialised markets face the same four failures, whatever the industry:

1. **The best buyers are not in any database.** They are visible only through scattered public traces: a job post, a permit, a tender award, a conference talk, a case study.
2. **Signals are fragmented.** Each source, script or spreadsheet keeps its own idea of what a "company" is, so the same company exists many times and no view is complete.
3. **Claims are unverifiable.** Lists say "uses X" without saying why, from where, or how recently.
4. **Results are counted inconsistently.** Replies, meetings and deals live in separate tools, and every report defines success differently.

### 0.2 Vision

A generic engine that comes alive when given a detail. The user describes a hypothesis in plain language ("engineering firms that design in a specific CAD tool and are hiring"). The platform configures itself, collects signals, resolves them into companies, proves or disproves the hypothesis per company with quoted evidence, researches the strongest ones, finds reachable people, and hands them to outreach. Every outcome flows back into the same record.

### 0.3 Positioning

|  | List-first tools | This platform |
| --- | --- | --- |
| Starting point | A list of rows the user brings or buys | A hypothesis the user describes |
| Unit of truth | A row with enriched columns | A company with sourced claims |
| "Adding a column" | Runs an enrichment on each row | Defines a new predicate, backfilled from evidence already collected |
| Answers | "What do we know about these rows?" | "Who out there matches, and how do we know?" |
| Best where | Buyers are well indexed | Buyers leave public footprints but are poorly indexed |

The platform's edge depends on the target having a **public, verifiable footprint**: physical deployments, permits, procurement, filings, hiring. Where adoption is invisible, it degrades towards a conventional enrichment tool. This is a stated boundary, not a hidden one.

### 0.4 Users

| Persona | Goal | Primary modules |
| --- | --- | --- |
| Growth or GTM lead | Define markets, judge evidence, run the motion | M1, M5, M8, M16 |
| Researcher or analyst | Verify accounts, write and review research | M5, M6, M11 |
| Sales or BD operator | Work replies, meetings and next steps | M10, M11, M12 |
| Organisation admin | Users, roles, integrations, budgets | M13, M14, S6 |
| Developer or agent | Read and write through API, webhooks, MCP | M13, M15 |

### 0.5 Design principles

These are binding. Each one is enforced by a specific element named in brackets.

1. **Lenses, not folders.** A source attaches evidence to a company; it never creates its own copy of it. \[F2, M4\]
2. **Observed, then resolved, then derived.** Raw records are immutable; identity is decided once; verdicts, scores, stages and summaries are always recomputed from facts, never typed. \[F3, F7\]
3. **Every claim has a receipt.** No fact exists without a source record and, where possible, a quote. \[F3\]
4. **Fixed schema, variable vocabulary.** Industries differ in configuration, never in tables. \[F3, M1\]
5. **Versioned meaning.** Any rule that changes what a number means is versioned, and every derived value records the version that produced it. \[F5\]
6. **Contracting is a filter; expanding is a job.** The interface makes the cost of each edit visible before it runs. \[M1, S6\]
7. **Loud failure.** Every pipeline reports its health; silence is treated as failure. \[M17\]
8. **Truth is external.** Only human marks and real-world outcomes count as ground truth. The platform never learns from its own outputs. \[M18, M19\]
9. **Tenant data is private by default.** Only public company facts may be shared across tenants; people data never is. \[F1\]
10. **Humans spend time where it teaches the most.** Automation is gated by confidence and reversibility. \[S1, M19\]

### 0.6 Non-goals

- **Not a full CRM.** The platform keeps an account workspace and a derived stage, and syncs with the customer's CRM. It does not replace deal management, quoting or forecasting.
- **Not an email sending infrastructure.** Sequencing and deliverability live in connected sequencers; the platform pushes lists and pulls back events.
- **Not a contact data broker.** People data is collected per tenant for that tenant's use.
- **Not unattended autonomy.** V1 automates only reversible, high-confidence actions.

## Part 1 — Foundation

The foundation is one fixed schema that never changes per industry. Data moves through it in one direction: **observed → resolved → derived**. Raw observations are immutable, identity is decided in one place, and everything a user sees as a judgement (verdict, score, stage, summary) is recomputed from facts.

### F1 — Tenancy and data scope

- **Hierarchy:** Organisation (the customer) → Workspace (a team or business unit) → Brief (one hypothesis). Every tenant-owned row carries `org_id` and `workspace_id`.
- **Enforcement:** Postgres row-level security on every tenant table. Application checks are a second line, never the only one.
- **Two data scopes**, recorded on every fact-bearing row as `scope`:
  - `shared`: facts about companies taken from public sources (a public job post, a tender award). Eligible for a cross-tenant fact layer.
  - `tenant`: everything else. Briefs, accounts, scores, activity, notes, uploads, exclusion lists, research, and all person data.
- **Rule:** person entities and contact points are always `tenant`. No exception, no setting.
- **V1 behaviour:** the `scope` column and policies exist from day one, but cross-tenant reuse of `shared` facts is switched off until the shared-facts decision in Part 10 closes. Turning it on must not need a migration.

### F2 — Identity

One permanent identifier per real-world thing. Names, domains and profile URLs are attributes, never keys.

| Table | Purpose | Key columns |
| --- | --- | --- |
| `entity` | One row per real-world thing | `id` (UUID), `entity_type`, `display_name`, `scope`, `status` (active, merged, retired), `merged_into` |
| `entity_alias` | Every handle that points at an entity | `entity_id`, `alias_type`, `value_normalised`, `confidence`, `source_record_id` |
| `account` | A workspace's working record on a company entity | `id`, `workspace_id`, `entity_id`, `owner_member_id`, `stage` (derived), `created_at` |

- **Entity types (V1):** `company` (any legal organisation, including public bodies), `person`, `site` (a physical location such as a plant, mine, port or office), `digital_asset` (a web page, profile or listing), `query` (a search or AI-answer query, for visibility use cases). New types are added by configuration plus a migration review, never per Brief.
- **Alias types, in matching priority:** `registry_id` (company number, tax ID) → `domain` → `profile_url` → `name_country` (normalised name plus country). Domains are not unique keys: parents share them with subsidiaries, and companies rebrand.
- **Merges never delete.** The losing entity gets `status = merged` and `merged_into`; its aliases move; every reference resolves through a canonical view (`entity_current`). A merge is reversible from the audit log.
- **Account versus entity:** the entity is the shared, factual thing. The account is one workspace's relationship with it: owner, stage, notes, tasks. Two workspaces can hold separate accounts on the same entity.

### F3 — Provenance and facts

The chain that makes every claim checkable.

| Table | Purpose | Key columns |
| --- | --- | --- |
| `source_record` | One fetched item, stored as fetched | `id`, `connector_id`, `source_type`, `url`, `fetched_at`, `content_hash`, `payload_ref`, `scope`, `language` |
| `mention` | A reference to a thing inside a source record, before identity is decided | `id`, `source_record_id`, `entity_type`, `raw_name`, `raw_domain`, `raw_country`, `entity_id` (null until resolved), `match_method`, `match_confidence` |
| `predicate` | The registry of things that can be claimed | `key`, `label`, `value_type`, `allowed_values`, `unit`, `decay_half_life_days`, `extraction_hint`, `scope` (global or workspace) |
| `claim` | One sourced fact about an entity | `id`, `entity_id`, `predicate_key`, `value`, `quote`, `source_record_id`, `observed_at`, `extracted_at`, `extractor_version`, `confidence`, `expires_at`, `status`, `scope` |

- **Raw records are immutable.** Payloads live in object storage; the row holds a reference and a content hash for deduplication.
- **Claims are facts, not opinions.** "Uses tool X" is a claim. "Matches the Brief" is a verdict (F7). This separation means one fact serves every Brief that cares about it.
- **Predicates are typed.** `value_type` is one of boolean, number, enum, text, date or entity reference. An extractor cannot write a value outside `allowed_values`. This stops one fact being stored three different ways.
- **Claims age.** `expires_at` is set from the predicate's decay setting. Expired claims stay for history but stop counting towards verdicts.
- **Claim status:** `active`, `superseded` (a newer claim on the same predicate replaced it), `retracted` (a human or a contradiction removed it).

### F4 — Relationships

One generic table for every link between entities.

| Column | Meaning |
| --- | --- |
| `from_entity_id`, `relation`, `to_entity_id` | The link, for example person `works_at` company, site `operated_by` company, company `resells` company, digital asset `belongs_to` company |
| `attributes` | Typed extras, for example title and persona on `works_at` |
| `valid_from`, `valid_to` | When the link held; employment ends rather than being deleted |
| `source_record_id`, `confidence`, `scope` | Provenance, as for claims |

Relations come from a registry like predicates. Employment is a relationship, so a person who changes company keeps one identity.

### F5 — Versioning

Anything that changes what a derived value means is versioned and immutable once published.

- `brief_version`: the full Brief at a point in time (M1).
- `extractor_version`: prompt, model and parser used to produce mentions and claims (S3).
- `rubric_version`: verdict rules and scoring weights, carried inside the Brief version.
- **Rule:** every verdict, score, label and research document stores the versions that produced it. Changing a Brief publishes a new version; it never overwrites one.

### F6 — Activity, outcomes and feedback

| Table | Purpose | Key columns |
| --- | --- | --- |
| `activity` | Append-only log of every touch and outcome | `id`, `workspace_id`, `account_id`, `person_entity_id`, `kind`, `channel`, `campaign_id`, `integration_id`, `external_id`, `occurred_at`, `payload` |
| `feedback` | Every human correction or confirmation | `id`, `member_id`, `target_type`, `target_id`, `action` (confirm, reject, correct), `corrected_value`, `reason`, `created_at` |

- `activity.external_id` plus `integration_id` is unique, so replays and backfills never double count.
- Activity kinds are a closed list (Appendix). Outcomes such as a reply, a held meeting and a created deal are activity kinds, not separate tables. A reply's class lives with the reply (M10).
- `feedback` is the platform's ground-truth store. V1 must record it even though learning arrives in V2 (Part 8).

### F7 — Derived tables

Never edited directly. Each is recomputed when its inputs change.

| Table | Derived from | Owner module |
| --- | --- | --- |
| `verdict` | Claims + Brief version rubric | M5 |
| `score` | Claims, verdict, contact points + Brief version weights | M8 |
| `label` | Brief rules over claims, relationships and sources | M8 |
| `account_stage_event` and `account.stage` | Activity | M11 |
| `account_card` | Claims, relationships, activity, research | M15 |
| `memory_chunk` | Source records, research documents, transcripts | M15 |

A human who disagrees with a derived value records `feedback`; the fix flows through the facts, never through an override on the derived row.

### F8 — Storage substrate

- **Postgres** for all structured data, with row-level security.
- **pgvector** in the same database for embeddings (M15). No separate vector store in V1.
- **Object storage** for raw payloads, uploads, research files and transcripts.
- **Reads above the API's page size** use server-side ID set functions, never long paginated selects.
- **Materialised projections** per heavy view (dashboards, KPIs, per-Brief flattened tables). Projections are disposable and rebuilt from the core tables; the core schema never bends for performance.

## Part 2 — Scaffolding

Scaffolding is the set of services every module stands on. No module may run its own scheduler, call an AI provider directly, emit its own webhooks or hold its own secrets. This is what keeps behaviour consistent as modules multiply.

### S1 — Job orchestrator and automation gate

- **Jobs** are durable, idempotent units of work (collect, extract, resolve, research, enrich, sync, recompute). Each has a type, inputs, an idempotency key, retries with backoff, a timeout and a per-type kill switch.
- **Schedules** live in the orchestrator, not in the database's cron or in module code.
- **Concurrency limits** per job type and per workspace, so one large expansion cannot starve other tenants.
- **Automation gate.** Every automated action carries a confidence from 0 to 100 and a reversibility flag:

| Confidence | Reversible action | Irreversible action |
| --- | --- | --- |
| 90 and above | Applied automatically, logged | Sent to the review queue |
| 40 to 89 | Sent to the review queue | Sent to the review queue |
| Below 40 | Reported in the weekly digest | Reported in the weekly digest |

Sending a message, writing to an external system and deleting data are irreversible for this purpose. Merges are reversible (F2), so they follow the reversible column. Thresholds are workspace settings with these defaults.

### S2 — Connector framework

One framework for everything that talks to the outside world, in either direction.

- **Connector definition:** kind (`source` or `destination`, or both), auth method, rate limits, cursor strategy, field mapping, supported access modes, health checks.
- **Source connectors** write only to `source_record`. Event connectors (sequencers, calendars, note-takers, CRMs) write engagement events to activity (through M10, M12 and M13) and documents such as transcripts to source\_record. Neither kind creates entities or claims directly.
- **Destination connectors** write only what their access mode and field ownership allow (M13).
- **Every connector instance** reports last success, last error, volume against its normal range, and credential expiry to M17.
- **Fetch safety:** an allowlisted egress proxy, robots and terms respected per source, no fetching of private network addresses.

### S3 — AI gateway

- **Provider-agnostic.** Modules call capabilities (extract, judge, summarise, embed, write), not vendors. The workspace may bring its own keys.
- **Prompt registry.** Every prompt is versioned; the version is recorded as `extractor_version` or on the derived row it produced (F5).
- **Typed outputs.** Responses are validated against the predicate registry and relationship registry before anything is written. Invalid output is rejected, not coerced.
- **Cost and latency** are recorded per call to the usage ledger (S6).
- **Evaluation hook.** Any prompt version can be run against a stored evaluation set before promotion (M18).

### S4 — Event bus

- **Transactional outbox:** an event is written in the same transaction as the change it describes, then published. No change is ever visible without its event.
- **Consumers:** the derivation engine (S5), webhooks (M13), notifications, the memory indexer (S9) and the health monitor (M17).
- **The event catalogue is closed** and listed in the Appendix. Adding an event is a reviewed change.

### S5 — Derivation engine

- Holds the dependency graph of derived tables (F7): claim → verdict → score → label → account card; activity → stage.
- Recomputes incrementally on events and fully on a new Brief version.
- Records, for every derived row, its inputs and the versions used, so any value can be explained and reproduced.
- **Shadow mode:** can compute a candidate version alongside the live one without exposing it, for previews (M1) and learning (M19).

### S6 — Metering and budgets

- **Usage ledger:** every billable unit (fetches, AI tokens, enrichment lookups, research documents) with workspace, Brief, job and cost.
- **Budgets:** per workspace and per Brief, daily and monthly caps. A job that would exceed a cap pauses and asks for approval.
- **Cost preview:** estimates the volume, cost and duration of any Brief edit before it runs (M1, "Narrow, Redefine, Expand").
- **Approval thresholds** are tied to roles (M14).

### S7 — Observability

Structured logs, metrics and traces for every job, connector and request, with tenant tags. Feeds the health loop (M17). Retention and redaction rules exclude personal data from logs.

### S8 — Security and secrets

- Secrets in a managed vault, never in code, environment files committed to source control, or logs. Rotation is scheduled and supported without downtime.
- Encryption in transit and at rest; field-level encryption for contact points and integration credentials.
- Scoped tokens for API and MCP (M13), with expiry and revocation.
- Every privileged write goes through audited server functions (M18). No client may call a write function anonymously.

### S9 — Search and index

- Full-text indexes on names, aliases, claims and documents.
- An indexing pipeline that chunks source records, research documents and transcripts, embeds them through S3, and writes `memory_chunk` rows tagged with entity, workspace, Brief, type and date (M15).
- Reindexing on merge: chunks follow the surviving entity.

## Part 3 — Structure: the intelligence modules

Eight modules turn a hypothesis into scored, researched, reachable accounts. Each is defined by what it owns, what it reads, what it emits and the rules it cannot break. A module writes only to the tables it owns.

### M1 — Brief Studio

**Purpose.** The input layer. The user describes what they are looking for, and every downstream module reads its behaviour from the result.

**Owns:** `brief`, `brief_version`, workspace-scoped `predicate` rows, `exclusion_list`.

**Brief contents**

| Field | Required | What it controls |
| --- | --- | --- |
| Offer | Yes | What is being sold, used in research angles and messaging |
| Hypothesis | Yes | The observable fact that marks a target, in plain language and as predicate conditions |
| Examples | Yes: at least 3 positive and 2 negative | Calibrate extraction and verdicts (M3, M5) |
| Sources | Yes: at least one radar | Where to look (M2) |
| Ideal customer profile | No | Verticals, geographies, size bands, commercial roles |
| Personas | No | Buyer, user and influencer titles to find (M7) |
| Predicates | No | Facts to collect beyond the hypothesis, such as team size or tools used |
| Evidence rules | No; defaults apply | What makes a verdict Confirmed, Likely or Contradicted (M5) |
| Seeds | No | Known customers for lookalike expansion |
| Exclusions | No | Customers, partners, competitors, do-not-contact lists |
| Research template | No; default applies | Sections of the research document (M6) |
| Scoring | No; defaults apply | Dimension weights, mappings and gates (M8) |
| Budget | No; workspace default applies | Spend caps for this Brief (S6) |
| Activation | No | Destination integrations and campaign mapping (M9) |

**How a Brief is created**

1. **Intake.** The user writes or speaks naturally. The AI gateway drafts a structured Brief.
2. **Edit.** The user sees fields as editable chips, never as raw configuration.
3. **Pilot.** The platform runs on a small sample (default 20 accounts) and shows verdicts and scores with their evidence.
4. **Calibrate.** The user marks verdicts right or wrong. Marks become `feedback` and labelled examples.
5. **Publish.** A `brief_version` is frozen and the Brief scales to its full scope.

**Brief states:** Draft → Pilot → Active ⇄ Paused → Archived.

**Edit tiers.** Every edit creates a new Brief version and shows a preview first: accounts entering or leaving, verdicts and bands that would change, cost and duration (S6). The tier sets the cost:

| Tier | Example edits | What runs | Cost |
| --- | --- | --- | --- |
| Narrow | Geography, size, persona filters, weights, exclusions | Recompute of scores and labels on existing facts | Instant, free, reversible |
| Redefine | Evidence rules, examples, a new predicate | Re-judging existing claims, or extraction over existing source records | Minutes, AI cost |
| Expand | New keywords, sources, geographies, hypothesis | New collection, extraction, resolution and research | Hours, real spend; may need approval |

**Adding a column.** Typing a question such as "Do they use product Y?" creates a typed predicate, writes its extraction hint, backfills it from existing source records (Redefine), and only then offers targeted collection (Expand) where evidence is missing.

**Emits:** `brief.version_published`.

**Acceptance criteria**

- A Brief cannot leave Draft without the required fields.
- No edit applies without a preview. An edit over budget waits for approval.
- Every derived row can name the Brief version that produced it.
- A user can compare any two Brief versions and see what changed.

### M2 — Source and Radar Hub

**Purpose.** Discovery. Turns a Brief into collection jobs across many source types.

**Owns:** `radar` (a configured source connector bound to a Brief), `radar_query`, `source_record`.

**Radar types (V1):** news and publications, job postings, public registries and permits, tenders and procurement, events (speakers and exhibitors), directories and partner lists, public social posts, company websites, CSV upload, customer CRM import. **V2:** AI-answer snapshots, review sites, marketplace listings.

**Rules**

- **Query generation:** the Brief's hypothesis, predicates and geographies become query sets, including local-language variants. Users can edit them.
- **Pre-filter:** a cheap lexicon check runs before AI extraction to save cost. Filtered records are counted, never silently dropped.
- **Freshness:** each radar keeps a cursor; only items newer than the cursor are processed. Sources that ignore date filters are deduplicated by content hash.
- **Targeted search:** M5 can request a narrow search for one entity when its verdict is Unverified.
- **Yield tracking:** per radar and query, the share of records that produce resolved entities passing the Brief's gates. Feeds budget allocation in V2.

**Emits:** `source_record.created`.

**Acceptance criteria:** re-fetching an item never creates a duplicate record; every record has a visible processing status; a radar with zero volume in its expected window raises a health alert (M17).

### M3 — Extraction

**Purpose.** Reads source records and writes mentions, claims and relationships.

**Owns:** `mention`, `claim` (as writer), `relationship` (as writer).

**Pipeline per source record**

1. **Relevance judge:** is this record about the hypothesis at all? Irrelevant records are marked, not deleted.
2. **Structured extraction** of entities, claims and relationships, limited to predicates in scope for the linked Briefs plus global predicates.
3. **Validation** against the predicate and relation registries (S3). Out-of-registry values are rejected.
4. **Quote capture:** a claim from text must carry the supporting quote. Non-English quotes keep the original and store a translation.

**Record statuses:** pending, extracted, irrelevant, failed. All four are counted on the radar's health view.

**Backfill mode:** when a predicate is added or an evidence rule changes, extraction re-runs over existing records for that predicate only.

**Emits:** `claim.created`.

**Acceptance criteria:** no claim without a source record; no claim value outside its predicate's type; the extractor version is recorded on every claim.

### M4 — Identity Resolution

**Purpose.** Decides which real-world entity each mention refers to. The only module that creates or merges entities.

**Owns:** `entity`, `entity_alias`, the resolution review queue.

**Matching:** aliases in priority order (registry ID → domain → profile URL → normalised name with country), then fuzzy name matching within country, then an AI tie-break for close candidates. The result is a match confidence from 0 to 100.

| Best match confidence | Action |
| --- | --- |
| 90 and above | Attach the mention to the entity |
| 40 to 89 | Send to the review queue; the mention waits |
| Below 40 | Create a new entity and attach the mention |

**Rules**

- **Duplicate sweep:** a scheduled job finds entity pairs that are likely the same and proposes merges through the automation gate (S1).
- **Merges** move aliases, claims, relationships, accounts and memory chunks to the survivor and leave a tombstone. Reversible from the audit log.
- **Parent and subsidiary** are a `subsidiary_of` relationship, never a merge.
- **Person resolution** uses profile URL, then email, then name plus current company, always within the tenant.

**Emits:** `entity.created`, `entity.merged`, `entity.unmerged`.

**Acceptance criteria:** no entity is keyed by a name or domain; every merge can be undone; the review queue shows both candidates side by side with their evidence.

### M5 — Evidence and Verdict

**Purpose.** Answers "does this entity match the hypothesis, and how do we know?" for each Brief.

**Owns:** `verdict` (derived).

**Verdict values:** Confirmed, Likely, Unverified, Contradicted.

**Rules**

- The rubric in the Brief version defines each value as conditions over claims: predicate, value, minimum number of independent sources, acceptable source types and maximum age.
- **Confirmed** needs at least one active, quoted claim from an acceptable source type.
- **Magnitudes need their own quote.** A claim that a quantity is above one (sites, seats, units) is never inferred from a claim that the thing exists.
- **Contradicted** means an active claim that explicitly negates the hypothesis, such as standardising on a rival product. A rival relationship can also feed the competitor gate in M8.
- **Conflicting claims** do not cancel silently; the entity goes to the verdict review queue with both claims shown.
- **Unverified** can trigger a targeted search (M2), within budget.
- Every verdict stores the claim IDs and quotes it rests on.

**Human checks:** a sample of new Confirmed verdicts goes to review each week (default 5 percent, minimum 10). Marks are stored as `feedback`.

**Emits:** `verdict.changed`, `claim.retracted`.

**Acceptance criteria:** clicking any verdict shows its quotes and sources; changing the rubric recomputes verdicts under a new Brief version and shows the diff before publishing.

### M6 — Research

**Purpose.** Produces a deep, sourced research document for an account that deserves the effort.

**Owns:** `research_doc` (versioned), `research_job`.

**Rules**

- **One account per job.** Batching multiple accounts into one generation is not allowed; it degrades quality.
- **Sections** come from the Brief's research template. Every factual sentence carries an inline source link.
- **Reuse:** when the entity already has a document under another Brief in the workspace, the new job writes a delta, not a rewrite.
- **Write-back:** facts found during research are written as claims, but only with the underlying fetched source as their `source_record`. The research document is never itself evidence.
- **Contacts found** become tenant-scoped person entities with `works_at` relationships, marked as discovered in research.
- **Triggers:** manual, or automatic for accounts that reach band High with verdict Confirmed or Likely (configurable).
- **Export** to a connected document destination is optional (M13).

**Emits:** `research_doc.published`.

**Acceptance criteria:** no section is empty without a stated reason; every link resolves to a stored source record; a document lists the Brief version it was written under.

### M7 — Enrichment

**Purpose.** Finds the right people at an account and verified ways to reach them.

**Owns:** `contact_point`, `enrichment_job`, suppression list (applied organisation-wide, M18).

**Rules**

- **Persona targeting** from the Brief: find people whose titles match buyer, user or influencer personas.
- **Provider waterfall** configured per workspace: providers are tried in order and stop at the first verified result.
- **Never pay twice:** no paid lookup for a person who already has a valid, recently verified contact point.
- **Verification gate:** a contact point is usable for activation only when its status is valid and its verification is recent (default 30 days; re-verified before push).
- **Caps:** per action and per day, enforced by S6.
- **Suppression:** do-not-contact and unsubscribed contact points are never enriched again and never pushed.

**Contact point fields:** person, type (email, phone, profile), encrypted value, verification status (valid, risky, invalid, unknown), verified at, provider, scope (always tenant).

**Emits:** `contact_point.verified`.

**Acceptance criteria:** spend per workspace never exceeds its cap; the account page shows coverage per persona and why any persona is uncovered.

### M8 — Scoring and Segmentation

**Purpose.** Ranks accounts and slices them into segments.

**Owns:** `score`, `label`, `segment` (saved query).

**Five dimensions, each 0 to 100:**

| Dimension | Question | Typical inputs |
| --- | --- | --- |
| Fit | Is this the right kind of company? | Vertical, size, geography, commercial role |
| Evidence | How sure are we the hypothesis holds? | Verdict, quote count, source quality, recency |
| Scale | How big is the opportunity? | Quantity predicates such as sites, seats, units |
| Timing | Why now? | Recent signals, hiring, tenders, regulatory triggers |
| Reachability | Can we reach the right people? | Verified contact points per persona |

**Outputs per account and Brief version**

- **Gates** applied first. Any match sets band Excluded: an exclusion list, verdict Contradicted, or a competitor relationship. Verdict Unverified caps the band at Low.
- **Priority:** weighted combination of Fit, Evidence, Scale and Timing, using the Brief's weights.
- **Readiness:** the Reachability score, kept separate so a high-priority, unreachable account is visible as an enrichment task rather than hidden.
- **Band:** High, Medium, Low or Excluded, from priority thresholds in the Brief.
- **Explanation:** each component, the claims behind it and the gates applied.

**Rules**

- Claim weight decays with age using the predicate's half-life.
- Engagement (replies, opens, stage) never feeds priority. It measures the outreach, not the account.
- **Labels are lenses:** which radar found the account, commercial role, vertical, geography, persona coverage, Brief. They are derived and never edited by hand.
- **Segments** are saved queries over claims, labels, verdicts and scores. A new segment never needs a new table.

**Emits:** `score.band_changed`.

**Acceptance criteria:** every score can be explained in the interface; weights changed in the Brief recompute under a new version; an account's band can always be traced to specific claims.

## Part 4 — Systems: from account to conversation

These modules take ranked accounts into the world and bring every result back into the same record. They never compute verdicts or scores; they read them.

### M9 — Activation

**Purpose.** Moves the right people from ranked accounts into outreach in a connected sequencer, with clean attribution.

**Owns:** `campaign`, `campaign_member`.

**Campaign** is the platform's record of an outreach motion, linked explicitly to a destination campaign in a sequencer. Fields: workspace, Brief, segment, motion, destination integration, external campaign ID, push mode, status.

**Motions** (campaign families, used for reporting): Geographic, Vertical, Event, Trigger, Re-engagement. A workspace may rename them but not remove the field.

**Rules**

- **Attribution is explicit.** A campaign counts because it is linked in the platform, never because of its name in the sequencer.
- **Only verified contact points** are pushed (M7). Suppressed and unsubscribed people are never pushed.
- **One active campaign per person** by default. A person already in an active campaign is held back, with the reason shown.
- **Push modes:** off, dry run (shows exactly what would be sent where), live. Every new destination starts in dry run.
- **Bounce guard:** a warning at 5 percent bounces in a campaign, and pushes pause automatically at 10 percent until the list is re-verified. Thresholds are workspace settings.
- **Personalisation variables** are passed as custom fields: the trigger signal, the supporting quote, the research document link, the persona. Copy suggestions from the AI gateway always need human approval in V1. Each sent message records the variables and copy variant it used (Part 8).

**Emits:** `campaign.member_added`, `campaign.paused`.

**Acceptance criteria:** a dry run matches the later live push exactly; no person is pushed without a valid, recent verification; pausing a campaign stops further pushes within one sync cycle.

### M10 — Engagement and Reply Handling

**Purpose.** Captures every response, classifies it, and turns it into work.

**Owns:** reply classification state, the unified inbox.

**Ingestion.** Sequencer events arrive by webhook and are reconciled by scheduled polling. Both run, because webhooks fail silently. Each event is recorded as an activity kind (Appendix A.3).

**Reply classes**

| Class | Counts as genuine | Typical follow-on |
| --- | --- | --- |
| Interested | Yes | Task to respond; optional deal creation |
| Referral | Yes | Task to contact the referred person; new person entity |
| Not now | No | Re-engagement date set |
| Not interested | No | Account flagged; may lead to Disqualified |
| Out of office | No | Sequence continues; never advances stage |
| Unsubscribe | No | Suppression applied immediately |

**Classification rules**

- A human mark always wins, and the latest human mark is the truth.
- Automatic classification (sequencer signal or AI) counts only when no human mark exists.
- Every human mark is stored as `feedback`.

**Notifications.** The first reply in each thread posts a card to the team's chat tool with actions: classify, create a task, create a deal in the CRM. Later replies in the same thread update the card rather than posting again.

**Deal creation** in an external CRM is an irreversible write, so it needs a human click by default (S1) and a write-capable access mode on deals (M13).

**Emits:** `reply.received`, `reply.classified`.

**Acceptance criteria:** an event received twice is recorded once; unclassified replies older than two working days appear in the review queue; a referral is never counted as a rejection.

### M11 — Account Workspace

**Purpose.** The single place where a person works an account. Also the platform's lightweight system of record for outbound.

**Owns:** `account` (with F2), `account_stage_event`, comments, files.

**The account page shows:** identity and aliases; verdict with quotes; score with its explanation; labels; people and an org chart; the activity timeline; research documents; relationships (partners, sites, parent companies); tasks and the next step; the account card (M15); comments and files.

**Stage machine** (derived from activity only):

| Stage | Entered when |
| --- | --- |
| Untouched | The account exists in the workspace with no outreach |
| Queued | A person from the account is in an active campaign, nothing sent yet |
| Contacted | At least one message has been sent |
| Engaged | A genuine reply (Interested or Referral) or an inbound contact |
| Qualified | A human marks it qualified, or a CRM deal is created |
| Meeting | A meeting is held (M12 proof rule) |
| Handed off | The CRM deal reaches the workspace's hand-off stage |
| Dormant (exit) | No genuine reply within the dormancy window after the last touch (default 45 days) |
| Disqualified (exit) | A human marks it disqualified, with a reason |

Notes, comments and out-of-office replies never move a stage. A new genuine reply or Timing signal moves a Dormant account back into the flow.

**Ownership.** Each account has an owner and a team. Assignment rules map labels to teams (for example, a vertical to a team). Reassignment is logged.

**Views.** A table with columns from any predicate, filters, saved views and export; a board by stage; a map for site entities.

**Emits:** `account.stage_changed`.

**Acceptance criteria:** no field on the page can be edited if it is derived; every write is audited; the timeline shows activity from every connected source in one order.

### M12 — Meetings, Tasks and Next Steps

**Purpose.** Makes sure every live account has a next action and that meetings are counted honestly.

**Owns:** `meeting`, `task`, `next_step`.

**Meetings**

- Calendar events are synced and matched to accounts through attendee contact points and domains. Unmatched meetings are kept in a review list, never dropped.
- **Proof rule:** a meeting counts as held only with a transcript or notes from a connected note-taker, or an explicit human confirmation. Reports show the two separately.
- **Attribution:** a meeting is outbound-attributed if a campaign touch reached the same person, then the same account, within the attribution window (default 90 days). The tier used is recorded.
- **Headline metric:** companies met, meaning distinct accounts with a first held meeting. Raw meeting counts are never used as a performance figure.
- Transcripts are tenant-scoped source records. An AI pass extracts tasks and claims from them, through M3.

**Tasks.** Owner, account, due date, origin (manual, reply, meeting, next-step engine), status. Daily reminders go to each owner through their chat tool or email.

**Next-step engine.** For every account in Contacted, Engaged, Qualified or Dormant, rules choose the next step (for example, follow up after silence, re-engage on a new Timing signal) and the AI gateway writes the wording.

- **Suggest mode** (default): steps appear on the account page and in the daily digest.
- **Active mode:** steps become tasks automatically. A workspace setting, switched on deliberately.

**Emits:** `meeting.held`, `task.created`.

**Acceptance criteria:** no live account without a next step or an explicit hold reason; companies met is computed from held meetings only; switching between Suggest and Active is audited.

## Part 5 — Utilities: integrations, access and memory

Utilities connect the building to the outside world and control who may enter which room. They are built on S2 (connectors), S4 (events) and S8 (security).

### M13 — Integrations Hub

**Purpose.** Every way data enters or leaves the platform: connectors, the public API, webhooks, MCP, import and export.

**Owns:** `integration` (a connected instance), `field_ownership`, `webhook_subscription`, `webhook_delivery`, `api_token`, `external_write_log`.

**Integration categories**

| Category | Examples | Typical access mode |
| --- | --- | --- |
| Sources | News, jobs, registries, tenders, events, directories | Read |
| Sequencers | Email and multichannel outreach tools | Read-write |
| CRMs | Account, contact and deal systems | Read by default |
| Chat | Team chat tools for cards and digests | Write (cards, digests); Read (button actions) |
| Calendars and note-takers | Calendar, meeting recorder, transcription | Read |
| Documents | Document and file storage | Write (export), Read (import) |
| Data warehouses (V2) | Analytics destinations | Write |

**Access modes.** An Admin chooses the mode for each integration and, within it, for each object type (accounts, contacts, deals, activities, tasks).

| Mode | The platform may | The platform may not |
| --- | --- | --- |
| Read | Pull data and events | Change anything in the external system |
| Write | Push or create records | Read external records beyond confirming its own writes |
| Read-write | Both, under field ownership | Overwrite a field it does not own |

- **New connections default to Read.** Moving to Write or Read-write is an explicit Admin action, logged in the audit trail.
- **Field ownership (Read-write only):** each mapped field has one owner, Platform or External. The owner's value wins. A change from the non-owner is recorded as a conflict and shown, not applied.
- **Write safety:** every external write has an idempotency key, passes the automation gate (S1), and is recorded in `external_write_log` with the external ID. New write paths start in dry run.
- **Health:** every integration shows last success, last error, volume against normal and credential expiry (M17).

**Public API**

- REST, versioned (`/v1`), described by an OpenAPI specification.
- Resources: entities, accounts, claims, verdicts, scores, labels, segments, briefs, campaigns, activity, tasks, meetings, research documents.
- Cursor pagination on every list; idempotency keys on every create; rate limits per token; filtering by Brief, segment, band, stage and updated time.
- Derived resources (verdicts, scores, stages, labels) are read-only through the API, as in the interface.

**Webhooks**

- Workspaces subscribe to events from the closed catalogue (Appendix).
- Payloads are signed with a per-subscription secret. Retries use exponential backoff for up to 24 hours.
- A delivery log shows every attempt; any delivery can be replayed.
- A subscription that fails repeatedly is disabled automatically and raises an alert.

**MCP server.** Exposes the same capabilities as the API as tools for AI agents. Tools respect token scopes. Writes are attributed to the token's owner; shared service tokens are read-only.

**Tokens.** Scoped to a workspace, with scopes Read, Write or Admin, an expiry, last-used time and one-click revocation.

**Import and export.** CSV import goes through a mapping wizard and lands as tenant-scoped source records, so imported rows are resolved like any other evidence. Export of any view follows the role's export permission (M14).

**Acceptance criteria:** no integration can write without an Admin selecting a write-capable mode; a field-ownership conflict is never resolved silently; every webhook delivery can be traced and replayed.

### M14 — Organisations and Access

**Purpose.** Who belongs to which tenant, what they may do, and what they may spend.

**Owns:** `organisation`, `workspace`, `member`, `team`, `role_assignment`, `invitation`, `auth_config`.

**Structure.** Owner and Admin roles are held at organisation level. Editor, Operator and Viewer are assigned per workspace, so one person can be an Editor in one workspace and a Viewer in another.

**Permission matrix**

| Action | Owner | Admin | Editor | Operator | Viewer |
| --- | --- | --- | --- | --- | --- |
| Billing, plan, delete organisation | Yes | No | No | No | No |
| Manage members, roles, teams | Yes | Yes | No | No | No |
| Connect integrations, set access modes, field ownership | Yes | Yes | No | No | No |
| Create API and MCP tokens | Yes | Yes | Read scope only | No | No |
| Create and edit Briefs, publish versions | Yes | Yes | Yes | No | No |
| Approve Expand edits within budget | Yes | Yes | Yes | No | No |
| Approve spend above budget | Yes | Yes | No | No | No |
| Work review queues (identity, verdict) | Yes | Yes | Yes | No | No |
| Run enrichment within caps | Yes | Yes | Yes | Yes | No |
| Manage campaigns, push live | Yes | Yes | Yes | Yes | No |
| Classify replies, manage tasks, comment | Yes | Yes | Yes | Yes | No |
| Export data that includes people | Yes | Yes | Yes | No | No |
| View accounts, research, reports | Yes | Yes | Yes | Yes | Yes |

**Teams** group members for account ownership and routing (M11). They do not grant permissions.

**Sign-in.** V1: Google and Microsoft sign-in, with an allowed email domain per organisation. V2: SAML single sign-on and SCIM provisioning.

**Rules:** every role, team and permission change is audited; removing a member revokes their tokens immediately; at least one Owner must always exist.

**Acceptance criteria:** a Viewer cannot reach any write path through the interface, API or MCP; an over-budget job cannot start without an Admin or Owner approval.

### M15 — Memory and Retrieval

**Purpose.** Makes everything known about an account retrievable by people and agents, in the right amount, with sources.

**Owns:** `account_card`, `memory_chunk`, retrieval services.

**Three layers of memory**

| Layer | Contents | Retrieved by |
| --- | --- | --- |
| Facts | Claims and relationships, with provenance | Exact structured queries |
| Episodes | Activity, meetings, tasks, replies | Account and time filters |
| Documents | Chunks of source records, research documents, transcripts | Semantic search within a filtered set |

**Account card.** A short, derived summary per account and Brief: who they are, the evidence, key people, history so far, open questions, the next step. Regenerated when its inputs change; every line cites the claim or activity it rests on; never edited by hand. It is the first thing loaded into an agent's context.

**Retrieval order**

1. Structured filters first: this workspace, this account, this Brief, a time window.
2. Semantic search inside that subset (S9).
3. Relationship expansion, one or two hops, for context such as parent, partners and sites.

**Rules**

- **Facts come from claims, not embeddings.** A question like "how many sites?" is answered from the latest active claim, never from the nearest-sounding text chunk.
- **Consolidation:** a scheduled job merges duplicate facts, flags contradictions to M5 and marks expired claims.
- **Isolation:** retrieval runs under the caller's row-level security. Shared facts appear only when cross-tenant sharing is enabled (F1).

**Interfaces:** in-app questions on the account page; API endpoints; MCP tools such as `get_account_card`, `search_account`, `get_claims` and `find_accounts`.

**Acceptance criteria:** every statement in a card links to its source; an answer to a factual question cites the claim it used; a merged entity's memory is found under the survivor.

## Part 6 — Finishes: screens and reporting

The interface is deliberately small: a handful of screens over one data model, rather than a page per source or module. Every screen reads derived values and writes only through audited functions.

### 6.1 Screens

| Screen | Job | Main modules |
| --- | --- | --- |
| Home | Daily briefing: key metrics, review queues waiting, accounts that moved, health alerts | M16, M17, M12 |
| Brief Studio | Create, pilot, calibrate and edit Briefs with cost previews | M1 |
| Discovery | Radars per Brief, their queries, volume, yield and processing status | M2, M3 |
| Accounts | Table with predicate columns, saved views, segments; board by stage; map of sites | M8, M11 |
| Account page | Everything about one account (M11 lists the contents) | M11, M15 |
| Inbox | All replies with classes, one-click actions | M10 |
| Campaigns | Linked campaigns by motion, push mode, health, bounce guard | M9 |
| Meetings and Tasks | Meetings with proof status, tasks, next steps | M12 |
| Review | One queue for every human decision: identity matches, verdicts, replies, unmatched meetings, gated actions | M4, M5, M10, M12, S1 |
| Reports | Funnel, cohorts, motion and segment cuts | M16 |
| Settings | Integrations and access modes, members and roles, budgets, tokens, health | M13, M14, S6, M17 |

**Interaction rules**

- **Derived values are visibly derived.** Verdicts, scores, stages, labels and cards show an "explain" action and have no edit control. Disagreement is recorded as feedback.
- **Every number links to its rows.** Clicking a metric opens the accounts or events behind it.
- **Cost before action.** Any action that spends (Expand edits, enrichment, research) shows its estimate first.
- **One review queue.** Every human decision lands in Review, ordered by how much the answer matters, so nothing waits in a hidden corner.
- **Mobile** supports reading, triage of replies and the review queue; configuration stays on desktop.

### M16 — Reporting and Analytics

**Purpose.** One definition of every metric, used everywhere.

**Owns:** `metric_definition` (versioned), metric projections, scheduled reports.

**Rules**

- **Define once.** Each metric is a single versioned function. The dashboard, Home, scheduled reports, the API and MCP all call the same function. No screen computes its own version.
- **Outbound-only counts.** Meetings and deals count towards outreach performance only when attributed to a linked campaign (M9, M12).
- **Every report shows its metric versions** and the period's data-completeness status from M17, so a report covering an outage says so.

**Canonical metrics**

| Metric | Definition |
| --- | --- |
| Accounts discovered | Company accounts in the workspace for a Brief |
| Accounts verified | Accounts with verdict Confirmed or Likely |
| Accounts researched | Accounts with a published research document |
| Accounts reachable | Accounts with at least one verified contact point in a Brief persona |
| Accounts contacted | Distinct accounts with at least one message sent from a linked campaign |
| People contacted | Distinct people with at least one message sent |
| Messages sent | Messages sent from linked campaigns |
| Genuine replies (people) | Distinct people whose latest classified reply is Interested or Referral |
| Genuine replies (accounts) | Distinct accounts with at least one genuine reply |
| Companies met (headline) | Distinct accounts with a first held, attributed meeting |
| Meetings held | All held, attributed meetings; supporting figure only |
| Pipeline deals and value | Attributed deals and value read from the connected CRM |
| Reply rate | Genuine replies (people) per 1,000 messages sent |
| Bounce rate | Bounced messages as a share of messages sent |
| High-band coverage | Share of High-band accounts that have been contacted |
| Stalled accounts | Accounts in Contacted longer than the stall threshold with no next step |

**Cuts:** Brief, motion, segment, band, verdict, vertical, geography, persona, team and period.

**Funnel view:** stage counts with the conversion between each pair and the median time spent in each stage.

**Scheduled reports** go to chat or email and can export to a spreadsheet destination (M13).

**Acceptance criteria:** the same metric shows the same value on every surface for the same filters; every figure links to its underlying rows; a metric definition change creates a new version and the old reports remain reproducible.

## Part 7 — Inspection: health, quality, audit and compliance

A building is inspected continuously, not once. These two modules make failures loud, keep facts honest and keep the platform lawful.

### M17 — Health and Observability

**Purpose.** Detects when anything stops working, including when it fails silently.

**Owns:** `health_check`, `health_incident`, `data_completeness`.

**What is watched**

| Area | Signals | Silence rule |
| --- | --- | --- |
| Source connectors and radars | Records fetched, extraction statuses, errors, credential expiry | Volume below its expected window raises an incident |
| Destination integrations | Writes, conflicts, rejections | Repeated rejections raise an incident |
| Event ingestion (webhooks) | Events received versus events found by reconciliation polling | Any gap raises an incident and triggers a backfill |
| Jobs | Queue depth, failure rate, age of oldest job | Stuck jobs raise an incident |
| Derivation | Lag between a fact change and its derived rows | Lag beyond threshold raises an incident |
| Deployments | Build and release status | A failed build raises an incident |
| Spend | Burn against budgets (S6) | Unusual burn raises an incident |
| Outreach | Bounce rates per campaign (M9) | Above threshold pauses pushes |
| Review queues | Age of the oldest waiting item | Items waiting too long raise an incident |

**Rules**

- **Silence is failure.** Every pipeline has an expected volume window; zero activity inside it is an incident, not a quiet day.
- **End-to-end canary:** a synthetic source record passes through extraction, resolution, verdict and scoring daily in every environment.
- **Alerts** go to the chat tool and email of the workspace's Admins and the module's named owner, with severity and a link to the failing component.
- **Data completeness** is recorded per workspace and day. Reports (M16) show it beside any period that had an incident.
- **Health digest:** a daily summary of incidents opened, resolved and still open.

**Emits:** `health.incident_opened`, `health.incident_resolved`.

**Acceptance criteria:** stopping any single connector or webhook produces an alert within its expected window; every incident has an owner and a resolution note.

### M18 — Quality, Audit and Compliance

**Purpose.** Keeps facts trustworthy, changes traceable and personal data handled lawfully.

**Owns:** `evaluation_set`, `audit_log`, `quarantine`, `data_subject_request`, `lawful_basis_record`.

**Quality**

- **Evaluation sets** per Brief are built from feedback: confirmed and rejected verdicts, identity decisions, reply classes.
- **Promotion gate:** a new prompt or extractor version is promoted only if it scores no worse than the current one on the evaluation set.
- **Sampling:** the weekly verdict review in M5 (5 percent of new Confirmed verdicts, minimum 10) feeds accuracy measures for extraction and verdicts.
- **Provenance audit:** any fact or entity without provenance is quarantined, not deleted, until a source is attached or it is rejected. Uploads always carry the uploader, file and time as their source record.

**Audit log**

- Append-only. Each entry records actor, action, target, before and after values, time and channel (interface, API, MCP or system).
- Covers every write, merge and unmerge, role change, access-mode change, field-ownership change, export, deletion and gated automatic action.

**Compliance**

- **Lawful basis** is recorded for each source of personal data and shown on the person record.
- **Data subject requests:** access and deletion are handled per organisation. Deletion removes the person entity's personal fields and contact points and adds the person to the suppression list; aggregate counts remain.
- **Retention:** configurable per organisation for raw payloads, transcripts and personal data.
- **Organisation-wide suppression:** a do-not-contact or unsubscribe in one workspace applies to every workspace in the organisation.
- **Source terms:** connectors respect each source's terms. Sources that need a person's own login session are not supported in V1; they are fragile and carry terms-of-service risk.
- **Security assurance:** scheduled key rotation, dependency scanning and an external penetration test before general availability.

**Acceptance criteria:** any value on screen can be traced to its sources and its change history; a deletion request completes within the organisation's configured window and leaves no contact point behind.

## Part 8 — Extensions: the self-learning layer

Learning ships in V2, but V1 must already record everything V2 will learn from. Data that was never captured cannot be recovered later.

### 8.1 V1 capture requirements (release gate for V1)

- [ ] Every human correction or confirmation is stored in `feedback` (F6): verdicts, identity matches, reply classes. Qualification and disqualification marks are activity kinds (A.3).
- [ ] Every outcome (genuine reply, held meeting, deal) is an activity linked to its account, person, campaign and the Brief version active at send time.
- [ ] Every derived row records the Brief, rubric and extractor versions that produced it (F5).
- [ ] Every radar and query records its yield (M2).
- [ ] Every sent message records which personalisation variables and copy variant it used (M9).
- [ ] Evaluation sets exist per Brief from the first pilot onwards (M18).

### M19 — Learning Loops (V2)

**Purpose.** Improves discovery, verdicts, scoring and messaging from real outcomes, with minimal human time.

**Owns:** `lesson`, `learning_run`, candidate versions held in shadow (S5).

**Four loops**

| Loop | Learns from | Changes | Promotion |
| --- | --- | --- | --- |
| Discovery | Radar and query yield | Budget split across radars and queries; new queries from terms in confirmed accounts | Automatic within the Brief budget |
| Verdicts | Feedback on verdicts and later contradicting or confirming claims | Examples in extraction and verdict prompts | Evaluation-set gate (M18) |
| Scoring | Outcomes against score components | Dimension weights and mappings | Shadow run, then human approval |
| Messaging | Genuine reply rates by variable and variant | Default variables and copy suggestions | Human approval |

**Lessons.** Short, sourced statements at Brief or workspace level, such as "one commercial role rarely replies in this geography" or "this source over-reports adoption". Agents read lessons before research and scoring. Each lesson cites the outcomes behind it and can be accepted, edited or rejected by an Editor.

**Guardrails**

1. **External truth only.** Labels come from `feedback` and outcomes, never from the platform's own verdicts, scores or research.
2. **Rewards that cannot be gamed.** The target is genuine replies and companies met, never raw replies or opens.
3. **Minimum evidence.** No weight changes before a minimum number of outcomes (default 30 per Brief), and no weight moves more than a set share per cycle (default 20 percent).
4. **Shadow first.** Every candidate runs beside the live version, is promoted only if it performs better, and can be rolled back in one action.
5. **Same gate as everything else.** Changes pass through the automation gate (S1); learning has no private path to production.

**Human time.** A weekly learning digest lists the items where a human answer would teach the most, typically about ten: borderline verdicts, uncertain identity matches, ambiguous replies. Humans stop teaching every case and answer only the hard ones.

**Acceptance criteria (V2):** every promoted change has a before and after measurement; any change can be rolled back; no learning run uses platform-generated values as labels.

## Part 9 — Build sequence

V1 is built bottom-up in four phases, each closed by a gate; nothing in a phase may ship until the gate below it has passed. Learning (V2) starts only when V1 has been capturing feedback and outcomes in production.

&#91;embedded content: build sequence · 4 V1 phases, 4 gates, then V2 and V3\]

Phase 3 is the V1 general availability release; its gate includes the capture checklist from Part 8.

**Sequencing rules**

- **Two industries from day one.** Every phase is tested on at least two unrelated Briefs (for example, users of a design software tool and operators of a physical asset). One industry alone will leak its assumptions into the schema.
- **Foundation changes are migrations, reviewed.** Once Phase 0 passes, a change to F1 to F8 needs a written migration plan and a backfill.
- **Design can run ahead; shipping cannot.** Interface and connector design for a later phase may start early, but no module goes live before its gate.
- **Kill switches before launch.** Every job type, connector and automated action has a kill switch before it is enabled in production.
- **Dry run before live.** Every write path to an external system ships in dry run first (M9, M13).

## Part 10 — Risks and open decisions

The largest risk is not technical: in industries where adoption leaves no public trace, the platform's advantage shrinks to that of an ordinary enrichment tool. The pilot step exists to reveal this early, per Brief.

### 10.1 Risks

| Risk | Impact | Mitigation | Where |
| --- | --- | --- | --- |
| Target industry has a weak public footprint | Few Confirmed verdicts; product feels generic | Pilot shows verdict coverage before scaling; Brief Studio warns when the pilot yields few Confirmed accounts | M1, M5 |
| Setting up a new industry needs an expert | Slow onboarding, churn after first Brief | Conversational intake, pilot and calibrate flow; industry templates in V3 | M1 |
| AI verdicts drift towards "confirmed" | Users stop trusting the evidence | Quotes required; weekly sampling; evaluation-gated prompt changes | M5, M18 |
| Pipelines fail silently | Missing replies and wrong metrics for weeks | Silence-is-failure rule; reconciliation polling; daily canary | M17 |
| Wrong merges corrupt accounts | Activity and evidence attached to the wrong company | Review queue for mid-confidence matches; reversible merges | M4 |
| Personal data regulation | Fines, blocked sales | People always tenant-scoped; lawful basis records; deletion workflow | F1, M18 |
| Sources block collection or change terms | Radars go dark | Connector health; no login-based sources; multiple radars per Brief | S2, M17 |
| Expand edits cause cost spikes | Bills that surprise customers | Cost preview, budgets and approvals | M1, S6 |
| Poor lists damage sending reputation | Deliverability loss across the customer's domain | Verification gate; bounce guard pauses pushes | M7, M9 |
| Generic schema is slow at scale | Slow screens, timeouts | Per-Brief materialised projections; ID-set reads | F8 |
| Scope creep into a full CRM | Diluted product, sync conflicts | Non-goal; field ownership; CRM stays the deal system | M11, M13 |

### 10.2 Open decisions

- [ ] Cross-tenant shared fact layer: legal basis, opt-in model and commercial terms (blocks V2 sharing).
- [ ] Pricing unit: credits per fetch, research and enrichment, seats, or a mix.
- [ ] Default AI providers and the bring-your-own-key policy.
- [ ] The first two sequencers and first two CRMs to support in V1.
- [ ] Data residency needs of the first customers.
- [ ] The verdict accuracy target used at Gate B.
- [ ] The two industries used to test every phase.
- [ ] Product name.

## Appendix — Canonical names

This appendix is the source of truth for names, states and events. Any section that disagrees with it is wrong.

### A.1 Glossary

| Term | Meaning | Not to be confused with |
| --- | --- | --- |
| Organisation | The customer tenant | Company (an entity type) |
| Workspace | A team or business unit inside an organisation | Brief |
| Brief | One hypothesis with its configuration; versioned | Segment |
| Brief version | An immutable snapshot of a Brief | — |
| Entity | One real-world thing with a permanent UUID | Account |
| Company | Entity type for any legal organisation, including public bodies | Organisation (tenant) |
| Account | A workspace's working record on a company entity | Entity |
| Source record | One fetched item, stored as fetched | Claim |
| Mention | A reference to a thing inside a source record, before resolution | Entity |
| Predicate | A typed kind of fact in the registry | Label |
| Claim | One sourced fact about an entity | Verdict |
| Verdict | Whether an entity matches a Brief's hypothesis, with evidence | Score |
| Score | Priority and readiness for an account under a Brief version | Band |
| Band | High, Medium, Low or Excluded | Stage |
| Label | A derived lens on an account, such as the radar that found it | Segment |
| Segment | A saved query over claims, labels, verdicts and scores | Brief |
| Radar | A configured source connector bound to a Brief | Integration |
| Integration | A connected instance of an external system, with an access mode | Radar |
| Motion | A family of campaigns, used for reporting | Campaign |
| Campaign | The platform's record of an outreach motion linked to a sequencer | Motion |
| Exclusion list | Companies a Brief must not target | Suppression list |
| Suppression list | People who must never be contacted; organisation-wide | Exclusion list |
| Companies met | Distinct accounts with a first held, attributed meeting; the headline metric | Meetings held |
| Feedback | A human correction or confirmation, used as ground truth | Activity |
| Lesson | A sourced learning statement (V2) | Feedback |

### A.2 Closed value lists

| List | Values |
| --- | --- |
| Entity type | company, person, site, digital\_asset, query |
| Alias type (matching priority) | registry\_id, domain, profile\_url, name\_country |
| Data scope | shared, tenant |
| Claim status | active, superseded, retracted |
| Extraction record status | pending, extracted, irrelevant, failed |
| Brief state | Draft, Pilot, Active, Paused, Archived |
| Edit tier | Narrow, Redefine, Expand |
| Verdict | Confirmed, Likely, Unverified, Contradicted |
| Score dimension | Fit, Evidence, Scale, Timing, Reachability |
| Band | High, Medium, Low, Excluded |
| Stage | Untouched, Queued, Contacted, Engaged, Qualified, Meeting, Handed off; exits Dormant, Disqualified |
| Reply class | Interested, Referral, Not now, Not interested, Out of office, Unsubscribe |
| Motion | Geographic, Vertical, Event, Trigger, Re-engagement |
| Push mode | off, dry run, live |
| Access mode | Read, Write, Read-write |
| Field owner | Platform, External |
| Contact verification status | valid, risky, invalid, unknown |
| Role | Owner, Admin, Editor, Operator, Viewer |
| Token scope | Read, Write, Admin |
| Next-step mode | Suggest, Active |
| Automation gate outcome | applied, review queue, digest |

### A.3 Activity kinds

message\_sent, message\_bounced, reply\_received, auto\_reply, unsubscribed, inbound\_contact, meeting\_scheduled, meeting\_held, call\_logged, note\_added, marked\_qualified, marked\_disqualified, deal\_created, deal\_stage\_changed, task\_completed.

### A.4 Event catalogue

| Event | Emitted by |
| --- | --- |
| `brief.version_published` | M1 |
| `source_record.created` | M2 |
| `claim.created`, `claim.retracted` | M3, M5 |
| `entity.created`, `entity.merged`, `entity.unmerged` | M4 |
| `verdict.changed` | M5 |
| `research_doc.published` | M6 |
| `contact_point.verified` | M7 |
| `score.band_changed` | M8 |
| `campaign.member_added`, `campaign.paused` | M9 |
| `reply.received`, `reply.classified` | M10 |
| `account.stage_changed` | M11 |
| `meeting.held`, `task.created` | M12 |
| `health.incident_opened`, `health.incident_resolved` | M17 |

### A.5 Module index

| ID | Name | Part |
| --- | --- | --- |
| F1–F8 | Tenancy, Identity, Provenance and facts, Relationships, Versioning, Activity and feedback, Derived tables, Storage | 1 |
| S1–S9 | Jobs and automation gate, Connectors, AI gateway, Event bus, Derivation engine, Metering, Observability, Security, Search and index | 2 |
| M1–M8 | Brief Studio, Source and Radar Hub, Extraction, Identity Resolution, Evidence and Verdict, Research, Enrichment, Scoring and Segmentation | 3 |
| M9–M12 | Activation, Engagement and Reply Handling, Account Workspace, Meetings Tasks and Next Steps | 4 |
| M13–M15 | Integrations Hub, Organisations and Access, Memory and Retrieval | 5 |
| M16 | Reporting and Analytics | 6 |
| M17–M18 | Health and Observability; Quality, Audit and Compliance | 7 |
| M19 | Learning Loops (V2) | 8 |

### A.6 Consistency verification record

Two passes were run over the whole document on 5 Nov 2026. Every item below was checked against this appendix; the fixes listed were applied.

| Check | Result |
| --- | --- |
| "Organisation" means the tenant only | Fixed: seven places used it for target companies; now "company" |
| Every module's emitted events match the catalogue (A.4) | Fixed: added missing declarations to M1, M3, M4, M5, M11, M12, M17 |
| Automation gate thresholds (90 / 40) | Consistent across S1, M4 and Part 0 |
| Merge reversibility | Fixed: S1 had listed merges as irreversible; F2 and M4 make them reversible |
| Where engagement events and transcripts are written | Fixed: S2 now routes events to activity and transcripts to source records |
| Reply class versus activity kinds versus feedback | Fixed: F6 and Part 8.1 now separate reply classes, activity kinds and feedback |
| Qualification and disqualification marks | Activity kinds (A.3); drive the stage machine in M11 |
| Verdict sampling rate (5 percent, minimum 10) | Consistent across M5 and M18 |
| Bounce guard (warn 5 percent, pause 10 percent) | Consistent across M9 and M17 |
| Dormancy (45 days) and attribution window (90 days) | Consistent across M11, M12 and M16 |
| Roles, token scopes and spend approvals | Consistent across M1, M13, M14 and S6 |
| Integration access modes | Fixed: chat integrations also read button actions; CRM deal creation now needs a write-capable mode |
| Suppression list ownership | Fixed: owned by M7, applied organisation-wide by M18 |
| Copy-variant capture for learning | Fixed: M9 now records it, matching Part 8.1 |
| Cross-references to parts | Fixed: shared-facts decision points to Part 10 |
| Roadmap covers every module once | Fixed: M14 named in full in Phase 0; all of M1 to M18 appear in V1 phases, M19 in V2 |
| Closed value lists match their sections | Consistent: verdicts, bands, stages, reply classes, roles, modes, Brief states |
