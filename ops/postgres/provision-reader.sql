-- Provision the API/auditor role after the role has been created by a
-- trusted database owner. Run with psql variables, for example:
--   psql "$EDGAR_MOE_REGISTRY_DATABASE_URL" \
--     -v api_reader_role=edgar_moe_api_reader \
--     -v migration_owner=edgar_moe_migrator \
--     -f ops/postgres/provision-reader.sql
--
-- This file intentionally contains no password and must never be run with the
-- API reader URL. The role receives only the privileges required by GET
-- endpoints and the independent read-only evidence auditor.

\set ON_ERROR_STOP on

BEGIN;

-- The public schema must not be writable by the serving role. The explicit
-- REVOKE also protects against a role-level grant left by an earlier setup.
REVOKE CREATE ON SCHEMA public FROM :"api_reader_role";
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public FROM :"api_reader_role";
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public FROM :"api_reader_role";
GRANT USAGE ON SCHEMA public TO :"api_reader_role";

GRANT SELECT ON TABLE
    public.forward_datasets,
    public.forward_models,
    public.forward_runs,
    public.forward_forecasts,
    public.forward_labels,
    public.forward_artifacts,
    public.forward_data_quality_checks,
    public.forward_audit_events
TO :"api_reader_role";

-- Future migrations run by the migration owner inherit the same SELECT-only
-- contract. This does not grant the reader any DDL or sequence privileges.
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_owner" IN SCHEMA public
    REVOKE ALL ON TABLES FROM :"api_reader_role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"migration_owner" IN SCHEMA public
    GRANT SELECT ON TABLES TO :"api_reader_role";

COMMIT;
