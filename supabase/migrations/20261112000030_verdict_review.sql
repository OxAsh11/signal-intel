-- Migration 0030: verdict review (M5).
--   * Conflicting claims never cancel silently: the verdict goes to review with the claims shown.
--   * A weekly sample of new Confirmed verdicts goes to review (workspace_settings.verdict_sample_*).
--   * A human mark is stored as feedback and holds the verdict until the entity's claims change.

-- ── 1. public.verdict_review ──────────────────────────────────────────────────
CREATE TABLE public.verdict_review (
  id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id          uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id    uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  verdict_id      uuid NOT NULL REFERENCES public.verdict (id),
  reason          text NOT NULL CHECK (reason IN ('conflict', 'sample')),
  verdict_value   public.verdict_value NOT NULL,          -- the value the reviewer was shown
  claim_ids       uuid[] NOT NULL DEFAULT '{}',           -- conflict: the claims that disagree
  status          text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'confirmed', 'corrected')),
  corrected_value public.verdict_value,
  resolved_by     uuid REFERENCES public.member (id),
  resolved_at     timestamptz,
  created_at      timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT corrected_has_value CHECK (status <> 'corrected' OR corrected_value IS NOT NULL)
);
CREATE UNIQUE INDEX verdict_review_one_pending_uq ON public.verdict_review (verdict_id, reason)
  WHERE status = 'pending';
CREATE INDEX verdict_review_ws_status_idx ON public.verdict_review (workspace_id, status, created_at);

ALTER TABLE public.verdict_review ENABLE ROW LEVEL SECURITY;
CREATE POLICY verdict_review_read ON public.verdict_review
  FOR SELECT TO authenticated USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.verdict_review TO authenticated;

