-- Hardening pass over Phases 1e–3. Found by scripts/check-sql.mjs (application SQL prepared
-- against the migrated schema) and by the deferred items in the phase reviews.
--   1. Privilege gates (anon-executable functions, missing service_role grant)
--   2. Stage machine: activity-keyed idempotency, repeatable dormancy
--   3. Labels: prune stale rows on recompute
--   4. Campaigns: status lifecycle, dry run before live, bounce guard that fires
--   5. Activity: one write path (dedupe, bounce counters, suppression, reply.received)
--   6. Replies: atomic classification (human mark wins)
--   7. Meetings: unmatched meetings kept, attribution tiers, held meeting → activity
--   8. Integrations and webhook subscriptions
--   9. Metrics: membership-checked public entry point, Brief-scoped definitions (v2)
--  10. API tokens
--  11. Interface entry points (public.*) for campaigns, replies, meetings and tasks

-- ── 1. Privilege gates ────────────────────────────────────────────────────────
REVOKE EXECUTE ON FUNCTION app.metric_definition_immutable() FROM public, anon, authenticated;
REVOKE EXECUTE ON FUNCTION app.update_brief_draft(uuid, jsonb) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.update_brief_draft(uuid, jsonb) TO postgres, service_role;
GRANT EXECUTE ON FUNCTION app.patch_activity_reply_class(uuid, public.reply_class, text) TO service_role;

-- ── 2. Stage machine ──────────────────────────────────────────────────────────
-- The old index gave every account a single slot for events without an activity, so a second
-- dormancy (or any second activity-less transition) was silently dropped.
DROP INDEX public.account_stage_event_idempotency;
CREATE UNIQUE INDEX account_stage_event_activity_uq
  ON public.account_stage_event (account_id, activity_id) WHERE activity_id IS NOT NULL;

