SELECT encode(sha256(convert_to(coalesce(
  string_agg(value::text,E'\n' ORDER BY kind COLLATE "C",key COLLATE "C"),''),'UTF8')),'hex') AS schema_sha256
FROM (
  SELECT 'relation' AS kind,n.nspname||'.'||c.relname AS key,
    jsonb_build_array(n.nspname,c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity) AS value
  FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f','S')
  UNION ALL
  SELECT 'column',n.nspname||'.'||c.relname||'.'||a.attnum,
    jsonb_build_array(n.nspname,c.relname,a.attnum,a.attname,format_type(a.atttypid,a.atttypmod),
      a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid))
  FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace
    LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum
  WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND c.relkind IN ('r','p','v','m','f') AND a.attnum>0 AND NOT a.attisdropped
  UNION ALL
  SELECT 'constraint',n.nspname||'.'||c.relname||'.'||p.conname,
    jsonb_build_array(n.nspname,c.relname,p.conname,p.contype,p.convalidated,pg_get_constraintdef(p.oid,false))
  FROM pg_constraint p JOIN pg_class c ON c.oid=p.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
  UNION ALL
  SELECT 'index',n.nspname||'.'||c.relname||'.'||i.indexrelid::regclass::text,
    jsonb_build_array(n.nspname,c.relname,pg_get_indexdef(i.indexrelid),i.indisvalid,i.indisready)
  FROM pg_index i JOIN pg_class c ON c.oid=i.indrelid JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
  UNION ALL
  SELECT 'trigger',n.nspname||'.'||c.relname||'.'||t.tgname,
    jsonb_build_array(n.nspname,c.relname,t.tgname,t.tgenabled,pg_get_triggerdef(t.oid,false))
  FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema' AND NOT t.tgisinternal
  UNION ALL
  SELECT 'policy',n.nspname||'.'||c.relname||'.'||p.polname,
    jsonb_build_array(n.nspname,c.relname,p.polname,p.polcmd,p.polpermissive,p.polroles,
      pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid))
  FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace
  WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
  UNION ALL
  SELECT 'function',n.nspname||'.'||p.proname||'('||pg_get_function_identity_arguments(p.oid)||')',
    jsonb_build_array(n.nspname,p.proname,p.prokind,p.prosecdef,p.provolatile,p.proleakproof,p.proconfig,
      CASE WHEN p.prokind IN ('f','p') THEN pg_get_functiondef(p.oid) ELSE p.prokind::text END)
  FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
) definitions