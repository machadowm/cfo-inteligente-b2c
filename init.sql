-- ====================================================================================
-- CFO INTELIGENTE B2C - SCHEMA FÍSICO MASTER DEFINITIVO (VERSÃO CONSOLIDADA V5)
-- Nível: SRE / Arquiteto & DBA Sênior Enterprise (Bank-Grade)
-- Alinhado com: banco_sobrinho.txt, backup_db.txt, schema_limpo.sql e Onboarding Conversacional
-- ====================================================================================

-- 1. Extensões e Esquemas Globais
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE SCHEMA IF NOT EXISTS evolution; -- Schema isolado para o gateway Evolution API

-- 2. Função Universal de Auto-Update de Timestamps
CREATE OR REPLACE FUNCTION public.update_timestamp_func()
RETURNS TRIGGER AS $$
BEGIN
    BEGIN
        NEW.updated_at = CURRENT_TIMESTAMP;
    EXCEPTION WHEN undefined_column THEN
        -- Ignora se a coluna não existir na tabela
    END;

    BEGIN
        NEW.atualizado_em = CURRENT_TIMESTAMP;
    EXCEPTION WHEN undefined_column THEN
        -- Ignora se a coluna não existir na tabela
    END;

    BEGIN
        NEW.ultima_atualizacao = CURRENT_TIMESTAMP;
    EXCEPTION WHEN undefined_column THEN
        -- Ignora se a coluna não existir na tabela
    END;

    RETURN NEW;
END;
$$ LANGUAGE plpgsql;


-- ====================================================================================
-- DOMÍNIO 1: USUÁRIOS, ASSINATURAS & ONBOARDING CONVERSACIONAL
-- ====================================================================================

-- Tabela 1: motoristas (Tenant Principal)
CREATE TABLE IF NOT EXISTS public.motoristas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telefone VARCHAR(50) UNIQUE NOT NULL,
    nome VARCHAR(150) NOT NULL,
    status_assinatura VARCHAR(32) NOT NULL DEFAULT 'TRIAL',
    ativo BOOLEAN DEFAULT TRUE,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Configurações e metas financeiras base
    meta_mensal_faturamento NUMERIC(14,4) DEFAULT 12000.00,
    dias_uteis_mes INT DEFAULT 26,
    
    -- Últimas implantações: dados de onboarding tático conversacional ( celular / fricção zero )
    nome_social VARCHAR(100), -- "Como deseja ser chamado"
    meta_faturamento_diario NUMERIC(14,4) DEFAULT 0.00,
    meta_faturamento_semanal NUMERIC(14,4) DEFAULT 0.00,
    meta_horas_diarias NUMERIC(5,2) DEFAULT 8.00, -- "Meta de horas de trabalho diária"
    meta_km_diarios NUMERIC(10,2) DEFAULT 0.00, -- "Kms rodados previstos"
    possui_multiplos_veiculos BOOLEAN DEFAULT FALSE -- "Se possui mais de um veículo"
);

CREATE TRIGGER trig_motoristas_atualizado_em
    BEFORE UPDATE ON public.motoristas
    FOR EACH ROW EXECUTE FUNCTION public.update_timestamp_func();