-- ── 2. app.queue_verdict_review ───────────────────────────────────────────────
-- Returns the new review id, or NULL when this question is already waiting or was already
-- answered for the same set of claims.
CREATE FUNCTION app.queue_verdict_review(
  p_verdict_id uuid,
  p_reason     text,
  p_claim_ids  uuid[] DEFAULT '{}'
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v        public.verdict%ROWTYPE;
  v_claims uuid[];
  v_id     uuid;
BEGIN
  IF p_reason NOT IN ('conflict', 'sample') THEN
    RAISE EXCEPTION 'invalid review reason %', p_reason USING ERRCODE = '22023';
  END IF;
  SELECT * INTO v FROM public.verdict WHERE id = p_verdict_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'verdict % not found', p_verdict_id USING ERRCODE = '22023';
  END IF;

  v_claims := ARRAY(SELECT DISTINCT x FROM unnest(coalesce(p_claim_ids, '{}'::uuid[])) AS x ORDER BY x);

  IF p_reason = 'conflict' AND EXISTS (
    SELECT 1 FROM public.verdict_review r
     WHERE r.verdict_id = p_verdict_id AND r.reason = 'conflict' AND r.claim_ids = v_claims
  ) THEN
    RETURN NULL;
  END IF;

  INSERT INTO public.verdict_review (org_id, workspace_id, verdict_id, reason, verdict_value, claim_ids)
  VALUES (v.org_id, v.workspace_id, p_verdict_id, p_reason, v.value, v_claims)
  ON CONFLICT (verdict_id, reason) WHERE status = 'pending' DO NOTHING
  RETURNING id INTO v_id;
  RETURN v_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.queue_verdict_review(uuid, text, uuid[]) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.queue_verdict_review(uuid, text, uuid[]) TO postgres, service_role;

-- ── 3. app.sample_verdicts_for_review ─────────────────────────────────────────
-- Weekly: a random sample of the Confirmed verdicts computed in the last seven days that have
-- never been sampled. Size is the workspace percentage with a floor (default 5 percent, minimum
-- 10), less anything already sampled this week, so a second run in the same week adds nothing.
CREATE FUNCTION app.sample_verdicts_for_review(p_workspace_id uuid) RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_pct     numeric;
  v_min     integer;
  v_new     integer;
  v_already integer;
  v_target  integer;
  v_queued  integer;
BEGIN
  SELECT verdict_sample_pct, verdict_sample_min INTO v_pct, v_min
    FROM public.workspace_settings WHERE workspace_id = p_workspace_id;
  IF NOT FOUND THEN
    RETURN 0;
  END IF;

  SELECT count(*) INTO v_new
    FROM public.verdict v
   WHERE v.workspace_id = p_workspace_id
     AND v.value = 'Confirmed'
     AND v.computed_at > now() - interval '7 days';

  SELECT count(*) INTO v_already
    FROM public.verdict_review r
   WHERE r.workspace_id = p_workspace_id
     AND r.reason = 'sample'
     AND r.created_at > now() - interval '6 days';

  v_target := greatest(ceil(v_new * v_pct / 100.0)::integer, least(v_min, v_new)) - v_already;
  IF v_target <= 0 THEN
    RETURN 0;
  END IF;

  WITH picked AS (
    SELECT v.id, v.org_id, v.value
      FROM public.verdict v
     WHERE v.workspace_id = p_workspace_id
       AND v.value = 'Confirmed'
       AND v.computed_at > now() - interval '7 days'
       AND NOT EXISTS (
         SELECT 1 FROM public.verdict_review r WHERE r.verdict_id = v.id AND r.reason = 'sample'
       )
     ORDER BY random()
     LIMIT v_target
  ), ins AS (
    INSERT INTO public.verdict_review (org_id, workspace_id, verdict_id, reason, verdict_value)
    SELECT p.org_id, p_workspace_id, p.id, 'sample', p.value FROM picked p
    ON CONFLICT (verdict_id, reason) WHERE status = 'pending' DO NOTHING
    RETURNING 1
  )
  SELECT count(*) INTO v_queued FROM ins;
  RETURN v_queued;
END $$;

REVOKE EXECUTE ON FUNCTION app.sample_verdicts_for_review(uuid) FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.sample_verdicts_for_review(uuid) TO postgres, service_role;

-- ── 4. public.resolve_verdict_review ──────────────────────────────────────────
-- Editor and above (PRD role table: "Work review queues (identity, verdict)").
-- 'confirm' keeps the verdict; 'correct' replaces its value. Either way the mark is stored as
-- feedback and written on the verdict with the claims that were live, so the verdict is not
-- recomputed over the human until the entity's claims change.
CREATE FUNCTION public.resolve_verdict_review(
  p_id     uuid,
  p_action text,
  p_value  public.verdict_value DEFAULT NULL,
  p_reason text DEFAULT NULL
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  r        public.verdict_review%ROWTYPE;
  v        public.verdict%ROWTYPE;
  v_member uuid;
  v_action text := p_action;
  v_value  public.verdict_value;
  v_live   uuid[];
BEGIN
  SELECT * INTO r FROM public.verdict_review WHERE id = p_id FOR UPDATE;
  IF NOT FOUND OR NOT app.has_workspace_role(r.workspace_id, 'Editor') THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;
  IF r.status <> 'pending' THEN
    RAISE EXCEPTION 'this review is already resolved' USING ERRCODE = '23514';
  END IF;
  IF v_action IS NULL OR v_action NOT IN ('confirm', 'correct') THEN
    RAISE EXCEPTION 'invalid action %', p_action USING ERRCODE = '22023';
  END IF;

  SELECT * INTO v FROM public.verdict WHERE id = r.verdict_id FOR UPDATE;
  IF v_action = 'correct' AND p_value IS NULL THEN
    RAISE EXCEPTION 'choose the correct verdict' USING ERRCODE = '22023';
  END IF;
  -- Choosing the value the verdict already has is a confirmation.
  IF v_action = 'correct' AND p_value = v.value THEN
    v_action := 'confirm';
  END IF;
  v_value := CASE WHEN v_action = 'correct' THEN p_value ELSE v.value END;
  IF v_value = 'Confirmed' AND cardinality(v.claim_ids) = 0 THEN
    RAISE EXCEPTION 'a Confirmed verdict needs at least one claim behind it' USING ERRCODE = '23514';
  END IF;

  v_member := app.current_member_id(r.org_id);
  IF v_member IS NULL THEN
    RAISE EXCEPTION 'permission denied' USING ERRCODE = '42501';
  END IF;

  INSERT INTO public.feedback
    (org_id, workspace_id, member_id, target_type, target_id, action, corrected_value, reason)
  VALUES
    (r.org_id, r.workspace_id, v_member, 'verdict', r.verdict_id, v_action,
     CASE WHEN v_action = 'correct' THEN to_jsonb(v_value::text) END, p_reason);

  v_live := ARRAY(
    SELECT c.id FROM public.claim c
     WHERE c.entity_id = v.entity_id AND c.status = 'active'
       AND (c.expires_at IS NULL OR c.expires_at > now())
     ORDER BY c.id
  );

  PERFORM app.upsert_verdict(
    v.brief_version_id, v.entity_id, v_value, v.claim_ids, v.quotes,
    v.inputs || jsonb_build_object(
      'human_override', true,
      'human_member_id', v_member,
      'human_review_id', r.id,
      'live_claim_ids', to_jsonb(v_live)
    )
  );

  UPDATE public.verdict_review
     SET status = CASE WHEN v_action = 'correct' THEN 'corrected' ELSE 'confirmed' END,
         corrected_value = CASE WHEN v_action = 'correct' THEN v_value END,
         resolved_by = v_member,
         resolved_at = now()
   WHERE id = p_id;

  PERFORM app.audit(r.org_id, r.workspace_id, 'verdict.reviewed', 'verdict', r.verdict_id,
                    jsonb_build_object('value', v.value::text),
                    jsonb_build_object('value', v_value::text, 'action', v_action, 'reason', r.reason));
END $$;

REVOKE EXECUTE ON FUNCTION public.resolve_verdict_review(uuid, text, public.verdict_value, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_verdict_review(uuid, text, public.verdict_value, text)
  TO authenticated;

-- ── 5. Kill-switch seed ───────────────────────────────────────────────────────
INSERT INTO public.job_type_config (job_type, enabled)
VALUES ('verdict.sample_weekly', true)
ON CONFLICT (job_type) DO NOTHING;
