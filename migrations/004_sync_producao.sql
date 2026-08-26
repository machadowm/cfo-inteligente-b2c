-- Migration 004: Sincronização completa com o schema de produção (banco local 2026-08-26)
-- Aplicar em bancos que já passaram pelas migrations 001-003.
-- Todas as operações são idempotentes (IF NOT EXISTS / IF EXISTS / OR REPLACE).

-- ============================================================
-- 1. motoristas — campos de metas estendidas + tipos maiores
-- ============================================================
ALTER TABLE public.motoristas
    ALTER COLUMN telefone TYPE character varying(50),
    ALTER COLUMN nome     TYPE character varying(150),
    ALTER COLUMN status_assinatura SET NOT NULL,
    ALTER COLUMN status_assinatura SET DEFAULT 'TRIAL',
    ALTER COLUMN meta_mensal_faturamento TYPE numeric(14,4);

ALTER TABLE public.motoristas
    ADD COLUMN IF NOT EXISTS meta_faturamento_diario  numeric(14,4) DEFAULT 0.00,
    ADD COLUMN IF NOT EXISTS meta_faturamento_semanal numeric(14,4) DEFAULT 0.00,
    ADD COLUMN IF NOT EXISTS meta_horas_diarias       numeric(5,2)  DEFAULT 8.00,
    ADD COLUMN IF NOT EXISTS meta_km_diarios          numeric(10,2) DEFAULT 0.00;

COMMENT ON COLUMN public.motoristas.meta_faturamento_diario  IS 'Meta de faturamento diário calculada ou definida pelo motorista.';
COMMENT ON COLUMN public.motoristas.meta_faturamento_semanal IS 'Meta de faturamento semanal calculada ou definida pelo motorista.';
COMMENT ON COLUMN public.motoristas.meta_horas_diarias       IS 'Meta de horas de trabalho por dia (padrão 8h).';
COMMENT ON COLUMN public.motoristas.meta_km_diarios          IS 'Meta de quilometragem diária de rodagem.';


-- ============================================================
-- 2. veiculos — flags escalares + tipos maiores
-- ============================================================
ALTER TABLE public.veiculos
    ALTER COLUMN placa           TYPE character varying(20),
    ALTER COLUMN modelo          TYPE character varying(150),
    ALTER COLUMN tipo_combustivel TYPE character varying(50),
    ALTER COLUMN contrato_personalizado DROP NOT NULL;

ALTER TABLE public.veiculos
    ADD COLUMN IF NOT EXISTS capacidade_bateria numeric(10,2) DEFAULT 0.00,
    ADD COLUMN IF NOT EXISTS is_flex            boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS qtd_tanques        integer DEFAULT 1,
    ADD COLUMN IF NOT EXISTS is_hibrido         boolean DEFAULT false,
    ADD COLUMN IF NOT EXISTS is_eletrico        boolean DEFAULT false;

COMMENT ON COLUMN public.veiculos.capacidade_bateria IS 'Capacidade nominal da bateria em kWh (0.00 para veículos não elétricos).';
COMMENT ON COLUMN public.veiculos.is_flex            IS 'Flag escalar de veículo flex — redundante com estoque_financeiro.meta, mantida para queries diretas.';
COMMENT ON COLUMN public.veiculos.is_hibrido         IS 'Flag escalar de veículo híbrido.';
COMMENT ON COLUMN public.veiculos.is_eletrico        IS 'Flag escalar de veículo elétrico puro.';


-- ============================================================
-- 3. transacoes — tanque_cheio, constraint valor >= 0
-- ============================================================
ALTER TABLE public.transacoes
    ALTER COLUMN litros_abastecidos TYPE numeric(10,2);

ALTER TABLE public.transacoes
    ADD COLUMN IF NOT EXISTS tanque_cheio boolean DEFAULT false NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'transacoes_valor_check' AND conrelid = 'public.transacoes'::regclass
    ) THEN
        ALTER TABLE public.transacoes
            ADD CONSTRAINT transacoes_valor_check CHECK (valor >= 0);
    END IF;
END;
$$;

COMMENT ON COLUMN public.transacoes.tanque_cheio IS 'Indica se o abastecimento foi até o tanque cheio — usado para calibração de consumo histórico.';


-- ============================================================
-- 4. fechamento_diario — provisao_descontada + turno nullable
-- ============================================================
ALTER TABLE public.fechamento_diario
    ALTER COLUMN turno_id DROP NOT NULL;

ALTER TABLE public.fechamento_diario
    ADD COLUMN IF NOT EXISTS provisao_descontada numeric(14,4) DEFAULT 0.00 NOT NULL;

COMMENT ON COLUMN public.fechamento_diario.provisao_descontada IS 'Valor descontado das caixas de provisão (manutenção, IPVA, etc.) no fechamento do dia.';

-- Recria FK de turno_id como SET NULL (era RESTRICT quando NOT NULL)
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'fechamento_diario_turno_id_fkey'
    ) THEN
        ALTER TABLE public.fechamento_diario DROP CONSTRAINT fechamento_diario_turno_id_fkey;
    END IF;
END;
$$;

ALTER TABLE ONLY public.fechamento_diario
    ADD CONSTRAINT fechamento_diario_turno_id_fkey
    FOREIGN KEY (turno_id) REFERENCES public.turnos(id) ON DELETE SET NULL;


