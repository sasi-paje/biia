-- =============================================================================
-- migrate_biia.sql — Migração do schema da tabela public.biia
--
-- Execute este SQL no Supabase SQL Editor:
--   Supabase Dashboard → SQL Editor → New Query → cole e execute
--
-- O que esta migração faz:
--   1. Garante que a extensão pgvector está ativa
--   2. Adiciona coluna `id` como chave primária (bigserial)
--   3. Migra `valor` (numeric) para dentro de `metadata` (jsonb)
--   4. Adiciona coluna `created_at` para auditoria
--   5. Cria índice GIN para buscas em metadata
--   6. Cria índice HNSW para busca vetorial eficiente
--
-- ATENÇÃO: Execute os passos em ordem. Os dados existentes são preservados.
-- =============================================================================

-- Passo 0: Garantir extensão pgvector ativa
CREATE EXTENSION IF NOT EXISTS vector SCHEMA extensions;

-- =============================================================================
-- Passo 1: Adicionar colunas novas (não destrutivo)
-- =============================================================================

-- Chave primária
ALTER TABLE public.biia ADD COLUMN IF NOT EXISTS id bigserial;

-- Metadata JSONB (vai absorver o campo `valor` e permitir outros atributos)
ALTER TABLE public.biia ADD COLUMN IF NOT EXISTS metadata jsonb DEFAULT '{}';

-- Timestamp de criação
ALTER TABLE public.biia ADD COLUMN IF NOT EXISTS created_at timestamptz DEFAULT now();

-- =============================================================================
-- Passo 2: Migrar dados do campo `valor` para `metadata`
-- =============================================================================

-- Copia o valor numérico existente para dentro do jsonb metadata
UPDATE public.biia
SET metadata = jsonb_build_object('valor', valor)
WHERE valor IS NOT NULL
  AND (metadata IS NULL OR metadata = '{}');

-- =============================================================================
-- Passo 3: Definir id como PRIMARY KEY
-- =============================================================================

DO $$
BEGIN
  -- Cria sequência se id ainda não tem default serial
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'public'
      AND table_name   = 'biia'
      AND column_name  = 'id'
      AND column_default LIKE 'nextval%'
  ) THEN
    CREATE SEQUENCE IF NOT EXISTS public.biia_id_seq;
    UPDATE public.biia SET id = nextval('public.biia_id_seq') WHERE id IS NULL;
  END IF;
END $$;

-- Adiciona PRIMARY KEY se ainda não existir
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.table_constraints
    WHERE table_schema    = 'public'
      AND table_name      = 'biia'
      AND constraint_type = 'PRIMARY KEY'
  ) THEN
    ALTER TABLE public.biia ADD PRIMARY KEY (id);
  END IF;
END $$;

-- =============================================================================
-- Passo 4: Garantir NOT NULL nas colunas críticas
-- =============================================================================

ALTER TABLE public.biia ALTER COLUMN item       SET NOT NULL;
ALTER TABLE public.biia ALTER COLUMN vetorizada SET NOT NULL;

-- =============================================================================
-- Passo 5: Índices
-- =============================================================================

-- Índice GIN para buscas e filtros dentro de metadata
CREATE INDEX IF NOT EXISTS biia_metadata_gin_idx
  ON public.biia USING gin(metadata);

-- Índice HNSW para busca vetorial aproximada (recomendado para > 1.000 registros)
CREATE INDEX IF NOT EXISTS biia_vetorizada_hnsw_idx
  ON public.biia
  USING hnsw (vetorizada vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

-- =============================================================================
-- Passo 6: (Opcional) Remover coluna `valor` após confirmar migração
-- =============================================================================
-- Só execute esta linha APÓS verificar que metadata está correto:
--
--   SELECT id, item, valor, metadata FROM public.biia LIMIT 10;
--
-- Se os dados estiverem corretos em metadata, então:
--
--   ALTER TABLE public.biia DROP COLUMN IF EXISTS valor;
--
-- =============================================================================

-- Verificação final
SELECT
  column_name,
  data_type,
  column_default,
  is_nullable
FROM information_schema.columns
WHERE table_schema = 'public'
  AND table_name   = 'biia'
ORDER BY ordinal_position;
