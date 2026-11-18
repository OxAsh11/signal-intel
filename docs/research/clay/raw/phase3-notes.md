# Phase 3 raw notes — data model (2026-11-18)
Method: read-only GETs (plus one POST list call the UI itself makes) against api.clay.com/v3 from the logged-in page;
a shape() helper printed key:type trees, not data. [V] = seen in a payload. [I] = inferred.

## Workspace  GET /v3/workspaces/{ws}  [V]
{id, name, createdByUserId, billingPlanType "postPricingChange2026Trial", billingEmail, customerId cus_…,
effectiveBillingPlanType, settings{CLAY_SEQUENCER_SMARTLEAD_CLIENT_ID}, featureFlags{~120 flags + limits},
credits{basic, longExpiry, actionExecution, cpjAllowance, agentAllowance}, creditBudgets{basic, longExpiry,
actionExecution, cpjAllowance}, currentPeriodEnd, centsPerCredit 5, onboardingData{teams[], crmTools[], attribution,
firstUseCase "not-enough-customers", workspaceName, integrationTools[], completedAt}, onboardingFlow "v4",
abilities{canUpdate, canCreateResource, canManageBilling, canManageClaygents, canExportToCRM, …},
audienceAbilities{…}, routineAbilities{…}, hasClayCliConnection, currentUserRole}.
Limits (this plan): tableColumnLimit 100, tableComputableColumnLimit 40, interTableDagLimit 20, planNumRowsLimit 1000,
workspaceRowLimit 10M, scheduledSourcesLimit 100, scheduledTablesLimit 100, syncedTablesLimit 15,
audienceWorkspaceRecordLimit 1M, audienceSegmentLimit 250, findPeopleSearchLimit 1000, peopleTableRowLimit 100,
audienceBitmapRefreshMinutes 60, audienceIntegrationSyncCadenceMinutes 1440, restoreHistoryLimit 30 (days).
Subscriptions GET ~/subscriptions: {limits{add-record-limit{limit,current}, allow-export-limit, …}, schedule[phases
with Stripe price/product, basicCredits, actionExecutionLimit], creditBalances{…5 buckets}, creditBudgets,
currentPeriodStart/End, cancelAtPeriodEnd, …}. Users: GET /v3/me; GET ~/permissions → userPermissions[{roleId,
email, pending, user, role, managedByScim}].

## Workbook  GET ~/workbooks/{wb}  [V]
{id wb_, workspaceId, name, description, parentFolderId, settings{}, annotations{}, defaultAccess, ownerId,
createdAt/updatedAt/deletedAt, isHidden, isHiddenFromNavigation, creditLimit, budgetId, abilities, owner, tags[],
orderedWorkbookTables[{id t_, name, tableType, firstViewId}]}.  ~/workbooks/{wb}/tables → table list.

## Table  GET /v3/tables/{t}  [V]
{id t_, workspaceId, createdByUserId, name, description, type ("company"|"spreadsheet"|…), icon, parentFolderId,
tableSettings{AUTO_RUN_ON, AUTO_RUN_MODE:"keep_existing"}, fieldGroupMap{gr_:{name, type:"message_v2", htmlMode,
settings{runAsButton}, emailType, groupDetails{bodyField{id, dsl[]}, subjectField{…}}}}, workbookId, budgetId,
defaultAccess, ownerId, isSandbox, isHiddenFromNavigation, abilities, firstViewId, owner, fields[], views[]}.

## Field  [V]
Common: {id f_, tableId, name, type, configRevision, isLocked, isSortable, supportedFilterOperators[{operator,
needsValue, isHidden}], createdAt, updatedAt, typeSettings, inputFieldIds[], conditionalRunFieldIds[], delayFieldIds[],
isExtractedField, extractedField{fieldIdExtractedFrom, extractedKeyPath}}.
Types seen: text, date (system f_created_at/f_updated_at), source, formula (formulaType text | waterfall), action.
(Add-column menu also offers number, currency, url, email, image, checkbox, select, multi-select, assigned-to [I same
"basic" family with dataTypeSettings].)
- formula: typeSettings{dataTypeSettings{type}, formulaType, formulaText "{{f_x}}?.path", mappedResultPath[]} —
  JavaScript expressions; lodash `_` available (`_.trim(x) || "fallback"`).
