-- =============================================================================
-- Migração v20 — Controle de Ciclo de Vida de Provisão (Despesas Parceladas)
-- =============================================================================
-- Adiciona suporte a despesas com término determinado (parcelamentos) e
-- despesas futuras únicas à tabela public.despesas_fixas_mensais.
--
-- Novas colunas:
--   parcelas_totais  INTEGER NULL DEFAULT NULL
--       NULL  → despesa perpétua/recorrente (comportamento anterior — retrocompat)
--       1     → despesa única (ex: boleto de manutenção única)
--       N > 1 → parcelamento em N vezes
--
--   parcelas_pagas  INTEGER NOT NULL DEFAULT 0
--       Contador incremental atualizado pelo ReminderService a cada baixa
--       automática confirmada. Quando parcelas_pagas = parcelas_totais,
--       a despesa é auto-desativada (ativo = FALSE).
--
--   data_inicio  DATE NULL DEFAULT NULL
--       Data a partir da qual a despesa começa a ser cobrada no DRE e a
--       gerar alertas. NULL = começa imediatamente (desde o cadastro).
--       Usado para despesas futuras: ex: "seguro começa a vencer dia 15/10".
--
-- Lógica de exaustão (aplicada no TurnoService e ReminderService):
--   • Perpetuo: parcelas_totais IS NULL → sempre cobra
--   • Em andamento: parcelas_pagas < parcelas_totais → cobra
--   • Exausto: parcelas_pagas >= parcelas_totais → NÃO cobra, ativo = FALSE
--
-- Retrocompatibilidade:
--   • Todas as despesas existentes recebem parcelas_totais = NULL e
--     parcelas_pagas = 0 por DEFAULT — comportamento 100% idêntico ao atual.
--   • A coluna data_inicio = NULL significa "vigente desde sempre".
--
-- Idempotente: usa ADD COLUMN IF NOT EXISTS e ADD CONSTRAINT IF NOT EXISTS.
-- =============================================================================

BEGIN;

-- ── 1. parcelas_totais ────────────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'despesas_fixas_mensais'
          AND column_name  = 'parcelas_totais'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD COLUMN parcelas_totais integer DEFAULT NULL;
    END IF;
END $$;

-- ── 2. parcelas_pagas ─────────────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'despesas_fixas_mensais'
          AND column_name  = 'parcelas_pagas'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD COLUMN parcelas_pagas integer NOT NULL DEFAULT 0;
    END IF;
END $$;

-- ── 3. data_inicio ────────────────────────────────────────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'despesas_fixas_mensais'
          AND column_name  = 'data_inicio'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD COLUMN data_inicio date DEFAULT NULL;
    END IF;
END $$;

-- ── 4. Constraints de integridade ─────────────────────────────────────────────
-- parcelas_totais deve ser positivo quando informado
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'despesas_fixas_mensais_parcelas_totais_check'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD CONSTRAINT despesas_fixas_mensais_parcelas_totais_check
            CHECK (parcelas_totais IS NULL OR parcelas_totais >= 1);
    END IF;
END $$;

-- parcelas_pagas nunca pode ser negativo
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'despesas_fixas_mensais_parcelas_pagas_check'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD CONSTRAINT despesas_fixas_mensais_parcelas_pagas_check
            CHECK (parcelas_pagas >= 0);
    END IF;
END $$;

-- ── 5. Índice parcial para monitoramento de despesas finitas ativas ───────────
-- Usado pelo ReminderService e TurnoService para localizar despesas em
-- andamento com contador de parcelas sem varrer o ledger completo.
CREATE INDEX IF NOT EXISTS idx_despesas_fixas_parceladas
    ON public.despesas_fixas_mensais (motorista_id, parcelas_pagas, parcelas_totais)
    WHERE ativo = TRUE AND parcelas_totais IS NOT NULL;

COMMIT;

-- =============================================================================
-- Como usar:
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c \
--     < migracao-v20-parcelas-ciclo-vida.sql
-- =============================================================================
