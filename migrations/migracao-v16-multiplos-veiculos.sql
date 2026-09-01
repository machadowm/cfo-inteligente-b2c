-- =============================================================================
-- Migração v16 — Suporte a múltiplos veículos por motorista
-- =============================================================================
-- Adiciona coluna `selecionado boolean DEFAULT false` na tabela public.veiculos.
-- Um índice único parcial garante que apenas UM veículo ativo por motorista
-- pode ter selecionado = TRUE simultaneamente, sem precisar de trigger.
--
-- Backfill: marca como selecionado o veículo ativo mais recente de cada motorista
-- (mesmo critério do ORDER BY created_at DESC LIMIT 1 vigente), preservando
-- o comportamento existente para todos os motoristas com um único veículo.
--
-- Idempotente: todos os comandos usam IF NOT EXISTS / ON CONFLICT safe.
-- =============================================================================

BEGIN;

-- 1. Adiciona coluna (idempotente via DO $$)
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'veiculos'
          AND column_name  = 'selecionado'
    ) THEN
        ALTER TABLE public.veiculos ADD COLUMN selecionado boolean NOT NULL DEFAULT false;
    END IF;
END $$;

-- 2. Índice único parcial: garante exclusividade de selecionado=TRUE por motorista.
--    A constraint é aplicada apenas quando ativo=TRUE AND selecionado=TRUE,
--    permitindo que veículos inativos (desativados) mantenham o flag sem conflito.
CREATE UNIQUE INDEX IF NOT EXISTS idx_veiculo_selecionado_unico
    ON public.veiculos (motorista_id)
    WHERE (ativo = TRUE AND selecionado = TRUE);

-- 3. Backfill: para cada motorista, seleciona o veículo ativo mais recente.
--    Respeita o critério ORDER BY created_at DESC LIMIT 1 que o sistema usa hoje.
UPDATE public.veiculos v
SET selecionado = TRUE
WHERE v.id IN (
    SELECT DISTINCT ON (motorista_id) id
    FROM public.veiculos
    WHERE ativo = TRUE
    ORDER BY motorista_id, created_at DESC
);

COMMIT;
