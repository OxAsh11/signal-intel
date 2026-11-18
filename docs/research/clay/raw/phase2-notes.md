# Phase 2 raw notes — journeys (2026-11-18)
Workbook wb_0top53ujFAKnKsF7jcF. Company table t_0top53upWyHHffgA2na (view gv_0top53vXffxaqpDCGeQ).
People table t_0top5cnpD2qEgg3qSK8 (view gv_0top5coNrkKxEYeXXjy).
Legend: [V] = verified by running it and reading the API; [I] = inferred.

## J1 Search → table
1. [V] Find leads › Companies → save-icon ▾ [Save | View saved searches] → dialog (Saved by you | Saved by anyone)
   → Select search → filters + preview restored.
2. [V] Continue → step panel: "How many results would you like to save?" (Max available 45 of ~45 | Custom N);
   "What would you like to do with these results?" (Save to table, up to 1K, new table); "Enrich to add more data?"
   pre-selected list with per-row cost: Enrich company 0.5 (locked on), Research company ICP with AI 0.1 (pre-ticked),
   News +1 provider ~0.5, Latest Funding +2 ~5.7, Job Openings +2 ~0.5, Competitors +1 ~5. Back | Save.
   We ticked News + Job Openings.
3. [V] Save → POST ~/search-dsl/send-to-table {dslQuery}, then POST ~/wizard/evaluate-step {workbookId, wizardId:
   "enrich-companies", wizardStepId, formInputs{tableId, viewId, enrichments:[{type:"action", assignedFieldId:
   "f_enrich_company", ...}]}}. A server-side "wizard" turns the ticked choices into columns. New workbook + table are
   auto-NAMED from the query: "Indian Energy Companies, Drone Inspections, 201-5,000 Employees". Actual rows 54
   (preview estimate said ~45).
4. [V] Only the first 10 rows ran. The wizard default is a 10-row test run (the people wizard button literally says
   "Save and run 10 rows"). Remaining rows stay empty; Stop disabled; footer "17% of table completed".

### Company table structure (25 fields; types source | formula | action | date)
- [V] Source column type "source": typeSettings{sourceIds:[s_...], canCreateRecords:true,
  deleteSourceLinkOnTableRecordDelete, destroySourceRecordOnTableRecordDelete}. Fill row "Up to date".
- [V] Search-result fields are extracted formulas: {{f_companies_search}}.name / .domain / .primary_location.city /
  .social_urls.linkedin ...
- [V] Enrich company (enrich-company-with-mixrank-v2) → Founded / Employee Count / Follower Count formulas.
- [V] Research company ICP with AI (use-ai, model "clay-argon", useCase "claygent"). Prompt is a JS string formula:
  "You are an analyst who is an expert on marketing and ICP. I'm analyzing " + {{Name}} + " that has a website at " +
  {{Domain}} + ". Your job: 1 ICP 2 industries targeted 3 personas 4 value prop ... analyze case studies, customers,
  blog ... Cost is not an issue ...". answerSchemaType json with fields incl. confidence:number, reasoning:string.
- [V] WATERFALL = formula column with formulaType "waterfall", waterfallType "company/jobOpenings",
  formulaWaterfall: [{formula:"({{p1}} && {{p1}}?.total_job_count)"}, {formula:"({{p2}} && {{p2}}?.count)"},
  {formula:"({{p3}} && {{p3}}?.jobCount)"}] — first truthy wins.
  Provider columns p1 Find Open Jobs (find-google-job-listings-v2), p2 Find open jobs (PredictLeads job openings v3),
  p3 Find active job openings (cpj-find-lists-of-jobs). Provider k carries conditionalRunFormulaText = AND over every
  earlier provider of `!!([null,'',false].includes((pX && pX?.count) ?? null))`, i.e. run only if all earlier
  providers are empty. News waterfall: Google News results → PredictLeads events (only if Google empty).
- [V] Formula language = JavaScript expressions over {{field}} references (&&, ?., ??, !!, [].includes, + concat).
- [V] An action declares many alias inputs (domain, url, companyDomain, website, query ...) all bound to the same
  field — the wizard auto-maps by semanticType.
- [V] UI: providers grouped under a collapsible "Waterfall «" header; provider cells show icon + ✓ "10 Results Found"
  / ✗ "No Results Found" / "Run condition not met"; AI cells cycle verbs (Refining…, Sculpting…, Orchestrating…)
  while AWAITING_CALLBACK.