- waterfall: typeSettings{formulaType:"waterfall", waterfallType "company/jobOpenings", formulaWaterfall[{formula}]}.
- action: typeSettings{dataTypeSettings{type:json}, actionKey, actionVersion, actionPackageId, inputsBinding[{name,
  formulaText}], conditionalRunFormulaText?, …} + actionDefinition (copy of catalogue entry incl. pricing).
- source: typeSettings{sourceIds[s_], canCreateRecords, deleteSourceLinkOnTableRecordDelete,
  destroySourceRecordOnTableRecordDelete}.
- message group: POST ~/tables/{t}/fields/message-group/v2 creates group + Subject/Body formula fields; group stores
  the editable DSL segments [{type:"Formula", formula} | {type:"CleanVariable", variable, fallback,
  formattingOptions, removeEmojisAndSpecialChars} | (AISnippet [I])]; the fields store the COMPILED formula.

## View  [V]  (in table.views[])
{id gv_, tableId, name, description, order, fields{f_:{order, width, isVisible}}, sort, filter{items[{type
(operator), fieldId, value?} | {filterType:"Group", items[], combinationMode}], combinationMode AND|OR}, limit, offset,
typeSettings{isPreconfigured, preconfiguredType: errored-rows | support-view | fully-enriched-rows |
non-enrichment-columns | …}}.

## Source  GET /v3/sources/{s}  [V]
{id s_, workspaceId, name, type "v3-action", createdAt/updatedAt/deletedAt, typeSettings{name, actionKey
"run-search-dsl-export-to-s3", actionPackageId, inputs{dslQuery, entityType, selectProjectionName,
selectProjectionVersion, previous_entities_bitmap sbmp_}, recordsPath "companies"|"people", idPath
"clay_metadata.clay_company_id"|"clay_metadata.clay_profile_id", sourceRecordLimit 50000, iconType, previewTextPath,
defaultPreviewText, hasEvaluatedInputs, createdFromWorkbookId, disableTriggerOnCreate, disableTriggerOnUpdate},
state{token ast_, action{status, logInfo, textPreview, imagePreview}, lambdaInfo{isFirstRun, numConsecutiveErrors},
numSourceRecords}, recordSourceUpdatedAt, sourceSubscriptions[{tableId, fieldId}]}.
Signals = sources with a schedule (UI: frequency, next run) [V UI, schedule field name not captured].

## Record  [V]
List: GET /v3/tables/{t}/views/{gv}/records/ids → {results:[r_]}; GET …/records?limit&offset; POST
~/tables/{t}/bulk-fetch-records {recordIds[]} ; one: GET /v3/tables/{t}/records/{r} (full, ~105 KB);
GET ~/tables/{t}/count → {tableTotalRecordsCount}.
{id r_, tableId, cells{f_: Cell}, recordMetadata{runHistory{f_:[{time, runId}]}}, createdAt, updatedAt, deletedBy,
dedupeValue}.

## Cell  [V]
List view: {value (preview), metadata{status, isPreview, imagePreview, confidence (AI), isStreamingProgress,
streamingProgressKey, isCoerced, sourceId, updatedAt}}.
Full record adds:
- extendedContent{runId, queuedAt, finishedAt, inputsHash} — or, if not run, {cellDidNotRunInfo{delayInfo
  {inputErrors[]}, inputToErrors{<input>:{evaluationError:"BLANK", inputDisplayName}}, conditionalRunInfo
  {inputErrors[]}}}.