-- Tabela 2: assinaturas (Billing SaaS B2C)
CREATE TABLE IF NOT EXISTS public.assinaturas (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES public.motoristas(id) ON DELETE RESTRICT,
    gateway_id VARCHAR(100),
    plano VARCHAR(50) NOT NULL,
    data_vencimento DATE NOT NULL,
    status VARCHAR(20) NOT NULL,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- ====================================================================================
-- DOMÍNIO 2: ATIVOS (FROTAS MULTI-COMBUSTÍVEL / LOCADORAS)
-- ====================================================================================

-- Tabela 3: veiculos (Ativos Multi-energia e Parâmetros de Onboarding de Frota)
CREATE TABLE IF NOT EXISTS public.veiculos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES public.motoristas(id) ON DELETE RESTRICT,
    placa VARCHAR(20) NOT NULL,
    modelo VARCHAR(150) NOT NULL,
    tipo_combustivel VARCHAR(50) NOT NULL, -- Híbrido, Elétrico, Flex, etc.
    estoque_financeiro JSONB DEFAULT '{"liquido": {"litros": 0.0, "custo_total": 0.0, "gasolina_litros": 0.0, "etanol_litros": 0.0, "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0, "km_l_gasolina": 12.0, "km_l_etanol": 8.5}, "eletricidade": {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5}}'::jsonb,
    ativo BOOLEAN DEFAULT TRUE,
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Dados contratuais da locadora (Zarp, etc.)
    locadora VARCHAR(100) DEFAULT 'Localiza Zarp',
    custo_aluguel_semanal NUMERIC(10,2) DEFAULT 1020.85,
    franquia_km_semanal NUMERIC(10,2) DEFAULT 1505.00,
    valor_km_excedente NUMERIC(10,4) DEFAULT 0.75,
    escala_trabalho VARCHAR(100) DEFAULT 'De quarta a segunda (6 dias)',
    contrato_personalizado BOOLEAN DEFAULT FALSE,
    
    -- Últimas implantações: parâmetros técnicos e físicos de combustão/carga coletados no onboarding
    capacidade_tanque NUMERIC(10,2) DEFAULT 50.00,
    capacidade_bateria NUMERIC(10,2) DEFAULT 30.00, -- Adicionado para carros Híbridos e Elétricos com fallback
    is_flex BOOLEAN DEFAULT FALSE,
    qtd_tanques INT DEFAULT 1,
    is_hibrido BOOLEAN DEFAULT FALSE,
    is_eletrico BOOLEAN DEFAULT FALSE
);


-- Tabela 4: turnos (Jornadas Operacionais)
CREATE TABLE IF NOT EXISTS public.turnos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES public.motoristas(id) ON DELETE RESTRICT,
    veiculo_id UUID NOT NULL REFERENCES public.veiculos(id) ON DELETE RESTRICT,
    km_inicial NUMERIC(10,2) NOT NULL,
    km_final NUMERIC(10,2) CHECK (km_final IS NULL OR km_final >= km_inicial),
    data_inicio TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    data_fim TIMESTAMP WITH TIME ZONE,
    status VARCHAR(20) DEFAULT 'em_andamento', -- 'em_andamento', 'em_pausa', 'concluido'
    
    -- Auditoria de Fuga de Hodômetro (km de uso pessoal)
    km_uso_pessoal NUMERIC(10,2) DEFAULT 0.00
);


-- Tabela 5: pausas_turno (Intervalos e Eficiência Logística)
CREATE TABLE IF NOT EXISTS public.pausas_turno (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    turno_id UUID NOT NULL REFERENCES public.turnos(id) ON DELETE CASCADE,
    inicio_pausa TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    fim_pausa TIMESTAMP WITH TIME ZONE,
    motivo VARCHAR(50)
);


-- ====================================================================================
-- DOMÍNIO 3: O NÚCLEO CONTÁBIL (LEDGER COMPLETO)
-- ====================================================================================

-- Tabela 6: transacoes (Livro Append-Only com Idempotência e Suporte a Descrições)
CREATE TABLE IF NOT EXISTS public.transacoes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES public.motoristas(id) ON DELETE RESTRICT,
    turno_id UUID REFERENCES public.turnos(id) ON DELETE SET NULL,
    veiculo_id UUID REFERENCES public.veiculos(id) ON DELETE RESTRICT,
    tipo_movimentacao VARCHAR(20) NOT NULL CHECK (tipo_movimentacao IN ('receita', 'despesa', 'neutro')),
    categoria VARCHAR(50) NOT NULL,
    valor NUMERIC(14,4) NOT NULL CHECK (valor > 0),
    estabelecimento VARCHAR(100),
    metodo_pagamento VARCHAR(50),
    contexto_operacional VARCHAR(50),
    comprovante_url VARCHAR(255),
    idempotencia_hash VARCHAR(100) UNIQUE,
    estornado BOOLEAN DEFAULT FALSE,
    data_transacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    
    -- Controle de auditoria textual e idempotência Evolution/WhatsApp
    descricao TEXT,
    wpp_msg_id VARCHAR(255) UNIQUE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- Tabela 7: despesas_fixas_mensais (Rateio Diário Contínuo)
