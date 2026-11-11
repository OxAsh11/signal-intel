-- Migration: compliance RPCs for M18
-- app.submit_dsr, app.add_evaluation_example, app.record_lawful_basis

-- ---------------------------------------------------------------------------
-- app.submit_dsr
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.submit_dsr(
    p_org_id            uuid,
    p_person_entity_id  uuid,
    p_type              text,
    p_requested_by      text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id uuid;
BEGIN
    INSERT INTO public.data_subject_request (org_id, type, person_entity_id, requested_by)
    VALUES (p_org_id, p_type, p_person_entity_id, p_requested_by)
    RETURNING id INTO v_id;

    IF p_type = 'deletion' THEN
        PERFORM app.process_dsr_deletion(p_org_id, p_person_entity_id);
    END IF;

    RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.submit_dsr(uuid, uuid, text, text) FROM public, anon, authenticated;
GRANT  EXECUTE ON FUNCTION app.submit_dsr(uuid, uuid, text, text) TO service_role;

-- ---------------------------------------------------------------------------
-- app.add_evaluation_example
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.add_evaluation_example(
    p_org_id            uuid,
    p_workspace_id      uuid,
    p_brief_version_id  uuid,
    p_example           jsonb
) RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
    INSERT INTO public.evaluation_set (org_id, workspace_id, brief_version_id, examples)
    VALUES (p_org_id, p_workspace_id, p_brief_version_id, jsonb_build_array(p_example))
    ON CONFLICT (brief_version_id) DO UPDATE
        SET examples    = evaluation_set.examples || jsonb_build_array(p_example),
            updated_at  = now();
END;
$$;

REVOKE EXECUTE ON FUNCTION app.add_evaluation_example(uuid, uuid, uuid, jsonb) FROM public, anon, authenticated;
GRANT  EXECUTE ON FUNCTION app.add_evaluation_example(uuid, uuid, uuid, jsonb) TO service_role;

-- ---------------------------------------------------------------------------
-- app.record_lawful_basis
-- ---------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION app.record_lawful_basis(
    p_org_id        uuid,
    p_source_type   text,
    p_basis         text
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id uuid;
BEGIN
    INSERT INTO public.lawful_basis_record (org_id, source_type, basis)
    VALUES (p_org_id, p_source_type, p_basis)
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.record_lawful_basis(uuid, text, text) FROM public, anon, authenticated;
GRANT  EXECUTE ON FUNCTION app.record_lawful_basis(uuid, text, text) TO service_role;
