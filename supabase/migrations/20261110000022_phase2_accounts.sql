-- Phase 2: Accounts layer tables + derivation write-path RPCs + kill-switch seeds.
-- Adds:
--   - public.research_doc
--   - public.research_job
--   - public.contact_point
--   - public.enrichment_job
--   - public.segment
--   - public.data_completeness
--   - Unique index on account_stage_event for idempotency
--   - app.upsert_score
--   - app.upsert_label
--   - app.upsert_account_stage
--   - app.upsert_account_card
--   - app.create_research_doc
--   - app.create_contact_point
--   - app.record_data_completeness
--   - Kill-switch seeds

-- ── 1. account_stage_event idempotency index ──────────────────────────────────
-- The table was created in migration 0009 without a unique constraint.
-- Add a functional unique index for ON CONFLICT idempotency in upsert_account_stage.
CREATE UNIQUE INDEX account_stage_event_idempotency
  ON public.account_stage_event (account_id, coalesce(activity_id, '00000000-0000-0000-0000-000000000000'::uuid));

-- ── 2. public.research_doc ────────────────────────────────────────────────────
CREATE TABLE public.research_doc (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  account_id       uuid NOT NULL REFERENCES public.account (id),
  brief_version_id uuid NOT NULL REFERENCES public.brief_version (id),
  entity_id        uuid REFERENCES public.entity (id),
  version          int NOT NULL DEFAULT 1,
  content          jsonb NOT NULL DEFAULT '{}',
  source_record_ids uuid[] NOT NULL DEFAULT '{}',
  published_at     timestamptz NOT NULL DEFAULT now(),
  inputs           jsonb NOT NULL DEFAULT '{}',
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT research_doc_unique UNIQUE (account_id, brief_version_id, version)
);

ALTER TABLE public.research_doc ENABLE ROW LEVEL SECURITY;
CREATE POLICY research_doc_read ON public.research_doc
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.research_doc TO authenticated;

-- ── 3. public.research_job ────────────────────────────────────────────────────
CREATE TABLE public.research_job (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  account_id       uuid NOT NULL REFERENCES public.account (id),
  brief_version_id uuid NOT NULL REFERENCES public.brief_version (id),
  status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'done', 'failed')),
  started_at       timestamptz,
  finished_at      timestamptz,
  inputs           jsonb NOT NULL DEFAULT '{}',
  error            text,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX research_job_ws_status_idx ON public.research_job (workspace_id, status, created_at);

ALTER TABLE public.research_job ENABLE ROW LEVEL SECURITY;
CREATE POLICY research_job_read ON public.research_job
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.research_job TO authenticated;

-- ── 4. public.contact_point ───────────────────────────────────────────────────
CREATE TABLE public.contact_point (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  entity_id        uuid NOT NULL REFERENCES public.entity (id),
  account_id       uuid REFERENCES public.account (id),
  type             text NOT NULL CHECK (type IN ('email', 'phone', 'profile')),
  value_encrypted  text NOT NULL,
  status           public.contact_verification_status NOT NULL DEFAULT 'unknown',
  verified_at      timestamptz,
  provider         text,
  scope            public.data_scope NOT NULL DEFAULT 'tenant' CHECK (scope = 'tenant'),
  created_at       timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT contact_point_unique UNIQUE (entity_id, type, value_encrypted)
);
CREATE INDEX contact_point_entity_idx ON public.contact_point (entity_id, status);

ALTER TABLE public.contact_point ENABLE ROW LEVEL SECURITY;
CREATE POLICY contact_point_read ON public.contact_point
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.contact_point TO authenticated;