### Cell status timeline (records API, 10 run rows)
- [V] 53s→136s: Enrich company SUCCESS 10; Google news SUCCESS 10; PredictLeads news RUN_CONDITION_NOT_MET 8 /
  SUCCESS_NO_DATA 2; Jobs p1 NO_DATA 7 / RCNM 3; p2 NO_DATA 7 / SUCCESS 3; p3 NO_DATA 6 / RCNM 3 / SUCCESS 1;
  AI AWAITING_CALLBACK 10 for 4+ minutes, then SUCCESS 9 / AWAITING 1 at ~8 min.
- Statuses observed: SUCCESS, SUCCESS_NO_DATA, ERROR_RUN_CONDITION_NOT_MET, AWAITING_CALLBACK, (absent = never run).

### Table Settings (footer gear) [V]
Name; Description + "Generate description" (AI); Source settings › Import schedule "Companies, People, Jobs —
Manually" ▸, View import history; Run settings › Auto-run ("Automatically run cells when their dependencies change
or when new records are added"), Keep existing results ("Only errored, empty, or new cells will run"), Re-run
columns on a schedule; Deduplication › Auto-dedupe rows (by a chosen column; locked while running); Chrome Extension;
Integrations › Enable for API; Share as template (columns, input mappings, first row).

## J2 Companies → People
- [V] Tools › Import › "Find people at these companies" → Find People chat pre-scoped: Target companies (Currently
  work at | Previously | Either) = this table "50 valid rows (out of 54)", View, "Match companies using: Company
  domain", column Domain → 11.7K people.
- [V] AI prompt "Senior people (manager and above) who own asset inspection, maintenance, operations, HSE or drone/UAV
  programs at these companies" → 68 people. DSL:
  select from people where clay.filter_to_companies(@table("t_...:gv_...:f_domain:domain"))
  and experiences.any(is_current = true and seniority in ("Manager","Director","VP","Head","C-suite","Owner",
  "Partner","Board Member") and job_title is_similar_to ("Asset Inspection Manager","Maintenance Manager",
  "Operations Manager","HSE Manager","Health Safety Environment Manager","Drone Program Manager","UAV Program Manager"))
- [V] QUALITY ISSUE: matching on domain pulled global Fugro staff (Cairo, Oslo, Brazil, NL, Germany) for "Fugro India"
  (fugro.com). The company's country is not carried over to people.
- [V] Continue ▾: Save to People (excludes existing) | Save to new table (~68) | Save to existing table | Save to
  workflow → wizard /w/enrich-people "Select enrichments": Enrich person 0.5 (locked), Summarize LinkedIn profile 0.1,
  Work email +6 ~0.6, Professional posts 2.5/result. Live footer: "~0.5 credits / ~1 action per row" → after ticking
  Summarize + Work email "~1.2 credits / ~3 actions per row". Button "Save and run 10 rows".
- [V] Result: second TABLE in the SAME workbook ("Operations Managers, Asset Inspection"), 76 rows.
  Fields: Company Table Data (action lookup-company-in-other-table → link to company row; ran on all 76 rows),
  name/title/location formulas from {{f_people_search}} (.matched_experience.title, .matched_experience.company.name,
  .structured_location.*), Enrich person (enrich-person-with-mixrank-v2) → Jobs Count / Summary / Connections /
  Headline, Summarize LinkedIn profile (use-ai).
- [V] WORK EMAIL WATERFALL: finders LeadMagic → Findymail → Prospeo → Dropcontact → Clay find-email-v2 → Datagma →
  Wiza; finder k runs IF all earlier finders empty; each finder paired with "Validate <provider>"
  (findymail-validate-email) that runs IF that finder returned an email not already verified; "Work email" =
  waterfall formula over validated results.
- [V] Result on 10 rows: LeadMagic SUCCESS 6 (validated 6), Findymail SUCCESS 1 (validated 1), remaining finders
  mostly RUN_CONDITION_NOT_MET; Dropcontact AWAITING_CALLBACK 2 (async provider). → 7/10 emails, cost only where needed.
  Final: Wiza (last) found 1 more with "Valid email" → Work email filled 9/10. Cells show "❌ No email found",
  "❌ Invalid email", "✅ Valid email".

