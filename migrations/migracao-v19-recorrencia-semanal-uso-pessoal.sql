-- =============================================================================
-- Migração v19 — Recorrência Semanal de Despesas + Uso Pessoal Contábil
-- =============================================================================
--
-- Mudança 1: despesas_fixas_mensais
--   • recorrencia_tipo VARCHAR(10) DEFAULT 'mensal' NOT NULL
--     Valores: 'mensal' | 'semanal'
--     Controla como dias_vencimento é interpretado:
--       mensal  → dias_vencimento = dias do mês  (ex: {5, 20})
--       semanal → dias_semana     = ISO weekday  (ex: {1} = segunda, {5} = sexta)
--
--   • dias_semana INTEGER[] DEFAULT NULL
--     Usado apenas quando recorrencia_tipo = 'semanal'.
--     Valores ISO-8601: 1=Segunda, 2=Terça, ... 7=Domingo.
--     NULL para despesas mensais (a separação evita colisão com dias do mês 1-7).
--
-- Mudança 2: transacoes
--   • contexto_operacional já existe como VARCHAR(50) nullable — nunca populado.
--     Esta migração adiciona uma CHECK constraint para documentar os valores
--     válidos e indexar o campo, sem alterar o tipo ou quebrar dados existentes.
--
-- Ambas as mudanças são idempotentes (IF NOT EXISTS / IF NOT EXISTS).
-- =============================================================================

BEGIN;

-- ── 1. Adiciona recorrencia_tipo em despesas_fixas_mensais ────────────────────
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'despesas_fixas_mensais'
          AND column_name  = 'recorrencia_tipo'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD COLUMN recorrencia_tipo character varying(10) NOT NULL DEFAULT 'mensal';
    END IF;
END $$;

-- Constraint de domínio: garante que apenas valores válidos sejam inseridos
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'despesas_fixas_mensais_recorrencia_tipo_check'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD CONSTRAINT despesas_fixas_mensais_recorrencia_tipo_check
            CHECK (recorrencia_tipo IN ('mensal', 'semanal'));
    END IF;
END $$;

-- ── 2. Adiciona dias_semana em despesas_fixas_mensais ─────────────────────────
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name   = 'despesas_fixas_mensais'
          AND column_name  = 'dias_semana'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD COLUMN dias_semana integer[] DEFAULT NULL;
    END IF;
END $$;

-- Constraint: cada elemento de dias_semana deve ser 1-7 (ISO weekday)
-- Usa o operador <@ ("está contido em") — sem subquery, compatível com CHECK.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'despesas_fixas_mensais_dias_semana_check'
    ) THEN
        ALTER TABLE public.despesas_fixas_mensais
            ADD CONSTRAINT despesas_fixas_mensais_dias_semana_check
            CHECK (
                dias_semana IS NULL
                OR (
                    array_length(dias_semana, 1) > 0
                    AND dias_semana <@ ARRAY[1,2,3,4,5,6,7]::integer[]
                )
            );
    END IF;
END $$;

-- Índice de suporte para o ReminderService filtrar por recorrencia_tipo
CREATE INDEX IF NOT EXISTS idx_despesas_fixas_recorrencia
    ON public.despesas_fixas_mensais (recorrencia_tipo)
    WHERE ativo = TRUE;

-- ── 3. Documenta e indexa contexto_operacional em transacoes ─────────────────
-- O campo já existe. Adicionamos constraint de valores permitidos e índice parcial
-- para que a query de Uso Pessoal no ProfileService rode por Index Scan.
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'transacoes_contexto_operacional_check'
    ) THEN
        ALTER TABLE public.transacoes
            ADD CONSTRAINT transacoes_contexto_operacional_check
            CHECK (
                contexto_operacional IS NULL
                OR contexto_operacional IN (
                    'uso_pessoal',     -- lançamento fora de turno (pessoal/familiar)
                    'operacional',     -- lançamento dentro de turno (default implícito)
                    'contrato',        -- despesa contratual automática
                    'provisao'         -- aporte de caixinha
                )
            );
    END IF;
END $$;

-- Índice parcial: suporta queries de Uso Pessoal sem varrer o ledger inteiro
CREATE INDEX IF NOT EXISTS idx_transacoes_uso_pessoal
    ON public.transacoes (motorista_id, data_transacao)
    WHERE contexto_operacional = 'uso_pessoal' AND estornado = FALSE;

COMMIT;

-- =============================================================================
-- Como usar:
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c \ < migracao-v19-recorrencia-semanal-uso-pessoal.sql
-- =============================================================================
