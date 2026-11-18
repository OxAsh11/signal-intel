# Phase 1 raw capture notes (2026-11-18)

Method: Claude in Chrome on the user's logged-in Chrome, workspace 1485570. A read-only XHR recorder
(`window.__cap`) was installed in the page. API base: `https://api.clay.com/v3` (`~` = `/workspaces/1485570`).
Telemetry on every page: Datadog RUM, Amplitude session replay, FullStory, Google Ads conversion.

## Boot (any page)
GET /me, /my-workspaces, ~, ~/actions, ~/subscriptions, ~/billing/auto-top-ups/failure, ~/billingplans,
~/rollover-multiplier, ~/editor-access-banner, ~/ad-audiences/sync-limit-status, ~/gtm-agent/billing-status,
~/credit-accrual, /auth/knock/token (Knock = in-app notifications), evs.cdp.clay.com (event CDP).

## Sidebar (href → visible content → API)
Groups: [Home, Find leads] / Audiences [People, Companies] / Orchestration [Signals, Ads (Upgrade), Sequencer,
Claygents, Functions, Workflows (Beta), Agent users (Beta), API and CLI] / [Exports, Trash, Settings, AI context]
+ user/workspace switcher. Top bar: Upgrade, tools (wrench), credits (db icon), help, notifications.

- home: "Hey Ashish" greeting, AI prompt box "Ask me anything about Clay or describe what you'd like to do",
  cards: Find leads / Import data / Build a segment / Start outreach / Try a template; All Files (All/Recent/
  Favorites, Owner filter, Filters; cols Name, Favorite, Tags, Created, Last opened, Owner, Access). Files are
  workbooks: wb_0top2dsTVdXkA2bUzEF (Clay Starter Table), wb_0top2g3HbCER3qPrhMH (Companies).
- people / companies (Audiences): tabs All People|Segments|Data hub|Reporting; Segments: Created by me/Recent/
  Archived; empty state "Get started with Audiences": Find people/companies, CSV file, Clay table, Browse all sources.
  API: ~/audiences/imports, ~/audiences/segments, POST ~/audiences/count.
- signals: New signal; quick links Topic intent, Job change, New hire, Job posting, Promotion, News & fundraising,
  Web intent, Custom. API: ~/credit-reporting/creditReportType/triggerDefinition.
- campaigns (Sequencer): Inbox, Analytics, Events, How it works, New sequence; tabs Sequences, Email accounts, Sender
  variables, Account orders, Blocklist. API: ~/campaigns, ~/all-campaigns, ~/clay-sequencer-email-accounts,
  ~/smart-senders-orders.
- claygents: "Build reusable, context-aware agents"; Templates; New; Upload files; Auto; Build agent; templates
  Prospecting, Account Scoring, Contact Scoring, Copywriting; filters Owner/Type. API: ~/claygents,
  ~/claygents/by-agent-type, ~/ai-quickstart/gtm-rules, ~/documents, ~/model-pricing/base-costs, ~/resources/starred,
  /app-accounts/types, ~/app-accounts/accounts/type/{notion,google-docs}.
- functions: How it works, New; All | Clay managed | Recent | Favorites; filters Owner, Resource type (Tables,
  Workspace managed). API: ~/subroutines (functions = "subroutines" internally).
- workflows (Beta): New; Published | Created by me. API: ~/tc-workflows-paginated.
- mcp (Agent users, Beta): default credit limit for agent users (1,000), hide default functions from agents,
  allowed MCP clients (ChatGPT, Claude, Grok, Cursor, Unknown; domain allowlist), Salesforce owner sync and
  restriction, connect via ChatGPT/Claude/Slack, agent-user table (name, platforms, credit limit, used, SF ID).
  API: ~/sales-reps*, ~/oauth/policy, ~/default-credit-limits.