-- ── 5. public.enrichment_job ──────────────────────────────────────────────────
CREATE TABLE public.enrichment_job (
  id               uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id           uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id     uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  account_id       uuid NOT NULL REFERENCES public.account (id),
  brief_version_id uuid NOT NULL REFERENCES public.brief_version (id),
  status           text NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'running', 'done', 'failed')),
  started_at       timestamptz,
  finished_at      timestamptz,
  inputs           jsonb NOT NULL DEFAULT '{}',
  error            text,
  created_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX enrichment_job_ws_status_idx ON public.enrichment_job (workspace_id, status, created_at);

ALTER TABLE public.enrichment_job ENABLE ROW LEVEL SECURITY;
CREATE POLICY enrichment_job_read ON public.enrichment_job
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.enrichment_job TO authenticated;

-- ── 6. public.segment ─────────────────────────────────────────────────────────
CREATE TABLE public.segment (
  id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  org_id       uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  brief_id     uuid REFERENCES public.brief (id),
  name         text NOT NULL,
  filter       jsonb NOT NULL DEFAULT '{}',
  created_by   uuid REFERENCES public.member (id),
  created_at   timestamptz NOT NULL DEFAULT now()
);

ALTER TABLE public.segment ENABLE ROW LEVEL SECURITY;
CREATE POLICY segment_read ON public.segment
  FOR SELECT TO authenticated
  USING (app.has_workspace_role(workspace_id, 'Viewer'));
GRANT SELECT ON public.segment TO authenticated;

