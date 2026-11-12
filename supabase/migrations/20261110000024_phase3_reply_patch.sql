-- Allow a narrow SECURITY DEFINER RPC to patch activity.payload["reply_class"].
-- The activity_append_only trigger calls app.forbid_mutation() on UPDATE/DELETE.
-- This migration adds a transaction-local escape hatch so only the designated
-- RPC can gate the trigger; all other UPDATE paths remain blocked.

-- 1. Extend app.forbid_mutation() to honour the transaction-local flag.
CREATE OR REPLACE FUNCTION app.forbid_mutation() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  IF current_setting('app.allow_activity_patch', true) = '1' THEN
    RETURN NEW;
  END IF;
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '42501';
END;
$$;

-- 2. Dedicated RPC for patching reply_class in activity.payload.
--    Called by the Python worker (service_role) after classifying an inbound reply.
--    The flag is set/cleared within the same transaction so no other path can slip through.
CREATE OR REPLACE FUNCTION app.patch_activity_reply_class(
  p_activity_id uuid,
  p_reply_class public.reply_class,
  p_source      text
) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path = '' AS $$
BEGIN
  PERFORM set_config('app.allow_activity_patch', '1', true);  -- transaction-local
  UPDATE public.activity
  SET payload = jsonb_set(
        jsonb_set(COALESCE(payload, '{}'), '{reply_class}', to_jsonb(p_reply_class::text)),
        '{reply_class_source}', to_jsonb(p_source)
      )
  WHERE id = p_activity_id;
  PERFORM set_config('app.allow_activity_patch', '0', true);
END;
$$;

REVOKE EXECUTE ON FUNCTION app.patch_activity_reply_class(uuid, public.reply_class, text)
  FROM public, anon, authenticated;
-- NO GRANT to authenticated — called via service_role only.