- externalContent{runId, status, logInfo{url (Datadog), requestId}, inputsHash, isRefunded, textPreview,
  imagePreview, isPrivateKey, fullValue (complete JSON result), hiddenValue (AI: {messages[{role, content}],
  langSmithTraceURL}), upfrontCreditUsage{totalCost, basicCreditsUsed, cpjAllowanceUsed, budgetCreditsUsed,
  signalCreditsUsed, workbookCreditsUsed, workflowCreditsUsed, actionExecutionsUsed, longExpiryCreditsUsed,
  workspaceSalesRepCreditsUsed, audienceBulkEnrichmentTableCreditsUsed}, additionalCreditUsage{totalCost,
  basicCreditsUsed, actionExecutionsUsed, longExpiryCreditsUsed} (AI true-up, e.g. −0.1), additionalCreditCost,
  creditCostAdditionalInfo, isStreamingProgress}.
Statuses: SUCCESS, SUCCESS_NO_DATA, ERROR_RUN_CONDITION_NOT_MET, AWAITING_CALLBACK, ERROR*, QUEUED/RUNNING/RETRY/
STALE (filter operators) [statuses seen live: first four].

## Run  [V partial]
PATCH ~/tables/{t}/run {fieldIds[], runRecords{recordIds[]}, forceRun, callerName} → {runMode:"INDIVIDUAL"}. Field
creation with "Save and run N" starts runs itself. run_ ids appear in cell extendedContent/externalContent and in
recordMetadata.runHistory. No standalone run/job list endpoint found [I: runs are per-cell]. Public-API runs:
~/tools/routine-runs {data[]}.

## Action catalogue  GET /v3/actions?workspaceId  [V Phase 1]
1,576 actions / 264 packages; {key, version, package{…}, displayName, inputParameterSchema[], outputParameterSchema[
{name, type, outputPath, semanticType, sampleValue}], auth{providerType}, rateLimitRules, actionLabels, pricing{credits
{basic}, refundRules{SUCCESS_NO_DATA}}, cacheable}. Connections (app accounts) ~/app-accounts/… [V UI].

## Claygent  GET ~/claygents/{c}  [V Phase 1–2]
{id c_, name, description, icon, agentType, currentVersionId, budgetId, publishedAt, currentVersion{id cv_,
versionNumber, userPrompt, variables[{name, type, required, description}], modelSettings{model, useCase,
memoryEnabled, internetSearchEnabled, peopleAndCompaniesSearchBudget, peopleAndCompaniesSearchEnabled,
shouldIncludeAllBusinessContext}, toolSettings, contextSettings[], outputFormat{type json, jsonType JSONSchema,
jsonSchema}, skills[], externalToolIds[], isPublished}}. Table column binds {claygentId} (no version pin).

## Business context  GET ~/ai-quickstart/gtm-rules  [V]
{gtmRules{companyDescription, (ICP), (personas)}, domain}.

## Search session  [V]
Chat conversation cc_: GET ~/ai-generation/chat-conversation/{cc}/messages → {messages[{id cm_, parts[], role,
attachments[], feedback}], conversation{id, title, conversationType, conversationData, settings, updatedAt}};
…/source-state → {sourceState{sourceType, sourceConfig{type:"search", entityType, mode:"dsl", filters{62 keys},
additionalRequirements, dslSearchConfig, originalQuery}}}; POST ~/search-dsl/query {query, selectMode:"preview",
conversationId} → {rows, rowCount, totalCount, durationMs, previewCoverage}; POST ~/search-dsl/send-to-table.
Saved searches = presets (~/presets/filtered, ~/presets/recent-searches).

## Audiences  [V]
- Fields: GET ~/audiences/contacts/columns → [{version, id, workspaceId, entityType, fieldType "SCALAR", displayName,
  description, dataType text|email|url, order, hidden, isDefaultField, isSystemField}] (16 default contact fields).
- Records: POST ~/audiences/contacts {limit, offset, includeDeleted, isArchived, shouldInjectDraftFilter,
  searchQuery, segmentType, includeData{accountIds, signals}} → {contacts[{entity{id (number), workspace_id,
  created_at, version, deleted, cpj_id, fields[{field_id, value_type, value}], signals_aggregated[], account_ids[]},
  aliases[], signalRelations[], signals{}, externalSourceSyncStatuses{}, contactsCount, accounts[],
  associatedAccountIds[]}], pagination}. EAV storage.
