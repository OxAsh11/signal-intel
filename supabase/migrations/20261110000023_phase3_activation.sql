-- Phase 3: Activation layer tables + RPCs + kill-switch seeds + metric_definition seeds.
-- Adds:
--   Tables: integration, field_ownership, campaign, campaign_member, meeting, task,
--           next_step, suppression_list, webhook_subscription, webhook_delivery,
--           api_token, external_write_log, evaluation_set, data_subject_request,
--           lawful_basis_record, metric_definition
--   RPCs: create_campaign, add_campaign_member, check_and_pause_if_bouncing,
--         set_campaign_push_mode, create_meeting, confirm_meeting, create_task,
--         upsert_next_step, create_webhook_subscription, record_webhook_delivery,
--         create_api_token, record_external_write, process_dsr_deletion, compute_metric
--   Kill-switch seeds (job_type_config)
--   15 canonical metric_definition seeds

-- ── 1. public.integration ─────────────────────────────────────────────────────
CREATE TABLE public.integration (
  id                     uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                 uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id           uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  name                   text NOT NULL,
  category               text NOT NULL,
  access_mode            public.access_mode NOT NULL DEFAULT 'Read',
  enabled                bool NOT NULL DEFAULT true,
  last_success_at        timestamptz,
  last_error             text,
  credentials_vault_key  text,
  config                 jsonb NOT NULL DEFAULT '{}',
  created_at             timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.integration ENABLE ROW LEVEL SECURITY;
CREATE POLICY integration_read ON public.integration
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.integration TO authenticated;

-- ── 2. public.field_ownership ─────────────────────────────────────────────────
CREATE TABLE public.field_ownership (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  integration_id uuid NOT NULL REFERENCES public.integration (id) ON DELETE CASCADE,
  object_type    text NOT NULL,
  field_name     text NOT NULL,
  owner          public.field_owner NOT NULL,
  created_at     timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT field_ownership_unique UNIQUE (integration_id, object_type, field_name)
);

ALTER TABLE public.field_ownership ENABLE ROW LEVEL SECURITY;
CREATE POLICY field_ownership_read ON public.field_ownership
  FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM public.integration i
     WHERE i.id = field_ownership.integration_id
       AND app.has_org_role(i.org_id, 'Admin')
  ));
GRANT SELECT ON public.field_ownership TO authenticated;

-- ── 3. public.campaign ────────────────────────────────────────────────────────
CREATE TABLE public.campaign (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                      uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id                uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  brief_id                    uuid REFERENCES public.brief (id),
  segment_id                  uuid,
  name                        text NOT NULL,
  motion                      public.motion NOT NULL,
  destination_integration_id  uuid REFERENCES public.integration (id),
  external_campaign_id        text,
  push_mode                   public.push_mode NOT NULL DEFAULT 'off',
  status                      text NOT NULL DEFAULT 'draft'
                                CHECK (status IN ('draft', 'active', 'paused', 'archived')),
  bounce_messages_sent        int NOT NULL DEFAULT 0,
  bounce_count                int NOT NULL DEFAULT 0,
  created_at                  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX campaign_ws_status_idx ON public.campaign (workspace_id, status, created_at);

ALTER TABLE public.campaign ENABLE ROW LEVEL SECURITY;
CREATE POLICY campaign_read ON public.campaign
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.campaign TO authenticated;

-- ── 4. public.campaign_member ─────────────────────────────────────────────────
CREATE TABLE public.campaign_member (
  id                 uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id             uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id       uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  campaign_id        uuid NOT NULL REFERENCES public.campaign (id) ON DELETE CASCADE,
  account_id         uuid NOT NULL REFERENCES public.account (id),
  person_entity_id   uuid,
  contact_point_id   uuid,
  variables          jsonb NOT NULL DEFAULT '{}',
  pushed_at          timestamptz,
  push_mode_at_send  public.push_mode,
  external_id        text,
  created_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT campaign_member_unique UNIQUE (campaign_id, person_entity_id)
);
CREATE INDEX campaign_member_campaign_idx ON public.campaign_member (campaign_id, pushed_at DESC);

ALTER TABLE public.campaign_member ENABLE ROW LEVEL SECURITY;
CREATE POLICY campaign_member_read ON public.campaign_member
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.campaign_member TO authenticated;

-- ── 5. public.meeting ─────────────────────────────────────────────────────────
CREATE TABLE public.meeting (
  id                          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id                      uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id                uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  account_id                  uuid NOT NULL REFERENCES public.account (id),
  person_entity_id            uuid,
  external_id                 text,
  source_integration_id       uuid,
  scheduled_at                timestamptz NOT NULL,
  held_at                     timestamptz,
  proof_status                text NOT NULL DEFAULT 'unconfirmed'
                                CHECK (proof_status IN ('unconfirmed', 'confirmed', 'has_transcript')),
  transcript_source_record_id uuid,
  attribution_campaign_id     uuid,
  attribution_tier            text,
  created_at                  timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT meeting_external_unique UNIQUE NULLS NOT DISTINCT (source_integration_id, external_id)
);
CREATE INDEX meeting_ws_held_idx ON public.meeting (workspace_id, held_at DESC);

ALTER TABLE public.meeting ENABLE ROW LEVEL SECURITY;
CREATE POLICY meeting_read ON public.meeting
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.meeting TO authenticated;

-- ── 6. public.task ────────────────────────────────────────────────────────────
CREATE TABLE public.task (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id    uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  account_id      uuid NOT NULL REFERENCES public.account (id),
  owner_member_id uuid,
  title           text NOT NULL,
  due_date        date,
  origin          text NOT NULL
                    CHECK (origin IN ('manual', 'reply', 'meeting', 'next_step')),
  status          text NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'done')),
  created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX task_ws_status_idx ON public.task (workspace_id, status, due_date ASC NULLS LAST);

