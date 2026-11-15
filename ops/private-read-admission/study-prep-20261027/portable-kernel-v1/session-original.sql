SELECT current_database() AS database_name,
  current_user AS role_name, session_user AS session_role,
  pg_backend_pid() AS backend_pid,
  to_char(a.backend_start AT TIME ZONE 'UTC','YYYY-MM-DD"T"HH24:MI:SS.US"Z"') AS backend_start,
  current_setting('application_name') AS application_name,
  current_setting('transaction_read_only') AS transaction_read_only,
  current_setting('statement_timeout') AS statement_timeout,
  (r.rolcanlogin AND NOT r.rolsuper AND NOT r.rolcreatedb AND NOT r.rolcreaterole
   AND NOT r.rolinherit AND NOT r.rolreplication AND NOT r.rolbypassrls
   AND r.rolconnlimit=2 AND current_user=session_user
   AND NOT EXISTS (SELECT 1 FROM pg_roles membership
      WHERE membership.oid<>r.oid AND pg_has_role(r.oid,membership.oid,'MEMBER'))
   AND has_database_privilege(r.oid,current_database(),'CONNECT')
   AND NOT has_database_privilege(r.oid,current_database(),'CREATE')
   AND NOT has_database_privilege(r.oid,current_database(),'TEMPORARY')
   AND has_schema_privilege(r.oid,'public','USAGE')
   AND NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname !~ '^pg_' AND
      nspname <> 'information_schema' AND has_schema_privilege(r.oid,oid,'CREATE'))
   AND has_table_privilege(r.oid,'public.alembic_version','SELECT') AND has_table_privilege(r.oid,'public.forward_artifacts','SELECT') AND has_table_privilege(r.oid,'public.forward_audit_events','SELECT') AND has_table_privilege(r.oid,'public.forward_data_quality_checks','SELECT') AND has_table_privilege(r.oid,'public.forward_datasets','SELECT') AND has_table_privilege(r.oid,'public.forward_forecasts','SELECT') AND has_table_privilege(r.oid,'public.forward_labels','SELECT') AND has_table_privilege(r.oid,'public.forward_models','SELECT') AND has_table_privilege(r.oid,'public.forward_runs','SELECT')
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','v','m','f')
      AND NOT (n.nspname='public' AND c.relname IN ('alembic_version','forward_artifacts','forward_audit_events','forward_data_quality_checks','forward_datasets','forward_forecasts','forward_labels','forward_models','forward_runs'))
      AND CASE WHEN c.relkind IN ('r','p','v','m','f')
         THEN has_any_column_privilege(r.oid,c.oid,'SELECT') ELSE false END)
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
    WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND c.relkind IN ('r','p','v','m','f')
      AND CASE WHEN c.relkind IN ('r','p','v','m','f') THEN
         (has_table_privilege(r.oid,c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER,MAINTAIN')
         OR has_any_column_privilege(r.oid,c.oid,'INSERT,UPDATE,REFERENCES')
         OR has_table_privilege(r.oid,c.oid,'SELECT WITH GRANT OPTION')
         OR EXISTS (SELECT 1 FROM pg_attribute attribute
            WHERE attribute.attrelid=c.oid AND attribute.attnum>0 AND NOT attribute.attisdropped
            AND has_column_privilege(r.oid,c.oid,attribute.attnum,'SELECT WITH GRANT OPTION')))
         ELSE false END)
   AND NOT EXISTS (SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
      WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND CASE WHEN c.relkind='S'
         THEN has_sequence_privilege(r.oid,c.oid,'USAGE,SELECT,UPDATE') ELSE false END)
   AND NOT EXISTS (SELECT 1 FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
      WHERE n.nspname !~ '^pg_' AND n.nspname <> 'information_schema' AND p.prosecdef AND has_function_privilege(r.oid,p.oid,'EXECUTE'))
   AND (SELECT sum(pg_total_relation_size(t::regclass)) FROM unnest(ARRAY['public.alembic_version','public.forward_artifacts','public.forward_audit_events','public.forward_data_quality_checks','public.forward_datasets','public.forward_forecasts','public.forward_labels','public.forward_models','public.forward_runs']) t) <= 10485760) AS permissions_ok
FROM pg_roles r JOIN pg_stat_activity a ON a.pid=pg_backend_pid()
WHERE r.rolname=current_user