// GENERATED from packages/contracts/schema/enums.json by scripts/gen.mjs. Do not edit.
import { z } from 'zod';

export const EntityType = z.enum(["company","person","site","digital_asset","query"]);
export type EntityType = z.infer<typeof EntityType>;

export const AliasType = z.enum(["registry_id","domain","profile_url","name_country"]);
export type AliasType = z.infer<typeof AliasType>;

export const DataScope = z.enum(["shared","tenant"]);
export type DataScope = z.infer<typeof DataScope>;

export const ClaimStatus = z.enum(["active","superseded","retracted"]);
export type ClaimStatus = z.infer<typeof ClaimStatus>;

export const ExtractionStatus = z.enum(["pending","extracted","irrelevant","failed"]);
export type ExtractionStatus = z.infer<typeof ExtractionStatus>;

export const BriefState = z.enum(["Draft","Pilot","Active","Paused","Archived"]);
export type BriefState = z.infer<typeof BriefState>;

export const EditTier = z.enum(["Narrow","Redefine","Expand"]);
export type EditTier = z.infer<typeof EditTier>;

export const Verdict = z.enum(["Confirmed","Likely","Unverified","Contradicted"]);
export type Verdict = z.infer<typeof Verdict>;

export const ScoreDimension = z.enum(["Fit","Evidence","Scale","Timing","Reachability"]);
export type ScoreDimension = z.infer<typeof ScoreDimension>;

export const Band = z.enum(["High","Medium","Low","Excluded"]);
export type Band = z.infer<typeof Band>;

export const Stage = z.enum(["Untouched","Queued","Contacted","Engaged","Qualified","Meeting","Handed off","Dormant","Disqualified"]);
export type Stage = z.infer<typeof Stage>;

export const ReplyClass = z.enum(["Interested","Referral","Not now","Not interested","Out of office","Unsubscribe"]);
export type ReplyClass = z.infer<typeof ReplyClass>;

export const Motion = z.enum(["Geographic","Vertical","Event","Trigger","Re-engagement"]);
export type Motion = z.infer<typeof Motion>;

export const PushMode = z.enum(["off","dry run","live"]);
export type PushMode = z.infer<typeof PushMode>;

export const AccessMode = z.enum(["Read","Write","Read-write"]);
export type AccessMode = z.infer<typeof AccessMode>;

export const FieldOwner = z.enum(["Platform","External"]);
export type FieldOwner = z.infer<typeof FieldOwner>;

export const ContactVerificationStatus = z.enum(["valid","risky","invalid","unknown"]);
export type ContactVerificationStatus = z.infer<typeof ContactVerificationStatus>;

export const Role = z.enum(["Owner","Admin","Editor","Operator","Viewer"]);
export type Role = z.infer<typeof Role>;

export const TokenScope = z.enum(["Read","Write","Admin"]);
export type TokenScope = z.infer<typeof TokenScope>;

export const NextStepMode = z.enum(["Suggest","Active"]);
export type NextStepMode = z.infer<typeof NextStepMode>;

export const GateOutcome = z.enum(["applied","review queue","digest"]);
export type GateOutcome = z.infer<typeof GateOutcome>;

export const ActivityKind = z.enum(["message_sent","message_bounced","reply_received","auto_reply","unsubscribed","inbound_contact","meeting_scheduled","meeting_held","call_logged","note_added","marked_qualified","marked_disqualified","deal_created","deal_stage_changed","task_completed"]);
export type ActivityKind = z.infer<typeof ActivityKind>;

export const EventType = z.enum(["brief.version_published","brief.intake_requested","source_record.created","claim.created","claim.retracted","entity.created","entity.merged","entity.unmerged","verdict.changed","research_doc.published","contact_point.verified","score.band_changed","campaign.member_added","campaign.paused","reply.received","reply.classified","account.stage_changed","meeting.held","task.created","health.incident_opened","health.incident_resolved","radar.fetch_queued","brief.preview_requested"]);
export type EventType = z.infer<typeof EventType>;

export const UsageUnit = z.enum(["ai_input_tokens","ai_output_tokens","embed_tokens","fetch","enrichment_lookup","research_doc"]);
export type UsageUnit = z.infer<typeof UsageUnit>;

export const BudgetPeriod = z.enum(["day","month"]);
export type BudgetPeriod = z.infer<typeof BudgetPeriod>;

export const ApprovalStatus = z.enum(["pending","approved","rejected","expired"]);
export type ApprovalStatus = z.infer<typeof ApprovalStatus>;

export const PG_ENUM_NAMES = {
  "EntityType": "entity_type",
  "AliasType": "alias_type",
  "DataScope": "data_scope",
  "ClaimStatus": "claim_status",
  "ExtractionStatus": "extraction_status",
  "BriefState": "brief_state",
  "EditTier": "edit_tier",
  "Verdict": "verdict_value",
  "ScoreDimension": "score_dimension",
  "Band": "band",
  "Stage": "stage",
  "ReplyClass": "reply_class",
  "Motion": "motion",
  "PushMode": "push_mode",
  "AccessMode": "access_mode",
  "FieldOwner": "field_owner",
  "ContactVerificationStatus": "contact_verification_status",
  "Role": "member_role",
  "TokenScope": "token_scope",
  "NextStepMode": "next_step_mode",
  "GateOutcome": "gate_outcome",
  "ActivityKind": "activity_kind",
  "EventType": "event_type",
  "UsageUnit": "usage_unit",
  "BudgetPeriod": "budget_period",
  "ApprovalStatus": "approval_status"
} as const;