-- ============================================================
-- 5. Nova tabela fechamentos_consolidados
-- ============================================================
CREATE TABLE IF NOT EXISTS public.fechamentos_consolidados (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    tipo_periodo character varying(20) NOT NULL,
    referencia character varying(20) NOT NULL,
    faturacao_bruta numeric(14,4) NOT NULL,
    custo_variavel_direto numeric(14,4) NOT NULL,
    custo_fixo_rateado numeric(14,4) NOT NULL,
    provisao_descontada numeric(14,4) DEFAULT 0.00 NOT NULL,
    lucro_liquido_real numeric(14,4) NOT NULL,
    km_rodados numeric(10,2) NOT NULL,
    horas_trabalhadas numeric(7,2) DEFAULT 0.00,
    status character varying(20) DEFAULT 'consolidado' NOT NULL,
    data_consolidacao timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);

ALTER TABLE ONLY public.fechamentos_consolidados
    ADD CONSTRAINT fechamentos_consolidados_pkey PRIMARY KEY (id)
    NOT VALID;
DO $$ BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fechamentos_consolidados_pkey') THEN
        ALTER TABLE ONLY public.fechamentos_consolidados ADD CONSTRAINT fechamentos_consolidados_pkey PRIMARY KEY (id);
    END IF;
END; $$;

CREATE UNIQUE INDEX IF NOT EXISTS idx_unico_fechamento_periodo
    ON public.fechamentos_consolidados (motorista_id, tipo_periodo, referencia);

CREATE INDEX IF NOT EXISTS idx_fechamentos_consolidados_motorista
    ON public.fechamentos_consolidados (motorista_id);

ALTER TABLE ONLY public.fechamentos_consolidados
    ADD CONSTRAINT fechamentos_consolidados_motorista_id_fkey
    FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT
    NOT VALID;


-- ============================================================
-- 6. Função e trigger de bloqueio de período contábil fechado
-- ============================================================
CREATE OR REPLACE FUNCTION public.function_verificar_periodo_fechado()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.fechamentos_consolidados
        WHERE motorista_id = COALESCE(NEW.motorista_id, OLD.motorista_id)
          AND tipo_periodo = 'diario'
          AND referencia = TO_CHAR(
              COALESCE(NEW.data_transacao, OLD.data_transacao) AT TIME ZONE 'America/Sao_Paulo',
              'YYYY-MM-DD'
          )
          AND status = 'consolidado'
    ) THEN
        RAISE EXCEPTION 'PERIODO_FECHADO: O período contábil já foi consolidado e não pode ser alterado.';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_trava_periodo_transacoes ON public.transacoes;
CREATE TRIGGER trg_trava_periodo_transacoes
    BEFORE INSERT OR UPDATE OR DELETE ON public.transacoes
    FOR EACH ROW EXECUTE FUNCTION public.function_verificar_periodo_fechado();


-- ============================================================
-- 7. Índices de performance novos
-- ============================================================
CREATE UNIQUE INDEX IF NOT EXISTS idx_unico_turno_aberto
    ON public.turnos (motorista_id)
    WHERE status IN ('em_andamento', 'em_pausa', 'ABERTO');

CREATE INDEX IF NOT EXISTS idx_transacoes_recalibracao_telemetria
    ON public.transacoes (veiculo_id, tanque_cheio, data_transacao)
    WHERE estornado = false AND categoria = 'combustivel';

CREATE INDEX IF NOT EXISTS idx_veiculos_estoque_gin ON public.veiculos USING gin (estoque_financeiro);
CREATE INDEX IF NOT EXISTS idx_veiculos_motorista    ON public.veiculos (motorista_id);
CREATE INDEX IF NOT EXISTS idx_turnos_motorista      ON public.turnos   (motorista_id);
CREATE INDEX IF NOT EXISTS idx_turnos_veiculo        ON public.turnos   (veiculo_id);
CREATE INDEX IF NOT EXISTS idx_transacoes_data       ON public.transacoes (data_transacao);
CREATE INDEX IF NOT EXISTS idx_transacoes_created    ON public.transacoes (created_at);
CREATE INDEX IF NOT EXISTS idx_fechamento_motorista  ON public.fechamento_diario (motorista_id);
CREATE INDEX IF NOT EXISTS idx_fechamento_turno      ON public.fechamento_diario (turno_id);


-- ============================================================
-- 8. RLS — habilitar e criar policies (idempotente)
-- ============================================================
ALTER TABLE public.fechamentos_consolidados ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_policies WHERE policyname = 'isolamento_fechamentos_macro' AND tablename = 'fechamentos_consolidados') THEN
        CREATE POLICY isolamento_fechamentos_macro ON public.fechamentos_consolidados
            USING (motorista_id = (NULLIF(current_setting('app.current_driver_id', true), ''))::uuid);
    END IF;
END;
$$;

-- Registra no log de auditoria LGPD
INSERT INTO public.lgpd_logs (motorista_id, acao_realizada, ip_origem)
SELECT id, 'APPLY_MIGRATION_004', '127.0.0.1'
FROM public.motoristas
LIMIT 1
ON CONFLICT DO NOTHING;
