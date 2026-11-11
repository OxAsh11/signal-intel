-- Migration 0018: review_item table, extraction/identity RPCs, kill-switch seeds.
-- Phase 1d Task 1: Extraction & Identity worker contract.

-- ── public.review_item ────────────────────────────────────────────────────────
create table public.review_item (
  id               uuid primary key default gen_random_uuid(),
  org_id           uuid not null references public.organisation (id) on delete restrict,
  workspace_id     uuid not null references public.workspace (id) on delete restrict,
  mention_id       uuid not null references public.mention (id),
  candidate_entity uuid references public.entity (id),
  match_method     text,
  match_confidence smallint check (match_confidence between 0 and 100),
  status           text not null default 'pending' check (status in ('pending', 'merged', 'skipped', 'created_new')),
  resolved_by      uuid references public.member (id),
  resolved_at      timestamptz,
  created_at       timestamptz not null default now()
);
create index review_item_ws_status_idx on public.review_item (workspace_id, status, created_at)
  where status = 'pending';
alter table public.review_item enable row level security;

-- S8: authenticated sees rows; writes go through app.resolve_review_item only.
create policy review_item_read on public.review_item
  for select to authenticated using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy review_item_write on public.review_item
  for insert to authenticated with check (app.has_workspace_role(workspace_id, 'Editor'));

grant select on public.review_item to authenticated;

