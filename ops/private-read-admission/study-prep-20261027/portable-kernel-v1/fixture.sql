SELECT 'alembic_version' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "version_num"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."alembic_version" t UNION ALL SELECT 'forward_artifacts' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "artifact_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_artifacts" t UNION ALL SELECT 'forward_audit_events' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "audit_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_audit_events" t UNION ALL SELECT 'forward_data_quality_checks' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "check_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_data_quality_checks" t UNION ALL SELECT 'forward_datasets' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "dataset_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_datasets" t UNION ALL SELECT 'forward_forecasts' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "forecast_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_forecasts" t UNION ALL SELECT 'forward_labels' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "label_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_labels" t UNION ALL SELECT 'forward_models' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "model_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_models" t UNION ALL SELECT 'forward_runs' AS table_name, count(*)::int AS row_count,
    encode(sha256(convert_to(coalesce(string_agg(to_jsonb(t)::text,E'\n' ORDER BY "run_id"),''),'UTF8')),'hex')
    AS content_sha256 FROM public."forward_runs" t ORDER BY table_name