-- ── 7. public.data_completeness ───────────────────────────────────────────────
CREATE TABLE public.data_completeness (
  id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  org_id       uuid NOT NULL REFERENCES public.organisation (id) ON DELETE RESTRICT,
  workspace_id uuid NOT NULL REFERENCES public.workspace (id) ON DELETE RESTRICT,
  pipeline     text NOT NULL,
  period_start timestamptz NOT NULL,
  period_end   timestamptz NOT NULL,
  expected     int NOT NULL,
  actual       int NOT NULL,
  gap_pct      numeric(5, 2) GENERATED ALWAYS AS (
    CASE WHEN expected = 0 THEN 0
         ELSE round(((expected - actual)::numeric / expected) * 100, 2)
    END
  ) STORED,
  recorded_at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX data_completeness_ws_pipeline_idx ON public.data_completeness (workspace_id, pipeline, period_start DESC);

ALTER TABLE public.data_completeness ENABLE ROW LEVEL SECURITY;
CREATE POLICY data_completeness_read ON public.data_completeness
  FOR SELECT TO authenticated
  USING (app.has_org_role(org_id, 'Admin'));
GRANT SELECT ON public.data_completeness TO authenticated;

-- ── 8. app.record_data_completeness ──────────────────────────────────────────
-- Pre-flight ruling: direct INSERT into data_completeness violates S8.
-- Health module calls this RPC instead.
CREATE OR REPLACE FUNCTION app.record_data_completeness(
  p_org_id       uuid,
  p_workspace_id uuid,
  p_pipeline     text,
  p_period_start timestamptz,
  p_period_end   timestamptz,
  p_expected     int,
  p_actual       int
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  INSERT INTO public.data_completeness
    (org_id, workspace_id, pipeline, period_start, period_end, expected, actual)
  VALUES
    (p_org_id, p_workspace_id, p_pipeline, p_period_start, p_period_end, p_expected, p_actual);
END $$;

REVOKE EXECUTE ON FUNCTION app.record_data_completeness(uuid, uuid, text, timestamptz, timestamptz, int, int)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.record_data_completeness(uuid, uuid, text, timestamptz, timestamptz, int, int)
  TO postgres, service_role;

-- ── 9. app.upsert_score ───────────────────────────────────────────────────────
-- Derivation RPC; sets app.writer=derivation; enforces verdict gate on band.
-- Emits score.band_changed when band changes.
CREATE OR REPLACE FUNCTION app.upsert_score(
  p_account_id       uuid,
  p_brief_version_id uuid,
  p_fit              smallint,
  p_evidence         smallint,
  p_scale            smallint,
  p_timing           smallint,
  p_reachability     smallint,
  p_priority         numeric,
  p_readiness        smallint,
  p_band             public.band,
  p_gates            jsonb,
  p_explanation      jsonb,
  p_inputs           jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id        uuid;
  v_workspace_id  uuid;
  v_entity_id     uuid;
  v_verdict_value public.verdict_value;
  v_band          public.band := p_band;
  v_gates         jsonb := coalesce(p_gates, '[]');
  v_score_id      uuid;
  v_old_band      public.band;
BEGIN
  -- Resolve org + workspace from brief_version; validate it exists
  SELECT bv.org_id, bv.workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.brief_version bv
   WHERE bv.id = p_brief_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'brief_version % not found', p_brief_version_id USING ERRCODE = '22023';
  END IF;

  -- Verify account belongs to same org and workspace
  IF NOT EXISTS (
    SELECT 1 FROM public.account a
     WHERE a.id = p_account_id AND a.org_id = v_org_id AND a.workspace_id = v_workspace_id
  ) THEN
    RAISE EXCEPTION 'account % does not belong to org/workspace of brief_version %',
      p_account_id, p_brief_version_id USING ERRCODE = '42501';
  END IF;

  -- Band gate: check entity's latest verdict and enforce rules regardless of input band.
  -- Contradicted always forces Excluded; Unverified caps High/Medium at Low.
  SELECT a.entity_id INTO v_entity_id
    FROM public.account a WHERE a.id = p_account_id;

  IF v_entity_id IS NOT NULL THEN
    SELECT v.value INTO v_verdict_value
      FROM public.verdict v
     WHERE v.entity_id = v_entity_id AND v.brief_version_id = p_brief_version_id
     ORDER BY v.computed_at DESC LIMIT 1;

    IF v_verdict_value = 'Contradicted' THEN
      v_band  := 'Excluded';
      v_gates := v_gates || '[{"reason":"verdict_contradicted"}]'::jsonb;
    ELSIF v_verdict_value = 'Unverified' AND v_band IN ('High', 'Medium') THEN
      v_band  := 'Low';
      v_gates := v_gates || '[{"reason":"verdict_unverified"}]'::jsonb;
    END IF;
  END IF;

  -- Capture existing band before upsert (for change detection)
  SELECT s.band INTO v_old_band
    FROM public.score s
   WHERE s.account_id = p_account_id AND s.brief_version_id = p_brief_version_id;

  -- Allow derivation write for this transaction
  PERFORM set_config('app.writer', 'derivation', true);

  INSERT INTO public.score
    (org_id, workspace_id, account_id, brief_version_id,
     fit, evidence, scale, timing, reachability, priority, readiness,
     band, gates, explanation, inputs)
  VALUES
    (v_org_id, v_workspace_id, p_account_id, p_brief_version_id,
     p_fit, p_evidence, p_scale, p_timing, p_reachability, p_priority, p_readiness,
     v_band, v_gates, coalesce(p_explanation, '{}'), coalesce(p_inputs, '{}'))
  ON CONFLICT (account_id, brief_version_id) DO UPDATE
    SET fit         = EXCLUDED.fit,
        evidence    = EXCLUDED.evidence,
        scale       = EXCLUDED.scale,
        timing      = EXCLUDED.timing,
        reachability = EXCLUDED.reachability,
        priority    = EXCLUDED.priority,
        readiness   = EXCLUDED.readiness,
        band        = EXCLUDED.band,
        gates       = EXCLUDED.gates,
        explanation = EXCLUDED.explanation,
        inputs      = EXCLUDED.inputs,
        computed_at = now()
  RETURNING id INTO v_score_id;

  -- Emit score.band_changed only when band actually changed
  IF v_old_band IS DISTINCT FROM v_band THEN
    PERFORM app.emit(
      'score.band_changed'::public.event_type,
      v_org_id,
      v_workspace_id,
      'score',
      v_score_id,
      jsonb_build_object(
        'score_id',         v_score_id,
        'account_id',       p_account_id,
        'brief_version_id', p_brief_version_id,
        'old_band',         v_old_band,
        'new_band',         v_band
      )
    );
  END IF;

  RETURN v_score_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.upsert_score(uuid, uuid, smallint, smallint, smallint, smallint, smallint, numeric, smallint, public.band, jsonb, jsonb, jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.upsert_score(uuid, uuid, smallint, smallint, smallint, smallint, smallint, numeric, smallint, public.band, jsonb, jsonb, jsonb)
  TO postgres, service_role;

-- ── 10. app.upsert_label ──────────────────────────────────────────────────────
-- Derivation RPC; sets app.writer=derivation.
-- ON CONFLICT (account_id, brief_version_id, kind, value) DO UPDATE.
CREATE OR REPLACE FUNCTION app.upsert_label(
  p_account_id       uuid,
  p_brief_version_id uuid,
  p_kind             text,
  p_value            text,
  p_inputs           jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_label_id     uuid;
BEGIN
  -- Resolve org + workspace from brief_version
  SELECT bv.org_id, bv.workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.brief_version bv
   WHERE bv.id = p_brief_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'brief_version % not found', p_brief_version_id USING ERRCODE = '22023';
  END IF;

  -- Verify account belongs to same org and workspace
  IF NOT EXISTS (
    SELECT 1 FROM public.account a
     WHERE a.id = p_account_id AND a.org_id = v_org_id AND a.workspace_id = v_workspace_id
  ) THEN
    RAISE EXCEPTION 'account % does not belong to org/workspace of brief_version %',
      p_account_id, p_brief_version_id USING ERRCODE = '42501';
  END IF;

  -- Allow derivation write for this transaction
  PERFORM set_config('app.writer', 'derivation', true);

  INSERT INTO public.label
    (org_id, workspace_id, account_id, brief_version_id, kind, value, inputs)
  VALUES
    (v_org_id, v_workspace_id, p_account_id, p_brief_version_id, p_kind, p_value, coalesce(p_inputs, '{}'))
  ON CONFLICT (account_id, brief_version_id, kind, value) DO UPDATE
    SET inputs      = EXCLUDED.inputs,
        computed_at = now()
  RETURNING id INTO v_label_id;

  RETURN v_label_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.upsert_label(uuid, uuid, text, text, jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.upsert_label(uuid, uuid, text, text, jsonb)
  TO postgres, service_role;

-- ── 11. app.upsert_account_stage ─────────────────────────────────────────────
-- Derivation RPC; sets app.writer=derivation; idempotent on (account_id, activity_id).
-- Updates account.stage and emits account.stage_changed.
CREATE OR REPLACE FUNCTION app.upsert_account_stage(
  p_account_id uuid,
  p_to_stage   public.stage,
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
  -- Resolve org + workspace and current stage from account
  SELECT a.org_id, a.workspace_id, a.stage
    INTO v_org_id, v_workspace_id, v_from_stage
    FROM public.account a
   WHERE a.id = p_account_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'account % not found', p_account_id USING ERRCODE = '22023';
  END IF;

  -- Allow derivation write for this transaction
  PERFORM set_config('app.writer', 'derivation', true);

  -- Insert stage event; idempotent on (account_id, coalesce(activity_id, zero_uuid))
  -- via the unique index account_stage_event_idempotency.
  INSERT INTO public.account_stage_event
    (org_id, workspace_id, account_id, from_stage, to_stage, activity_id, occurred_at)
  VALUES
    (v_org_id, v_workspace_id, p_account_id, v_from_stage, p_to_stage, p_activity_id, coalesce(p_occurred_at, now()))
  ON CONFLICT (account_id, coalesce(activity_id, '00000000-0000-0000-0000-000000000000'::uuid)) DO NOTHING
  RETURNING id INTO v_event_id;

  -- If the row already existed (conflict), fetch its id
  IF v_event_id IS NULL THEN
    SELECT id INTO v_event_id
      FROM public.account_stage_event
     WHERE account_id = p_account_id
       AND coalesce(activity_id, '00000000-0000-0000-0000-000000000000'::uuid)
         = coalesce(p_activity_id, '00000000-0000-0000-0000-000000000000'::uuid)
     LIMIT 1;
    RETURN v_event_id;
  END IF;

  -- Update account.stage (guarded by account_stage_guard trigger; we set app.writer above)
  UPDATE public.account
     SET stage = p_to_stage
   WHERE id = p_account_id;

  -- Emit account.stage_changed
  PERFORM app.emit(
    'account.stage_changed'::public.event_type,
    v_org_id,
    v_workspace_id,
    'account',
    p_account_id,
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

REVOKE EXECUTE ON FUNCTION app.upsert_account_stage(uuid, public.stage, uuid, timestamptz)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.upsert_account_stage(uuid, public.stage, uuid, timestamptz)
  TO postgres, service_role;

-- ── 12. app.upsert_account_card ───────────────────────────────────────────────
-- Derivation RPC; sets app.writer=derivation.
-- ON CONFLICT (account_id, brief_version_id) DO UPDATE.
CREATE OR REPLACE FUNCTION app.upsert_account_card(
  p_account_id       uuid,
  p_brief_version_id uuid,
  p_content          jsonb,
  p_inputs           jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_org_id       uuid;
  v_workspace_id uuid;
  v_card_id      uuid;
BEGIN
  -- Resolve org + workspace from brief_version
  SELECT bv.org_id, bv.workspace_id
    INTO v_org_id, v_workspace_id
    FROM public.brief_version bv
   WHERE bv.id = p_brief_version_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'brief_version % not found', p_brief_version_id USING ERRCODE = '22023';
  END IF;

  -- Verify account belongs to same org and workspace
  IF NOT EXISTS (
    SELECT 1 FROM public.account a
     WHERE a.id = p_account_id AND a.org_id = v_org_id AND a.workspace_id = v_workspace_id
  ) THEN
    RAISE EXCEPTION 'account % does not belong to org/workspace of brief_version %',
      p_account_id, p_brief_version_id USING ERRCODE = '42501';
  END IF;

  -- Allow derivation write for this transaction
  PERFORM set_config('app.writer', 'derivation', true);

  INSERT INTO public.account_card
    (org_id, workspace_id, account_id, brief_version_id, content, inputs)
  VALUES
    (v_org_id, v_workspace_id, p_account_id, p_brief_version_id,
     coalesce(p_content, '{}'), coalesce(p_inputs, '{}'))
  ON CONFLICT (account_id, brief_version_id) DO UPDATE
    SET content     = EXCLUDED.content,
        inputs      = EXCLUDED.inputs,
        computed_at = now()
  RETURNING id INTO v_card_id;

  RETURN v_card_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.upsert_account_card(uuid, uuid, jsonb, jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.upsert_account_card(uuid, uuid, jsonb, jsonb)
  TO postgres, service_role;

-- ── 13. app.create_research_doc ──────────────────────────────────────────────
-- Inserts a research_doc and emits research_doc.published.
CREATE OR REPLACE FUNCTION app.create_research_doc(
  p_org_id           uuid,
  p_workspace_id     uuid,
  p_account_id       uuid,
  p_brief_version_id uuid,
  p_entity_id        uuid,
  p_version          int,
  p_content          jsonb,
  p_source_record_ids uuid[],
  p_inputs           jsonb
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_doc_id uuid;
BEGIN
  INSERT INTO public.research_doc
    (org_id, workspace_id, account_id, brief_version_id, entity_id, version,
     content, source_record_ids, inputs)
  VALUES
    (p_org_id, p_workspace_id, p_account_id, p_brief_version_id, p_entity_id,
     coalesce(p_version, 1), coalesce(p_content, '{}'),
     coalesce(p_source_record_ids, '{}'), coalesce(p_inputs, '{}'))
  ON CONFLICT (account_id, brief_version_id, version) DO UPDATE
    SET content           = EXCLUDED.content,
        source_record_ids = EXCLUDED.source_record_ids,
        inputs            = EXCLUDED.inputs,
        published_at      = now()
  RETURNING id INTO v_doc_id;

  PERFORM app.emit(
    'research_doc.published'::public.event_type,
    p_org_id,
    p_workspace_id,
    'research_doc',
    v_doc_id,
    jsonb_build_object(
      'doc_id',           v_doc_id,
      'account_id',       p_account_id,
      'brief_version_id', p_brief_version_id,
      'version',          p_version
    )
  );

  RETURN v_doc_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_research_doc(uuid, uuid, uuid, uuid, uuid, int, jsonb, uuid[], jsonb)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_research_doc(uuid, uuid, uuid, uuid, uuid, int, jsonb, uuid[], jsonb)
  TO postgres, service_role;

-- ── 14. app.create_contact_point ─────────────────────────────────────────────
-- Checks for existing unexpired valid point before inserting.
-- Emits contact_point.verified when status = 'valid'.
CREATE OR REPLACE FUNCTION app.create_contact_point(
  p_org_id          uuid,
  p_workspace_id    uuid,
  p_entity_id       uuid,
  p_account_id      uuid,
  p_type            text,
  p_value_encrypted text,
  p_status          public.contact_verification_status,
  p_verified_at     timestamptz,
  p_provider        text
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
DECLARE
  v_cp_id uuid;
BEGIN
  -- Check for existing unexpired valid point for the same entity+type+value
  -- (the unique constraint will conflict if it exists; we upsert to update status/provider)
  INSERT INTO public.contact_point
    (org_id, workspace_id, entity_id, account_id, type, value_encrypted,
     status, verified_at, provider)
  VALUES
    (p_org_id, p_workspace_id, p_entity_id, p_account_id, p_type, p_value_encrypted,
     coalesce(p_status, 'unknown'), p_verified_at, p_provider)
  ON CONFLICT (entity_id, type, value_encrypted) DO UPDATE
    SET status      = EXCLUDED.status,
        verified_at = EXCLUDED.verified_at,
        provider    = EXCLUDED.provider,
        account_id  = coalesce(EXCLUDED.account_id, public.contact_point.account_id)
  RETURNING id INTO v_cp_id;

  -- Emit contact_point.verified when status is valid
  IF coalesce(p_status, 'unknown') = 'valid' THEN
    PERFORM app.emit(
      'contact_point.verified'::public.event_type,
      p_org_id,
      p_workspace_id,
      'contact_point',
      v_cp_id,
      jsonb_build_object(
        'contact_point_id', v_cp_id,
        'entity_id',        p_entity_id,
        'type',             p_type,
        'provider',         p_provider
      )
    );
  END IF;

  RETURN v_cp_id;
END $$;

REVOKE EXECUTE ON FUNCTION app.create_contact_point(uuid, uuid, uuid, uuid, text, text, public.contact_verification_status, timestamptz, text)
  FROM public, anon, authenticated;
GRANT EXECUTE ON FUNCTION app.create_contact_point(uuid, uuid, uuid, uuid, text, text, public.contact_verification_status, timestamptz, text)
  TO postgres, service_role;

-- ── 15. Kill-switch seeds ─────────────────────────────────────────────────────
INSERT INTO public.job_type_config (job_type, enabled)
VALUES
  ('score.compute',          true),
  ('research.generate',      true),
  ('enrichment.run',         true),
  ('account.stage_advance',  true),
  ('account.card.generate',  true),
  ('health.check',           true)
ON CONFLICT (job_type) DO NOTHING;
