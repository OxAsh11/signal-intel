# GENERATED from packages/contracts/schema/enums.json by scripts/gen.mjs. Do not edit.
"""Canonical value lists (PRD Appendix A)."""

from enum import StrEnum


class EntityType(StrEnum):
    COMPANY = "company"
    PERSON = "person"
    SITE = "site"
    DIGITAL_ASSET = "digital_asset"
    QUERY = "query"


class AliasType(StrEnum):
    """Listed in matching priority order."""

    REGISTRY_ID = "registry_id"
    DOMAIN = "domain"
    PROFILE_URL = "profile_url"
    NAME_COUNTRY = "name_country"


class DataScope(StrEnum):
    SHARED = "shared"
    TENANT = "tenant"


class ClaimStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class ExtractionStatus(StrEnum):
    PENDING = "pending"
    EXTRACTED = "extracted"
    IRRELEVANT = "irrelevant"
    FAILED = "failed"


class BriefState(StrEnum):
    DRAFT = "Draft"
    PILOT = "Pilot"
    ACTIVE = "Active"
    PAUSED = "Paused"
    ARCHIVED = "Archived"


class EditTier(StrEnum):
    NARROW = "Narrow"
    REDEFINE = "Redefine"
    EXPAND = "Expand"


class Verdict(StrEnum):
    CONFIRMED = "Confirmed"
    LIKELY = "Likely"
    UNVERIFIED = "Unverified"
    CONTRADICTED = "Contradicted"


class ScoreDimension(StrEnum):
    FIT = "Fit"
    EVIDENCE = "Evidence"
    SCALE = "Scale"
    TIMING = "Timing"
    REACHABILITY = "Reachability"


class Band(StrEnum):
    HIGH = "High"
    MEDIUM = "Medium"
    LOW = "Low"
    EXCLUDED = "Excluded"


class Stage(StrEnum):
    UNTOUCHED = "Untouched"
    QUEUED = "Queued"
    CONTACTED = "Contacted"
    ENGAGED = "Engaged"
    QUALIFIED = "Qualified"
    MEETING = "Meeting"
    HANDED_OFF = "Handed off"
    DORMANT = "Dormant"
    DISQUALIFIED = "Disqualified"


class ReplyClass(StrEnum):
    INTERESTED = "Interested"
    REFERRAL = "Referral"
    NOT_NOW = "Not now"
    NOT_INTERESTED = "Not interested"
    OUT_OF_OFFICE = "Out of office"
    UNSUBSCRIBE = "Unsubscribe"


class Motion(StrEnum):
    GEOGRAPHIC = "Geographic"
    VERTICAL = "Vertical"
    EVENT = "Event"
    TRIGGER = "Trigger"
    RE_ENGAGEMENT = "Re-engagement"


class PushMode(StrEnum):
    OFF = "off"
    DRY_RUN = "dry run"
    LIVE = "live"


class AccessMode(StrEnum):
    READ = "Read"
    WRITE = "Write"
    READ_WRITE = "Read-write"


class FieldOwner(StrEnum):
    PLATFORM = "Platform"
    EXTERNAL = "External"


class ContactVerificationStatus(StrEnum):
    VALID = "valid"
    RISKY = "risky"
    INVALID = "invalid"
    UNKNOWN = "unknown"


class Role(StrEnum):
    """Owner and Admin are organisation-level; Editor, Operator, Viewer are per workspace."""

    OWNER = "Owner"
    ADMIN = "Admin"
    EDITOR = "Editor"
    OPERATOR = "Operator"
    VIEWER = "Viewer"


class TokenScope(StrEnum):
    READ = "Read"
    WRITE = "Write"
    ADMIN = "Admin"


class NextStepMode(StrEnum):
    SUGGEST = "Suggest"
    ACTIVE = "Active"


class GateOutcome(StrEnum):
    APPLIED = "applied"
    REVIEW_QUEUE = "review queue"
    DIGEST = "digest"


class ActivityKind(StrEnum):
    MESSAGE_SENT = "message_sent"
    MESSAGE_BOUNCED = "message_bounced"
    REPLY_RECEIVED = "reply_received"
    AUTO_REPLY = "auto_reply"
    UNSUBSCRIBED = "unsubscribed"
    INBOUND_CONTACT = "inbound_contact"
    MEETING_SCHEDULED = "meeting_scheduled"
    MEETING_HELD = "meeting_held"
    CALL_LOGGED = "call_logged"
    NOTE_ADDED = "note_added"
    MARKED_QUALIFIED = "marked_qualified"
    MARKED_DISQUALIFIED = "marked_disqualified"
    DEAL_CREATED = "deal_created"
    DEAL_STAGE_CHANGED = "deal_stage_changed"
    TASK_COMPLETED = "task_completed"


class EventType(StrEnum):
    """Closed event catalogue (A.4). Adding an event is a reviewed change."""

    BRIEF_VERSION_PUBLISHED = "brief.version_published"
    SOURCE_RECORD_CREATED = "source_record.created"
    CLAIM_CREATED = "claim.created"
    CLAIM_RETRACTED = "claim.retracted"
    ENTITY_CREATED = "entity.created"
    ENTITY_MERGED = "entity.merged"
    ENTITY_UNMERGED = "entity.unmerged"
    VERDICT_CHANGED = "verdict.changed"
    RESEARCH_DOC_PUBLISHED = "research_doc.published"
    CONTACT_POINT_VERIFIED = "contact_point.verified"
    SCORE_BAND_CHANGED = "score.band_changed"
    CAMPAIGN_MEMBER_ADDED = "campaign.member_added"
    CAMPAIGN_PAUSED = "campaign.paused"
    REPLY_RECEIVED = "reply.received"
    REPLY_CLASSIFIED = "reply.classified"
    ACCOUNT_STAGE_CHANGED = "account.stage_changed"
    MEETING_HELD = "meeting.held"
    TASK_CREATED = "task.created"
    HEALTH_INCIDENT_OPENED = "health.incident_opened"
    HEALTH_INCIDENT_RESOLVED = "health.incident_resolved"


class UsageUnit(StrEnum):
    """S6 metering units."""

    AI_INPUT_TOKENS = "ai_input_tokens"
    AI_OUTPUT_TOKENS = "ai_output_tokens"
    EMBED_TOKENS = "embed_tokens"
    FETCH = "fetch"
    ENRICHMENT_LOOKUP = "enrichment_lookup"
    RESEARCH_DOC = "research_doc"


class BudgetPeriod(StrEnum):
    DAY = "day"
    MONTH = "month"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


PG_ENUM_NAMES: dict[str, str] = {
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
    "ApprovalStatus": "approval_status",
}