CREATE OR REPLACE FUNCTION app.upsert_account_stage(
  p_account_id  uuid,
  p_to_stage    public.stage,
  p_activity_id uuid,
  p_occurred_at timestamptz
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_from_stage   public.stage;
  v_event_id     uuid;
BEGIN
  SELECT a.org_id, a.workspace_id, a.stage
    INTO v_org_id, v_workspace_id, v_from_stage
    FROM public.account a
   WHERE a.id = p_account_id
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account % not found', p_account_id USING ERRCODE = '22023';
  END IF;

  -- A replayed activity returns the event it already produced.
  IF p_activity_id IS NOT NULL THEN
    SELECT id INTO v_event_id
      FROM public.account_stage_event
     WHERE account_id = p_account_id AND activity_id = p_activity_id;
    IF FOUND THEN
      RETURN v_event_id;
    END IF;
  END IF;

  IF v_from_stage = p_to_stage THEN
    RETURN NULL;
  END IF;

  PERFORM set_config('app.writer', 'derivation', true);

  INSERT INTO public.account_stage_event
    (org_id, workspace_id, account_id, from_stage, to_stage, activity_id, occurred_at)
  VALUES
    (v_org_id, v_workspace_id, p_account_id, v_from_stage, p_to_stage, p_activity_id,
     coalesce(p_occurred_at, now()))
  RETURNING id INTO v_event_id;

  UPDATE public.account
     SET stage = p_to_stage, updated_at = now()
   WHERE id = p_account_id;

  PERFORM app.emit(
    'account.stage_changed'::public.event_type,
    v_org_id, v_workspace_id, 'account', p_account_id,
    jsonb_build_object(
      'event_id',    v_event_id,
      'account_id',  p_account_id,
      'from_stage',  v_from_stage,
      'to_stage',    p_to_stage,
      'activity_id', p_activity_id
    )
  );

  RETURN v_event_id;
END $$;

-- ── 3. Labels ─────────────────────────────────────────────────────────────────
-- Labels are recomputed with the score; rows the new computation no longer produces are removed.
CREATE FUNCTION app.prune_labels(
  p_account_id       uuid,
  p_brief_version_id uuid,
  p_kinds            text[],
  p_values           text[]
) RETURNS int
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_removed int;
BEGIN
  IF coalesce(cardinality(p_kinds), 0) <> coalesce(cardinality(p_values), 0) THEN
    RAISE EXCEPTION 'p_kinds and p_values must have the same length' USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.writer', 'derivation', true);

  DELETE FROM public.label l
   WHERE l.account_id = p_account_id
     AND l.brief_version_id = p_brief_version_id
     AND NOT EXISTS (
       SELECT 1
         FROM unnest(coalesce(p_kinds, '{}'::text[]), coalesce(p_values, '{}'::text[])) AS k(kind, value)
        WHERE k.kind = l.kind AND k.value = l.value
     );
  GET DIAGNOSTICS v_removed = ROW_COUNT;
  RETURN v_removed;
END $$;

REVOKE EXECUTE ON FUNCTION app.prune_labels(uuid, uuid, text[], text[]) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.prune_labels(uuid, uuid, text[], text[]) TO postgres, service_role;

-- ── 4. Campaigns ──────────────────────────────────────────────────────────────
-- draft → active (first dry run or live) ⇄ paused → archived. Pushes run only while active.
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
   WHERE id = p_campaign_id
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = '22023';
  END IF;

  -- Every new destination starts in dry run (M9, M13).
  IF p_push_mode = 'live' AND v_old_mode <> 'dry run' THEN
    RAISE EXCEPTION 'a campaign must run in dry run before it goes live' USING ERRCODE = '22023';
  END IF;

  UPDATE public.campaign
     SET push_mode = p_push_mode,
         status    = CASE WHEN p_push_mode <> 'off' AND status = 'draft' THEN 'active' ELSE status END
   WHERE id = p_campaign_id;

  PERFORM app.audit(
    v_org_id, v_workspace_id,
    'campaign.push_mode_changed', 'campaign', p_campaign_id,
    jsonb_build_object('push_mode', v_old_mode),
    jsonb_build_object('push_mode', p_push_mode)
  );
END $$;

CREATE FUNCTION app.set_campaign_status(
  p_campaign_id uuid,
  p_status      text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_old_status   text;
BEGIN
  IF p_status NOT IN ('active', 'paused', 'archived') THEN
    RAISE EXCEPTION 'invalid campaign status %', p_status USING ERRCODE = '22023';
  END IF;

  SELECT org_id, workspace_id, status
    INTO v_org_id, v_workspace_id, v_old_status
    FROM public.campaign
   WHERE id = p_campaign_id
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = '22023';
  END IF;
  IF v_old_status = 'archived' THEN
    RAISE EXCEPTION 'an archived campaign cannot be changed' USING ERRCODE = '22023';
  END IF;
  IF v_old_status = p_status THEN
    RETURN;
  END IF;

  UPDATE public.campaign SET status = p_status WHERE id = p_campaign_id;

  PERFORM app.audit(
    v_org_id, v_workspace_id,
    'campaign.status_changed', 'campaign', p_campaign_id,
    jsonb_build_object('status', v_old_status),
    jsonb_build_object('status', p_status)
  );

  IF p_status = 'paused' THEN
    PERFORM app.emit(
      'campaign.paused'::public.event_type,
      v_org_id, v_workspace_id, 'campaign', p_campaign_id,
      jsonb_build_object('campaign_id', p_campaign_id, 'reason', 'manual')
    );
  END IF;
END $$;

REVOKE EXECUTE ON FUNCTION app.set_campaign_status(uuid, text) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.set_campaign_status(uuid, text) TO postgres, service_role;

-- Members are added only to an active, live campaign; the row records when and in which mode.
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
  v_status       text;
  v_push_mode    public.push_mode;
  v_member_id    uuid;
BEGIN
  SELECT org_id, workspace_id, status, push_mode
    INTO v_org_id, v_workspace_id, v_status, v_push_mode
    FROM public.campaign
   WHERE id = p_campaign_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = '22023';
  END IF;
  IF v_status <> 'active' OR v_push_mode <> 'live' THEN
    RAISE EXCEPTION 'campaign % is not live (status %, push mode %)', p_campaign_id, v_status, v_push_mode
      USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.account a
     WHERE a.id = p_account_id AND a.workspace_id = v_workspace_id
  ) THEN
    RAISE EXCEPTION 'account % is not in the campaign workspace', p_account_id USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.campaign_member
    (org_id, workspace_id, campaign_id, account_id, person_entity_id,
     contact_point_id, variables, pushed_at, push_mode_at_send)
  VALUES
    (v_org_id, v_workspace_id, p_campaign_id, p_account_id, p_person_entity_id,
     p_contact_point_id, coalesce(p_variables, '{}'), now(), v_push_mode)
  ON CONFLICT (campaign_id, person_entity_id) DO NOTHING
  RETURNING id INTO v_member_id;

  IF v_member_id IS NULL THEN
    SELECT id INTO v_member_id
      FROM public.campaign_member
     WHERE campaign_id = p_campaign_id
       AND person_entity_id = p_person_entity_id;
    RETURN v_member_id;
  END IF;

  PERFORM app.emit(
    'campaign.member_added'::public.event_type,
    v_org_id, v_workspace_id, 'campaign_member', v_member_id,
    jsonb_build_object(
      'campaign_id',      p_campaign_id,
      'account_id',       p_account_id,
      'person_entity_id', p_person_entity_id,
      'contact_point_id', p_contact_point_id,
      'member_id',        v_member_id,
      'variables',        coalesce(p_variables, '{}')
    )
  );

  RETURN v_member_id;
END $$;

-- Bounce guard with the workspace's thresholds: warn once at bounce_warn_pct, pause at bounce_pause_pct.
CREATE OR REPLACE FUNCTION app.check_and_pause_if_bouncing(
  p_campaign_id uuid
) RETURNS bool
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_status       text;
  v_bounced      int;
  v_sent         int;
  v_rate_pct     numeric;
  v_warn_pct     numeric;
  v_pause_pct    numeric;
BEGIN
  SELECT org_id, workspace_id, status, bounce_count, bounce_messages_sent
    INTO v_org_id, v_workspace_id, v_status, v_bounced, v_sent
    FROM public.campaign
   WHERE id = p_campaign_id
     FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'campaign % not found', p_campaign_id USING ERRCODE = '22023';
  END IF;
  IF v_status = 'paused' THEN
    RETURN true;
  END IF;

  SELECT ws.bounce_warn_pct, ws.bounce_pause_pct
    INTO v_warn_pct, v_pause_pct
    FROM public.workspace_settings ws
   WHERE ws.workspace_id = v_workspace_id;

  v_rate_pct := v_bounced::numeric * 100 / NULLIF(v_sent, 0);
  IF v_rate_pct IS NULL THEN
    RETURN false;
  END IF;

  IF v_rate_pct >= coalesce(v_pause_pct, 10) THEN
    UPDATE public.campaign SET status = 'paused' WHERE id = p_campaign_id;
    PERFORM app.emit(
      'campaign.paused'::public.event_type,
      v_org_id, v_workspace_id, 'campaign', p_campaign_id,
      jsonb_build_object(
        'campaign_id',          p_campaign_id,
        'reason',               'bounce_guard',
        'bounce_count',         v_bounced,
        'bounce_messages_sent', v_sent,
        'bounce_rate_pct',      round(v_rate_pct, 2)
      )
    );
    RETURN true;
  END IF;

  IF v_rate_pct >= coalesce(v_warn_pct, 5) AND NOT EXISTS (
    SELECT 1 FROM public.audit_log al
     WHERE al.target_id = p_campaign_id AND al.action = 'campaign.bounce_warning'
  ) THEN
    PERFORM app.audit(
      v_org_id, v_workspace_id,
      'campaign.bounce_warning', 'campaign', p_campaign_id,
      NULL,
      jsonb_build_object('bounce_rate_pct', round(v_rate_pct, 2), 'bounce_count', v_bounced,
                         'bounce_messages_sent', v_sent)
    );
  END IF;

  RETURN false;
END $$;

-- ── 5. Activity write path ────────────────────────────────────────────────────
DROP FUNCTION app.create_activity(uuid, uuid, uuid, text, timestamptz, uuid, text);

CREATE FUNCTION app.create_activity(
  p_org_id           uuid,
  p_workspace_id     uuid,
  p_account_id       uuid,
  p_kind             text,
  p_occurred_at      timestamptz DEFAULT now(),
  p_integration_id   uuid        DEFAULT NULL,
  p_external_id      text        DEFAULT NULL,
  p_person_entity_id uuid        DEFAULT NULL,
  p_campaign_id      uuid        DEFAULT NULL,
  p_payload          jsonb       DEFAULT '{}',
  p_channel          text        DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_id               uuid;
  v_brief_version_id uuid;
BEGIN
  IF p_account_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.account a
     WHERE a.id = p_account_id AND a.org_id = p_org_id AND a.workspace_id = p_workspace_id
  ) THEN
    RAISE EXCEPTION 'account % is not in workspace %', p_account_id, p_workspace_id USING ERRCODE = '42501';
  END IF;

  IF p_campaign_id IS NOT NULL THEN
    -- Part 8.1: the outcome carries the Brief version active when it happened.
    SELECT b.current_version_id INTO v_brief_version_id
      FROM public.campaign c
      LEFT JOIN public.brief b ON b.id = c.brief_id
     WHERE c.id = p_campaign_id AND c.org_id = p_org_id AND c.workspace_id = p_workspace_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'campaign % is not in workspace %', p_campaign_id, p_workspace_id USING ERRCODE = '42501';
    END IF;
  END IF;

  INSERT INTO public.activity (
    org_id, workspace_id, account_id, person_entity_id, kind, channel, campaign_id,
    integration_id, external_id, brief_version_id, occurred_at, payload
  ) VALUES (
    p_org_id, p_workspace_id, p_account_id, p_person_entity_id, p_kind::public.activity_kind,
    p_channel, p_campaign_id, p_integration_id, p_external_id, v_brief_version_id,
    coalesce(p_occurred_at, now()), coalesce(p_payload, '{}')
  )
  ON CONFLICT (integration_id, external_id) DO NOTHING
  RETURNING id INTO v_id;

  -- A replayed external event is recorded once (F6): return the first copy, no side effects.
  IF v_id IS NULL THEN
    SELECT id INTO v_id
      FROM public.activity
     WHERE integration_id = p_integration_id AND external_id = p_external_id;
    RETURN v_id;
  END IF;

  IF p_kind = 'message_sent' AND p_campaign_id IS NOT NULL THEN
    UPDATE public.campaign
       SET bounce_messages_sent = bounce_messages_sent + 1
     WHERE id = p_campaign_id;
  ELSIF p_kind = 'message_bounced' AND p_campaign_id IS NOT NULL THEN
    UPDATE public.campaign
       SET bounce_count = bounce_count + 1
     WHERE id = p_campaign_id;
    PERFORM app.check_and_pause_if_bouncing(p_campaign_id);
  ELSIF p_kind = 'unsubscribed' AND p_person_entity_id IS NOT NULL THEN
    PERFORM app.add_to_suppression_list(p_org_id, p_person_entity_id, 'unsubscribe');
  ELSIF p_kind = 'reply_received' THEN
    PERFORM app.emit(
      'reply.received'::public.event_type,
      p_org_id, p_workspace_id, 'activity', v_id,
      jsonb_build_object(
        'activity_id',      v_id,
        'account_id',       p_account_id,
        'person_entity_id', p_person_entity_id,
        'campaign_id',      p_campaign_id
      )
    );
  END IF;

  RETURN v_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_activity(uuid, uuid, uuid, text, timestamptz, uuid, text, uuid, uuid, jsonb, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_activity(uuid, uuid, uuid, text, timestamptz, uuid, text, uuid, uuid, jsonb, text)
  TO postgres, service_role;

-- ── 6. Reply classification ───────────────────────────────────────────────────
-- One transaction: human-wins check, payload patch, feedback, suppression, reply.classified.
CREATE FUNCTION app.classify_reply(
  p_activity_id uuid,
  p_reply_class public.reply_class,
  p_source      text,
  p_member_id   uuid DEFAULT NULL
) RETURNS boolean
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  a public.activity%ROWTYPE;
BEGIN
  IF p_source NOT IN ('human', 'ai', 'sequencer') THEN
    RAISE EXCEPTION 'invalid classification source %', p_source USING ERRCODE = '22023';
  END IF;

  SELECT * INTO a FROM public.activity WHERE id = p_activity_id FOR UPDATE;
  IF NOT FOUND OR a.kind <> 'reply_received' THEN
    RAISE EXCEPTION 'activity % is not a reply', p_activity_id USING ERRCODE = '22023';
  END IF;

  -- The latest human mark is the truth; automatic classification never overrides it (M10).
  IF a.payload ->> 'reply_class_source' = 'human' AND p_source <> 'human' THEN
    RETURN false;
  END IF;
  IF p_source = 'human' AND p_member_id IS NULL THEN
    RAISE EXCEPTION 'a human classification needs a member' USING ERRCODE = '22023';
  END IF;

  PERFORM set_config('app.allow_activity_patch', '1', true);
  UPDATE public.activity
     SET payload = coalesce(payload, '{}')
                   || jsonb_build_object('reply_class', p_reply_class::text, 'reply_class_source', p_source)
   WHERE id = p_activity_id;
  PERFORM set_config('app.allow_activity_patch', '0', true);

  IF p_source = 'human' THEN
    INSERT INTO public.feedback
      (org_id, workspace_id, member_id, target_type, target_id, action, corrected_value)
    VALUES
      (a.org_id, a.workspace_id, p_member_id, 'reply_class', p_activity_id, 'correct',
       to_jsonb(p_reply_class::text));
  END IF;

  IF p_reply_class = 'Unsubscribe' AND a.person_entity_id IS NOT NULL THEN
    PERFORM app.add_to_suppression_list(a.org_id, a.person_entity_id, 'unsubscribe');
  END IF;

  PERFORM app.emit(
    'reply.classified'::public.event_type,
    a.org_id, a.workspace_id, 'activity', p_activity_id,
    jsonb_build_object(
      'activity_id',        p_activity_id,
      'account_id',         a.account_id,
      'person_entity_id',   a.person_entity_id,
      'reply_class',        p_reply_class::text,
      'reply_class_source', p_source
    )
  );
  RETURN true;
END $$;

REVOKE EXECUTE ON FUNCTION app.classify_reply(uuid, public.reply_class, text, uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.classify_reply(uuid, public.reply_class, text, uuid) TO postgres, service_role;

-- ── 7. Meetings ───────────────────────────────────────────────────────────────
-- Unmatched meetings are kept for review, never dropped (M12).
ALTER TABLE public.meeting ALTER COLUMN account_id DROP NOT NULL;

-- Outbound attribution: a campaign touch on the same person, then the same account, inside the
-- workspace's attribution window. A touch is a sent message or a push into a live campaign.
CREATE FUNCTION app.meeting_attribution(
  p_meeting_id uuid,
  p_held_at    timestamptz,
  OUT campaign_id uuid,
  OUT tier        text
)
LANGUAGE plpgsql STABLE SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_workspace_id uuid;
  v_account_id   uuid;
  v_person_id    uuid;
  v_from         timestamptz;
BEGIN
  SELECT m.workspace_id, m.account_id, m.person_entity_id,
         p_held_at - make_interval(days => ws.attribution_window_days)
    INTO v_workspace_id, v_account_id, v_person_id, v_from
    FROM public.meeting m
    JOIN public.workspace_settings ws ON ws.workspace_id = m.workspace_id
   WHERE m.id = p_meeting_id;
  IF NOT FOUND THEN
    RETURN;
  END IF;

  IF v_person_id IS NOT NULL THEN
    SELECT t.campaign_id INTO campaign_id
      FROM (
        SELECT act.campaign_id, act.occurred_at AS touched_at
          FROM public.activity act
         WHERE act.workspace_id = v_workspace_id AND act.kind = 'message_sent'
           AND act.campaign_id IS NOT NULL AND act.person_entity_id = v_person_id
        UNION ALL
        SELECT cm.campaign_id, cm.pushed_at
          FROM public.campaign_member cm
         WHERE cm.workspace_id = v_workspace_id AND cm.person_entity_id = v_person_id
           AND cm.pushed_at IS NOT NULL
      ) t
     WHERE t.touched_at BETWEEN v_from AND p_held_at
     ORDER BY t.touched_at DESC
     LIMIT 1;
    IF campaign_id IS NOT NULL THEN
      tier := 'person';
      RETURN;
    END IF;
  END IF;

  IF v_account_id IS NOT NULL THEN
    SELECT t.campaign_id INTO campaign_id
      FROM (
        SELECT act.campaign_id, act.occurred_at AS touched_at
          FROM public.activity act
         WHERE act.workspace_id = v_workspace_id AND act.kind = 'message_sent'
           AND act.campaign_id IS NOT NULL AND act.account_id = v_account_id
        UNION ALL
        SELECT cm.campaign_id, cm.pushed_at
          FROM public.campaign_member cm
         WHERE cm.workspace_id = v_workspace_id AND cm.account_id = v_account_id
           AND cm.pushed_at IS NOT NULL
      ) t
     WHERE t.touched_at BETWEEN v_from AND p_held_at
     ORDER BY t.touched_at DESC
     LIMIT 1;
    IF campaign_id IS NOT NULL THEN
      tier := 'account';
    END IF;
  END IF;
END $$;

REVOKE EXECUTE ON FUNCTION app.meeting_attribution(uuid, timestamptz) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.meeting_attribution(uuid, timestamptz) TO postgres, service_role;

-- A meeting counts as held only with proof (M12). The first proof logs a meeting_held activity,
-- which is what moves the stage (M11), and emits meeting.held.
DROP FUNCTION app.confirm_meeting(uuid, timestamptz, text, uuid);

CREATE FUNCTION app.confirm_meeting(
  p_meeting_id              uuid,
  p_held_at                 timestamptz,
  p_proof_status            text,
  p_attribution_campaign_id uuid DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  m             public.meeting%ROWTYPE;
  v_campaign_id uuid := p_attribution_campaign_id;
  v_tier        text;
  v_activity_id uuid;
BEGIN
  IF p_proof_status NOT IN ('confirmed', 'has_transcript') THEN
    RAISE EXCEPTION 'proof status % does not prove a held meeting', p_proof_status USING ERRCODE = '22023';
  END IF;

  SELECT * INTO m FROM public.meeting WHERE id = p_meeting_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'meeting % not found', p_meeting_id USING ERRCODE = '22023';
  END IF;

  IF v_campaign_id IS NULL THEN
    SELECT att.campaign_id, att.tier INTO v_campaign_id, v_tier
      FROM app.meeting_attribution(p_meeting_id, p_held_at) att;
  ELSE
    v_tier := 'manual';
  END IF;

  UPDATE public.meeting
     SET held_at                 = p_held_at,
         proof_status            = p_proof_status,
         attribution_campaign_id = coalesce(v_campaign_id, attribution_campaign_id),
         attribution_tier        = coalesce(v_tier, attribution_tier)
   WHERE id = p_meeting_id;

  -- Already proven once: the outcome was logged then.
  IF m.proof_status <> 'unconfirmed' THEN
    RETURN NULL;
  END IF;

  IF m.account_id IS NOT NULL THEN
    v_activity_id := app.create_activity(
      m.org_id, m.workspace_id, m.account_id, 'meeting_held', p_held_at,
      NULL, NULL, m.person_entity_id, coalesce(v_campaign_id, m.attribution_campaign_id),
      jsonb_build_object('meeting_id', p_meeting_id, 'proof_status', p_proof_status,
                         'attribution_tier', v_tier),
      'meeting'
    );
  END IF;

  PERFORM app.emit(
    'meeting.held'::public.event_type,
    m.org_id, m.workspace_id, 'meeting', p_meeting_id,
    jsonb_build_object(
      'meeting_id',              p_meeting_id,
      'account_id',              m.account_id,
      'activity_id',             v_activity_id,
      'held_at',                 p_held_at,
      'proof_status',            p_proof_status,
      'attribution_campaign_id', coalesce(v_campaign_id, m.attribution_campaign_id),
      'attribution_tier',        v_tier
    )
  );

  RETURN v_activity_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.confirm_meeting(uuid, timestamptz, text, uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.confirm_meeting(uuid, timestamptz, text, uuid) TO postgres, service_role;

-- A human can match an unmatched meeting to an account from the review list.
CREATE FUNCTION app.match_meeting(
  p_meeting_id uuid,
  p_account_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
BEGIN
  SELECT org_id, workspace_id INTO v_org_id, v_workspace_id
    FROM public.meeting WHERE id = p_meeting_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'meeting % not found', p_meeting_id USING ERRCODE = '22023';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM public.account a WHERE a.id = p_account_id AND a.workspace_id = v_workspace_id
  ) THEN
    RAISE EXCEPTION 'account % is not in the meeting workspace', p_account_id USING ERRCODE = '42501';
  END IF;

  UPDATE public.meeting SET account_id = p_account_id WHERE id = p_meeting_id;
  PERFORM app.audit(v_org_id, v_workspace_id, 'meeting.matched', 'meeting', p_meeting_id,
                    NULL, jsonb_build_object('account_id', p_account_id));
END $$;

REVOKE EXECUTE ON FUNCTION app.match_meeting(uuid, uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.match_meeting(uuid, uuid) TO postgres, service_role;

CREATE FUNCTION app.complete_task(p_task_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  t public.task%ROWTYPE;
BEGIN
  SELECT * INTO t FROM public.task WHERE id = p_task_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'task % not found', p_task_id USING ERRCODE = '22023';
  END IF;
  IF t.status = 'done' THEN
    RETURN;
  END IF;

  UPDATE public.task SET status = 'done' WHERE id = p_task_id;
  PERFORM app.create_activity(
    t.org_id, t.workspace_id, t.account_id, 'task_completed', now(),
    NULL, NULL, NULL, NULL, jsonb_build_object('task_id', p_task_id, 'title', t.title), 'task'
  );
END $$;

REVOKE EXECUTE ON FUNCTION app.complete_task(uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.complete_task(uuid) TO postgres, service_role;

-- ── 8. Integrations and webhook subscriptions ─────────────────────────────────
-- New connections default to Read; the stored credential is ciphertext (signal_core.secrets).
CREATE FUNCTION app.create_integration(
  p_org_id                uuid,
  p_workspace_id          uuid,
  p_name                  text,
  p_category              text,
  p_access_mode           public.access_mode DEFAULT 'Read',
  p_credentials_vault_key text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_id uuid;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.workspace w WHERE w.id = p_workspace_id AND w.org_id = p_org_id
  ) THEN
    RAISE EXCEPTION 'workspace % is not in organisation %', p_workspace_id, p_org_id USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.integration
    (org_id, workspace_id, name, category, access_mode, credentials_vault_key)
  VALUES
    (p_org_id, p_workspace_id, p_name, p_category, coalesce(p_access_mode, 'Read'), p_credentials_vault_key)
  RETURNING id INTO v_id;

  PERFORM app.audit(
    p_org_id, p_workspace_id, 'integration.created', 'integration', v_id, NULL,
    jsonb_build_object('name', p_name, 'category', p_category,
                       'access_mode', coalesce(p_access_mode, 'Read'))
  );
  RETURN v_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_integration(uuid, uuid, text, text, public.access_mode, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_integration(uuid, uuid, text, text, public.access_mode, text)
  TO postgres, service_role;

-- Moving to a write-capable mode is an explicit, audited change (M13).
CREATE FUNCTION app.set_integration_access_mode(
  p_integration_id uuid,
  p_access_mode    public.access_mode
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_old          public.access_mode;
BEGIN
  SELECT org_id, workspace_id, access_mode INTO v_org_id, v_workspace_id, v_old
    FROM public.integration WHERE id = p_integration_id FOR UPDATE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'integration % not found', p_integration_id USING ERRCODE = '22023';
  END IF;

  UPDATE public.integration SET access_mode = p_access_mode WHERE id = p_integration_id;
  PERFORM app.audit(
    v_org_id, v_workspace_id, 'integration.access_mode_changed', 'integration', p_integration_id,
    jsonb_build_object('access_mode', v_old), jsonb_build_object('access_mode', p_access_mode)
  );
END $$;

REVOKE EXECUTE ON FUNCTION app.set_integration_access_mode(uuid, public.access_mode) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.set_integration_access_mode(uuid, public.access_mode) TO postgres, service_role;

-- A subscription that fails repeatedly is disabled and the change is audited (M13).
CREATE OR REPLACE FUNCTION app.disable_webhook_subscription(
  p_subscription_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
BEGIN
  UPDATE public.webhook_subscription
     SET enabled = false
   WHERE id = p_subscription_id AND enabled
  RETURNING org_id, workspace_id INTO v_org_id, v_workspace_id;
  IF FOUND THEN
    PERFORM app.audit(v_org_id, v_workspace_id, 'webhook_subscription.disabled',
                      'webhook_subscription', p_subscription_id, NULL,
                      jsonb_build_object('reason', 'repeated delivery failures'));
  END IF;
END $$;

-- ── 9. Metrics ────────────────────────────────────────────────────────────────
-- app.compute_metric runs stored SQL as its owner, so clients reach it only through a wrapper
-- that checks workspace membership first.
REVOKE EXECUTE ON FUNCTION app.compute_metric(uuid, uuid, text, jsonb) FROM public, anon, authenticated;

CREATE FUNCTION public.compute_metric(
  p_workspace_id uuid,
  p_brief_id     uuid,
  p_metric_key   text,
  p_filters      jsonb DEFAULT '{}'
) RETURNS jsonb
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF NOT app.has_workspace_role(p_workspace_id, 'Viewer') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  IF p_brief_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM public.brief b WHERE b.id = p_brief_id AND b.workspace_id = p_workspace_id
  ) THEN
    RAISE EXCEPTION 'brief % is not in workspace %', p_brief_id, p_workspace_id USING ERRCODE = '42501';
  END IF;
  RETURN app.compute_metric(p_workspace_id, p_brief_id, p_metric_key, p_filters);
END $$;

REVOKE EXECUTE ON FUNCTION public.compute_metric(uuid, uuid, text, jsonb) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.compute_metric(uuid, uuid, text, jsonb) TO authenticated;

-- Version 2 of the outreach metrics: counted only from campaigns linked to the Brief (M16
-- "outbound-only counts"), genuine replies use each person's latest classified reply, and
-- companies met counts attributed, proven meetings. Version 1 rows stay for reproducibility.
INSERT INTO public.metric_definition (org_id, key, version, sql_body, published_at)
VALUES

(NULL, 'accounts_reachable', 2,
 $sql$
   SELECT to_jsonb(count(DISTINCT cp.account_id))
     FROM public.contact_point cp
     JOIN public.account a ON a.id = cp.account_id
    WHERE a.workspace_id = $1
      AND cp.status = 'valid'
      AND EXISTS (
        SELECT 1 FROM public.score s
         WHERE s.account_id = a.id
           AND s.brief_version_id IN (SELECT bv.id FROM public.brief_version bv WHERE bv.brief_id = $2)
      )
 $sql$, now()),

(NULL, 'accounts_contacted', 2,
 $sql$
   SELECT to_jsonb(count(DISTINCT act.account_id))
     FROM public.activity act
     JOIN public.campaign c ON c.id = act.campaign_id
    WHERE act.workspace_id = $1 AND c.brief_id = $2 AND act.kind = 'message_sent'
 $sql$, now()),

(NULL, 'people_contacted', 2,
 $sql$
   SELECT to_jsonb(count(DISTINCT act.person_entity_id))
     FROM public.activity act
     JOIN public.campaign c ON c.id = act.campaign_id
    WHERE act.workspace_id = $1 AND c.brief_id = $2 AND act.kind = 'message_sent'
      AND act.person_entity_id IS NOT NULL
 $sql$, now()),

(NULL, 'messages_sent', 2,
 $sql$
   SELECT to_jsonb(count(*))
     FROM public.activity act
     JOIN public.campaign c ON c.id = act.campaign_id
    WHERE act.workspace_id = $1 AND c.brief_id = $2 AND act.kind = 'message_sent'
 $sql$, now()),

(NULL, 'genuine_replies_people', 2,
 $sql$
   SELECT to_jsonb(count(*))
     FROM (
       SELECT DISTINCT ON (act.person_entity_id) act.payload ->> 'reply_class' AS reply_class
         FROM public.activity act
         JOIN public.campaign c ON c.id = act.campaign_id
        WHERE act.workspace_id = $1 AND c.brief_id = $2 AND act.kind = 'reply_received'
          AND act.person_entity_id IS NOT NULL
          AND act.payload ? 'reply_class'
        ORDER BY act.person_entity_id, act.occurred_at DESC
     ) latest
    WHERE latest.reply_class IN ('Interested', 'Referral')
 $sql$, now()),

(NULL, 'genuine_replies_accounts', 2,
 $sql$
   SELECT to_jsonb(count(DISTINCT act.account_id))
     FROM public.activity act
     JOIN public.campaign c ON c.id = act.campaign_id
    WHERE act.workspace_id = $1 AND c.brief_id = $2 AND act.kind = 'reply_received'
      AND (act.payload ->> 'reply_class') IN ('Interested', 'Referral')
 $sql$, now()),

(NULL, 'companies_met', 2,
 $sql$
   SELECT to_jsonb(count(DISTINCT m.account_id))
     FROM public.meeting m
     JOIN public.campaign c ON c.id = m.attribution_campaign_id
    WHERE m.workspace_id = $1 AND c.brief_id = $2
      AND m.proof_status IN ('confirmed', 'has_transcript')
 $sql$, now()),

(NULL, 'meetings_held', 2,
 $sql$
   SELECT to_jsonb(count(*))
     FROM public.meeting m
     JOIN public.campaign c ON c.id = m.attribution_campaign_id
    WHERE m.workspace_id = $1 AND c.brief_id = $2
      AND m.proof_status IN ('confirmed', 'has_transcript')
 $sql$, now()),

(NULL, 'reply_rate', 2,
 $sql$
   SELECT to_jsonb(round(
     (SELECT count(*)
        FROM (
          SELECT DISTINCT ON (act.person_entity_id) act.payload ->> 'reply_class' AS reply_class
            FROM public.activity act
            JOIN public.campaign c ON c.id = act.campaign_id
           WHERE act.workspace_id = $1 AND c.brief_id = $2 AND act.kind = 'reply_received'
             AND act.person_entity_id IS NOT NULL
             AND act.payload ? 'reply_class'
           ORDER BY act.person_entity_id, act.occurred_at DESC
        ) latest
       WHERE latest.reply_class IN ('Interested', 'Referral'))::numeric * 1000
     /
     NULLIF((SELECT count(*)
               FROM public.activity act2
               JOIN public.campaign c2 ON c2.id = act2.campaign_id
              WHERE act2.workspace_id = $1 AND c2.brief_id = $2 AND act2.kind = 'message_sent'), 0),
     2))
 $sql$, now()),

(NULL, 'bounce_rate', 2,
 $sql$
   SELECT to_jsonb(round(
     (SELECT count(*)
        FROM public.activity act
        JOIN public.campaign c ON c.id = act.campaign_id
       WHERE act.workspace_id = $1 AND c.brief_id = $2 AND act.kind = 'message_bounced')::numeric
     /
     NULLIF((SELECT count(*)
               FROM public.activity act2
               JOIN public.campaign c2 ON c2.id = act2.campaign_id
              WHERE act2.workspace_id = $1 AND c2.brief_id = $2 AND act2.kind = 'message_sent'), 0),
     4))
 $sql$, now()),

(NULL, 'high_band_coverage', 2,
 $sql$
   SELECT to_jsonb(round(
     (SELECT count(DISTINCT s.account_id)
        FROM public.score s
        JOIN public.brief b ON b.current_version_id = s.brief_version_id
       WHERE s.workspace_id = $1 AND b.id = $2 AND s.band = 'High'
         AND EXISTS (
           SELECT 1
             FROM public.activity act
             JOIN public.campaign c ON c.id = act.campaign_id
            WHERE act.account_id = s.account_id AND c.brief_id = $2 AND act.kind = 'message_sent'
         ))::numeric
     /
     NULLIF((SELECT count(DISTINCT s2.account_id)
               FROM public.score s2
               JOIN public.brief b2 ON b2.current_version_id = s2.brief_version_id
              WHERE s2.workspace_id = $1 AND b2.id = $2 AND s2.band = 'High'), 0),
     4))
 $sql$, now())

ON CONFLICT (org_id, key, version) DO NOTHING;

-- ── 10. API tokens ────────────────────────────────────────────────────────────
-- The token is generated here, shown once and stored only as a SHA-256 hash.
-- Owner/Admin may create any scope; an Editor may create Read tokens (M14 matrix).
CREATE FUNCTION public.create_api_token(
  p_workspace_id uuid,
  p_name         text,
  p_scope        public.token_scope DEFAULT 'Read',
  p_expires_at   timestamptz DEFAULT NULL
) RETURNS text
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id uuid;
  v_token  text;
BEGIN
  SELECT w.org_id INTO v_org_id FROM public.workspace w WHERE w.id = p_workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  IF NOT (app.has_org_role(v_org_id, 'Admin')
          OR (p_scope = 'Read' AND app.has_workspace_role(p_workspace_id, 'Editor'))) THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  IF length(btrim(coalesce(p_name, ''))) = 0 THEN
    RAISE EXCEPTION 'a token needs a name' USING ERRCODE = '22023';
  END IF;
  IF p_expires_at IS NOT NULL AND p_expires_at <= now() THEN
    RAISE EXCEPTION 'expiry must be in the future' USING ERRCODE = '22023';
  END IF;

  v_token := 'sig_' || encode(extensions.gen_random_bytes(32), 'hex');
  PERFORM app.create_api_token(
    v_org_id, p_workspace_id, app.current_member_id(v_org_id), p_scope, btrim(p_name),
    encode(extensions.digest(v_token, 'sha256'), 'hex'), p_expires_at
  );
  RETURN v_token;
END $$;

REVOKE EXECUTE ON FUNCTION public.create_api_token(uuid, text, public.token_scope, timestamptz)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_api_token(uuid, text, public.token_scope, timestamptz) TO authenticated;

CREATE FUNCTION public.revoke_api_token(p_token_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  t public.api_token%ROWTYPE;
BEGIN
  SELECT * INTO t FROM public.api_token WHERE id = p_token_id FOR UPDATE;
  IF NOT FOUND
     OR NOT (app.has_org_role(t.org_id, 'Admin') OR t.member_id = app.current_member_id(t.org_id)) THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  IF t.revoked_at IS NOT NULL THEN
    RETURN;
  END IF;

  UPDATE public.api_token SET revoked_at = now() WHERE id = p_token_id;
  PERFORM app.audit(t.org_id, t.workspace_id, 'api_token.revoked', 'api_token', p_token_id,
                    NULL, jsonb_build_object('name', t.name));
END $$;

REVOKE EXECUTE ON FUNCTION public.revoke_api_token(uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.revoke_api_token(uuid) TO authenticated;

-- Removing a member revokes their tokens immediately (M14).
CREATE FUNCTION app.revoke_member_tokens() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF NEW.status <> 'active' AND OLD.status = 'active' THEN
    UPDATE public.api_token SET revoked_at = now()
     WHERE member_id = NEW.id AND revoked_at IS NULL;
  END IF;
  RETURN NEW;
END $$;

REVOKE EXECUTE ON FUNCTION app.revoke_member_tokens() FROM public, anon, authenticated;

CREATE TRIGGER member_revoke_tokens AFTER UPDATE OF status ON public.member
  FOR EACH ROW EXECUTE FUNCTION app.revoke_member_tokens();

-- Admins see every token in their organisation, members their own.
DROP POLICY api_token_read ON public.api_token;
CREATE POLICY api_token_read ON public.api_token
  FOR SELECT TO authenticated
  USING (member_id = app.current_member_id(org_id) OR app.has_org_role(org_id, 'Admin'));
REVOKE SELECT ON public.api_token FROM authenticated;
GRANT SELECT (id, org_id, workspace_id, member_id, scope, name, last_used_at, expires_at, revoked_at, created_at)
  ON public.api_token TO authenticated;

-- ── 11. Interface entry points ────────────────────────────────────────────────
-- Operators manage campaigns, classify replies and work meetings and tasks (M14 matrix).

CREATE FUNCTION public.create_campaign(
  p_workspace_id uuid,
  p_brief_id     uuid,
  p_name         text,
  p_motion       public.motion
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id uuid;
BEGIN
  IF NOT app.has_workspace_role(p_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  SELECT b.org_id INTO v_org_id
    FROM public.brief b WHERE b.id = p_brief_id AND b.workspace_id = p_workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'brief % is not in workspace %', p_brief_id, p_workspace_id USING ERRCODE = '42501';
  END IF;
  IF length(btrim(coalesce(p_name, ''))) = 0 THEN
    RAISE EXCEPTION 'a campaign needs a name' USING ERRCODE = '22023';
  END IF;
  RETURN app.create_campaign(v_org_id, p_workspace_id, p_brief_id, btrim(p_name), p_motion, NULL);
END $$;

REVOKE EXECUTE ON FUNCTION public.create_campaign(uuid, uuid, text, public.motion) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_campaign(uuid, uuid, text, public.motion) TO authenticated;

CREATE FUNCTION public.set_campaign_push_mode(
  p_campaign_id uuid,
  p_push_mode   public.push_mode
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_workspace_id uuid;
BEGIN
  SELECT workspace_id INTO v_workspace_id FROM public.campaign WHERE id = p_campaign_id;
  IF NOT FOUND OR NOT app.has_workspace_role(v_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  PERFORM app.set_campaign_push_mode(p_campaign_id, p_push_mode);
END $$;

REVOKE EXECUTE ON FUNCTION public.set_campaign_push_mode(uuid, public.push_mode) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_campaign_push_mode(uuid, public.push_mode) TO authenticated;

CREATE FUNCTION public.set_campaign_status(
  p_campaign_id uuid,
  p_status      text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_workspace_id uuid;
BEGIN
  SELECT workspace_id INTO v_workspace_id FROM public.campaign WHERE id = p_campaign_id;
  IF NOT FOUND OR NOT app.has_workspace_role(v_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  PERFORM app.set_campaign_status(p_campaign_id, p_status);
END $$;

REVOKE EXECUTE ON FUNCTION public.set_campaign_status(uuid, text) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_campaign_status(uuid, text) TO authenticated;

CREATE FUNCTION public.classify_reply(
  p_activity_id uuid,
  p_reply_class public.reply_class
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
BEGIN
  SELECT org_id, workspace_id INTO v_org_id, v_workspace_id
    FROM public.activity WHERE id = p_activity_id;
  IF NOT FOUND OR NOT app.has_workspace_role(v_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  PERFORM app.classify_reply(p_activity_id, p_reply_class, 'human', app.current_member_id(v_org_id));
END $$;

REVOKE EXECUTE ON FUNCTION public.classify_reply(uuid, public.reply_class) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.classify_reply(uuid, public.reply_class) TO authenticated;

-- An explicit human confirmation is one of the two accepted proofs of a held meeting (M12).
CREATE FUNCTION public.confirm_meeting(
  p_meeting_id uuid,
  p_held_at    timestamptz DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_workspace_id uuid;
  v_scheduled_at timestamptz;
BEGIN
  SELECT workspace_id, scheduled_at INTO v_workspace_id, v_scheduled_at
    FROM public.meeting WHERE id = p_meeting_id;
  IF NOT FOUND OR NOT app.has_workspace_role(v_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  PERFORM app.confirm_meeting(p_meeting_id, coalesce(p_held_at, least(v_scheduled_at, now())), 'confirmed', NULL);
END $$;

REVOKE EXECUTE ON FUNCTION public.confirm_meeting(uuid, timestamptz) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.confirm_meeting(uuid, timestamptz) TO authenticated;

CREATE FUNCTION public.match_meeting(
  p_meeting_id uuid,
  p_account_id uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_workspace_id uuid;
BEGIN
  SELECT workspace_id INTO v_workspace_id FROM public.meeting WHERE id = p_meeting_id;
  IF NOT FOUND OR NOT app.has_workspace_role(v_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  PERFORM app.match_meeting(p_meeting_id, p_account_id);
END $$;

REVOKE EXECUTE ON FUNCTION public.match_meeting(uuid, uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.match_meeting(uuid, uuid) TO authenticated;

CREATE FUNCTION public.create_task(
  p_account_id uuid,
  p_title      text,
  p_due_date   date DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
BEGIN
  SELECT org_id, workspace_id INTO v_org_id, v_workspace_id
    FROM public.account WHERE id = p_account_id;
  IF NOT FOUND OR NOT app.has_workspace_role(v_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  IF length(btrim(coalesce(p_title, ''))) = 0 THEN
    RAISE EXCEPTION 'a task needs a title' USING ERRCODE = '22023';
  END IF;
  RETURN app.create_task(v_org_id, v_workspace_id, p_account_id, btrim(p_title), 'manual',
                         app.current_member_id(v_org_id), p_due_date);
END $$;

REVOKE EXECUTE ON FUNCTION public.create_task(uuid, text, date) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.create_task(uuid, text, date) TO authenticated;

CREATE FUNCTION public.complete_task(p_task_id uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_workspace_id uuid;
BEGIN
  SELECT workspace_id INTO v_workspace_id FROM public.task WHERE id = p_task_id;
  IF NOT FOUND OR NOT app.has_workspace_role(v_workspace_id, 'Operator') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  PERFORM app.complete_task(p_task_id);
END $$;

REVOKE EXECUTE ON FUNCTION public.complete_task(uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.complete_task(uuid) TO authenticated;

-- ── 12. Source payloads ───────────────────────────────────────────────────────
-- The fetched text was never kept, so extraction, indexing and research had only the URL to
-- read. The text of each source record is now stored beside it, immutable like the record.
-- (F8 places raw payloads in object storage; text in Postgres is the V1 form of that store.)
CREATE TABLE public.source_payload (
  source_record_id uuid PRIMARY KEY REFERENCES public.source_record (id),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid REFERENCES public.workspace (id) ON DELETE RESTRICT,
  content          text NOT NULL,
  created_at       timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.source_payload ENABLE ROW LEVEL SECURITY;
CREATE POLICY source_payload_read ON public.source_payload
  FOR SELECT TO authenticated
  USING (workspace_id IS NOT NULL AND app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.source_payload TO authenticated;

CREATE TRIGGER source_payload_immutable BEFORE UPDATE OR DELETE ON public.source_payload
  FOR EACH ROW EXECUTE FUNCTION app.forbid_mutation();

-- A new record stores its text and emits source_record.created, which is what starts
-- extraction (M2 → M3). A re-fetched item is a duplicate by content hash and does neither.
DROP FUNCTION app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text);

CREATE FUNCTION app.create_source_record(
  p_org          uuid,
  p_workspace    uuid,
  p_connector_id text,
  p_source_type  text,
  p_url          text,
  p_fetched_at   timestamptz,
  p_content_hash text,
  p_payload_ref  text,
  p_language     text,
  p_content      text DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_id uuid;
BEGIN
  INSERT INTO public.source_record
    (org_id, workspace_id, connector_id, source_type, url, fetched_at, content_hash, payload_ref, language)
  VALUES
    (p_org, p_workspace, p_connector_id, p_source_type, p_url, p_fetched_at, p_content_hash, p_payload_ref, p_language)
  ON CONFLICT (org_id, content_hash) DO NOTHING
  RETURNING id INTO v_id;

  IF v_id IS NULL THEN
    SELECT id INTO v_id FROM public.source_record WHERE org_id = p_org AND content_hash = p_content_hash;
    RETURN v_id;
  END IF;

  IF length(btrim(coalesce(p_content, ''))) > 0 THEN
    INSERT INTO public.source_payload (source_record_id, org_id, workspace_id, content)
    VALUES (v_id, p_org, p_workspace, left(p_content, 200000));
  END IF;

  PERFORM app.emit(
    'source_record.created'::public.event_type,
    p_org, p_workspace, 'source_record', v_id,
    jsonb_build_object('source_record_id', v_id, 'source_type', p_source_type,
                       'connector_id', p_connector_id)
  );
  RETURN v_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_source_record(uuid, uuid, text, text, text, timestamptz, text, text, text, text)
  TO postgres, service_role;

-- A fetch result must not un-pause a radar the user removed.
CREATE OR REPLACE FUNCTION app.update_radar_cursor(
  p_radar        uuid,
  p_cursor_state jsonb,
  p_fetched      int,
  p_yield        int
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  UPDATE public.radar SET
    cursor_state    = p_cursor_state,
    last_fetched_at = now(),
    record_count    = record_count + p_fetched,
    yield_count     = yield_count  + p_yield,
    status          = CASE WHEN status = 'paused' THEN 'paused' ELSE 'active' END,
    last_error      = NULL,
    updated_at      = now()
  WHERE id = p_radar;
END $$;

-- An account is a workspace's record on a company (F2). Mentions of people resolve to person
-- entities but never open an account.
CREATE OR REPLACE FUNCTION app.create_account(
  p_org uuid,
  p_workspace uuid,
  p_entity uuid
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid;
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM public.entity e
     WHERE e.id = p_entity AND e.org_id = p_org AND e.entity_type = 'company'
  ) THEN
    RETURN NULL;
  END IF;

  INSERT INTO public.account (org_id, workspace_id, entity_id)
  VALUES (p_org, p_workspace, p_entity)
  ON CONFLICT (workspace_id, entity_id) DO NOTHING
  RETURNING id INTO v_id;
  IF v_id IS NULL THEN
    SELECT id INTO v_id FROM public.account
    WHERE workspace_id = p_workspace AND entity_id = p_entity;
  END IF;
  RETURN v_id;
END;
$$;

-- ── 13. Enrichment contacts ───────────────────────────────────────────────────
-- A contact belongs to a person (always tenant-scoped, F1), linked to the company by works_at.
-- The value is stored encrypted; value_hash is a keyed digest used only to recognise a value
-- already held, so the same address is never paid for or stored twice (M7).
ALTER TABLE public.contact_point ADD COLUMN value_hash text;
CREATE UNIQUE INDEX contact_point_value_hash_uq
  ON public.contact_point (org_id, type, value_hash) WHERE value_hash IS NOT NULL;

CREATE FUNCTION app.upsert_person_contact(
  p_org_id           uuid,
  p_workspace_id     uuid,
  p_account_id       uuid,
  p_display_name     text,
  p_title            text,
  p_persona          text,
  p_type             text,
  p_value_encrypted  text,
  p_value_hash       text,
  p_status           public.contact_verification_status,
  p_verified_at      timestamptz,
  p_provider         text,
  p_source_record_id uuid
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_company_id uuid;
  v_person_id  uuid;
  v_cp_id      uuid;
BEGIN
  SELECT a.entity_id INTO v_company_id
    FROM public.account a
   WHERE a.id = p_account_id AND a.org_id = p_org_id AND a.workspace_id = p_workspace_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account % is not in workspace %', p_account_id, p_workspace_id USING ERRCODE = '42501';
  END IF;

  SELECT cp.id, cp.entity_id INTO v_cp_id, v_person_id
    FROM public.contact_point cp
   WHERE cp.org_id = p_org_id AND cp.type = p_type AND cp.value_hash = p_value_hash;

  -- Do-not-contact and unsubscribed people are never enriched again (M7).
  IF v_person_id IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.suppression_list sl
     WHERE sl.org_id = p_org_id AND sl.person_entity_id = v_person_id
  ) THEN
    RETURN NULL;
  END IF;

  IF v_cp_id IS NOT NULL THEN
    UPDATE public.contact_point
       SET status      = coalesce(p_status, status),
           verified_at = coalesce(p_verified_at, verified_at),
           provider    = coalesce(p_provider, provider),
           account_id  = coalesce(account_id, p_account_id)
     WHERE id = v_cp_id;
  ELSE
    v_person_id := app.create_entity(p_org_id, 'person'::public.entity_type,
                                     coalesce(nullif(btrim(p_display_name), ''), 'Unknown contact'));
    INSERT INTO public.relationship
      (org_id, from_entity_id, relation, to_entity_id, attributes, source_record_id, confidence)
    VALUES
      (p_org_id, v_person_id, 'works_at', v_company_id,
       jsonb_strip_nulls(jsonb_build_object('title', p_title, 'persona', p_persona,
                                            'discovered_by', p_provider)),
       p_source_record_id, 70);
    INSERT INTO public.contact_point
      (org_id, workspace_id, entity_id, account_id, type, value_encrypted, value_hash,
       status, verified_at, provider)
    VALUES
      (p_org_id, p_workspace_id, v_person_id, p_account_id, p_type, p_value_encrypted, p_value_hash,
       coalesce(p_status, 'unknown'), p_verified_at, p_provider)
    RETURNING id INTO v_cp_id;
  END IF;

  IF coalesce(p_status, 'unknown') = 'valid' THEN
    PERFORM app.emit(
      'contact_point.verified'::public.event_type,
      p_org_id, p_workspace_id, 'contact_point', v_cp_id,
      jsonb_build_object('contact_point_id', v_cp_id, 'entity_id', v_person_id,
                         'account_id', p_account_id, 'type', p_type, 'provider', p_provider)
    );
  END IF;

  RETURN v_cp_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.upsert_person_contact(uuid, uuid, uuid, text, text, text, text, text, text, public.contact_verification_status, timestamptz, text, uuid)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.upsert_person_contact(uuid, uuid, uuid, text, text, text, text, text, text, public.contact_verification_status, timestamptz, text, uuid)
  TO postgres, service_role;

-- ── Kill-switch seeds for the jobs added with this pass ───────────────────────
INSERT INTO public.job_type_config (job_type, enabled)
VALUES
  ('campaign.push_cycle', true),
  ('account.dormancy_sweep', true),
  ('reply.auto_classify', true),
  ('radar.fetch_cycle', true),
  -- Registered since Phase 1b / Phase 3 but never seeded: an unseeded job type is skipped (S1),
  -- so Brief intake and the daily next-step sweep never ran.
  ('brief.intake', true),
  ('next_step.cron', true)
ON CONFLICT (job_type) DO NOTHING;
