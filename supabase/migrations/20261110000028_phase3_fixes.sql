-- Phase 3 final-review fixes.
-- Fix C4: app.record_feedback SECURITY DEFINER RPC (S8 write discipline)
-- Fix C5: app.add_to_suppression_list / app.disable_webhook_subscription RPCs (S8)
-- Fix C3: add org_id filter to app.compute_metric metric_definition lookup
-- Fix C6: narrow app.forbid_mutation() escape hatch to activity UPDATE only
-- Fix I2: add org_id to meeting uniqueness constraint
-- Fix I6: add missing service_role grants for Phase 3 RPCs

-- ── Fix C6: narrow the append-only escape hatch ───────────────────────────────
-- Change back to SECURITY INVOKER (the GUC-based escape hatch works at invoker
-- level; SECURITY DEFINER was added inadvertently in migration 0024).
-- Only allows the bypass for activity UPDATE so no other append-only table is
-- affected by the reply_class patch flag.

CREATE OR REPLACE FUNCTION app.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY INVOKER SET search_path = '' AS $$
BEGIN
  -- Only allow the escape hatch for activity row UPDATE (reply_class patch).
  -- All other tables and operations remain fully append-only.
  IF TG_TABLE_NAME = 'activity'
     AND TG_OP = 'UPDATE'
     AND current_setting('app.allow_activity_patch', true) = '1' THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '42501';
END;
$$;

-- ── Fix I2: org-scoped meeting uniqueness ─────────────────────────────────────
-- The old constraint (source_integration_id, external_id) allows two tenants
-- using the same integration with the same external meeting id to collide.
-- Drop and re-create with org_id included.

ALTER TABLE public.meeting
  DROP CONSTRAINT IF EXISTS meeting_external_unique;

ALTER TABLE public.meeting
  ADD CONSTRAINT meeting_source_integration_id_external_id_org_key
  UNIQUE NULLS NOT DISTINCT (org_id, source_integration_id, external_id);

-- Update app.create_meeting to reflect the new constraint (conflict detection
-- and SELECT fallback both need org_id guard).
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
  ON CONFLICT (org_id, source_integration_id, external_id) DO NOTHING
  RETURNING id INTO v_meeting_id;

  -- If conflict (already exists), fetch existing id scoped to this org
  IF v_meeting_id IS NULL THEN
    SELECT id INTO v_meeting_id
      FROM public.meeting
     WHERE org_id = p_org_id
       AND source_integration_id IS NOT DISTINCT FROM p_source_integration_id
       AND external_id IS NOT DISTINCT FROM p_external_id;
  END IF;

  RETURN v_meeting_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_meeting(uuid, uuid, uuid, uuid, text, uuid, timestamptz)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_meeting(uuid, uuid, uuid, uuid, text, uuid, timestamptz)
  TO service_role;

-- ── Fix C4: app.record_feedback (S8 write discipline) ─────────────────────────
-- Replaces direct INSERT INTO public.feedback in m10_inbox.py.

CREATE OR REPLACE FUNCTION app.record_feedback(
  p_org_id          uuid,
  p_workspace_id    uuid,
  p_member_id       uuid,
  p_target_type     text,
  p_target_id       uuid,
  p_action          text,
  p_corrected_value jsonb DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  INSERT INTO public.feedback
    (org_id, workspace_id, member_id, target_type, target_id, action, corrected_value)
  VALUES
    (p_org_id, p_workspace_id, p_member_id, p_target_type, p_target_id, p_action, p_corrected_value);
END;
$$;

REVOKE EXECUTE ON FUNCTION app.record_feedback(uuid, uuid, uuid, text, uuid, text, jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.record_feedback(uuid, uuid, uuid, text, uuid, text, jsonb)
  TO service_role;

-- ── Fix C4: app.add_to_suppression_list (S8 write discipline) ─────────────────
-- Replaces direct INSERT INTO public.suppression_list in m10_inbox.py.

CREATE OR REPLACE FUNCTION app.add_to_suppression_list(
  p_org_id            uuid,
  p_person_entity_id  uuid,
  p_reason            text DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  INSERT INTO public.suppression_list (org_id, person_entity_id, reason)
  VALUES (p_org_id, p_person_entity_id, p_reason)
  ON CONFLICT (org_id, person_entity_id) DO NOTHING;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.add_to_suppression_list(uuid, uuid, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.add_to_suppression_list(uuid, uuid, text)
  TO service_role;

-- ── Fix C5: app.disable_webhook_subscription (S8 write discipline) ────────────
-- Replaces direct UPDATE public.webhook_subscription in m13_integrations.py.

CREATE OR REPLACE FUNCTION app.disable_webhook_subscription(
  p_subscription_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  UPDATE public.webhook_subscription
  SET enabled = false
  WHERE id = p_subscription_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.disable_webhook_subscription(uuid)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.disable_webhook_subscription(uuid)
  TO service_role;

-- ── Fix C3: add org_id scoping to app.compute_metric ─────────────────────────
-- Keep authenticated grant (plan-mandated — web pages call this via supabase.rpc).
-- Add org_id filter so a workspace cannot pull metrics for a different tenant's
-- metric_definition even if they guess the key.

CREATE OR REPLACE FUNCTION app.compute_metric(
  p_workspace_id  uuid,
  p_brief_id      uuid,
  p_metric_key    text,
  p_filters       jsonb DEFAULT '{}'
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_sql_body text;
  v_result   jsonb;
  v_org_id   uuid;
BEGIN
  -- Resolve the org for this workspace so we can scope the metric lookup
  SELECT org_id INTO v_org_id FROM public.workspace WHERE id = p_workspace_id;

  -- Look up the latest published version for this key, restricted to this org
  SELECT sql_body INTO v_sql_body
    FROM public.metric_definition
   WHERE key = p_metric_key
     AND (org_id IS NULL OR org_id = v_org_id)
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
  TO authenticated, service_role;

-- ── Fix I6: add missing service_role grants for Phase 3 RPCs ─────────────────
-- Migration 0023 had REVOKE-only for these functions; the Python worker uses
-- service_role and needs EXECUTE to call them.

GRANT EXECUTE ON FUNCTION app.create_campaign(uuid, uuid, uuid, text, public.motion, uuid)
  TO service_role;
GRANT EXECUTE ON FUNCTION app.add_campaign_member(uuid, uuid, uuid, uuid, jsonb)
  TO service_role;
GRANT EXECUTE ON FUNCTION app.check_and_pause_if_bouncing(uuid)
  TO service_role;
GRANT EXECUTE ON FUNCTION app.set_campaign_push_mode(uuid, public.push_mode)
  TO service_role;
GRANT EXECUTE ON FUNCTION app.confirm_meeting(uuid, timestamptz, text, uuid)
  TO service_role;
GRANT EXECUTE ON FUNCTION app.create_task(uuid, uuid, uuid, text, text, uuid, date)
  TO service_role;
GRANT EXECUTE ON FUNCTION app.upsert_next_step(uuid, text, public.next_step_mode)
  TO service_role;
-- Note: app.create_webhook_subscription, app.record_webhook_delivery,
--       app.create_api_token, app.record_external_write, app.process_dsr_deletion
--       already have service_role grants in migration 0023.
-- Note: app.patch_activity_reply_class already has the right grants (migration 0024).
