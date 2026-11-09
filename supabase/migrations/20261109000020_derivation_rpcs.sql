-- Phase 1e: Derivation write-path RPCs + kill-switch seeds.
-- Adds:
--   - app.upsert_verdict    (service_role only — sets app.writer=derivation transaction-locally)
--   - app.upsert_memory_chunk (service_role only — sets app.writer=derivation transaction-locally)
--   - Kill-switch seeds for 'verdict.compute' and 'index.chunk_embed' job types

-- ── 1. app.upsert_verdict ──────────────────────────────────────────────────────
-- Sets app.writer='derivation' transaction-locally so the guard trigger allows the write.
-- Validates that entity and brief_version belong to the same org.
-- ON CONFLICT (brief_version_id, entity_id) DO UPDATE.
-- Emits verdict.changed outbox event in the same transaction.
CREATE OR REPLACE FUNCTION app.upsert_verdict(
  p_brief_version_id uuid,
  p_entity_id        uuid,
  p_value            public.verdict_value,
  p_claim_ids        uuid[],
  p_quotes           jsonb,
  p_inputs           jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_verdict_id   uuid;
BEGIN
  -- Resolve org + workspace from brief_version
  SELECT bv.org_id, bv.workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.brief_version bv WHERE bv.id = p_brief_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'brief_version % not found', p_brief_version_id USING ERRCODE = '22023';
  END IF;

  -- Verify entity belongs to same org (cross-org guard)
  IF NOT EXISTS (SELECT 1 FROM public.entity e WHERE e.id = p_entity_id AND e.org_id = v_org_id) THEN
    RAISE EXCEPTION 'entity % does not belong to org %', p_entity_id, v_org_id USING ERRCODE = '42501';
  END IF;

  -- Allow derivation write for this transaction (is_local = true)
  PERFORM set_config('app.writer', 'derivation', true);

  INSERT INTO public.verdict
    (org_id, workspace_id, brief_version_id, entity_id, value, claim_ids, quotes, inputs)
  VALUES
    (v_org_id, v_workspace_id, p_brief_version_id, p_entity_id, p_value, p_claim_ids, p_quotes, p_inputs)
  ON CONFLICT (brief_version_id, entity_id) DO UPDATE
    SET value       = EXCLUDED.value,
        claim_ids   = EXCLUDED.claim_ids,
        quotes      = EXCLUDED.quotes,
        inputs      = EXCLUDED.inputs,
        computed_at = now()
  RETURNING id INTO v_verdict_id;

  -- Emit verdict.changed outbox event in same transaction
  INSERT INTO public.outbox (event, org_id, workspace_id, aggregate_type, aggregate_id, payload)
  VALUES (
    'verdict.changed'::public.event_type,
    v_org_id,
    v_workspace_id,
    'verdict',
    v_verdict_id,
    jsonb_build_object(
      'verdict_id',        v_verdict_id,
      'entity_id',         p_entity_id,
      'brief_version_id',  p_brief_version_id,
      'value',             p_value
    )
  );

  RETURN v_verdict_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.upsert_verdict(uuid, uuid, public.verdict_value, uuid[], jsonb, jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.upsert_verdict(uuid, uuid, public.verdict_value, uuid[], jsonb, jsonb)
  TO postgres, service_role;

-- ── 2. app.upsert_memory_chunk ─────────────────────────────────────────────────
-- Sets app.writer='derivation' transaction-locally so the guard trigger allows the write.
-- ON CONFLICT (doc_type, doc_id, chunk_index, embedding_model) DO UPDATE.
CREATE OR REPLACE FUNCTION app.upsert_memory_chunk(
  p_org_id          uuid,
  p_workspace_id    uuid,
  p_entity_id       uuid,
  p_brief_id        uuid,
  p_doc_type        text,
  p_doc_id          uuid,
  p_chunk_index     int,
  p_content         text,
  p_embedding       extensions.vector,
  p_embedding_model text,
  p_scope           public.data_scope,
  p_occurred_at     timestamptz
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_id uuid;
BEGIN
  -- Allow derivation write for this transaction (is_local = true)
  PERFORM set_config('app.writer', 'derivation', true);

  INSERT INTO public.memory_chunk
    (org_id, workspace_id, entity_id, brief_id, doc_type, doc_id, chunk_index,
     content, embedding, embedding_model, scope, occurred_at)
  VALUES
    (p_org_id, p_workspace_id, p_entity_id, p_brief_id, p_doc_type, p_doc_id, p_chunk_index,
     p_content, p_embedding, p_embedding_model, p_scope, p_occurred_at)
  ON CONFLICT (doc_type, doc_id, chunk_index, embedding_model) DO UPDATE
    SET content     = EXCLUDED.content,
        embedding   = EXCLUDED.embedding,
        computed_at = now()
  RETURNING id INTO v_id;

  RETURN v_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.upsert_memory_chunk(uuid, uuid, uuid, uuid, text, uuid, int, text, extensions.vector, text, public.data_scope, timestamptz)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.upsert_memory_chunk(uuid, uuid, uuid, uuid, text, uuid, int, text, extensions.vector, text, public.data_scope, timestamptz)
  TO postgres, service_role;

-- ── 3. Kill-switch seeds ───────────────────────────────────────────────────────
INSERT INTO public.job_type_config (job_type, enabled)
VALUES
  ('verdict.compute',    true),
  ('index.chunk_embed',  true)
ON CONFLICT (job_type) DO NOTHING;