### Table chaining [V]
- Saving people "at these companies" ALSO added an action column to the COMPANY table: "Update People Search
  (Operations Managers…)" = trigger-find-people-source {sourceId:"s_…", companyIdentifier:{{Domain}},
  enableAutoUpdate:true}. So each new company row triggers the people search for that company → people table grows.
- People rows carry {{f_people_search}}.clay_metadata.company_table_id / company_record_id; "Company Table Data"
  = lookup-company-in-other-table on those → whole company row available (the "/" menu shows "Company Table Data
  15 items").

## J3/J4 Score accounts with the Claygent [V]
- Tools search "Score Account Fit" → results list mixes Claygent (By me), enrichments, templates.
- "Add agent to table" panel: Agent card (prompt excerpt), Table, Columns: "All inputs (4 of 4 mapped)" auto-mapped
  company_name→Name, company_domain→Domain, industry→Industry, employee_count→Employee Count; "Suggest input mappings
  with AI"; Outputs: tick fitTier / confidence / reasoning → each becomes a column. Cancel | Add to table.
- Then column config panel ("Agent template being used", Quick set-up | Details, Map agent inputs with "/ to insert
  column", Edit Claygent link) and Save ▾ = "Save and run 10 rows (~10 actions, ~19 credits total)" | "Save and
  run 50 rows in this view (~50, ~95)" | "Save and don't run".
- API: POST ~/tables/{t}/fields {type:"action", name, typeSettings{actionKey:"use-ai", inputsBinding:[{claygentId},
  {prompt: copy of agent prompt}, …]}} + one POST /fields per output: {type:"formula", typeSettings{formula:
  "{{agent}}?.fitTier", mappedResultPath:["fitTier"], formulaType:"text"}}. Run starts from field creation.
- RESULT: all 10 = "Moderate Fit". Workspace business context (ICP) is empty and the agent's ICP placeholders were
  never wired → identical, meaningless scores. Lesson: scoring needs a defined ICP (our Brief).
- The pre-ticked "Research company ICP with AI" column returned JSON objects (preview "Response").

## J5 Compose email from columns (Message column) [V]
- Add column › Message → right panel "Message drafting — Draft messages using AI and conditional logic to send to
  your sequencer": Enable HTML formatting; Email name (internal); Message type (New email thread | …); Subject; Body
  ("Use / to insert variables or AI snippets"); Refresh preview; Cancel | Save. LEFT: live Preview of 5 rows
  (Row | Output subject + body) that updates as you type.
- "/" menu: Clean variable | AI snippet | Conditional snippet | Spintax variable | column groups with "Insert all N
  items" (this table 7 items; Company Table Data 15 items) | individual columns.
- Column picker = expandable JSON tree (clay_metadata, matched_experience › company › name/domain/social_urls) with a
  side panel of each row's actual value.
- Clean variable: "Column to use if present", Variable formatting [None | Title Case | lower case | Sentence case |
  UPPER CASE], Remove emojis and special characters, Fallback ("Simple text to display if variable is empty").
- AI snippet inserted as "{}" token; editing blocked in this browser by the Grammarly extension (not studied here;
  see Sequencer AI snippet in Phase 1).
- Save → POST ~/tables/{t}/fields/message-group/v2 {subjectFieldDSL:[{type:"Formula", formula:"\"Drone inspections
  at \""}, {type:"CleanVariable", variable:"{{f_people_search}}?.matched_experience?.company?.name", fallback:
  "your team", formattingOptions:"none", removeEmojisAndSpecialChars:false}], bodyFieldDSL:[{type:"Formula",
  formula:"\"Hi \"+{{First Name}}+\", …\""}], settings{runAsButton:false}, emailType:"NEW_EMAIL_THREAD", name,
  htmlMode:false, groupId:"gr_…"} → a FIELD GROUP "New email" with columns "New email: Subject" / "New email: Body",
  computed instantly for every row (no credits).

## J6 Push to sequencer [V]
- First attempt froze the browser tab (heavy page + my recorder); recovered in a fresh tab without the recorder.
- Tools › Export › "Send to external sequencer" → panel "Export messages to sequencer — Export message drafts from
  this table to an email sequencer". Sequencer list: Clay (Legacy), Smartlead.ai, Instantly, EmailBison, Reply.io,
  Lemlist, La Growth Machine, HeyReach, Customer.io, Outreach, Salesloft, Apollo.io, Woodpecker, Gong, Snov.io, Groove,
  Nooks. External ones need the user's account → not connected.
- Clay → modal "Use the new sequencer? Setup takes under a minute. You'll get A/B testing, Sculptor, and spam checking"
  [Continue with legacy | Create new]. Create new → wizard /w/audiences-table-import?entityType=CONTACT&
  postImportAction=create-sequencer:
  Step 1 Select a Clay table (Source name, People table, View) + live preview.
  Step 2 Field mapping: "Import record matching* — Combine records from this table with existing People when a
  selected alias field matches exactly" [Set up] → dialog: "Matching runs when each record is first imported. Later
  updates won't trigger matching again." Match existing records (Recommended) | Import without matching; rule
  When <alias field: External record ID | Email | Phone number | LinkedIn URL | Twitter handle> In <table column>
  Equals (same value) Then (Merge record) → Confirm "People records may be merged and cannot be unmerged. Changes
  apply only to newly imported records." Person field mapping (Clay People ← Table columns) with Auto-map ("match
  existing Person fields and create new Clay ones where necessary"), + Add mapping, unmapped rows flagged Required.
  AUTO-MAP FLAW: mapped Email to one provider's raw `email` output instead of the merged "Work email" waterfall; we
  re-mapped manually (picker groups: Waterfall › 7 providers › … › Work email).
- Import → new sequence "Untitled sequence" Draft (cam_0top657z8jz9cQCus9s?awaitingImport=1), toast "Clay table import
  started". Lead setup = segment "Operations Manager…" 18 leads + Exclusions (Audiences, cooldown, blocked). The table's
  Message column is NOT carried over — new sequencer has its own composer (Message columns = legacy path).
- Stopped before Sender accounts / Launch (needs the user's mailbox OAuth).

## J7 First-run business context + re-score [V]
- Settings › AI context › domain "flytbase.com" › Generate → ~60 s (UI promises 10–30 s) → fills Company description,
  Ideal customer profile, Ideal buyer personas; auto-saved ("Save rules" disabled). API GET ~/ai-quickstart/gtm-rules →
  {gtmRules{companyDescription, …}, domain}. Quality: accurate ("enterprise physical-AI and autonomous-drone operations
  platform … orchestrates mixed fleets"; ICP 500+ employees, strongest 1,000+, distributed hazardous assets; personas
  Director/VP Security …).
- Claygent › Settings: toggled "Access all business context" ON. UI contradiction: Web search ON but banner "Tools are
  not available for the selected model" (Auto). Save → version 2 (cv_0top69vmoveAxehFgYy) isPublished:false,
  modelSettings.shouldIncludeAllBusinessContext:true. Save = draft version; Deploy = publish.
- Table column binds claygentId only (no version pin) + copy of prompt. After context + agent change, existing cells
  stayed SUCCESS — NOT marked stale. AI cell metadata carries {status, isPreview, confidence:"low", isStreamingProgress,
  streamingProgressKey:run_…}.
- Forced re-run of 3 rows (PATCH ~/tables/{t}/run {fieldIds, runRecords{recordIds}, forceRun:true} → runMode
  INDIVIDUAL): new reasoning cites FlytBase ICP ("aligns strongly with FlytBase's target vertical", "meets the scale
  threshold (2,415 employees)"); confidence High/Medium (was Low); fitTier still "Moderate Fit" for all 3 → the live
  column follows the agent's latest saved draft; coarse 3-tier output hides real differences.

## J8 Signal loop [V from Phase 1]
- Custom signal "Monitor Google News RSS Feed" (monthly) created a workbook whose source column re-polls on schedule;
  rows append; downstream columns auto-run on new rows (Auto-run: "when new records are added"). Next run Dec 18.

## Not reproduced
- Brand-new-user onboarding (would need a fresh account). Observed entry points only: Home greeting + AI box + 5
  start cards; AI context "Generate from domain"; "Get started with Audiences" empty states.
- Running the remaining 44 company rows (mechanics known: column ▷ / Run column ▸ "Run empty or out-of-date rows" /
  "Force run all"; Save ▾ "run 10 | run N in view | don't run").
