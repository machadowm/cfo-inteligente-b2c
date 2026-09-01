-- =============================================================================
-- Migração v14 — Múltiplos vencimentos por despesa fixa
-- =============================================================================
-- Substitui dia_vencimento INTEGER por dias_vencimento INTEGER[].
-- Permite despesas que vencem em mais de um dia do mês
-- (ex: cartão vence dias 5 e 20; IPVA em meses alternados via parcelas).
--
-- Passos:
--   1. Adiciona coluna nova dias_vencimento INTEGER[] NOT NULL DEFAULT '{1}'
--   2. Copia dia_vencimento existente para o array
--   3. Remove coluna antiga
--
-- Uso:
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c < migracao-v14-multiplos-vencimentos.sql
-- =============================================================================

BEGIN;

-- 1. Nova coluna (nullable temporariamente para permitir o backfill)
ALTER TABLE public.despesas_fixas_mensais
    ADD COLUMN IF NOT EXISTS dias_vencimento integer[]
        DEFAULT ARRAY[1]::integer[];

-- 2. Backfill — converte inteiro → array de um elemento
UPDATE public.despesas_fixas_mensais
SET dias_vencimento = ARRAY[dia_vencimento]
WHERE dias_vencimento IS NULL
   OR dias_vencimento = '{}';

-- 3. Garante NOT NULL após backfill
ALTER TABLE public.despesas_fixas_mensais
    ALTER COLUMN dias_vencimento SET NOT NULL,
    ALTER COLUMN dias_vencimento SET DEFAULT ARRAY[1]::integer[];

-- 4. Remove coluna antiga
ALTER TABLE public.despesas_fixas_mensais
    DROP COLUMN IF EXISTS dia_vencimento;

-- Log LGPD
INSERT INTO public.lgpd_logs (motorista_id, acao_realizada, ip_origem, data_evento)
VALUES (
    '00000000-0000-0000-0000-000000000000',
    'migracao_v14_multiplos_vencimentos',
    'migration_script',
    NOW()
);

COMMIT;
