-- Migration 0025: app.touch_api_token SECURITY DEFINER RPC
-- Provides an S8-compliant write path for updating api_token.last_used_at
-- Called by the API service on every authenticated request.

CREATE OR REPLACE FUNCTION app.touch_api_token(p_token_id uuid)
RETURNS void
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
BEGIN
  UPDATE public.api_token SET last_used_at = now() WHERE id = p_token_id;
END;
$$;

REVOKE EXECUTE ON FUNCTION app.touch_api_token(uuid) FROM public, anon, authenticated;
GRANT  EXECUTE ON FUNCTION app.touch_api_token(uuid) TO service_role;