- api-and-cli: agent-plugin prompt (github clay-run/agent-plugins), Search usage 0 of 10,000 results (resets
  2026-12-02), tabs Routine runs | API keys | Webhooks; routine runs table (Run ID, Type, Mode Batch, Status,
  Progress, Name...). API: ~/tools, ~/tools/routine-runs, ~/search-api/period-quota.
- exports: list; API ~/exports/my-active-exports.  trash: API ~/deleted-resources/.
- settings: Workspace | AI context | Team | Connections | Web intent | Plan & billing | Usage | Referrals |
  Enrichments | Ads | Sequencer; Account | Appearance. Workspace page: name, billing email, Slack notifications,
  beta program, delete workspace. API: ~/permissions, ~/alerts/slack/token.
- settings/ai-context: "My business context" for "Sculptor" (Clay's AI): Generate with AI from domain (10-30s);
  fields Company description, Ideal customer profile, Ideal buyer personas; Save rules. API: ~/ai-quickstart/gtm-rules.

## Workbook / table (wb_0top2dsTVdXkA2bUzEF → t_0top2ds77gDB8qkqsEa → view gv_0top2dsWG2zabPQjcyu)
URL shape: /workbooks/{wb}/tables/{t}/views/{gv}. Breadcrumb Home / workbook / table.
Toolbar: Auto-run toggle + count (0), view picker (Default view), "3/5 columns", "10/10 rows", filter, sort, search;
right: Sandbox Mode, Sculptor (AI), Tools panel. Row 0 under header = per-column fill % (e.g. ✓ 100%).
Footer: "+ Add [10] more rows at the bottom"; bottom tabs: Overview | table tabs | + Add; status bar
"100% of table completed" + Stop + History + settings.
Action column header has an inline ▷ run button. Cells show logo + preview text.

### Add column menu (exact order)
Add enrichment | Use AI | Functions | Message | Waterfall | Formula | Merge columns || Text | Number | Currency |
Date | URL | Email | Image from URL | Checkbox | Select | Multi-select | Assigned to

### Tools panel (Add enrichment opens a right side panel "Tools")
Search "providers, enrichments, owners"; tabs For you | Import | Signals | Enrich | Functions | Export.
For you: tiles New in Clay, Build with AI, CRM connections, Waterfalls, Use cases, Templates; list Suggested |
Favorites | Created by you, e.g. "Use AI — Enrichment · By Clay — / model"; "Company Revenue — Waterfall · By Clay
— +3 providers, ~3.5 credits/row".
API: ~/tables/{t}/suggested-enrichments, ~/presets/filtered, ~/resources/{pkg}/{action}/star,
/enrichment-search/{ws}/institutional-knowledge-labels-by-action-id, ~/all-disabled-actions,
~/ai-generation/chat-conversations/table/{t} (Sculptor chat per table).

### Action catalogue: GET /v3/actions?workspaceId=1485570 (36 MB)
1,576 actions across 264 packages (providers). Action keys: key, version, package{id,key,displayName,description,
icon,categories,termsAndConditions}, displayName, description, categories, inputParameterSchema, inputSample,
outputParameterSchema, auth{providerType}, isPublic (863 true/713 false), rateLimitRules, actionLabels, iconUri,
actionEnablementInfo, cacheable.
actionLabels.type: Enrich Data 1111 / Send Data 170 / Add Data 117 / none 180.
actionLabels.categories (entity): People 709 / Company 697 / none 329.
Top tags: Export 162, CRMs & Pipeline 134, Enrich people 110, Contact Info 106, Company Basics & Identity 106,
Sequencers & Sales Engagement 105, Enrich company 95, Automation and AI 72, Database 70, Firmographics &
Financials 61, Company insights 55, Intent & Signals 45, Find people 40.
auth: 419 need none (Clay-managed key); rest bring-your-own (salesforce 51, hubspot 37, …).
use-ai inputs: useCase, prompt (longtext), temperature, reasoningLevel, reasoningBudget, claygentId,
claygentFieldMapping, maxTokens, model, maxCostInCents, jsonMode, systemPrompt, answerSchemaType, tableExamples,
stopSequence, runBudget, topP, image params (width/height/aspectRatio/referenceImageURL), contextDocumentIds,
mcpSettings, … semanticTags list every model family.

### Table object: GET /v3/tables/{t}
keys: id, workspaceId, createdByUserId, name, description, type (company), icon, parentFolderId,
tableSettings {AUTO_RUN_ON:true, AUTO_RUN_MODE:"keep_existing"}, fieldGroupMap, workbookId, budgetId, defaultAccess,
ownerId, isSandbox, isHiddenFromNavigation, abilities, firstViewId, owner, fields[], views[].
Field (action): id, type "action", name, configRevision, isLocked, isSortable, supportedFilterOperators,
typeSettings {dataTypeSettings{type:json}, actionKey "enrich-company-with-mixrank-v2", actionVersion,
actionPackageId, inputsBinding:[{name:"company_identifier", formulaText:"{{f_domain}}"}]},
conditionalRunFieldIds[], delayFieldIds[], inputFieldIds[] (dependency edges), actionDefinition (full copy incl.
pricing {credits{basic:1}, refundRules{SUCCESS_NO_DATA:true}, postPricingChange2026{basic:0.5,actionExecution:1}},
suggestedOutputParams).
Cell-state filter operators on action fields: MISSING_INPUT, HAS_ERROR, RESULTS, NO_RESULTS, HAS_NOT_RUN, QUEUED,
AWAITING_CALLBACK, RETRY, RUN_CONDITION_NOT_MET, IS_STALE, IS_RUNNING, (+ negations, INTERNAL_IS_CANCELLED/SUCCESS).
Text field operators: EQUAL, NOT_EQUAL, CONTAIN, CONTAIN_ANY, NOT_CONTAIN, …
Field (derived): type "formula", typeSettings{formulaType:text, formulaText:"{{f_action}}.url",
dataTypeSettings{type:url}}, isExtractedField, extractedField{fieldIdExtractedFrom, extractedKeyPath:".url"}.
table-schema-v2: per-field {type: action|basic, dataType, semanticType (company-domain, company-linkedin-url,
company-name, …), extractedFrom, children (nested output tree)} + exampleRecords.
System fields: f_created_at, f_updated_at.
Views: {id, name, order, fields{fieldId:{order,width,isVisible}}, sort, filter{items[{type,fieldId}|Group],
combinationMode AND/OR}, limit, offset, typeSettings{isPreconfigured, preconfiguredType}}. Preconfigured:
Errored rows, Support view, Fully enriched rows, Non enrichment columns, All rows, Default view.

### Records
GET /v3/tables/{t}/views/{gv}/records/ids → {results:[r_…]}; GET …/records?limit=n;
UI uses POST ~/tables/{t}/bulk-fetch-records, ~/tables/{t}/count, …/autocomplete-suggestions.
Record: {id, tableId, cells{fieldId:{value, metadata{status:SUCCESS, isPreview:true, imagePreview}}},
recordMetadata{runHistory{fieldId:[{time, runId}]}}, createdAt, updatedAt, deletedBy, dedupeValue}.
Action cells hold a preview value; full JSON fetched separately. Basic cells: {value} only (+isCoerced on system).

### Tools panel tabs (inside a table)
- Import: "Import new data based on the existing sources": Find more companies (add to this table), Find people at
  these companies (new table), Find jobs at these companies (new table), Import from CSV, Webhook, View all sources.
- Signals: Topic intent, Job change, Job posting, Promotion, New hire, News & fundraising, … (each "Alerts you when a
  monitored company/contact …").
- Enrich: Most popular | Browse by provider | Connections | Favorites | Suggested. Each item shows cost/row and kind:
  Use AI (/model), Company Revenue (Waterfall, +3 providers, ~3.5/row), Employee Count (+6, ~3.3), Website Techstack
  (+3, ~2), Company Funding Stage (+6, ~4.6), Company Total Funding (+5, ~8.3), Perform search (Google 0.2),
  Scrape website (0.1), HTTP API, Use formula (Transformation).
- Functions: All | Favorites | Clay managed; "reusable blocks of logic across tables": Person LinkedIn URL 2.2,
  Company Funding Stage ~5, Enrich Person (CPJ+Trigify+Forager) 1.8, Work Email 1.1, Person Job Title 2, Person
  Location 1.2, Website Traffic 3.5 … (type "Table function", By Clay).
- Export: Download CSV, Send table data, Invoke workflow, Export to CRMs, Create Clay email sequence (Legacy), Send to
  external sequencer, Export to Databases, View all export options.

### Use AI column panel
Header: Use AI (rename pencil), layout toggle, "Create Claygent" (promote to reusable agent).
Two modes: Generate ("What would you like AI to do?" → system writes optimised prompt + settings) | Configure.
Configure: Use case [Web research (Claygent) — internet access | Image generation | Create or modify content — no
internet], Model [Auto ~1.9 "Balanced", Compare models link], Configuration › Prompt ("Type / to insert column",
placeholder "For the company /{{Domain}} …", Generate button), Define outputs › Output format [Fields | JSON Schema |
Text] + Add output, Run settings › Auto-run, Add run condition ("Only run if this formula resolves to true"),
Delay run (immediate | after N seconds ≤600).
Footer: warning "This column doesn't reference any other columns, so it will never run automatically…", cost chip
"~1.9 / row", Save ▾ (split button). Model options load via POST ~/actions/dynamicFields
{dynamicRequests:[{actionPackageId, actionKey:"use-ai", parameterPath:"model", type:"select", inputs:{useCase:
"claygent", model:"clay-auto"}, tableId}]}.

### Column header menu (action column)
Rename column | Edit column | Insert 1 column left ▸ | Insert 1 column right ▸ | Edit description | Change color ▸ |
Go to parent column | Used in… | Run column ▸ [Choose number of rows to run | Run empty or out-of-date rows | Force
run all 10 rows] | Duplicate | Save as function | Filter on this column | Pin | Hide | Delete.

### Cell details panel
Opening an action cell → right panel "Cell details" with search and every output key as a typed chip (Url, Name,
Size, Slug, Type, Domain, Org Id, Country, Founded, Website, Industry, Locality, Logo Url, …). Hover tooltip on
cell: "View full results". Each output key can be extracted to its own column.

### Run API (observed)
PATCH ~/tables/{t}/run {fieldIds:[f], runRecords:{recordIds:[r]}, forceRun:true, callerName:
"ActionCellDetailsPanel"} → {runMode:"INDIVIDUAL"}; then POST ~/tables/{t}/bulk-fetch-records {recordIds}.
After run: recordMetadata.runHistory[f] gets a new {time, runId}. Toast "Cells running".

### INCIDENT 2026-11-18 ~16:57 IST
Pressing Enter on the selected Enrich Company cell (Perplexity, row 1) force-re-ran it (one cell, enrich-company,
priced 0.5–1 credit, cacheable). Not intended; reported to user. Lesson: Enter in a cell = run. Use mouse to open
cell details; never press Enter/Space on action cells.

## (after user OK'd credit spend, 2026-11-18)
### Workbook Overview (/workbooks/{wb}/all-tables)
Canvas (zoomable, dotted grid) with each table as a node: name, "Company table", "10 rows, 5 columns", "0.5 / row"
cost, ▶ run. Left palette "Create": Sources [Find people, Find companies, Find jobs, Find with topic intent, Find local
businesses, Import from CSV, Import from CRM, All sources], Signals [Topic intent, Job change, News & fundraising, New
hire, Job posting, Promotion, Web intent, Custom], Other [Blank table]. Right: workbook name ▾, Workbook settings,
Description, Tables list, Tags (+ New tag). A workbook = a graph of linked tables.

### Table History (footer) menu: Usage history | Row deduplication | Change log
Usage history dialog: group by Time | Column | Run; When (This week …); Aggregate by Day; Show enrichments; Download
CSV. Totals split into TWO meters: "Total data credits used" (0.5) and "Total actions used" (1) — matches action
pricing basic:0.5 + actionExecution:1. API ~/realtime-credit-usage/table/{t}/time.
Row deduplication: "No deduped records found" (~/tables/{t}/deduped-records).

### Sculptor (table-level AI assistant, Beta)
Docked left panel: new chat, settings, list, history. "I'm Sculptor… help you build your table, find the right data,
or troubleshoot". Tip: "Right-click an AI column cell to add it to Sculptor's context". Context-aware suggestions:
Add tech stack data / Analyze company distribution / Find decision makers. Input "Ask questions and add
enrichments…". API: ~/ai-table/context-chat-conversations/table/{t}, POST ~/ai-table/context-chat-conversation,
~/ai-generation/chat-conversation/{cc}/messages|state, ~/ai-generation/chat-suggestions.

### Sandbox Mode
Creates a shadow table (new id t_0top3yuw…, title "(Sandbox)", red top border), via POST ~/subroutines/{t}/sandbox.
Tabs Test data | All data; Add test data; Review changes; publish to full table. Welcome: 1 Customize rows, 2 Build
workflow on test data, 3 Review changes, 4 Publish. Toast "Sandbox mode turned on/off".

### Find leads (sidebar flyout)
Search directly: People, Companies, Jobs, Local businesses, Company lookalikes. Create a workflow (Beta): People,
Companies.

### Find Companies (/chats/cc_… — a search session is an AI chat conversation)
Left: Sculptor "Describe what you're looking for — We'll set up your filters" + suggested prompts. Middle: Filters ▾
[Find with filters | Find lookalikes], view toggle Filters | Query mode (</>), settings, delete, save. Criteria:
Search filters box, Suggested filters chips (Industry, Country, Company size, Description, Department headcount ≥ 10,
Hiring for ≥ 5 roles), Company identifiers, Excluded companies (Upgrade), Result limits. Right: Preview "50 of 48.6M
(~48.6M found)" grid (Name, Description, Primary industry, Size, Type, Location, Country, LinkedIn URL).
Continue ▾: Save to Companies (Audience, 1M limit, excludes existing) | Save to new table (1,000 plan limit) | Save to
existing table | Save to workflow.
API: POST ~/search-dsl/query {query:"select from companies limit 50", selectMode:"preview", conversationId} →
{rows,rowCount,totalCount,durationMs,previewCoverage}; ~/ai-generation/chat-conversation/{cc}/source-state →
{sourceState{sourceType:"companies", sourceConfig{type:"search", entityType, mode:"dsl", filters{62 keys},
additionalRequirements, dslSearchConfig, originalQuery}}}; ~/presets/recent-searches; ~/peopleSearchLimit.
62 filter keys incl. semantic_description, description_keywords(+exclude), derived_industries/subindustries/
business_types/revenue_streams, technographics_vendors/products/categories, lookalike (useRadialKnn,
radialKnnMinScore, lookalike_similarity_tier, clustering_method, cluster_count), locations (cities/states/regions/
postal codes, HQ only), sizes, annual_revenues, funding_amounts, follower/member counts, has_resolved_domain,
resolved_domain_is_live, exclude_entities_bitmap/previous_entities_bitmap (dedupe vs prior pulls),
company_audience_segment_id.

NL test: "Mining, oil & gas and solar energy companies in India with 200-5000 employees that run drone inspections"
→ ~18s → "Search is ready." Sculptor explains + "Applied approximation: 'run drone inspections' broadened to semantic
match … because the narrower query returned no results. Are you comfortable with this broader matching?" + 👍👎.
Criteria became a visual tree (Companies match all of: HQ location Country is India; Company size is 201-500 +2;
Industry is Mining, Oil and Gas +4; Companies match any of: Products and services is similar to "drone inspections"
…). Query mode DSL:
  select from companies
  where locations.any(is_headquarters = true and country_name = "India")
    and company_size in ("201-500","501-1,000","1,001-5,000")
    and industry in ("Mining","Oil and Gas","Oil, Gas, and Mining","Solar Electric Power Generation",
                     "Renewable Energy Power Generation","Renewables & Environment")
    and ( products_and_services is_similar_to ("drone inspections")
          or description contains ("drone","drones","inspection","inspections") )
Result 45 of 45 (Engineers India, Hinduja Renewables, MECON, Fugro India, …). "Agent skill" export button + copy.

### Saved search
Leaving a search prompts "Save this search? You can run it again anytime from your saved searches" → Save search
dialog (Search name*, Description). Saved: "Study - India drone inspection targets".

### Find People (/chats/cc_…)
Preview 50 of 300.8M. Suggested filters: Job title, Seniority, Country, Company industry, Target companies; sections
Target companies, Excluded people (Upgrade). sourceType "people", 62 filter keys incl. job_title_mode,
job_title_keywords, job_functions, seniority, headline/about/profile/certification/job_description keywords,
school_names, languages, company_sizes/industries/annual_revenues, company_table_id/view/field (people at companies
from a table), include_past_experiences, role_range_start/end_month, follower/connection/experience counts,
locations (+exclude), exclude/previous entity bitmaps.

### Signals → New signal ▾: Topic intent… Job change… New hire… Job posting… Promotion… News & fundraising… Web
intent… Custom…
Custom signal = 4-step wizard (/w/custom-signal, "Clay Wizard") with live Preview (Table | Workbook tabs):
1 Select a source to monitor ("We'll monitor your signal activity on your chosen schedule and add any new data"):
  Social listening [professional posts 0.5, interactions with posts 0.5, Adbeat ads 1, Reddit mentions, Google News
  RSS, YouTube 0.1, Modash influencers 1, Upfluence 1, Trigify engagers 1, GitHub stargazers/contributors/forks],
  1st party signals [Snowflake, Databricks, ClickHouse, Mixpanel cohort, Gong calls, Crossbeam accounts],
  Company sourcing [TrustRadius buying intent ~4, Openmart local 0.5, Google Maps ~1, Store Leads 1, Pitchbook
  shared search], Other [RSS Feed, Google Search results, Phantombuster, Apify actor, Airtable].
2 Setup inputs (Google News: Exact phrase, Has words, Exclude words, Website, Date* [Past hour|24h|week|30 days],
  Limit 10) → preview fills with 10 real sample rows.
3 Choose run frequency: Quarterly | Monthly | Biweekly | Weekly | Daily.
4 Configure optional enrichments: Suggested [Slack Send message to channel, Salesforce Look up/Update record, HubSpot
  Look up/Update object] + Add enrichment. Workbook preview shows graph: Signal node (No data yet, Monthly) → Custom
  table node.
Save → new workbook "Custom signal" with table "Event: Monitor Google News RSS Feed Table" (25 rows). Source column
header shows source icon + toggle; fill row says "Waiting for events…". Right panel: Cost 0/result, Next scheduled
run Dec 18 2026 5:05 PM, setup inputs editable, Save only | Save and re-run. Footer "Table up to date".
=> A signal is a table whose source column re-polls on a schedule and appends rows; enrichments then run per new row.

### Claygents (reusable AI agents)
List page: big prompt box "Describe what the Claygent should do. Type @ to mention files and context." + model (Auto)
+ Upload files + Build agent; templates Prospecting, Account Scoring, Contact Scoring, Copywriting; filters Owner/Type.
Template click → /claygents/new: Sculptor is auto-fed a long meta-brief (e.g. "Build an account scoring Claygent…
inputs company_name, company_domain, industry, employee_count, reference My Business Context fields … return
structured JSON with fit (Strong/Moderate/Weak), confidence, reasoning. Create the full prompt, variables, output
schema, and tool wiring … tool-capable model and internet search"). Sculptor thinks, then writes everything, names
the agent ("Score Account Fit"), autosaves ("Agent saved successfully"), generates 3 test cases (Twitter edge case,
Allbirds likely weak, one strong), and lists "One manual step required" (replace placeholders with @-mentions to
business context so ICP stays in sync).
Builder layout: top toggles Settings | Prompt | Chat; Test | Save | Deploy. Left Configuration: Model; Inputs
("Use {{inputName}} syntax in the prompt to define inputs"); Outputs (Fields | JSON schema | Text); Skills
("reusable methodologies the agent loads on demand", Create skill); Tools [Account context (Upgrade — fields from
Audiences), Web search, Find contacts and jobs 0.5/run, Access all business context]. Prompt editor: "Use @ to add
documents, enrichments, and business context. Highlight text to generate changes." Test pane: + Tests, 1/3 pager,
Input fields, Run → streaming "Generating… Calling Search Google — Query: …" (show/hide stream details) → Output as
fields (fitTier: Moderate Fit, reasoning: …, which honestly flagged missing ICP).
Deploy agent → Tables ("Add this agent as a column in a table") | Workflows ("Run this agent as a step").
API GET ~/claygents/{c} → {claygent{id, name, description, icon, agentType, currentVersionId, budgetId, publishedAt,
currentVersion{id cv_…, versionNumber, userPrompt, variables[{name,type(text|url|number),required,description}],
modelSettings{model:"clay-auto", useCase:"claygent", memoryEnabled, internetSearchEnabled,
peopleAndCompaniesSearchBudget:4, peopleAndCompaniesSearchEnabled, shouldIncludeAllBusinessContext}, toolSettings,
contextSettings[], outputFormat{type:"json", jsonType:"JSONSchema", jsonSchema:"…"}, skills[], externalToolIds[],
isPublished}}}. Agents are versioned.

### Sequencer (/campaigns)
Header: Inbox | Analytics | Events | How it works | + New sequence. Tabs Sequences | Email accounts | Sender variables |
Account orders | Blocklist.
Add email accounts: "Buy managed accounts — we set up domains and mailboxes, warmup included" (Growth+ only) | "Bring
your own accounts — Gmail, Outlook, or SMTP". Then Gmail (OAuth) | Outlook (OAuth) | Manual SMTP & IMAP | Bulk CSV
SMTP & IMAP. "Your credentials will be securely shared with our email integration partner Smartlead." (Clay's
sequencer runs on Smartlead.) Not connected (user's sign-in).
New sequence dialog: audience source People (search segments) | Tables (Legacy) | Workflow; + Create blank sequence.
Sequence editor (/campaigns/cam_…): title ▾ + Draft badge; tabs Sequence | Sender accounts | Analytics | Activity |
Replies; header cost chips "1 / lead" actions + "~0" credits, Save, Launch sequence; "Unsaved changes".
Left: Lead setup (No leads selected, + Add leads); AI context ("Messages are personalized using your lead data, goals,
documents, and workspace AI context"; Sequence goals "Use / to add variables, @ for documents"; + Add context); Spam
check; Sample data (Lead data | Sender data).
Middle: Refresh | A/B test; step card "# 1" (⋮), Subject, body "Use / to insert variables or AI snippets"; + Add
message. "/" menu: AI snippet | Spintax | Sender data ▸ | lead fields [Name, First/Last name, Title, Email, LinkedIn
URL, Phone, City, State, Country, Location, Seniority, Department, Hashed email 1-3, Company name, Domain, HQ location,
Salesforce Owner ID, Technographics, Industry, Employee count, Annual revenue range, Logo URL].
AI snippet = inline chip + box "Describe what this snippet should generate…" with Update preview | Discard | Accept;
inserting one moved cost to "2 / lead" + "~0.40" credits.
Right: Sculptor "How can I help with this sequence?" — Summarize this sequence / Suggest improvements to my email copy /
Why are leads not progressing through this sequence?
A draft "Untitled sequence" (cam_0top4f1RqcfETG6mAmo) was created by the study; left unlaunched.

### Settings sub-pages
- Usage (/settings/credit-usage): tabs Overview | Workbooks | Functions | Workflows | Integrations | Signals | MCP | API |
  Agent; When (This month); Balance | Spend breakdown chart. Workspace balances: Data credits (trial 1,000; available
  999.5; bi-weekly recurring 1,000) and Actions (trial 5,000; available 4,999; bi-weekly 5,000). Manage plan.
- Connections (/settings/accounts): 132 connections; filters Provider, Account type, Spend period (30d); columns Provider,
  Connection name ("Clay-managed X account"), Account type (Clay-provided | own), Status, Credit spend (30d), Resources
  (count using it), Added by, Date added, Actions; + Create.
- Enrichments (/settings/enrichments): tabs Enablement | Compliance. "Save enrichment data in Audiences (Beta) On —
  Saved values live on the record in Audiences, where Clay reuses them instead of buying the same data twice. Reuse is
  opt-in per action, and each action can have its own custom expiration window. When a value expires, the next run buys
  it fresh and saves it again." Provider list with description, "N actions · can't save data", Manage provider.
- Team: members table (Name, Role Admin), Export CSV, Invite.
- Plan & billing: Plan Trial; Free; bi-weekly 1,000 credits / 5,000 actions; payment method, billing info, next payment
  $0.00 on Dec 2 2026, transaction history. (Not touched.)
- Web intent (/settings/website-tracking): identify anonymous visitors; Add connection.
- /settings/usage is a 404 (route is credit-usage).

### Workflows (Beta) (/workflows/tc-workflows/wf_…)
New → AI-first: "Agent now uses credits", prompt "What should we build today?" + attach; suggestions (Route & enrich MQLs
from a form webhook; Score & route PQLs from product signals; Enrich, score & prioritize a target-account list for reps;
Enrich & route inbound leads from a CSV/list import — each "Fill ↵"); Import a workbook; Guides; Build manually; "Try
building workflows with Clay's agent plugin".
Manual: canvas with Trigger node ("New trigger — Finish setting up"); top Graph | Runs; settings, Analytics, Table,
Publish; Draft badge. Add palette: Enrich, People and Companies ▸, Flow ▸, Transform ▸, AI ▸, Send and export ▸;
Frequently used: Trigger, Code, Conditional, Function, Claygent, Write to Audiences; "Ask Clay — Build and edit
workflows". Create trigger → Source: Segment | Find leads | Webhook | Signal | Activity (Beta) | CSV | None.
A draft "Untitled workflow" (wf_0top4i3AHbMgBS4XP3A) was created by the study.

### Audiences Data hub (/companies/data-hub/fields)
Left: Companies ▾ | All Companies | Segments | Data hub | Reporting; Segments list (Created by me, Recent), Archived.
Data hub tabs Fields | Workflows | Signals | Claygents; + Add field. Fields table: Name (typed icon), Fill rate (bar +
%), Records covered, Enrichments. Default company fields: Company name, Industry, Employee count, Domain, Annual revenue
range, Headquarters location, City, State, Country, Logo URL, LinkedIn URL, Salesforce Owner ID, Technographics, …
=> Canonical record + field catalogue with coverage; tables/workflows/signals/agents write into it.
