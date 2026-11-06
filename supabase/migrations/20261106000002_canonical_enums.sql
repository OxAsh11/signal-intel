-- Canonical closed value lists (PRD Appendix A.2, A.3, A.4).
-- Must match packages/contracts/schema/enums.json; `pnpm db:check` fails on drift.
-- Changing a list later = a new migration (alter type ... add value), reviewed.

create type public.entity_type as enum ('company', 'person', 'site', 'digital_asset', 'query');  -- EntityType
create type public.alias_type as enum ('registry_id', 'domain', 'profile_url', 'name_country');  -- AliasType
create type public.data_scope as enum ('shared', 'tenant');  -- DataScope
create type public.claim_status as enum ('active', 'superseded', 'retracted');  -- ClaimStatus
create type public.extraction_status as enum ('pending', 'extracted', 'irrelevant', 'failed');  -- ExtractionStatus
create type public.brief_state as enum ('Draft', 'Pilot', 'Active', 'Paused', 'Archived');  -- BriefState
create type public.edit_tier as enum ('Narrow', 'Redefine', 'Expand');  -- EditTier
create type public.verdict_value as enum ('Confirmed', 'Likely', 'Unverified', 'Contradicted');  -- Verdict
create type public.score_dimension as enum ('Fit', 'Evidence', 'Scale', 'Timing', 'Reachability');  -- ScoreDimension
create type public.band as enum ('High', 'Medium', 'Low', 'Excluded');  -- Band
create type public.stage as enum ('Untouched', 'Queued', 'Contacted', 'Engaged', 'Qualified', 'Meeting', 'Handed off', 'Dormant', 'Disqualified');  -- Stage
create type public.reply_class as enum ('Interested', 'Referral', 'Not now', 'Not interested', 'Out of office', 'Unsubscribe');  -- ReplyClass
create type public.motion as enum ('Geographic', 'Vertical', 'Event', 'Trigger', 'Re-engagement');  -- Motion
create type public.push_mode as enum ('off', 'dry run', 'live');  -- PushMode
create type public.access_mode as enum ('Read', 'Write', 'Read-write');  -- AccessMode
create type public.field_owner as enum ('Platform', 'External');  -- FieldOwner
create type public.contact_verification_status as enum ('valid', 'risky', 'invalid', 'unknown');  -- ContactVerificationStatus
create type public.member_role as enum ('Owner', 'Admin', 'Editor', 'Operator', 'Viewer');  -- Role
create type public.token_scope as enum ('Read', 'Write', 'Admin');  -- TokenScope
create type public.next_step_mode as enum ('Suggest', 'Active');  -- NextStepMode
create type public.gate_outcome as enum ('applied', 'review queue', 'digest');  -- GateOutcome
create type public.activity_kind as enum ('message_sent', 'message_bounced', 'reply_received', 'auto_reply', 'unsubscribed', 'inbound_contact', 'meeting_scheduled', 'meeting_held', 'call_logged', 'note_added', 'marked_qualified', 'marked_disqualified', 'deal_created', 'deal_stage_changed', 'task_completed');  -- ActivityKind
create type public.event_type as enum ('brief.version_published', 'source_record.created', 'claim.created', 'claim.retracted', 'entity.created', 'entity.merged', 'entity.unmerged', 'verdict.changed', 'research_doc.published', 'contact_point.verified', 'score.band_changed', 'campaign.member_added', 'campaign.paused', 'reply.received', 'reply.classified', 'account.stage_changed', 'meeting.held', 'task.created', 'health.incident_opened', 'health.incident_resolved');  -- EventType
