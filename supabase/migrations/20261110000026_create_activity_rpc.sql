-- Migration 0026: app.create_activity SECURITY DEFINER RPC
-- Provides an S8-compliant write path for inserting activity rows.
-- Consumed by: inbound webhook ingestion (T7), campaign module (T2),
-- inbox classification (T3), and worker job handlers (T11).

CREATE OR REPLACE FUNCTION app.create_activity(
    p_org_id         uuid,
    p_workspace_id   uuid,
    p_account_id     uuid,
    p_kind           text,
    p_occurred_at    timestamptz DEFAULT now(),
    p_integration_id uuid        DEFAULT NULL,
    p_external_id    text        DEFAULT NULL
) RETURNS uuid
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
    v_id uuid;
BEGIN
    INSERT INTO public.activity (
        org_id, workspace_id, account_id, kind,
        occurred_at, integration_id, external_id
    ) VALUES (
        p_org_id, p_workspace_id, p_account_id, p_kind::public.activity_kind,
        p_occurred_at, p_integration_id, p_external_id
    )
    RETURNING id INTO v_id;

    RETURN v_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.create_activity(uuid, uuid, uuid, text, timestamptz, uuid, text)
    FROM public, anon, authenticated;
GRANT  EXECUTE ON FUNCTION app.create_activity(uuid, uuid, uuid, text, timestamptz, uuid, text)
    TO service_role;
