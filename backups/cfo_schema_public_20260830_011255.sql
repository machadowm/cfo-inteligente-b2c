--
-- PostgreSQL database dump
--

\restrict m6SHsaC9awsGAU96TaEqK6NH2anoVhoK01tHpgQjOIcWIO0bi5pwWkaXDvHrGph

-- Dumped from database version 15.18
-- Dumped by pg_dump version 15.18

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

DROP POLICY IF EXISTS isolamento_veiculos ON public.veiculos;
DROP POLICY IF EXISTS isolamento_turnos ON public.turnos;
DROP POLICY IF EXISTS isolamento_transacoes ON public.transacoes;
DROP POLICY IF EXISTS isolamento_motoristas ON public.motoristas;
DROP POLICY IF EXISTS isolamento_lgpd ON public.lgpd_logs;
DROP POLICY IF EXISTS isolamento_fechamentos_macro ON public.fechamentos_consolidados;
DROP POLICY IF EXISTS isolamento_fechamento ON public.fechamento_diario;
DROP POLICY IF EXISTS isolamento_dlq ON public.dlq_eventos;
DROP POLICY IF EXISTS isolamento_despesas ON public.despesas_fixas_mensais;
DROP POLICY IF EXISTS isolamento_caixas ON public.caixas_provisao;
DROP POLICY IF EXISTS isolamento_assinaturas ON public.assinaturas;
ALTER TABLE IF EXISTS ONLY public.veiculos DROP CONSTRAINT IF EXISTS veiculos_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.turnos DROP CONSTRAINT IF EXISTS turnos_veiculo_id_fkey;
ALTER TABLE IF EXISTS ONLY public.turnos DROP CONSTRAINT IF EXISTS turnos_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.transacoes DROP CONSTRAINT IF EXISTS transacoes_veiculo_id_fkey;
ALTER TABLE IF EXISTS ONLY public.transacoes DROP CONSTRAINT IF EXISTS transacoes_turno_id_fkey;
ALTER TABLE IF EXISTS ONLY public.transacoes DROP CONSTRAINT IF EXISTS transacoes_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.regras_manutencao DROP CONSTRAINT IF EXISTS regras_manutencao_veiculo_id_fkey;
ALTER TABLE IF EXISTS ONLY public.pausas_turno DROP CONSTRAINT IF EXISTS pausas_turno_turno_id_fkey;
ALTER TABLE IF EXISTS ONLY public.historico_manutencao DROP CONSTRAINT IF EXISTS historico_manutencao_veiculo_id_fkey;
ALTER TABLE IF EXISTS ONLY public.historico_manutencao DROP CONSTRAINT IF EXISTS historico_manutencao_transacao_id_fkey;
ALTER TABLE IF EXISTS ONLY public.historico_manutencao DROP CONSTRAINT IF EXISTS historico_manutencao_regra_id_fkey;
ALTER TABLE IF EXISTS ONLY public.fechamentos_consolidados DROP CONSTRAINT IF EXISTS fechamentos_consolidados_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.fechamento_diario DROP CONSTRAINT IF EXISTS fechamento_diario_turno_id_fkey;
ALTER TABLE IF EXISTS ONLY public.fechamento_diario DROP CONSTRAINT IF EXISTS fechamento_diario_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.dlq_eventos DROP CONSTRAINT IF EXISTS dlq_eventos_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.despesas_fixas_mensais DROP CONSTRAINT IF EXISTS despesas_fixas_mensais_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.caixas_provisao DROP CONSTRAINT IF EXISTS caixas_provisao_motorista_id_fkey;
ALTER TABLE IF EXISTS ONLY public.assinaturas DROP CONSTRAINT IF EXISTS assinaturas_motorista_id_fkey;
DROP TRIGGER IF EXISTS trig_motoristas_atualizado_em ON public.motoristas;
DROP TRIGGER IF EXISTS trig_caixas_atualizacao ON public.caixas_provisao;
DROP TRIGGER IF EXISTS trg_trava_periodo_transacoes ON public.transacoes;
DROP INDEX IF EXISTS public.idx_veiculos_motorista;
DROP INDEX IF EXISTS public.idx_veiculos_estoque_gin;
DROP INDEX IF EXISTS public.idx_unico_turno_aberto;
DROP INDEX IF EXISTS public.idx_unico_fechamento_periodo;
DROP INDEX IF EXISTS public.idx_turnos_veiculo;
DROP INDEX IF EXISTS public.idx_turnos_motorista;
DROP INDEX IF EXISTS public.idx_transacoes_veiculo;
DROP INDEX IF EXISTS public.idx_transacoes_turno;
DROP INDEX IF EXISTS public.idx_transacoes_recalibracao_telemetria;
DROP INDEX IF EXISTS public.idx_transacoes_motorista;
DROP INDEX IF EXISTS public.idx_transacoes_data;
DROP INDEX IF EXISTS public.idx_transacoes_created;
DROP INDEX IF EXISTS public.idx_regras_veiculo;
DROP INDEX IF EXISTS public.idx_pausas_turno;
DROP INDEX IF EXISTS public.idx_lgpd_motorista;
DROP INDEX IF EXISTS public.idx_historico_veiculo;
DROP INDEX IF EXISTS public.idx_fechamentos_consolidados_motorista;
DROP INDEX IF EXISTS public.idx_fechamento_turno;
DROP INDEX IF EXISTS public.idx_fechamento_motorista;
DROP INDEX IF EXISTS public.idx_dlq_payload_gin;
DROP INDEX IF EXISTS public.idx_dlq_motorista;
DROP INDEX IF EXISTS public.idx_despesas_fixas_motorista;
DROP INDEX IF EXISTS public.idx_caixas_provisao_motorista;
DROP INDEX IF EXISTS public.idx_assinaturas_motorista;
ALTER TABLE IF EXISTS ONLY public.veiculos DROP CONSTRAINT IF EXISTS veiculos_placa_key;
ALTER TABLE IF EXISTS ONLY public.veiculos DROP CONSTRAINT IF EXISTS veiculos_pkey;
ALTER TABLE IF EXISTS ONLY public.turnos DROP CONSTRAINT IF EXISTS turnos_pkey;
ALTER TABLE IF EXISTS ONLY public.transacoes DROP CONSTRAINT IF EXISTS transacoes_wpp_msg_id_key;
ALTER TABLE IF EXISTS ONLY public.transacoes DROP CONSTRAINT IF EXISTS transacoes_pkey;
ALTER TABLE IF EXISTS ONLY public.transacoes DROP CONSTRAINT IF EXISTS transacoes_idempotencia_hash_key;
ALTER TABLE IF EXISTS ONLY public.regras_manutencao DROP CONSTRAINT IF EXISTS regras_manutencao_pkey;
ALTER TABLE IF EXISTS ONLY public.pausas_turno DROP CONSTRAINT IF EXISTS pausas_turno_pkey;
ALTER TABLE IF EXISTS ONLY public.motoristas DROP CONSTRAINT IF EXISTS motoristas_telefone_key;
ALTER TABLE IF EXISTS ONLY public.motoristas DROP CONSTRAINT IF EXISTS motoristas_pkey;
ALTER TABLE IF EXISTS ONLY public.lgpd_logs DROP CONSTRAINT IF EXISTS lgpd_logs_pkey;
ALTER TABLE IF EXISTS ONLY public.historico_manutencao DROP CONSTRAINT IF EXISTS historico_manutencao_pkey;
ALTER TABLE IF EXISTS ONLY public.fechamentos_consolidados DROP CONSTRAINT IF EXISTS fechamentos_consolidados_pkey;
ALTER TABLE IF EXISTS ONLY public.fechamento_diario DROP CONSTRAINT IF EXISTS fechamento_diario_pkey;
ALTER TABLE IF EXISTS ONLY public.dlq_eventos DROP CONSTRAINT IF EXISTS dlq_eventos_pkey;
ALTER TABLE IF EXISTS ONLY public.despesas_fixas_mensais DROP CONSTRAINT IF EXISTS despesas_fixas_mensais_pkey;
ALTER TABLE IF EXISTS ONLY public.caixas_provisao DROP CONSTRAINT IF EXISTS caixas_provisao_pkey;
ALTER TABLE IF EXISTS ONLY public.caixas_provisao DROP CONSTRAINT IF EXISTS caixas_provisao_motorista_id_nome_caixa_key;
ALTER TABLE IF EXISTS ONLY public.assinaturas DROP CONSTRAINT IF EXISTS assinaturas_pkey;
DROP TABLE IF EXISTS public.veiculos;
DROP TABLE IF EXISTS public.turnos;
DROP TABLE IF EXISTS public.transacoes;
DROP TABLE IF EXISTS public.regras_manutencao;
DROP TABLE IF EXISTS public.pausas_turno;
DROP TABLE IF EXISTS public.motoristas;
DROP TABLE IF EXISTS public.lgpd_logs;
DROP TABLE IF EXISTS public.historico_manutencao;
DROP TABLE IF EXISTS public.fechamentos_consolidados;
DROP TABLE IF EXISTS public.fechamento_diario;
DROP TABLE IF EXISTS public.dlq_eventos;
DROP TABLE IF EXISTS public.despesas_fixas_mensais;
DROP TABLE IF EXISTS public.caixas_provisao;
DROP TABLE IF EXISTS public.assinaturas;
DROP FUNCTION IF EXISTS public.update_timestamp_func();
DROP FUNCTION IF EXISTS public.function_verificar_periodo_fechado();
DROP SCHEMA IF EXISTS public;
--
-- Name: public; Type: SCHEMA; Schema: -; Owner: -
--

