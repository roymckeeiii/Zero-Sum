-- Database role for the unified view, run as Inbox Zero's database owner. Idempotent.
-- Used by `python app.py init-db` (see README "Install"). Run it again after upgrading Zero Sum.
-- The password is set separately (ALTER ROLE ... PASSWORD) so it never sits in this file.
--
-- What it may do, and nothing more:
--   read  a few columns of "Account" / "EmailAccount" (the linked Google accounts and their encrypted tokens)
--   read  "ExecutedRule" (which Inbox Zero rule handled which email)
--   write "Rule", "Action" (rule sync), "Newsletter" (sender unsubscribe / auto-archive status)
--   own   schema "unified" (its own tables)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'iz_unified') THEN
    CREATE ROLE iz_unified LOGIN;
  END IF;
  EXECUTE format('GRANT CONNECT ON DATABASE %I TO iz_unified', current_database());
END $$;
GRANT USAGE ON SCHEMA public TO iz_unified;
GRANT SELECT (id, provider, access_token, refresh_token, expires_at) ON "Account" TO iz_unified;
GRANT SELECT (id, email, name, "accountId") ON "EmailAccount" TO iz_unified;
GRANT SELECT ON "ExecutedRule" TO iz_unified;
GRANT SELECT, INSERT, UPDATE, DELETE ON "Rule", "Action", "Newsletter" TO iz_unified;
CREATE SCHEMA IF NOT EXISTS unified AUTHORIZATION iz_unified;
