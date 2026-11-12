-- Migration 0031: identity review, Merge and Create new.
-- app.resolve_review_item passed an integer where app.resolve_mention takes a smallint, so both
-- actions failed with "function does not exist" (pgTAP suite 09, test 11). Same body, with casts.
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
END;
$$;