CREATE SCHEMA public;


--
-- Name: SCHEMA public; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON SCHEMA public IS 'standard public schema';


--
-- Name: function_verificar_periodo_fechado(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.function_verificar_periodo_fechado() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;


--
-- Name: update_timestamp_func(); Type: FUNCTION; Schema: public; Owner: -
--

CREATE FUNCTION public.update_timestamp_func() RETURNS trigger
    LANGUAGE plpgsql
    AS $$
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
$$;


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: assinaturas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.assinaturas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    gateway_id character varying(100),
    plano character varying(50) NOT NULL,
    data_vencimento date NOT NULL,
    status character varying(20) NOT NULL,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: caixas_provisao; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.caixas_provisao (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    nome_caixa character varying(50) NOT NULL,
    saldo_atual numeric(14,4) DEFAULT 0.00,
    ultima_atualizacao timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: despesas_fixas_mensais; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.despesas_fixas_mensais (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    nome character varying(50) NOT NULL,
    valor_mensal numeric(14,4) NOT NULL,
    dias_trabalho_previstos integer NOT NULL,
    valor_pro_rata_diario numeric(14,4) GENERATED ALWAYS AS ((valor_mensal / (NULLIF(dias_trabalho_previstos, 0))::numeric)) STORED,
    dia_vencimento integer NOT NULL,
    ativo boolean DEFAULT true,
    CONSTRAINT despesas_fixas_mensais_dias_trabalho_previstos_check CHECK ((dias_trabalho_previstos > 0))
);


--
-- Name: dlq_eventos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dlq_eventos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid,
    payload_original jsonb NOT NULL,
    motivo_falha text NOT NULL,
    tentativas integer DEFAULT 1,
    status character varying(20) DEFAULT 'pendente'::character varying,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: fechamento_diario; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fechamento_diario (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    turno_id uuid,
    faturamento_bruto numeric(14,4) NOT NULL,
    custo_variavel_direto numeric(14,4) NOT NULL,
    custo_fixo_rateado numeric(14,4) NOT NULL,
    lucro_liquido_real numeric(14,4) NOT NULL,
    km_rodados numeric(10,2) NOT NULL,
    clima_predominante character varying(30),
    data_fechamento date DEFAULT CURRENT_DATE,
    provisao_descontada numeric(14,4) DEFAULT 0.00 NOT NULL
);


--
-- Name: fechamentos_consolidados; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.fechamentos_consolidados (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    tipo_periodo character varying(10) NOT NULL,
    referencia character varying(7) NOT NULL,
    faturacao_bruta numeric(14,4) DEFAULT 0.00 NOT NULL,
    custo_variavel_direto numeric(14,4) DEFAULT 0.00 NOT NULL,
    custo_fixo_rateado numeric(14,4) DEFAULT 0.00 NOT NULL,
    provisao_descontada numeric(14,4) DEFAULT 0.00 NOT NULL,
    lucro_liquido_real numeric(14,4) DEFAULT 0.00 NOT NULL,
    km_rodados numeric(10,2) DEFAULT 0.00 NOT NULL,
    horas_trabalhadas numeric(10,2) DEFAULT 0.00 NOT NULL,
    status character varying(20) DEFAULT 'FECHADO'::character varying NOT NULL,
    data_consolidacao timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT fechamentos_consolidados_tipo_periodo_check CHECK (((tipo_periodo)::text = ANY ((ARRAY['MENSAL'::character varying, 'ANUAL'::character varying])::text[])))
);


--
-- Name: historico_manutencao; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.historico_manutencao (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    veiculo_id uuid NOT NULL,
    regra_id uuid,
    transacao_id uuid,
    km_execucao numeric(10,2) NOT NULL,
    data_execucao timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: lgpd_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.lgpd_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    acao_realizada character varying(50) NOT NULL,
    ip_origem character varying(50),
    data_evento timestamp with time zone DEFAULT CURRENT_TIMESTAMP
);


--
-- Name: motoristas; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.motoristas (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    telefone character varying(50) NOT NULL,
    nome character varying(150) NOT NULL,
    status_assinatura character varying(32) DEFAULT 'TRIAL'::character varying NOT NULL,
    ativo boolean DEFAULT true,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    atualizado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    meta_mensal_faturamento numeric(14,4) DEFAULT 12000.00,
    dias_uteis_mes integer DEFAULT 26,
    nome_social character varying(100),
    meta_faturamento_diario numeric(14,4) DEFAULT 0.00,
    meta_faturamento_semanal numeric(14,4) DEFAULT 0.00,
    meta_horas_diarias numeric(5,2) DEFAULT 8.00,
    meta_km_diarios numeric(10,2) DEFAULT 0.00,
    possui_multiplos_veiculos boolean DEFAULT false
);


--
-- Name: pausas_turno; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.pausas_turno (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    turno_id uuid NOT NULL,
    inicio_pausa timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    fim_pausa timestamp with time zone,
    motivo character varying(50)
);


--
-- Name: regras_manutencao; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.regras_manutencao (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    veiculo_id uuid NOT NULL,
    tipo_servico character varying(100) NOT NULL,
    intervalo_km integer NOT NULL,
    aviso_previo_km integer DEFAULT 500 NOT NULL,
    ativo boolean DEFAULT true
);


--
-- Name: transacoes; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transacoes (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    turno_id uuid,
    veiculo_id uuid,
    tipo_movimentacao character varying(20) NOT NULL,
    categoria character varying(50) NOT NULL,
    valor numeric(14,4) NOT NULL,
    estabelecimento character varying(100),
    metodo_pagamento character varying(50),
    contexto_operacional character varying(50),
    comprovante_url character varying(255),
    idempotencia_hash character varying(100),
    estornado boolean DEFAULT false,
    data_transacao timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    descricao text,
    wpp_msg_id character varying(255),
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    litros_abastecidos numeric(10,2),
    preco_por_litro numeric(10,4),
    odometro_abastecimento numeric(10,2),
    tanque_cheio boolean DEFAULT false NOT NULL,
    CONSTRAINT transacoes_tipo_movimentacao_check CHECK (((tipo_movimentacao)::text = ANY ((ARRAY['receita'::character varying, 'despesa'::character varying, 'neutro'::character varying])::text[]))),
    CONSTRAINT transacoes_valor_check CHECK ((valor >= (0)::numeric))
);


--
-- Name: turnos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.turnos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    veiculo_id uuid NOT NULL,
    km_inicial numeric(10,2) NOT NULL,
    km_final numeric(10,2),
    data_inicio timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    data_fim timestamp with time zone,
    status character varying(20) DEFAULT 'em_andamento'::character varying,
    km_uso_pessoal numeric(10,2) DEFAULT 0.00,
    CONSTRAINT turnos_check CHECK (((km_final IS NULL) OR (km_final >= km_inicial)))
);


--
-- Name: veiculos; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.veiculos (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    motorista_id uuid NOT NULL,
    placa character varying(20) NOT NULL,
    modelo character varying(150) NOT NULL,
    tipo_combustivel character varying(50) NOT NULL,
    estoque_financeiro jsonb DEFAULT '{"liquido": {"litros": 0.0, "custo_total": 0.0, "km_l_etanol": 8.5, "etanol_litros": 0.0, "km_l_gasolina": 12.0, "gasolina_litros": 0.0, "etanol_proporcao": 0.0, "gasolina_proporcao": 1.0}, "eletricidade": {"kwh": 0.0, "km_kwh": 6.5, "custo_total": 0.0}}'::jsonb,
    ativo boolean DEFAULT true,
    criado_em timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    created_at timestamp with time zone DEFAULT CURRENT_TIMESTAMP,
    locadora character varying(100) DEFAULT 'Localiza Zarp'::character varying,
    custo_aluguel_semanal numeric(10,2) DEFAULT 1020.85,
    franquia_km_semanal numeric(10,2) DEFAULT 1505.00,
    valor_km_excedente numeric(10,4) DEFAULT 0.75,
    escala_trabalho character varying(100) DEFAULT 'De quarta a segunda (6 dias)'::character varying,
    contrato_personalizado boolean DEFAULT false,
    capacidade_bateria numeric(10,2) DEFAULT 30.00,
    is_flex boolean DEFAULT false,
    qtd_tanques integer DEFAULT 1,
    is_hibrido boolean DEFAULT false,
    is_eletrico boolean DEFAULT false
);


--
-- Data for Name: assinaturas; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.assinaturas (id, motorista_id, gateway_id, plano, data_vencimento, status, criado_em) FROM stdin;
\.


--
-- Data for Name: caixas_provisao; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.caixas_provisao (id, motorista_id, nome_caixa, saldo_atual, ultima_atualizacao) FROM stdin;
59539b40-b01d-4021-858a-3857593d175b	4ef8c731-7878-4091-923d-50f1c06c34d0	Manutenção Corretiva (Pneus/Freios)	0.0000	2026-08-29 09:33:13.797524-03
1a5fb189-3c22-43ee-bf7c-82cc7fed79f2	4ef8c731-7878-4091-923d-50f1c06c34d0	Amortização de IPVA/Seguro	0.0000	2026-08-29 09:33:13.797524-03
\.


--
-- Data for Name: despesas_fixas_mensais; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.despesas_fixas_mensais (id, motorista_id, nome, valor_mensal, dias_trabalho_previstos, dia_vencimento, ativo) FROM stdin;
\.


--
-- Data for Name: dlq_eventos; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.dlq_eventos (id, motorista_id, payload_original, motivo_falha, tentativas, status, criado_em) FROM stdin;
\.


--
-- Data for Name: fechamento_diario; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fechamento_diario (id, motorista_id, turno_id, faturamento_bruto, custo_variavel_direto, custo_fixo_rateado, lucro_liquido_real, km_rodados, clima_predominante, data_fechamento, provisao_descontada) FROM stdin;
92803e60-cf0f-4404-8fa6-1d52cc2985f3	4ef8c731-7878-4091-923d-50f1c06c34d0	92dc9647-4d84-462a-9303-5c17c0c3203a	347.8600	174.0000	42.0000	131.8600	138.00	\N	2026-08-29	0.0000
\.


--
-- Data for Name: fechamentos_consolidados; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.fechamentos_consolidados (id, motorista_id, tipo_periodo, referencia, faturacao_bruta, custo_variavel_direto, custo_fixo_rateado, provisao_descontada, lucro_liquido_real, km_rodados, horas_trabalhadas, status, data_consolidacao) FROM stdin;
\.


--
-- Data for Name: historico_manutencao; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.historico_manutencao (id, veiculo_id, regra_id, transacao_id, km_execucao, data_execucao) FROM stdin;
\.


--
-- Data for Name: lgpd_logs; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.lgpd_logs (id, motorista_id, acao_realizada, ip_origem, data_evento) FROM stdin;
\.


--
-- Data for Name: motoristas; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.motoristas (id, telefone, nome, status_assinatura, ativo, criado_em, atualizado_em, meta_mensal_faturamento, dias_uteis_mes, nome_social, meta_faturamento_diario, meta_faturamento_semanal, meta_horas_diarias, meta_km_diarios, possui_multiplos_veiculos) FROM stdin;
4ef8c731-7878-4091-923d-50f1c06c34d0	5513997971393	Willian Machado	TRIAL	t	2026-08-29 09:33:13.797524-03	2026-08-29 09:35:05.48117-03	9100.0000	26	\N	0.0000	0.0000	8.00	0.00	f
\.


--
-- Data for Name: pausas_turno; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.pausas_turno (id, turno_id, inicio_pausa, fim_pausa, motivo) FROM stdin;
ebd8895f-050f-486e-aa6f-b6a51f66adc2	92dc9647-4d84-462a-9303-5c17c0c3203a	2026-08-29 13:52:07.491309-03	2026-08-29 16:45:14.636099-03	Pausa Operacional
\.


--
-- Data for Name: regras_manutencao; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.regras_manutencao (id, veiculo_id, tipo_servico, intervalo_km, aviso_previo_km, ativo) FROM stdin;
\.


--
-- Data for Name: transacoes; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.transacoes (id, motorista_id, turno_id, veiculo_id, tipo_movimentacao, categoria, valor, estabelecimento, metodo_pagamento, contexto_operacional, comprovante_url, idempotencia_hash, estornado, data_transacao, descricao, wpp_msg_id, created_at, litros_abastecidos, preco_por_litro, odometro_abastecimento, tanque_cheio) FROM stdin;
05dc5ca1-686d-401c-a7b5-e773b533d262	4ef8c731-7878-4091-923d-50f1c06c34d0	92dc9647-4d84-462a-9303-5c17c0c3203a	7debe768-155b-4d25-b8a1-22a7b8bd13f5	despesa	combustivel	57.0000	\N	\N	\N	\N	\N	f	2026-08-29 10:30:49.576782-03	Abastecer	ACC6DC5DD266EBD2584F30ABE750F65F	2026-08-29 10:30:49.576782-03	17.87	3.1900	179734.00	f
98ecaa23-6e4a-402c-a6d9-94b8d8f6c86b	4ef8c731-7878-4091-923d-50f1c06c34d0	92dc9647-4d84-462a-9303-5c17c0c3203a	7debe768-155b-4d25-b8a1-22a7b8bd13f5	despesa	geral	25.0000	\N	\N	\N	\N	\N	f	2026-08-29 15:47:02.872728-03	Gastei 25 na cacau center	ACF1413F1443C917B1FA45A0BA11E598	2026-08-29 15:47:02.872728-03	\N	\N	\N	f
680abca7-c9ef-4759-a903-2630ef1ad7f7	4ef8c731-7878-4091-923d-50f1c06c34d0	92dc9647-4d84-462a-9303-5c17c0c3203a	7debe768-155b-4d25-b8a1-22a7b8bd13f5	despesa	geral	35.0000	\N	\N	\N	\N	\N	f	2026-08-29 15:47:08.307229-03	Gastei 35 na farmácia	ACA336765594877D29CE138D9B7DE91C	2026-08-29 15:47:08.307229-03	\N	\N	\N	f
b965e2f5-6339-4ff8-be4a-f815003f0319	4ef8c731-7878-4091-923d-50f1c06c34d0	92dc9647-4d84-462a-9303-5c17c0c3203a	7debe768-155b-4d25-b8a1-22a7b8bd13f5	receita	geral	347.8600	\N	\N	\N	\N	\N	f	2026-08-29 22:00:12.810435-03	Ganhei 347,86 na Uber	AC483C3F829DEC1E97119EC050E86A28	2026-08-29 22:00:12.810435-03	\N	\N	\N	f
\.


--
-- Data for Name: turnos; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.turnos (id, motorista_id, veiculo_id, km_inicial, km_final, data_inicio, data_fim, status, km_uso_pessoal) FROM stdin;
92dc9647-4d84-462a-9303-5c17c0c3203a	4ef8c731-7878-4091-923d-50f1c06c34d0	7debe768-155b-4d25-b8a1-22a7b8bd13f5	179729.00	179867.00	2026-08-29 10:05:30.638362-03	2026-08-29 22:00:23.86756-03	concluido	0.00
\.


--
-- Data for Name: veiculos; Type: TABLE DATA; Schema: public; Owner: -
--

COPY public.veiculos (id, motorista_id, placa, modelo, tipo_combustivel, estoque_financeiro, ativo, criado_em, created_at, locadora, custo_aluguel_semanal, franquia_km_semanal, valor_km_excedente, escala_trabalho, contrato_personalizado, capacidade_bateria, is_flex, qtd_tanques, is_hibrido, is_eletrico) FROM stdin;
7debe768-155b-4d25-b8a1-22a7b8bd13f5	4ef8c731-7878-4091-923d-50f1c06c34d0	EPT8C00	Uno Fiat	etanol	{"gnv": {"m3": 0.0, "km_m3": 14.0, "custo_total": 0.0}, "meta": {"is_flex": false, "is_hibrido": false, "is_eletrico": false, "qtd_tanques": 1, "tipo_veiculo": "etanol", "capacidade_tanque_l": 40.0, "capacidade_bateria_kwh": 0.0}, "liquido": {"litros": 0.0, "custo_total": 0.0, "km_l_etanol": 8.5, "etanol_litros": 0.0, "km_l_gasolina": 12.0, "gasolina_litros": 0.0, "etanol_proporcao": 0.0, "gasolina_proporcao": 1.0}, "eletricidade": {"kwh": 0.0, "km_kwh": 6.5, "custo_total": 0.0}}	t	2026-08-29 09:33:13.797524-03	2026-08-29 09:33:13.797524-03	Financiado	252.00	0.00	0.7500	De quarta a segunda (6 dias)	t	30.00	f	1	f	f
\.


--
-- Name: assinaturas assinaturas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assinaturas
    ADD CONSTRAINT assinaturas_pkey PRIMARY KEY (id);


--
-- Name: caixas_provisao caixas_provisao_motorista_id_nome_caixa_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caixas_provisao
    ADD CONSTRAINT caixas_provisao_motorista_id_nome_caixa_key UNIQUE (motorista_id, nome_caixa);


--
-- Name: caixas_provisao caixas_provisao_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caixas_provisao
    ADD CONSTRAINT caixas_provisao_pkey PRIMARY KEY (id);


--
-- Name: despesas_fixas_mensais despesas_fixas_mensais_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.despesas_fixas_mensais
    ADD CONSTRAINT despesas_fixas_mensais_pkey PRIMARY KEY (id);


--
-- Name: dlq_eventos dlq_eventos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dlq_eventos
    ADD CONSTRAINT dlq_eventos_pkey PRIMARY KEY (id);


--
-- Name: fechamento_diario fechamento_diario_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fechamento_diario
    ADD CONSTRAINT fechamento_diario_pkey PRIMARY KEY (id);


--
-- Name: fechamentos_consolidados fechamentos_consolidados_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fechamentos_consolidados
    ADD CONSTRAINT fechamentos_consolidados_pkey PRIMARY KEY (id);


--
-- Name: historico_manutencao historico_manutencao_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_pkey PRIMARY KEY (id);


--
-- Name: lgpd_logs lgpd_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.lgpd_logs
    ADD CONSTRAINT lgpd_logs_pkey PRIMARY KEY (id);


--
-- Name: motoristas motoristas_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.motoristas
    ADD CONSTRAINT motoristas_pkey PRIMARY KEY (id);


--
-- Name: motoristas motoristas_telefone_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.motoristas
    ADD CONSTRAINT motoristas_telefone_key UNIQUE (telefone);


--
-- Name: pausas_turno pausas_turno_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pausas_turno
    ADD CONSTRAINT pausas_turno_pkey PRIMARY KEY (id);


--
-- Name: regras_manutencao regras_manutencao_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regras_manutencao
    ADD CONSTRAINT regras_manutencao_pkey PRIMARY KEY (id);


--
-- Name: transacoes transacoes_idempotencia_hash_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_idempotencia_hash_key UNIQUE (idempotencia_hash);


--
-- Name: transacoes transacoes_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_pkey PRIMARY KEY (id);


--
-- Name: transacoes transacoes_wpp_msg_id_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_wpp_msg_id_key UNIQUE (wpp_msg_id);


--
-- Name: turnos turnos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.turnos
    ADD CONSTRAINT turnos_pkey PRIMARY KEY (id);


--
-- Name: veiculos veiculos_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.veiculos
    ADD CONSTRAINT veiculos_pkey PRIMARY KEY (id);


--
-- Name: veiculos veiculos_placa_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.veiculos
    ADD CONSTRAINT veiculos_placa_key UNIQUE (placa);


--
-- Name: idx_assinaturas_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_assinaturas_motorista ON public.assinaturas USING btree (motorista_id);


--
-- Name: idx_caixas_provisao_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_caixas_provisao_motorista ON public.caixas_provisao USING btree (motorista_id);


--
-- Name: idx_despesas_fixas_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_despesas_fixas_motorista ON public.despesas_fixas_mensais USING btree (motorista_id);


--
-- Name: idx_dlq_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dlq_motorista ON public.dlq_eventos USING btree (motorista_id);


--
-- Name: idx_dlq_payload_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dlq_payload_gin ON public.dlq_eventos USING gin (payload_original);


--
-- Name: idx_fechamento_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fechamento_motorista ON public.fechamento_diario USING btree (motorista_id);


--
-- Name: idx_fechamento_turno; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fechamento_turno ON public.fechamento_diario USING btree (turno_id);


--
-- Name: idx_fechamentos_consolidados_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_fechamentos_consolidados_motorista ON public.fechamentos_consolidados USING btree (motorista_id);


--
-- Name: idx_historico_veiculo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_historico_veiculo ON public.historico_manutencao USING btree (veiculo_id);


--
-- Name: idx_lgpd_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_lgpd_motorista ON public.lgpd_logs USING btree (motorista_id);


--
-- Name: idx_pausas_turno; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_pausas_turno ON public.pausas_turno USING btree (turno_id);


--
-- Name: idx_regras_veiculo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_regras_veiculo ON public.regras_manutencao USING btree (veiculo_id);


--
-- Name: idx_transacoes_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transacoes_created ON public.transacoes USING btree (created_at);


--
-- Name: idx_transacoes_data; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transacoes_data ON public.transacoes USING btree (data_transacao);


--
-- Name: idx_transacoes_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transacoes_motorista ON public.transacoes USING btree (motorista_id);


--
-- Name: idx_transacoes_recalibracao_telemetria; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transacoes_recalibracao_telemetria ON public.transacoes USING btree (veiculo_id, tanque_cheio, data_transacao) WHERE ((estornado = false) AND ((categoria)::text = 'combustivel'::text));


--
-- Name: idx_transacoes_turno; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transacoes_turno ON public.transacoes USING btree (turno_id);


--
-- Name: idx_transacoes_veiculo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transacoes_veiculo ON public.transacoes USING btree (veiculo_id);


--
-- Name: idx_turnos_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_turnos_motorista ON public.turnos USING btree (motorista_id);


--
-- Name: idx_turnos_veiculo; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_turnos_veiculo ON public.turnos USING btree (veiculo_id);


--
-- Name: idx_unico_fechamento_periodo; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_unico_fechamento_periodo ON public.fechamentos_consolidados USING btree (motorista_id, tipo_periodo, referencia);


--
-- Name: idx_unico_turno_aberto; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX idx_unico_turno_aberto ON public.turnos USING btree (motorista_id) WHERE ((status)::text = ANY ((ARRAY['em_andamento'::character varying, 'em_pausa'::character varying, 'ABERTO'::character varying])::text[]));


--
-- Name: idx_veiculos_estoque_gin; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_veiculos_estoque_gin ON public.veiculos USING gin (estoque_financeiro);


--
-- Name: idx_veiculos_motorista; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_veiculos_motorista ON public.veiculos USING btree (motorista_id);


--
-- Name: transacoes trg_trava_periodo_transacoes; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trg_trava_periodo_transacoes BEFORE INSERT OR DELETE OR UPDATE ON public.transacoes FOR EACH ROW EXECUTE FUNCTION public.function_verificar_periodo_fechado();


--
-- Name: caixas_provisao trig_caixas_atualizacao; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trig_caixas_atualizacao BEFORE UPDATE ON public.caixas_provisao FOR EACH ROW EXECUTE FUNCTION public.update_timestamp_func();


--
-- Name: motoristas trig_motoristas_atualizado_em; Type: TRIGGER; Schema: public; Owner: -
--

CREATE TRIGGER trig_motoristas_atualizado_em BEFORE UPDATE ON public.motoristas FOR EACH ROW EXECUTE FUNCTION public.update_timestamp_func();


--
-- Name: assinaturas assinaturas_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.assinaturas
    ADD CONSTRAINT assinaturas_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: caixas_provisao caixas_provisao_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.caixas_provisao
    ADD CONSTRAINT caixas_provisao_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: despesas_fixas_mensais despesas_fixas_mensais_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.despesas_fixas_mensais
    ADD CONSTRAINT despesas_fixas_mensais_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: dlq_eventos dlq_eventos_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dlq_eventos
    ADD CONSTRAINT dlq_eventos_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE CASCADE;


--
-- Name: fechamento_diario fechamento_diario_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fechamento_diario
    ADD CONSTRAINT fechamento_diario_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: fechamento_diario fechamento_diario_turno_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fechamento_diario
    ADD CONSTRAINT fechamento_diario_turno_id_fkey FOREIGN KEY (turno_id) REFERENCES public.turnos(id) ON DELETE SET NULL;


--
-- Name: fechamentos_consolidados fechamentos_consolidados_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.fechamentos_consolidados
    ADD CONSTRAINT fechamentos_consolidados_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: historico_manutencao historico_manutencao_regra_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_regra_id_fkey FOREIGN KEY (regra_id) REFERENCES public.regras_manutencao(id) ON DELETE SET NULL;


--
-- Name: historico_manutencao historico_manutencao_transacao_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_transacao_id_fkey FOREIGN KEY (transacao_id) REFERENCES public.transacoes(id) ON DELETE RESTRICT;


--
-- Name: historico_manutencao historico_manutencao_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.historico_manutencao
    ADD CONSTRAINT historico_manutencao_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE RESTRICT;


--
-- Name: pausas_turno pausas_turno_turno_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.pausas_turno
    ADD CONSTRAINT pausas_turno_turno_id_fkey FOREIGN KEY (turno_id) REFERENCES public.turnos(id) ON DELETE CASCADE;


--
-- Name: regras_manutencao regras_manutencao_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.regras_manutencao
    ADD CONSTRAINT regras_manutencao_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE CASCADE;


--
-- Name: transacoes transacoes_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: transacoes transacoes_turno_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_turno_id_fkey FOREIGN KEY (turno_id) REFERENCES public.turnos(id) ON DELETE SET NULL;


--
-- Name: transacoes transacoes_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transacoes
    ADD CONSTRAINT transacoes_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE RESTRICT;


--
-- Name: turnos turnos_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.turnos
    ADD CONSTRAINT turnos_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: turnos turnos_veiculo_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.turnos
    ADD CONSTRAINT turnos_veiculo_id_fkey FOREIGN KEY (veiculo_id) REFERENCES public.veiculos(id) ON DELETE RESTRICT;


--
-- Name: veiculos veiculos_motorista_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.veiculos
    ADD CONSTRAINT veiculos_motorista_id_fkey FOREIGN KEY (motorista_id) REFERENCES public.motoristas(id) ON DELETE RESTRICT;


--
-- Name: assinaturas; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.assinaturas ENABLE ROW LEVEL SECURITY;

--
-- Name: caixas_provisao; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.caixas_provisao ENABLE ROW LEVEL SECURITY;

--
-- Name: despesas_fixas_mensais; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.despesas_fixas_mensais ENABLE ROW LEVEL SECURITY;

--
-- Name: dlq_eventos; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.dlq_eventos ENABLE ROW LEVEL SECURITY;

--
-- Name: fechamento_diario; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.fechamento_diario ENABLE ROW LEVEL SECURITY;

--
-- Name: fechamentos_consolidados; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.fechamentos_consolidados ENABLE ROW LEVEL SECURITY;

--
-- Name: assinaturas isolamento_assinaturas; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_assinaturas ON public.assinaturas USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: caixas_provisao isolamento_caixas; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_caixas ON public.caixas_provisao USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: despesas_fixas_mensais isolamento_despesas; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_despesas ON public.despesas_fixas_mensais USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: dlq_eventos isolamento_dlq; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_dlq ON public.dlq_eventos USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: fechamento_diario isolamento_fechamento; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_fechamento ON public.fechamento_diario USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: fechamentos_consolidados isolamento_fechamentos_macro; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_fechamentos_macro ON public.fechamentos_consolidados USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: lgpd_logs isolamento_lgpd; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_lgpd ON public.lgpd_logs USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: motoristas isolamento_motoristas; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_motoristas ON public.motoristas USING ((id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: transacoes isolamento_transacoes; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_transacoes ON public.transacoes USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: turnos isolamento_turnos; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_turnos ON public.turnos USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: veiculos isolamento_veiculos; Type: POLICY; Schema: public; Owner: -
--

CREATE POLICY isolamento_veiculos ON public.veiculos USING ((motorista_id = (NULLIF(current_setting('app.current_driver_id'::text, true), ''::text))::uuid));


--
-- Name: lgpd_logs; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.lgpd_logs ENABLE ROW LEVEL SECURITY;

--
-- Name: motoristas; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.motoristas ENABLE ROW LEVEL SECURITY;

--
-- Name: transacoes; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.transacoes ENABLE ROW LEVEL SECURITY;

--
-- Name: turnos; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.turnos ENABLE ROW LEVEL SECURITY;

--
-- Name: veiculos; Type: ROW SECURITY; Schema: public; Owner: -
--

ALTER TABLE public.veiculos ENABLE ROW LEVEL SECURITY;

--
-- PostgreSQL database dump complete
--

\unrestrict m6SHsaC9awsGAU96TaEqK6NH2anoVhoK01tHpgQjOIcWIO0bi5pwWkaXDvHrGph

