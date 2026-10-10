-- PostgreSQL Row-Level Security (defence in depth; the API also checks ownership on every request).
-- Run after `python -m app.cli migrate`, as a superuser (it creates a role; nexfox_owner has no CREATEROLE), from a
-- root shell so db-owner.env is readable. :'app_password' quotes the value itself, so pass it unquoted:
--   source /opt/nexfox/shared/db-owner.env
--   sudo -u postgres psql -d nexfox -v app_password="$APP_DB_PASSWORD" -f infra/sql/010_rls.sql
-- The API must then connect as nexfox_app (not the owner): RLS does not apply to table owners / superusers.
--
-- How it works: for customer requests the API runs `set_config('app.tenant_id', <tenant>, true)` at the start of
-- every transaction (app/db.py). Admin, scheduler, EA-gateway and worker-gateway sessions set no tenant and see all
-- rows; their access is controlled by role checks and worker/license authentication in the application.

-- (psql variables are not substituted inside DO $$...$$ blocks, hence \gexec)
SELECT format('CREATE ROLE nexfox_app LOGIN PASSWORD %L', :'app_password')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'nexfox_app') \gexec
SELECT format('ALTER ROLE nexfox_app PASSWORD %L', :'app_password') \gexec
GRANT USAGE ON SCHEMA public TO nexfox_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO nexfox_app;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO nexfox_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO nexfox_app;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO nexfox_app;
REVOKE ALL ON alembic_version FROM nexfox_app;
GRANT SELECT ON alembic_version TO nexfox_app;

DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['broker_accounts','account_configs','positions','commands','subscriptions','invoices','licenses',
                           'alert_prefs','notifications','telegram_links','bot_instances','bot_events','bot_runs',
                           'order_intents','ai_tasks','worker_jobs']
  LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS tenant_isolation ON %I', t);
    EXECUTE format($p$CREATE POLICY tenant_isolation ON %I USING (
        coalesce(current_setting('app.tenant_id', true), '') = ''
        OR tenant_id = current_setting('app.tenant_id', true)::int)$p$, t);
  END LOOP;
END $$;
