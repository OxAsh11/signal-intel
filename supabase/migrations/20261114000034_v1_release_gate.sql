-- V1 release gate (PRD Part 8.1): identity decisions stored as feedback, evaluation sets filled
-- from human verdict marks, and two job tables that nothing used removed.

-- ── 1. Unused tables ─────────────────────────────────────────────────────────
-- Research and enrichment runs are tracked by the job queue (Procrastinate) and their outputs
-- (research_doc, contact_point); these tables were never read or written.
drop table public.research_job;
drop table public.enrichment_job;

-- ── 2. Identity decisions are feedback ───────────────────────────────────────
CREATE OR REPLACE FUNCTION app.resolve_review_item(
  p_id uuid,
  p_action text,
  p_new_entity_name text default null
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_item        public.review_item;
  v_new_entity  uuid;
  v_entity_type public.entity_type;
  v_prov_entity uuid;
  v_member_id   uuid;
BEGIN
  IF p_action IS NULL OR p_action NOT IN ('merge', 'skip', 'create_new') THEN
    RAISE EXCEPTION 'invalid action: %', p_action USING ERRCODE = '22023';
  END IF;

  SELECT * INTO v_item FROM public.review_item WHERE id = p_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'review_item not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_item.status <> 'pending' THEN
    RAISE EXCEPTION 'review_item already resolved' USING ERRCODE = '23514';
  END IF;
  IF NOT app.has_workspace_role(v_item.workspace_id, 'Editor') THEN
    RAISE EXCEPTION 'insufficient role' USING ERRCODE = '42501';
  END IF;

  IF p_action = 'merge' THEN
    IF v_item.candidate_entity IS NULL THEN
      RAISE EXCEPTION 'review_item has no candidate entity for merge' USING ERRCODE = '22023';
    END IF;
    IF (SELECT org_id FROM public.entity WHERE id = v_item.candidate_entity) <> v_item.org_id THEN
      RAISE EXCEPTION 'candidate entity belongs to a different organisation' USING ERRCODE = '23514';
    END IF;
  END IF;

  v_member_id := app.current_member_id(v_item.org_id);
  SELECT entity_id INTO v_prov_entity FROM public.mention WHERE id = v_item.mention_id;

  IF p_action = 'merge' THEN
    IF v_prov_entity IS NOT NULL AND v_prov_entity <> v_item.candidate_entity THEN
      PERFORM app.merge_entities(v_item.candidate_entity, v_prov_entity, v_member_id);
    END IF;
    PERFORM app.resolve_mention(v_item.mention_id, v_item.candidate_entity, 'manual_merge', 100::smallint);
    PERFORM app.create_account(v_item.org_id, v_item.workspace_id, v_item.candidate_entity);

  ELSIF p_action = 'create_new' THEN
    SELECT entity_type INTO v_entity_type FROM public.mention WHERE id = v_item.mention_id;
    v_new_entity := app.create_entity(v_item.org_id, v_entity_type,
      coalesce(p_new_entity_name, (SELECT raw_name FROM public.mention WHERE id = v_item.mention_id)));
    IF v_prov_entity IS NOT NULL AND v_prov_entity <> v_new_entity THEN
      PERFORM app.merge_entities(v_new_entity, v_prov_entity, v_member_id);
    END IF;
    PERFORM app.resolve_mention(v_item.mention_id, v_new_entity, 'manual_create', 100::smallint);
    PERFORM app.create_account(v_item.org_id, v_item.workspace_id, v_new_entity);
  END IF;

  UPDATE public.review_item
  SET status = CASE p_action
                 WHEN 'merge'      THEN 'merged'
                 WHEN 'skip'       THEN 'skipped'
                 WHEN 'create_new' THEN 'created_new'
               END,
      resolved_by = v_member_id,
      resolved_at = now()
  WHERE id = p_id;

  -- PRD Part 8.1: an identity decision is a human label. Merging into the suggested company
  -- confirms the match; creating a new company rejects it; skipping says nothing.
  IF p_action <> 'skip' AND v_member_id IS NOT NULL THEN
    INSERT INTO public.feedback (org_id, workspace_id, member_id, target_type, target_id, action, reason)
    VALUES (v_item.org_id, v_item.workspace_id, v_member_id, 'identity_match', p_id,
            CASE p_action WHEN 'merge' THEN 'confirm' ELSE 'reject' END,
            CASE p_action WHEN 'merge' THEN 'same company' ELSE 'different company' END);
  END IF;
END;
$$;

-- ── 3. Human verdict marks build the evaluation set ──────────────────────────
CREATE OR REPLACE FUNCTION public.resolve_verdict_review(
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

  -- PRD Part 8.1 / M18: every human-marked verdict becomes a labelled example in its Brief
  -- version's evaluation set, so prompt and rubric changes can be scored against people.
  PERFORM app.add_evaluation_example(r.org_id, r.workspace_id, v.brief_version_id,
    jsonb_build_object(
      'entity_id', v.entity_id,
      'verdict_id', v.id,
      'label', v_value::text,
      'model_value', v.value::text,
      'claim_ids', to_jsonb(v.claim_ids),
      'source', r.reason,
      'member_id', v_member,
      'marked_at', now()
    ));

  PERFORM app.audit(r.org_id, r.workspace_id, 'verdict.reviewed', 'verdict', r.verdict_id,
                    jsonb_build_object('value', v.value::text),
                    jsonb_build_object('value', v_value::text, 'action', v_action, 'reason', r.reason));
END $$;