ALTER TABLE public.task ENABLE ROW LEVEL SECURITY;
CREATE POLICY task_read ON public.task
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.task TO authenticated;

-- ── 7. public.next_step ───────────────────────────────────────────────────────
CREATE TABLE public.next_step (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id   uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  account_id     uuid NOT NULL REFERENCES public.account (id) UNIQUE,
  suggested_text text NOT NULL,
  mode           public.next_step_mode NOT NULL DEFAULT 'Suggest',
  created_at     timestamptz NOT NULL DEFAULT now(),
  updated_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.next_step ENABLE ROW LEVEL SECURITY;
CREATE POLICY next_step_read ON public.next_step
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.next_step TO authenticated;

-- ── 8. public.suppression_list ────────────────────────────────────────────────
CREATE TABLE public.suppression_list (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  person_entity_id uuid NOT NULL,
  reason           text,
  added_at         timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT suppression_list_unique UNIQUE (org_id, person_entity_id)
);

ALTER TABLE public.suppression_list ENABLE ROW LEVEL SECURITY;
CREATE POLICY suppression_list_read ON public.suppression_list
  FOR SELECT TO authenticated
  USING (app.has_org_role(org_id, 'Admin'));
GRANT SELECT ON public.suppression_list TO authenticated;

-- ── 9. public.webhook_subscription ───────────────────────────────────────────
CREATE TABLE public.webhook_subscription (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  event_types      text[] NOT NULL,
  url              text NOT NULL,
  secret_vault_key text NOT NULL,
  enabled          bool NOT NULL DEFAULT true,
  fail_count       int NOT NULL DEFAULT 0,
  created_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.webhook_subscription ENABLE ROW LEVEL SECURITY;
CREATE POLICY webhook_subscription_read ON public.webhook_subscription
  FOR SELECT TO authenticated
  USING (app.has_org_role(org_id, 'Admin'));
GRANT SELECT ON public.webhook_subscription TO authenticated;

-- ── 10. public.webhook_delivery ───────────────────────────────────────────────
CREATE TABLE public.webhook_delivery (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subscription_id  uuid NOT NULL REFERENCES public.webhook_subscription (id) ON DELETE CASCADE,
  event_id         uuid,
  payload          jsonb NOT NULL,
  signature        text NOT NULL,
  status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'delivered', 'failed')),
  attempt_count    int NOT NULL DEFAULT 0,
  last_attempted_at timestamptz,
  delivered_at     timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT webhook_delivery_unique UNIQUE (subscription_id, event_id)
);
CREATE INDEX webhook_delivery_sub_status_idx ON public.webhook_delivery (subscription_id, status, created_at);

ALTER TABLE public.webhook_delivery ENABLE ROW LEVEL SECURITY;
CREATE POLICY webhook_delivery_read ON public.webhook_delivery
  FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM public.webhook_subscription ws
     WHERE ws.id = webhook_delivery.subscription_id
       AND app.has_org_role(ws.org_id, 'Admin')
  ));
GRANT SELECT ON public.webhook_delivery TO authenticated;

-- ── 11. public.api_token ──────────────────────────────────────────────────────
CREATE TABLE public.api_token (
  id             uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id         uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id   uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  member_id      uuid,
  scope          public.token_scope NOT NULL,
  name           text NOT NULL,
  token_hash     text NOT NULL UNIQUE,
  last_used_at   timestamptz,
  expires_at     timestamptz,
  rate_limit_rpm int NOT NULL DEFAULT 100,
  revoked_at     timestamptz,
  created_at     timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.api_token ENABLE ROW LEVEL SECURITY;
-- Authenticated users see only their own tokens
CREATE POLICY api_token_read ON public.api_token
  FOR SELECT TO authenticated
  USING (member_id = app.current_member_id(org_id));
GRANT SELECT ON public.api_token TO authenticated;

-- ── 12. public.external_write_log ─────────────────────────────────────────────
CREATE TABLE public.external_write_log (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  integration_id   uuid,
  action_type      text,
  target_type      text,
  target_id        uuid,
  external_id      text,
  idempotency_key  text NOT NULL UNIQUE,
  dry_run          bool NOT NULL DEFAULT false,
  status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'ok', 'error')),
  error            text,
  executed_at      timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX external_write_log_ws_status_idx ON public.external_write_log (workspace_id, status, created_at DESC);

