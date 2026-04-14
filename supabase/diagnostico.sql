-- =============================================================================
-- diagnostico.sql — Execute no Supabase SQL Editor para ver o estado atual
-- =============================================================================

-- 1. Colunas da tabela public.biia
SELECT column_name, data_type, is_nullable, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'biia'
ORDER BY ordinal_position;

-- 2. Funções match_biia existentes no schema public (todas as sobrecargas)
SELECT
  p.proname                          AS function_name,
  pg_get_function_arguments(p.oid)   AS arguments,
  pg_get_function_result(p.oid)      AS return_type
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.proname  = 'match_biia'
  AND n.nspname  = 'public';

-- 3. Primeiras 3 linhas da tabela
SELECT * FROM public.biia LIMIT 3;
