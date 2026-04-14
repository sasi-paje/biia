-- =============================================================================
-- match_biia — Recria a função RPC no schema public
--
-- Execute este SQL no Supabase SQL Editor:
--   Supabase Dashboard → SQL Editor → New Query → cole e execute
-- =============================================================================

-- Drop direto por assinatura exata (sem bloco DO, evita ambiguidade de oid)
DROP FUNCTION IF EXISTS public.match_biia(public.vector, int, double precision, jsonb);
DROP FUNCTION IF EXISTS public.match_biia(public.vector, int, float, jsonb);
DROP FUNCTION IF EXISTS public.match_biia(public.vector, int);
DROP FUNCTION IF EXISTS public.match_biia(extensions.vector, int, double precision, jsonb);
DROP FUNCTION IF EXISTS public.match_biia(extensions.vector, int, float, jsonb);
DROP FUNCTION IF EXISTS public.match_biia(extensions.vector, int);

CREATE OR REPLACE FUNCTION public.match_biia(
  query_embedding      vector,
  match_count          int              DEFAULT 5,
  similarity_threshold double precision DEFAULT 0.0,
  filter               jsonb            DEFAULT '{}'
)
RETURNS TABLE (
  id          bigint,
  item        text,
  metadata    jsonb,
  similarity  double precision
)
LANGUAGE plpgsql
AS $$
BEGIN
  RETURN QUERY
  SELECT
    b.id,
    b.item::text,
    b.metadata,
    (1 - (b.vetorizada <=> query_embedding))::double precision AS similarity
  FROM public.biia b
  WHERE
    (1 - (b.vetorizada <=> query_embedding)) >= similarity_threshold
    AND (filter = '{}'::jsonb OR b.metadata @> filter)
  ORDER BY b.vetorizada <=> query_embedding
  LIMIT match_count;
END;
$$;

-- Verificação: deve retornar a função com os novos tipos
SELECT
  p.proname                        AS function_name,
  pg_get_function_arguments(p.oid) AS arguments,
  pg_get_function_result(p.oid)    AS return_type
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE p.proname = 'match_biia'
  AND n.nspname = 'public';