- POST ~/audiences/count {entityType CONTACT|ACCOUNT|CUSTOM, objectType "OPPORTUNITY" (custom objects), isArchived,
  segmentType} → {count}.
- Imports: GET ~/audiences/imports → [{id audimp_, sourceId (table), fieldMapping{version, fieldMappings[]},
  entityType, status, importedCount, errorMessage, displayName, importSourceType, importMetadata{type, viewId,
  tableId, tableName, totalCount, startTime, endTime}}]. Matching rule: alias field (External ID | Email | Phone |
  LinkedIn URL | Twitter) equals column → merge; first import only; merges irreversible.
- Segments: GET ~/audiences/segments → [{id audseg_, name, description, filterAst{id, type:"GroupOp", items[{id,
  key, type:"BinOp", value, dataPath[], operator}]}, entityType, signalDaysLookback, segmentType, bitmapProcessedAt,
  estimatedSize, order, ownerId, tags[]}] (bitmaps refreshed hourly → estimatedSize 0 right after import).
- Other: ~/audiences/{CONTACT}/signals, bulk-enrichment-tables, bulk-enrichment-mapped-fields, audiences-exports,
  scheduled-searches, source-company-segments, ~/audiences/roi/fill-rate, ~/tc-workflows/audience-enrichment.
- Data hub after our import: Name 100%, Title 100%, First/Last name 8% (Auto-map picked a provider output).

## Campaign (sequence)  GET ~/all-campaigns  [V]
{id cam_, name, status draft, enrollmentMode "segment", sequence[] (steps), settings{unsubscribeText,
trackingSettings{trackEmailOpen, trackLinkClick}, scheduleSettings{scheduleType "prioritize_deliverability",
timezone, daysOfWeek[], startHour, endHour, minTimeBetweenSteps, maxNewLea…}, …}, providers{smartlead{clientId,
campaignId}}, leadBaseSegmentId audseg_, leadExclusionSegmentIds[], budgetId, claygentId c_ (agent writes copy),
claygentContext{attachedContextItems[]}, createdByUserId}. Creating it also created a "Campaign events" workbook.

## Function (subroutine)  GET ~/subroutines  [V]
[{sourceId s_, table{…24 keys, type "spreadsheet", name "Person LinkedIn URL"}, cost 2.2, actionExecutionCost 0.9,
referenceCount, containsVariablePricing, managedSubroutineSubscription{subscriptionId msubsub_, managedSubroutineId
msub_}}] — a function IS a table with an input source; Clay-managed ones are subscriptions.

## Workflow  [V]
GET ~/tc-workflows-paginated → {workflows[{id wf_, name, description(+Generated), type, liveWorkflowSnapshotId,
subworkflowConfig, waterfallConfig, lastRunAt, stats{…6}, triggerSummaries[], budgetId, can*}], nextCursor}.
GET ~/tc-workflows/{wf}/graph → {nodes[{id wfn_, workflowId, name(+Generated), nodeType, tools[], nodeConfig
{nodeType}, subroutineIds[], position{x,y}}], edges[], nodeGroups, validation, workflowInputSchemasByTriggerNodeId,
recommendedManualSchemasByTriggerNodeId}.

## Credit reporting  [V]
/v3/credit-reporting/{ws}/balanceTimeSeries?interval=day&timeRange[startTime|endTime]; …/spikeEvents; …/spendTimeSeries;
~/credit-reporting/creditReportType/{workspace|triggerDefinition}; ~/realtime-credit-usage/table/{t}/time (needs
params). UI rollup Workspace 66/95 → workbook "Indian Energy…" 65/94 → company table 55/56, people table 10/38;
table History adds per-column/per-run. Ledger source of truth = per-cell upfrontCreditUsage + additionalCreditUsage
[I that reporting aggregates these].

## Real-time / paging
Records paged by ids + bulk fetch (virtualised grid) [V]; audiences limit/offset [V]; workflows cursor [V]. Live
updates: UI re-polls bulk-fetch-records while cells run [V repeated calls]; notifications via Knock (/auth/knock/token)
[V]; AI streaming via streamingProgressKey [V field, transport not seen].