-- ── RPC 1: app.update_extraction_status ──────────────────────────────────────
CREATE OR REPLACE FUNCTION app.update_extraction_status(
  p_id uuid,
  p_status public.extraction_status,
  p_detail text default null
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  UPDATE public.source_record
  SET extraction_status = p_status,
      status_detail = p_detail,
      updated_at = now()
  WHERE id = p_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.update_extraction_status(uuid, public.extraction_status, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.update_extraction_status(uuid, public.extraction_status, text)
  TO service_role;

-- ── RPC 2: app.create_mention ─────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_mention(
  p_org uuid,
  p_source_record uuid,
  p_entity_type public.entity_type,
  p_raw_name text,
  p_raw_domain text,
  p_raw_country text,
  p_raw_attributes jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.mention (org_id, source_record_id, entity_type, raw_name, raw_domain, raw_country, raw_attributes)
  VALUES (p_org, p_source_record, p_entity_type, p_raw_name, p_raw_domain, p_raw_country,
          coalesce(p_raw_attributes, '{}'))
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.create_mention(uuid, uuid, public.entity_type, text, text, text, jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_mention(uuid, uuid, public.entity_type, text, text, text, jsonb)
  TO service_role;

-- ── RPC 3: app.create_claim ───────────────────────────────────────────────────
-- Note: the claim table column is extractor_version_id (not extractor_version).
CREATE OR REPLACE FUNCTION app.create_claim(
  p_org uuid,
  p_entity uuid,
  p_predicate uuid,
  p_value jsonb,
  p_quote text,
  p_quote_original text,
  p_quote_language text,
  p_source_record uuid,
  p_observed_at timestamptz,
  p_extractor_version uuid,
  p_confidence smallint
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.claim (org_id, entity_id, predicate_id, value, quote, quote_original, quote_language,
    source_record_id, observed_at, extractor_version_id, confidence)
  VALUES (p_org, p_entity, p_predicate, p_value, p_quote, p_quote_original, p_quote_language,
    p_source_record, p_observed_at, p_extractor_version, p_confidence)
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.create_claim(uuid, uuid, uuid, jsonb, text, text, text, uuid, timestamptz, uuid, smallint)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_claim(uuid, uuid, uuid, jsonb, text, text, text, uuid, timestamptz, uuid, smallint)
  TO service_role;

-- ── RPC 4: app.create_entity ──────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_entity(
  p_org uuid,
  p_entity_type public.entity_type,
  p_display_name text,
  p_scope public.data_scope default 'tenant'
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.entity (org_id, entity_type, display_name, scope)
  VALUES (p_org, p_entity_type, trim(p_display_name), p_scope)
  RETURNING id INTO v_id;
  PERFORM app.audit(p_org, null, 'entity.created', 'entity', v_id);
  RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.create_entity(uuid, public.entity_type, text, public.data_scope)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_entity(uuid, public.entity_type, text, public.data_scope)
  TO service_role;

-- ── RPC 5: app.create_entity_alias ───────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_entity_alias(
  p_org uuid,
  p_entity uuid,
  p_alias_type public.alias_type,
  p_value_normalised text,
  p_confidence smallint,
  p_source_record uuid
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.entity_alias (org_id, entity_id, alias_type, value_normalised, confidence, source_record_id)
  VALUES (p_org, p_entity, p_alias_type, p_value_normalised, p_confidence, p_source_record)
  ON CONFLICT (org_id, alias_type, value_normalised, entity_id)
  DO UPDATE SET confidence = greatest(entity_alias.confidence, excluded.confidence)
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.create_entity_alias(uuid, uuid, public.alias_type, text, smallint, uuid)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_entity_alias(uuid, uuid, public.alias_type, text, smallint, uuid)
  TO service_role;

-- ── RPC 6: app.resolve_mention ────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.resolve_mention(
  p_mention uuid,
  p_entity uuid,
  p_match_method text,
  p_match_confidence smallint
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  UPDATE public.mention
  SET entity_id = p_entity,
      match_method = p_match_method,
      match_confidence = p_match_confidence
  WHERE id = p_mention;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.resolve_mention(uuid, uuid, text, smallint)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.resolve_mention(uuid, uuid, text, smallint)
  TO service_role;

-- ── RPC 7: app.create_account ─────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_account(
  p_org uuid,
  p_workspace uuid,
  p_entity uuid
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid;
BEGIN
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

REVOKE EXECUTE ON FUNCTION app.create_account(uuid, uuid, uuid)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_account(uuid, uuid, uuid)
  TO service_role;

-- ── RPC 8: app.merge_entities ─────────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.merge_entities(
  p_winner uuid,
  p_loser  uuid,
  p_actor  uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org      uuid;
  v_loser_org uuid;
BEGIN
  IF p_winner = p_loser THEN
    RAISE EXCEPTION 'cannot merge entity with itself' USING ERRCODE = '22023';
  END IF;

  SELECT org_id INTO v_org       FROM public.entity WHERE id = p_winner;
  SELECT org_id INTO v_loser_org FROM public.entity WHERE id = p_loser;

  IF v_org IS NULL THEN
    RAISE EXCEPTION 'winner entity not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_loser_org IS NULL THEN
    RAISE EXCEPTION 'loser entity not found' USING ERRCODE = 'P0002';
  END IF;
  IF v_org <> v_loser_org THEN
    RAISE EXCEPTION 'cannot merge entities across organisations' USING ERRCODE = '23514';
  END IF;
  IF (SELECT status FROM public.entity WHERE id = p_winner) = 'merged' THEN
    RAISE EXCEPTION 'winner entity is already merged' USING ERRCODE = '23514';
  END IF;

  -- Re-point mentions
  UPDATE public.mention SET entity_id = p_winner WHERE entity_id = p_loser;

  -- Re-point claims
  UPDATE public.claim SET entity_id = p_winner WHERE entity_id = p_loser;

  -- Re-point aliases; drop duplicates that would violate alias_unique_per_entity
  DELETE FROM public.entity_alias
  WHERE entity_id = p_loser
    AND (org_id, alias_type, value_normalised) IN (
      SELECT org_id, alias_type, value_normalised
        FROM public.entity_alias WHERE entity_id = p_winner
    );
  UPDATE public.entity_alias SET entity_id = p_winner WHERE entity_id = p_loser;

  -- Re-point accounts; drop duplicates that would violate one_account_per_entity
  DELETE FROM public.account
  WHERE entity_id = p_loser
    AND workspace_id IN (
      SELECT workspace_id FROM public.account WHERE entity_id = p_winner
    );
  UPDATE public.account SET entity_id = p_winner WHERE entity_id = p_loser;

  -- Mark loser as merged
  UPDATE public.entity
  SET status = 'merged', merged_into = p_winner, updated_at = now()
  WHERE id = p_loser;

  PERFORM app.audit(v_org, null, 'entity.merged', 'entity', p_loser, null,
                    jsonb_build_object('winner', p_winner));
END;
$$;

REVOKE EXECUTE ON FUNCTION app.merge_entities(uuid, uuid, uuid)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.merge_entities(uuid, uuid, uuid)
  TO service_role;

-- ── RPC 9: app.create_review_item ────────────────────────────────────────────
CREATE OR REPLACE FUNCTION app.create_review_item(
  p_org uuid,
  p_workspace uuid,
  p_mention uuid,
  p_candidate_entity uuid,
  p_match_method text,
  p_match_confidence smallint
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.review_item (org_id, workspace_id, mention_id, candidate_entity, match_method, match_confidence)
  VALUES (p_org, p_workspace, p_mention, p_candidate_entity, p_match_method, p_match_confidence)
  RETURNING id INTO v_id;
  RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.create_review_item(uuid, uuid, uuid, uuid, text, smallint)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_review_item(uuid, uuid, uuid, uuid, text, smallint)
  TO service_role;

-- ── RPC 10: app.resolve_review_item ──────────────────────────────────────────
-- Callable only via the public.resolve_review_item wrapper below.
-- Editor role gate is enforced inside the function body.
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
  -- Validate action before any reads or side effects
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

  -- Cross-org guard: candidate entity must belong to the same org
  IF p_action = 'merge' THEN
    IF v_item.candidate_entity IS NULL THEN
      RAISE EXCEPTION 'review_item has no candidate entity for merge' USING ERRCODE = '22023';
    END IF;
    IF (SELECT org_id FROM public.entity WHERE id = v_item.candidate_entity) <> v_item.org_id THEN
      RAISE EXCEPTION 'candidate entity belongs to a different organisation' USING ERRCODE = '23514';
    END IF;
  END IF;

  v_member_id   := app.current_member_id(v_item.org_id);
  -- Provisional entity currently linked to the mention (may be NULL)
  SELECT entity_id INTO v_prov_entity FROM public.mention WHERE id = v_item.mention_id;

  IF p_action = 'merge' THEN
    -- Merge provisional entity into candidate (moves claims/aliases) before resolving mention
    IF v_prov_entity IS NOT NULL AND v_prov_entity <> v_item.candidate_entity THEN
      PERFORM app.merge_entities(v_item.candidate_entity, v_prov_entity, v_member_id);
    END IF;
    PERFORM app.resolve_mention(v_item.mention_id, v_item.candidate_entity, 'manual_merge', 100);
    PERFORM app.create_account(v_item.org_id, v_item.workspace_id, v_item.candidate_entity);

  ELSIF p_action = 'create_new' THEN
    SELECT entity_type INTO v_entity_type FROM public.mention WHERE id = v_item.mention_id;
    v_new_entity := app.create_entity(v_item.org_id, v_entity_type,
      coalesce(p_new_entity_name, (SELECT raw_name FROM public.mention WHERE id = v_item.mention_id)));
    -- Merge provisional entity into new entity (moves claims/aliases)
    IF v_prov_entity IS NOT NULL AND v_prov_entity <> v_new_entity THEN
      PERFORM app.merge_entities(v_new_entity, v_prov_entity, v_member_id);
    END IF;
    PERFORM app.resolve_mention(v_item.mention_id, v_new_entity, 'manual_create', 100);
    PERFORM app.create_account(v_item.org_id, v_item.workspace_id, v_new_entity);
  END IF;
  -- 'skip' falls through — just updates status below

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

REVOKE EXECUTE ON FUNCTION app.resolve_review_item(uuid, text, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.resolve_review_item(uuid, text, text)
  TO authenticated;

-- ── RPC 10 public wrapper: PostgREST/supabase.rpc entry point ────────────────
-- PostgREST only exposes the 'public' schema; this thin wrapper delegates to
-- app.resolve_review_item which enforces the Editor role gate internally.
CREATE OR REPLACE FUNCTION public.resolve_review_item(
  p_id uuid,
  p_action text,
  p_new_entity_name text default null
) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = '' AS $$
  SELECT app.resolve_review_item(p_id, p_action, p_new_entity_name);
$$;

REVOKE EXECUTE ON FUNCTION public.resolve_review_item(uuid, text, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.resolve_review_item(uuid, text, text)
  TO authenticated;

-- ── Kill-switch seeds ─────────────────────────────────────────────────────────
INSERT INTO public.job_type_config (job_type, enabled)
VALUES
  ('extract.relevance_judge', true),
  ('extract.structured', true),
  ('identity.resolve', true),
  ('identity.duplicate_sweep', true)
ON CONFLICT (job_type) DO NOTHING;