ALTER TABLE public.external_write_log ENABLE ROW LEVEL SECURITY;
CREATE POLICY external_write_log_read ON public.external_write_log
  FOR SELECT TO authenticated
  USING (app.has_org_role(org_id, 'Admin'));
GRANT SELECT ON public.external_write_log TO authenticated;

-- ── 13. public.evaluation_set ─────────────────────────────────────────────────
CREATE TABLE public.evaluation_set (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  brief_version_id uuid NOT NULL REFERENCES public.brief_version (id),
  examples         jsonb NOT NULL DEFAULT '[]',
  created_at       timestamptz NOT NULL DEFAULT now(),
  updated_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT evaluation_set_unique UNIQUE (brief_version_id)
);

ALTER TABLE public.evaluation_set ENABLE ROW LEVEL SECURITY;
-- Editor-level policy for evaluation_set
CREATE POLICY evaluation_set_read ON public.evaluation_set
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Editor'));
GRANT SELECT ON public.evaluation_set TO authenticated;

-- ── 14. public.data_subject_request ──────────────────────────────────────────
CREATE TABLE public.data_subject_request (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  type             text NOT NULL CHECK (type IN ('access', 'deletion')),
  person_entity_id uuid,
  requested_by     text NOT NULL,
  completed_at     timestamptz,
  created_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.data_subject_request ENABLE ROW LEVEL SECURITY;
CREATE POLICY data_subject_request_read ON public.data_subject_request
  FOR SELECT TO authenticated
  USING (app.has_org_role(org_id, 'Admin'));
GRANT SELECT ON public.data_subject_request TO authenticated;

-- ── 15. public.lawful_basis_record ────────────────────────────────────────────
CREATE TABLE public.lawful_basis_record (
  id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id      uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  source_type text NOT NULL,
  basis       text NOT NULL,
  recorded_at timestamptz NOT NULL DEFAULT now(),
  created_at  timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.lawful_basis_record ENABLE ROW LEVEL SECURITY;
CREATE POLICY lawful_basis_record_read ON public.lawful_basis_record
  FOR SELECT TO authenticated
  USING (app.has_org_role(org_id, 'Admin'));
GRANT SELECT ON public.lawful_basis_record TO authenticated;

-- ── 16. public.metric_definition ─────────────────────────────────────────────
CREATE TABLE public.metric_definition (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid,  -- NULL = global/canonical; org-scoped rows override for that org
  key          text NOT NULL,
  version      int NOT NULL DEFAULT 1,
  sql_body     text NOT NULL,
  published_at timestamptz,
  created_at   timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT metric_definition_unique UNIQUE NULLS NOT DISTINCT (org_id, key, version)
);

ALTER TABLE public.metric_definition ENABLE ROW LEVEL SECURITY;
CREATE POLICY metric_definition_read ON public.metric_definition
  FOR SELECT TO authenticated
  USING (org_id IS NULL OR app.has_org_role(org_id, 'Admin'));
GRANT SELECT ON public.metric_definition TO authenticated;

-- INSERT-only protection: once published_at is set, revoke UPDATE/DELETE
-- (enforced at column level by trigger)
CREATE FUNCTION app.metric_definition_immutable() RETURNS trigger
LANGUAGE plpgsql SET search_path = '' AS $$
BEGIN
  IF OLD.published_at IS NOT NULL THEN
    RAISE EXCEPTION 'metric_definition rows are insert-only once published' USING ERRCODE = '42501';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER metric_definition_no_update
  BEFORE UPDATE OR DELETE ON public.metric_definition
  FOR EACH ROW EXECUTE FUNCTION app.metric_definition_immutable();

-- ── RPCs ──────────────────────────────────────────────────────────────────────

-- ── app.create_campaign ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_campaign(
  p_org_id                    uuid,
  p_workspace_id              uuid,
  p_brief_id                  uuid,
  p_name                      text,
  p_motion                    public.motion,
  p_destination_integration_id uuid DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_campaign_id uuid;
BEGIN
  INSERT INTO public.campaign
    (org_id, workspace_id, brief_id, name, motion, destination_integration_id)
  VALUES
    (p_org_id, p_workspace_id, p_brief_id, p_name, p_motion, p_destination_integration_id)
  RETURNING id INTO v_campaign_id;

  PERFORM app.audit(
    p_org_id, p_workspace_id,
    'campaign.created', 'campaign', v_campaign_id,
    NULL,
    jsonb_build_object(
      'name',   p_name,
      'motion', p_motion
    )
  );

  RETURN v_campaign_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_campaign(uuid, uuid, uuid, text, public.motion, uuid)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.add_campaign_member ───────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.add_campaign_member(
  p_campaign_id      uuid,
  p_account_id       uuid,
  p_person_entity_id uuid,
  p_contact_point_id uuid DEFAULT NULL,
  p_variables        jsonb DEFAULT '{}'
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_member_id    uuid;
BEGIN
  SELECT org_id, workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.campaign
   WHERE id = p_campaign_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.campaign_member
    (org_id, workspace_id, campaign_id, account_id, person_entity_id,
     contact_point_id, variables)
  VALUES
    (v_org_id, v_workspace_id, p_campaign_id, p_account_id, p_person_entity_id,
     p_contact_point_id, coalesce(p_variables, '{}'))
  ON CONFLICT (campaign_id, person_entity_id) DO NOTHING
  RETURNING id INTO v_member_id;

  -- If conflict (already exists), fetch existing id
  IF v_member_id IS NULL THEN
    SELECT id INTO v_member_id
      FROM public.campaign_member
     WHERE campaign_id = p_campaign_id
       AND person_entity_id = p_person_entity_id;
    RETURN v_member_id;
  END IF;

  PERFORM app.emit(
    'campaign.member_added'::public.event_type,
    v_org_id, v_workspace_id,
    'campaign_member', v_member_id,
    jsonb_build_object(
      'campaign_id',      p_campaign_id,
      'account_id',       p_account_id,
      'person_entity_id', p_person_entity_id,
      'member_id',        v_member_id
    )
  );

  RETURN v_member_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.add_campaign_member(uuid, uuid, uuid, uuid, jsonb)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.check_and_pause_if_bouncing ───────────────────────────────────────────
-- Uses SELECT FOR UPDATE to prevent race condition.
CREATE OR REPLACE FUNCTION app.check_and_pause_if_bouncing(
  p_campaign_id uuid
) RETURNS bool
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id              uuid;
  v_workspace_id        uuid;
  v_bounce_count        int;
  v_bounce_messages_sent int;
  v_bounce_rate         numeric;
BEGIN
  -- Row-lock the campaign to prevent race condition
  SELECT org_id, workspace_id, bounce_count, bounce_messages_sent
    INTO v_org_id, v_workspace_id, v_bounce_count, v_bounce_messages_sent
    FROM public.campaign
   WHERE id = p_campaign_id
     FOR UPDATE;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = '22023';
  END IF;

  -- Compute bounce rate; guard against division by zero
  v_bounce_rate := v_bounce_count::numeric / NULLIF(v_bounce_messages_sent, 0);

  -- Pause if bounce rate >= 10%
  IF v_bounce_rate IS NOT NULL AND v_bounce_rate >= 0.10 THEN
    UPDATE public.campaign
       SET status = 'paused'
     WHERE id = p_campaign_id;

    PERFORM app.emit(
      'campaign.paused'::public.event_type,
      v_org_id, v_workspace_id,
      'campaign', p_campaign_id,
      jsonb_build_object(
        'campaign_id',          p_campaign_id,
        'bounce_count',         v_bounce_count,
        'bounce_messages_sent', v_bounce_messages_sent,
        'bounce_rate',          v_bounce_rate
      )
    );

    RETURN true;
  END IF;

  RETURN false;
END $$;

REVOKE EXECUTE ON FUNCTION app.check_and_pause_if_bouncing(uuid)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.set_campaign_push_mode ────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.set_campaign_push_mode(
  p_campaign_id uuid,
  p_push_mode   public.push_mode
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_old_mode     public.push_mode;
BEGIN
  SELECT org_id, workspace_id, push_mode
    INTO v_org_id, v_workspace_id, v_old_mode
    FROM public.campaign
   WHERE id = p_campaign_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = '22023';
  END IF;

  UPDATE public.campaign
     SET push_mode = p_push_mode
   WHERE id = p_campaign_id;

  PERFORM app.audit(
    v_org_id, v_workspace_id,
    'campaign.push_mode_changed', 'campaign', p_campaign_id,
    jsonb_build_object('push_mode', v_old_mode),
    jsonb_build_object('push_mode', p_push_mode)
  );
END $$;

REVOKE EXECUTE ON FUNCTION app.set_campaign_push_mode(uuid, public.push_mode)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.create_meeting ────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_meeting(
  p_org_id              uuid,
  p_workspace_id        uuid,
  p_account_id          uuid,
  p_person_entity_id    uuid,
  p_external_id         text,
  p_source_integration_id uuid,
  p_scheduled_at        timestamptz
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_meeting_id uuid;
BEGIN
  INSERT INTO public.meeting
    (org_id, workspace_id, account_id, person_entity_id,
     external_id, source_integration_id, scheduled_at)
  VALUES
    (p_org_id, p_workspace_id, p_account_id, p_person_entity_id,
     p_external_id, p_source_integration_id, p_scheduled_at)
  ON CONFLICT (source_integration_id, external_id) DO NOTHING
  RETURNING id INTO v_meeting_id;

  -- If conflict (already exists), fetch existing id
  IF v_meeting_id IS NULL THEN
    SELECT id INTO v_meeting_id
      FROM public.meeting
     WHERE source_integration_id IS NOT DISTINCT FROM p_source_integration_id
       AND external_id IS NOT DISTINCT FROM p_external_id;
  END IF;

  RETURN v_meeting_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_meeting(uuid, uuid, uuid, uuid, text, uuid, timestamptz)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.confirm_meeting ───────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.confirm_meeting(
  p_meeting_id              uuid,
  p_held_at                 timestamptz,
  p_proof_status            text,
  p_attribution_campaign_id uuid DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
BEGIN
  SELECT org_id, workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.meeting
   WHERE id = p_meeting_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'meeting % not found', p_meeting_id USING ERRCODE = '22023';
  END IF;

  UPDATE public.meeting
     SET held_at                  = p_held_at,
         proof_status             = p_proof_status,
         attribution_campaign_id  = coalesce(p_attribution_campaign_id, attribution_campaign_id)
   WHERE id = p_meeting_id;

  IF p_proof_status IN ('confirmed', 'has_transcript') THEN
    PERFORM app.emit(
      'meeting.held'::public.event_type,
      v_org_id, v_workspace_id,
      'meeting', p_meeting_id,
      jsonb_build_object(
        'meeting_id',             p_meeting_id,
        'held_at',                p_held_at,
        'proof_status',           p_proof_status,
        'attribution_campaign_id', p_attribution_campaign_id
      )
    );
  END IF;
END $$;

REVOKE EXECUTE ON FUNCTION app.confirm_meeting(uuid, timestamptz, text, uuid)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.create_task ───────────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_task(
  p_org_id          uuid,
  p_workspace_id    uuid,
  p_account_id      uuid,
  p_title           text,
  p_origin          text,
  p_owner_member_id uuid DEFAULT NULL,
  p_due_date        date DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_task_id uuid;
BEGIN
  INSERT INTO public.task
    (org_id, workspace_id, account_id, owner_member_id, title, due_date, origin)
  VALUES
    (p_org_id, p_workspace_id, p_account_id, p_owner_member_id,
     p_title, p_due_date, p_origin)
  RETURNING id INTO v_task_id;

  PERFORM app.emit(
    'task.created'::public.event_type,
    p_org_id, p_workspace_id,
    'task', v_task_id,
    jsonb_build_object(
      'task_id',    v_task_id,
      'account_id', p_account_id,
      'title',      p_title,
      'origin',     p_origin
    )
  );

  RETURN v_task_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_task(uuid, uuid, uuid, text, text, uuid, date)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.upsert_next_step ──────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.upsert_next_step(
  p_account_id    uuid,
  p_suggested_text text,
  p_mode          public.next_step_mode
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_next_step_id uuid;
BEGIN
  SELECT org_id, workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.account
   WHERE id = p_account_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account % not found', p_account_id USING ERRCODE = '22023';
  END IF;

  INSERT INTO public.next_step
    (org_id, workspace_id, account_id, suggested_text, mode)
  VALUES
    (v_org_id, v_workspace_id, p_account_id, p_suggested_text, p_mode)
  ON CONFLICT (account_id) DO UPDATE
    SET suggested_text = EXCLUDED.suggested_text,
        mode           = EXCLUDED.mode,
        updated_at     = now()
  RETURNING id INTO v_next_step_id;

  RETURN v_next_step_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.upsert_next_step(uuid, text, public.next_step_mode)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only

-- ── app.create_webhook_subscription ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_webhook_subscription(
  p_org_id          uuid,
  p_workspace_id    uuid,
  p_event_types     text[],
  p_url             text,
  p_secret_vault_key text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_sub_id uuid;
BEGIN
  INSERT INTO public.webhook_subscription
    (org_id, workspace_id, event_types, url, secret_vault_key)
  VALUES
    (p_org_id, p_workspace_id, p_event_types, p_url, p_secret_vault_key)
  RETURNING id INTO v_sub_id;

  PERFORM app.audit(
    p_org_id, p_workspace_id,
    'webhook_subscription.created', 'webhook_subscription', v_sub_id,
    NULL,
    jsonb_build_object('url', p_url, 'event_types', p_event_types)
  );

  RETURN v_sub_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_webhook_subscription(uuid, uuid, text[], text, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_webhook_subscription(uuid, uuid, text[], text, text)
  TO postgres, service_role;

-- ── app.record_webhook_delivery ───────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.record_webhook_delivery(
  p_subscription_id uuid,
  p_event_id        uuid,
  p_payload         jsonb,
  p_signature       text,
  p_status          text,
  p_attempt_count   int,
  p_delivered_at    timestamptz DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_delivery_id uuid;
BEGIN
  -- Upsert by (subscription_id, event_id); preserve original payload (never re-sign)
  INSERT INTO public.webhook_delivery
    (subscription_id, event_id, payload, signature, status,
     attempt_count, last_attempted_at, delivered_at)
  VALUES
    (p_subscription_id, p_event_id, p_payload, p_signature, p_status,
     p_attempt_count, now(), p_delivered_at)
  ON CONFLICT (subscription_id, event_id) DO UPDATE
    SET status            = EXCLUDED.status,
        attempt_count     = EXCLUDED.attempt_count,
        last_attempted_at = now(),
        delivered_at      = EXCLUDED.delivered_at
  RETURNING id INTO v_delivery_id;

  -- Update fail_count on subscription when delivery fails
  IF p_status = 'failed' THEN
    UPDATE public.webhook_subscription
       SET fail_count = fail_count + 1
     WHERE id = p_subscription_id;
  ELSIF p_status = 'delivered' THEN
    -- Reset fail_count on success
    UPDATE public.webhook_subscription
       SET fail_count = 0
     WHERE id = p_subscription_id;
  END IF;

  RETURN v_delivery_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.record_webhook_delivery(uuid, uuid, jsonb, text, text, int, timestamptz)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.record_webhook_delivery(uuid, uuid, jsonb, text, text, int, timestamptz)
  TO postgres, service_role;

-- ── app.create_api_token ──────────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_api_token(
  p_org_id       uuid,
  p_workspace_id uuid,
  p_member_id    uuid,
  p_scope        public.token_scope,
  p_name         text,
  p_token_hash   text,
  p_expires_at   timestamptz DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_token_id uuid;
BEGIN
  INSERT INTO public.api_token
    (org_id, workspace_id, member_id, scope, name, token_hash, expires_at)
  VALUES
    (p_org_id, p_workspace_id, p_member_id, p_scope, p_name, p_token_hash, p_expires_at)
  RETURNING id INTO v_token_id;

  PERFORM app.audit(
    p_org_id, p_workspace_id,
    'api_token.created', 'api_token', v_token_id,
    NULL,
    jsonb_build_object('name', p_name, 'scope', p_scope)
  );

  RETURN v_token_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_api_token(uuid, uuid, uuid, public.token_scope, text, text, timestamptz)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_api_token(uuid, uuid, uuid, public.token_scope, text, text, timestamptz)
  TO postgres, service_role;

-- ── app.record_external_write ─────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.record_external_write(
  p_org_id          uuid,
  p_workspace_id    uuid,
  p_integration_id  uuid,
  p_action_type     text,
  p_target_type     text,
  p_target_id       uuid,
  p_external_id     text,
  p_idempotency_key text,
  p_dry_run         bool,
  p_status          text,
  p_error           text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_log_id uuid;
BEGIN
  INSERT INTO public.external_write_log
    (org_id, workspace_id, integration_id, action_type, target_type, target_id,
     external_id, idempotency_key, dry_run, status, error, executed_at)
  VALUES
    (p_org_id, p_workspace_id, p_integration_id, p_action_type, p_target_type, p_target_id,
     p_external_id, p_idempotency_key, coalesce(p_dry_run, false), p_status, p_error, now())
  ON CONFLICT (idempotency_key) DO UPDATE
    SET status      = EXCLUDED.status,
        error       = EXCLUDED.error,
        executed_at = now()
  RETURNING id INTO v_log_id;

  RETURN v_log_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.record_external_write(uuid, uuid, uuid, text, text, uuid, text, text, bool, text, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.record_external_write(uuid, uuid, uuid, text, text, uuid, text, text, bool, text, text)
  TO postgres, service_role;

-- ── app.process_dsr_deletion ──────────────────────────────────────────────────
-- One transaction: delete contact_point, anonymise entity, upsert suppression_list,
-- mark data_subject_request complete.
CREATE OR REPLACE FUNCTION app.process_dsr_deletion(
  p_org_id          uuid,
  p_person_entity_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  -- 1. Delete contact points for this person in this org
  DELETE FROM public.contact_point
   WHERE org_id = p_org_id
     AND entity_id = p_person_entity_id;

  -- 2. Anonymise entity display_name and retire
  UPDATE public.entity
     SET display_name = '[deleted]',
         status       = 'retired'
   WHERE id = p_person_entity_id
     AND org_id = p_org_id;

  -- 3. Add to suppression list (idempotent)
  INSERT INTO public.suppression_list (org_id, person_entity_id, reason)
  VALUES (p_org_id, p_person_entity_id, 'dsr_deletion')
  ON CONFLICT (org_id, person_entity_id) DO NOTHING;

  -- 4. Mark any open DSR for this person as complete
  UPDATE public.data_subject_request
     SET completed_at = now()
   WHERE person_entity_id = p_person_entity_id
     AND completed_at IS NULL;
END $$;

REVOKE EXECUTE ON FUNCTION app.process_dsr_deletion(uuid, uuid)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.process_dsr_deletion(uuid, uuid)
  TO postgres, service_role;

-- ── app.compute_metric ────────────────────────────────────────────────────────
-- Looks up the latest published metric_definition for the given key,
-- then EXECUTEs its sql_body with p_workspace_id/$1, p_brief_id/$2, p_filters/$3.
CREATE OR REPLACE FUNCTION app.compute_metric(
  p_workspace_id uuid,
  p_brief_id     uuid,
  p_metric_key   text,
  p_filters      jsonb DEFAULT '{}'
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_sql_body text;
  v_result   jsonb;
BEGIN
  -- Look up the latest published version for this key
  SELECT sql_body INTO v_sql_body
    FROM public.metric_definition
   WHERE key = p_metric_key
     AND published_at IS NOT NULL
   ORDER BY version DESC
   LIMIT 1;

  IF NOT FOUND THEN
    RETURN NULL;
  END IF;

  -- Execute the stored SQL body with workspace_id/$1, brief_id/$2, filters/$3
  EXECUTE v_sql_body
    INTO v_result
    USING p_workspace_id, p_brief_id, coalesce(p_filters, '{}');

  RETURN v_result;
END $$;

REVOKE EXECUTE ON FUNCTION app.compute_metric(uuid, uuid, text, jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.compute_metric(uuid, uuid, text, jsonb)
  TO postgres, service_role, authenticated;

-- ── Kill-switch seeds ─────────────────────────────────────────────────────────
INSERT INTO public.job_type_config (job_type, enabled)
VALUES
  ('campaign.push',       true),
  ('reply.classify',      true),
  ('inbox.reconcile',     true),
  ('meeting.sync',        true),
  ('next_step.suggest',   true),
  ('webhook.deliver',     true),
  ('metric.snapshot',     true)
ON CONFLICT (job_type) DO NOTHING;

-- ── Canonical metric_definition seeds ────────────────────────────────────────
-- 15 canonical metrics; org_id = NULL = global/applies to all orgs.
-- sql_body receives: $1 = workspace_id, $2 = brief_id, $3 = filters (jsonb).
-- Each body returns a single jsonb row via SELECT to_jsonb(...).
INSERT INTO public.metric_definition (org_id, key, version, sql_body, published_at)
VALUES

-- 1. accounts_discovered
(NULL, 'accounts_discovered', 1,
 $sql$
   SELECT to_jsonb(count(*))
     FROM public.account a
    WHERE a.workspace_id = $1
      AND EXISTS (
        SELECT 1 FROM public.verdict v
          JOIN public.entity e ON e.id = v.entity_id
         WHERE e.id = a.entity_id
           AND v.brief_version_id IN (
             SELECT bv.id FROM public.brief_version bv WHERE bv.brief_id = $2
           )
      )
 $sql$, now()),

-- 2. accounts_verified
(NULL, 'accounts_verified', 1,
 $sql$
   SELECT to_jsonb(count(*))
     FROM public.account a
    WHERE a.workspace_id = $1
      AND EXISTS (
        SELECT 1 FROM public.verdict v
          JOIN public.entity e ON e.id = v.entity_id
         WHERE e.id = a.entity_id
           AND v.brief_version_id IN (
             SELECT bv.id FROM public.brief_version bv WHERE bv.brief_id = $2
           )
           AND v.value IN ('Confirmed', 'Likely')
      )
 $sql$, now()),

-- 3. accounts_researched
(NULL, 'accounts_researched', 1,
 $sql$
   SELECT to_jsonb(count(DISTINCT rd.account_id))
     FROM public.research_doc rd
    WHERE rd.workspace_id = $1
      AND rd.brief_version_id IN (
        SELECT bv.id FROM public.brief_version bv WHERE bv.brief_id = $2
      )
 $sql$, now()),

-- 4. accounts_reachable
(NULL, 'accounts_reachable', 1,
 $sql$
   SELECT to_jsonb(count(DISTINCT cp.account_id))
     FROM public.contact_point cp
     JOIN public.account a ON a.id = cp.account_id
    WHERE a.workspace_id = $1
      AND cp.status = 'valid'
 $sql$, now()),

-- 5. accounts_contacted
(NULL, 'accounts_contacted', 1,
 $sql$
   SELECT to_jsonb(count(DISTINCT act.account_id))
     FROM public.activity act
    WHERE act.workspace_id = $1
      AND act.kind = 'message_sent'
 $sql$, now()),

-- 6. people_contacted
(NULL, 'people_contacted', 1,
 $sql$
   SELECT to_jsonb(count(DISTINCT act.person_entity_id))
     FROM public.activity act
    WHERE act.workspace_id = $1
      AND act.kind = 'message_sent'
      AND act.person_entity_id IS NOT NULL
 $sql$, now()),

-- 7. messages_sent
(NULL, 'messages_sent', 1,
 $sql$
   SELECT to_jsonb(count(*))
     FROM public.activity act
    WHERE act.workspace_id = $1
      AND act.kind = 'message_sent'
 $sql$, now()),

-- 8. genuine_replies_people
(NULL, 'genuine_replies_people', 1,
 $sql$
   SELECT to_jsonb(count(DISTINCT act.person_entity_id))
     FROM public.activity act
    WHERE act.workspace_id = $1
      AND act.kind = 'reply_received'
      AND act.person_entity_id IS NOT NULL
      AND (act.payload ->> 'reply_class') IN ('Interested', 'Referral')
 $sql$, now()),

-- 9. genuine_replies_accounts
(NULL, 'genuine_replies_accounts', 1,
 $sql$
   SELECT to_jsonb(count(DISTINCT act.account_id))
     FROM public.activity act
    WHERE act.workspace_id = $1
      AND act.kind = 'reply_received'
      AND (act.payload ->> 'reply_class') IN ('Interested', 'Referral')
 $sql$, now()),

-- 10. companies_met
(NULL, 'companies_met', 1,
 $sql$
   SELECT to_jsonb(count(DISTINCT m.account_id))
     FROM public.meeting m
    WHERE m.workspace_id = $1
      AND m.proof_status IN ('confirmed', 'has_transcript')
 $sql$, now()),

-- 11. meetings_held
(NULL, 'meetings_held', 1,
 $sql$
   SELECT to_jsonb(count(*))
     FROM public.meeting m
    WHERE m.workspace_id = $1
      AND m.proof_status IN ('confirmed', 'has_transcript')
 $sql$, now()),

-- 12. reply_rate (genuine replies per 1000 messages sent)
(NULL, 'reply_rate', 1,
 $sql$
   SELECT to_jsonb(
     (SELECT count(DISTINCT act.person_entity_id)
        FROM public.activity act
       WHERE act.workspace_id = $1
         AND act.kind = 'reply_received'
         AND (act.payload ->> 'reply_class') IN ('Interested', 'Referral'))::numeric
     /
     NULLIF(
       (SELECT count(*) FROM public.activity act2
         WHERE act2.workspace_id = $1 AND act2.kind = 'message_sent')::numeric / 1000.0,
       0
     )
   )
 $sql$, now()),

-- 13. bounce_rate
(NULL, 'bounce_rate', 1,
 $sql$
   SELECT to_jsonb(
     (SELECT count(*) FROM public.activity act
       WHERE act.workspace_id = $1 AND act.kind = 'message_bounced')::numeric
     /
     NULLIF(
       (SELECT count(*) FROM public.activity act2
         WHERE act2.workspace_id = $1 AND act2.kind = 'message_sent')::numeric,
       0
     )
   )
 $sql$, now()),

-- 14. high_band_coverage (accounts_contacted / high_band_accounts)
(NULL, 'high_band_coverage', 1,
 $sql$
   SELECT to_jsonb(
     (SELECT count(DISTINCT act.account_id)
        FROM public.activity act
       WHERE act.workspace_id = $1 AND act.kind = 'message_sent')::numeric
     /
     NULLIF(
       (SELECT count(*)
          FROM public.score s
          JOIN public.account a ON a.id = s.account_id
         WHERE a.workspace_id = $1
           AND s.brief_version_id IN (
             SELECT bv.id FROM public.brief_version bv WHERE bv.brief_id = $2
           )
           AND s.band = 'High')::numeric,
       0
     )
   )
 $sql$, now()),

-- 15. stalled_accounts (Contacted > 14 days with no next_step)
(NULL, 'stalled_accounts', 1,
 $sql$
   SELECT to_jsonb(count(*))
     FROM public.account a
    WHERE a.workspace_id = $1
      AND a.stage = 'Contacted'
      AND a.updated_at < now() - interval '14 days'
      AND NOT EXISTS (
        SELECT 1 FROM public.next_step ns WHERE ns.account_id = a.id
      )
 $sql$, now())

ON CONFLICT (org_id, key, version) DO NOTHING;
