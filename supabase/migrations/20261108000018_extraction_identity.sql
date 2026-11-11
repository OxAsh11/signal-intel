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

create policy review_item_read on public.review_item
  for select using (app.has_workspace_role(workspace_id, 'Viewer'));
create policy review_item_write on public.review_item
  for insert using (app.has_workspace_role(workspace_id, 'Editor'));

grant select, insert on public.review_item to authenticated;

-- ── RPC 1: app.update_extraction_status ──────────────────────────────────────
CREATE OR REPLACE FUNCTION app.update_extraction_status(
  p_id uuid,
  p_status public.extraction_status,
  p_detail text default null
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
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
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.mention (org_id, source_record_id, entity_type, raw_name, raw_domain, raw_country, raw_attributes)
  VALUES (p_org, p_source_record, p_entity_type, p_raw_name, p_raw_domain, p_raw_country, p_raw_attributes)
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
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
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
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.entity (org_id, entity_type, display_name, scope)
  VALUES (p_org, p_entity_type, trim(p_display_name), p_scope)
  RETURNING id INTO v_id;
  INSERT INTO public.audit_log (org_id, actor_id, action, target_type, target_id)
  VALUES (p_org, NULL, 'entity.created', 'entity', v_id);
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
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
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
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
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
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_id uuid;
BEGIN
  INSERT INTO public.account (org_id, workspace_id, entity_id)
  VALUES (p_org, p_workspace, p_entity)
  ON CONFLICT (workspace_id, entity_id) DO NOTHING
  RETURNING id INTO v_id;
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
  p_loser uuid,
  p_actor uuid
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE v_org uuid;
BEGIN
  SELECT org_id INTO v_org FROM public.entity WHERE id = p_winner;
  UPDATE public.mention SET entity_id = p_winner WHERE entity_id = p_loser;
  UPDATE public.entity_alias SET entity_id = p_winner WHERE entity_id = p_loser;
  UPDATE public.entity SET status = 'merged', merged_into = p_winner WHERE id = p_loser;
  INSERT INTO public.audit_log (org_id, actor_id, action, target_type, target_id, detail)
  VALUES (v_org, p_actor, 'entity.merged', 'entity', p_loser, jsonb_build_object('winner', p_winner));
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
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
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
-- Granted to authenticated (Editor role gate is enforced inside the function body).
CREATE OR REPLACE FUNCTION app.resolve_review_item(
  p_id uuid,
  p_action text,
  p_new_entity_name text default null
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = public, app AS $$
DECLARE
  v_item public.review_item;
  v_new_entity uuid;
BEGIN
  SELECT * INTO v_item FROM public.review_item WHERE id = p_id;
  IF v_item IS NULL THEN RAISE EXCEPTION 'review_item not found' USING ERRCODE = 'P0002'; END IF;
  IF v_item.status <> 'pending' THEN RAISE EXCEPTION 'review_item already resolved' USING ERRCODE = '23514'; END IF;
  IF NOT app.has_workspace_role(v_item.workspace_id, 'Editor') THEN
    RAISE EXCEPTION 'insufficient role' USING ERRCODE = '42501';
  END IF;

  IF p_action = 'merge' THEN
    PERFORM app.resolve_mention(v_item.mention_id, v_item.candidate_entity, 'manual_merge', 100);
    PERFORM app.create_account(v_item.org_id, v_item.workspace_id, v_item.candidate_entity);
  ELSIF p_action = 'create_new' THEN
    SELECT entity_type INTO v_item.candidate_entity FROM public.mention WHERE id = v_item.mention_id;
    v_new_entity := app.create_entity(v_item.org_id,
      (SELECT entity_type FROM public.mention WHERE id = v_item.mention_id),
      coalesce(p_new_entity_name, (SELECT raw_name FROM public.mention WHERE id = v_item.mention_id)));
    PERFORM app.resolve_mention(v_item.mention_id, v_new_entity, 'manual_create', 100);
    PERFORM app.create_account(v_item.org_id, v_item.workspace_id, v_new_entity);
  END IF;
  -- 'skip' falls through — just updates status below

  UPDATE public.review_item
  SET status = p_action,
      resolved_by = auth.uid(),
      resolved_at = now()
  WHERE id = p_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.resolve_review_item(uuid, text, text)
  FROM public, anon;
GRANT EXECUTE ON FUNCTION app.resolve_review_item(uuid, text, text)
  TO authenticated;

-- ── Kill-switch seeds ─────────────────────────────────────────────────────────
INSERT INTO public.job_type_config (job_type, enabled)
VALUES
  ('extract.relevance_judge', true),
  ('extract.structured', true),
  ('identity.resolve', true),
  ('identity.duplicate_sweep', true)
ON CONFLICT (job_type) DO NOTHING;