CREATE TABLE IF NOT EXISTS public.despesas_fixas_mensais (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES motoristas(id) ON DELETE RESTRICT,
    nome VARCHAR(50) NOT NULL,
    valor_mensal NUMERIC(14,4) NOT NULL,
    dias_trabalho_previstos INTEGER NOT NULL CHECK (dias_trabalho_previstos > 0),
    valor_pro_rata_diario NUMERIC(14,4) GENERATED ALWAYS AS (valor_mensal / NULLIF(dias_trabalho_previstos, 0)) STORED,
    dia_vencimento INTEGER NOT NULL,
    ativo BOOLEAN DEFAULT TRUE
);


-- Tabela 8: caixas_provisao (Provisões de Manutenção e Amortização de Caixa)
CREATE TABLE IF NOT EXISTS public.caixas_provisao (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES public.motoristas(id) ON DELETE RESTRICT,
    nome_caixa VARCHAR(50) NOT NULL,
    saldo_atual NUMERIC(14,4) DEFAULT 0.00,
    ultima_atualizacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE TRIGGER trig_caixas_atualizacao
    BEFORE UPDATE ON public.caixas_provisao
    FOR EACH ROW EXECUTE FUNCTION public.update_timestamp_func();


-- Tabela 9: fechamento_diario (Consolidação de Margens Contábeis e DRE Real)
CREATE TABLE IF NOT EXISTS public.fechamento_diario (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES public.motoristas(id) ON DELETE RESTRICT,
    turno_id UUID REFERENCES public.turnos(id) ON DELETE SET NULL,
    faturamento_bruto NUMERIC(14,4) NOT NULL,
    custo_variavel_direto NUMERIC(14,4) NOT NULL,
    custo_fixo_rateado NUMERIC(14,4) NOT NULL,
    lucro_liquido_real NUMERIC(14,4) NOT NULL,
    km_rodados NUMERIC(10,2) NOT NULL,
    clima_predominante VARCHAR(30),
    data_fechamento DATE DEFAULT CURRENT_DATE,
    
    -- Despesa de provisão descontada automaticamente do caixa
    provisao_descontada NUMERIC(14,4) NOT NULL DEFAULT 0.00
);


-- Tabela 10: fechamentos_consolidados (OLAP interno para DRE consolidada imutável)
CREATE TABLE IF NOT EXISTS public.fechamentos_consolidados (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL REFERENCES public.motoristas(id) ON DELETE RESTRICT,
    tipo_periodo VARCHAR(10) NOT NULL CHECK (tipo_periodo IN ('MENSAL', 'ANUAL')),
    referencia VARCHAR(7) NOT NULL, -- Formato ISO: 'YYYY-MM' ou 'YYYY'
    faturacao_bruta NUMERIC(14,4) NOT NULL DEFAULT 0.00,
    custo_variavel_direto NUMERIC(14,4) NOT NULL DEFAULT 0.00,
    custo_fixo_rateado NUMERIC(14,4) NOT NULL DEFAULT 0.00,
    provisao_descontada NUMERIC(14,4) NOT NULL DEFAULT 0.00,
    lucro_liquido_real NUMERIC(14,4) NOT NULL DEFAULT 0.00,
    km_rodados NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    horas_trabalhadas NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    status VARCHAR(20) NOT NULL DEFAULT 'FECHADO',
    data_consolidacao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_unico_fechamento_periodo 
    ON public.fechamentos_consolidados(motorista_id, tipo_periodo, referencia);


-- ====================================================================================
-- DOMÍNIO 4: GOVERNANÇA, ALARMES E HISTÓRICO DE MANUTENÇÃO
-- ====================================================================================

-- Tabela 11: regras_manutencao (Configuração de Alarmes de Amortização)
CREATE TABLE IF NOT EXISTS public.regras_manutencao (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    veiculo_id UUID NOT NULL REFERENCES public.veiculos(id) ON DELETE CASCADE,
    tipo_servico VARCHAR(100) NOT NULL,
    intervalo_km INTEGER NOT NULL,
    aviso_previo_km INTEGER NOT NULL DEFAULT 500,
    ativo BOOLEAN DEFAULT TRUE
);


-- Tabela 12: historico_manutencao (Auditoria de Intervenções Técnicas)
CREATE TABLE IF NOT EXISTS public.historico_manutencao (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    veiculo_id UUID NOT NULL REFERENCES public.veiculos(id) ON DELETE RESTRICT,
    regra_id UUID REFERENCES public.regras_manutencao(id) ON DELETE SET NULL,
    transacao_id UUID REFERENCES public.transacoes(id) ON DELETE RESTRICT,
    km_execucao NUMERIC(10,2) NOT NULL,
    data_execucao TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- Tabela 13: dlq_eventos (Dead Letter Queue para contingência de Webhooks)
CREATE TABLE IF NOT EXISTS public.dlq_eventos (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID REFERENCES public.motoristas(id) ON DELETE CASCADE,
    payload_original JSONB NOT NULL,
    motivo_falha TEXT NOT NULL,
    tentativas INTEGER DEFAULT 1,
    status VARCHAR(20) DEFAULT 'pendente',
    criado_em TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- Tabela 14: lgpd_logs (Trilha de Consentimento e Privacidade)
CREATE TABLE IF NOT EXISTS public.lgpd_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    motorista_id UUID NOT NULL,
    acao_realizada VARCHAR(50) NOT NULL,
    ip_origem VARCHAR(50),
    data_evento TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);


-- ====================================================================================
-- ÍNDICES DE PERFORMANCE (PREVENÇÃO DE SEQUENTIAL SCANS SOB RLS MULTI-TENANT)
-- ====================================================================================
CREATE INDEX IF NOT EXISTS idx_assinaturas_motorista ON public.assinaturas(motorista_id);
CREATE INDEX IF NOT EXISTS idx_veiculos_motorista ON public.veiculos(motorista_id);
CREATE INDEX IF NOT EXISTS idx_turnos_motorista ON public.turnos(motorista_id);
CREATE INDEX IF NOT EXISTS idx_turnos_veiculo ON public.turnos(veiculo_id);
CREATE INDEX IF NOT EXISTS idx_pausas_turno ON public.pausas_turno(turno_id);
CREATE INDEX IF NOT EXISTS idx_transacoes_motorista ON public.transacoes(motorista_id);
CREATE INDEX IF NOT EXISTS idx_transacoes_turno ON public.transacoes(turno_id);
CREATE INDEX IF NOT EXISTS idx_transacoes_veiculo ON public.transacoes(veiculo_id);
CREATE INDEX IF NOT EXISTS idx_despesas_fixas_motorista ON public.despesas_fixas_mensais(motorista_id);
CREATE INDEX IF NOT EXISTS idx_caixas_provisao_motorista ON public.caixas_provisao(motorista_id);
CREATE INDEX IF NOT EXISTS idx_fechamento_motorista ON public.fechamento_diario(motorista_id);
CREATE INDEX IF NOT EXISTS idx_fechamento_turno ON public.fechamento_diario(turno_id);
CREATE INDEX IF NOT EXISTS idx_regras_veiculo ON public.regras_manutencao(veiculo_id);
CREATE INDEX IF NOT EXISTS idx_historico_veiculo ON public.historico_manutencao(veiculo_id);
CREATE INDEX IF NOT EXISTS idx_dlq_motorista ON public.dlq_eventos(motorista_id);
CREATE INDEX IF NOT EXISTS idx_lgpd_motorista ON public.lgpd_logs(motorista_id);
CREATE INDEX IF NOT EXISTS idx_fechamentos_consolidados_motorista ON public.fechamentos_consolidados(motorista_id);

-- Índices de auditoria temporal
CREATE INDEX IF NOT EXISTS idx_transacoes_created ON public.transacoes(created_at);
CREATE INDEX IF NOT EXISTS idx_transacoes_data ON public.transacoes(data_transacao);

-- Índices GIN para busca estruturada e indexada no JSONB
CREATE INDEX IF NOT EXISTS idx_veiculos_estoque_gin ON public.veiculos USING GIN (estoque_financeiro);
CREATE INDEX IF NOT EXISTS idx_dlq_payload_gin ON public.dlq_eventos USING GIN (payload_original);

-- Trava de Concorrência Física: Impede múltiplos turnos ativos/abertos simultaneamente por motorista
CREATE UNIQUE INDEX IF NOT EXISTS idx_unico_turno_aberto 
    ON public.turnos (motorista_id) 
    WHERE status IN ('em_andamento', 'em_pausa', 'ABERTO');


-- ====================================================================================
-- GATILHOS (TRIGGERS) E REGRAS DE INTEGRIDADE CONTÁBIL
-- ====================================================================================

-- Função de Bloqueio de Período (Imutabilidade Contábil)
CREATE OR REPLACE FUNCTION public.function_verificar_periodo_fechado()
RETURNS TRIGGER AS $$
DECLARE
    mes_referencia VARCHAR(7);
    periodo_fechado BOOLEAN;
BEGIN
    -- Determina o mês de referência da transação no formato 'YYYY-MM'
    IF TG_OP = 'INSERT' THEN
        mes_referencia := to_char(NEW.data_transacao AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM');
    ELSE
        mes_referencia := to_char(OLD.data_transacao AT TIME ZONE 'America/Sao_Paulo', 'YYYY-MM');
    END IF;

    -- Verifica se já existe um fechamento consolidado mensal trancado
    SELECT EXISTS (
        SELECT 1 FROM public.fechamentos_consolidados
        WHERE motorista_id = COALESCE(NEW.motorista_id, OLD.motorista_id)
          AND tipo_periodo = 'MENSAL'
          AND referencia = mes_referencia
          AND status = 'FECHADO'
    ) INTO periodo_fechado;

    IF periodo_fechado THEN
        RAISE EXCEPTION 'PERIODO_FECHADO: Não é possível modificar transações no período % porque o fechamento mensal já foi executado e trancado.', mes_referencia;
    END IF;

    IF TG_OP = 'DELETE' THEN
        RETURN OLD;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- Acopla a trava de período contábil à tabela de transações
DROP TRIGGER IF EXISTS trg_trava_periodo_transacoes ON public.transacoes;
CREATE TRIGGER trg_trava_periodo_transacoes
BEFORE INSERT OR UPDATE OR DELETE ON public.transacoes
FOR EACH ROW EXECUTE FUNCTION public.function_verificar_periodo_fechado();


-- ====================================================================================
-- RLS (ROW-LEVEL SECURITY) - BLINDAGEM SUÍÇA DE TENANTS
-- ====================================================================================

-- Habilitar RLS em todas as tabelas de Domínio public
ALTER TABLE public.motoristas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.assinaturas ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.veiculos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.turnos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.transacoes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.despesas_fixas_mensais ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.caixas_provisao ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fechamento_diario ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.dlq_eventos ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lgpd_logs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.fechamentos_consolidados ENABLE ROW LEVEL SECURITY;

-- Políticas utilizando estritamente a variável de sessão 'app.current_driver_id'
-- Alinhado à perfeição com o backend em Python (DatabaseService/TurnoService/TransacaoService)
DROP POLICY IF EXISTS isolamento_motoristas ON public.motoristas;
CREATE POLICY isolamento_motoristas ON public.motoristas FOR ALL USING (id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_assinaturas ON public.assinaturas;
CREATE POLICY isolamento_assinaturas ON public.assinaturas FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_veiculos ON public.veiculos;
CREATE POLICY isolamento_veiculos ON public.veiculos FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_turnos ON public.turnos;
CREATE POLICY isolamento_turnos ON public.turnos FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_transacoes ON public.transacoes;
CREATE POLICY isolamento_transacoes ON public.transacoes FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_despesas ON public.despesas_fixas_mensais;
CREATE POLICY isolamento_despesas ON public.despesas_fixas_mensais FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_caixas ON public.caixas_provisao;
CREATE POLICY isolamento_caixas ON public.caixas_provisao FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_fechamento ON public.fechamento_diario;
CREATE POLICY isolamento_fechamento ON public.fechamento_diario FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_dlq ON public.dlq_eventos;
CREATE POLICY isolamento_dlq ON public.dlq_eventos FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_lgpd ON public.lgpd_logs;
CREATE POLICY isolamento_lgpd ON public.lgpd_logs FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);

DROP POLICY IF EXISTS isolamento_fechamentos_macro ON public.fechamentos_consolidados;
CREATE POLICY isolamento_fechamentos_macro ON public.fechamentos_consolidados FOR ALL USING (motorista_id = nullif(current_setting('app.current_driver_id', true), '')::uuid);
