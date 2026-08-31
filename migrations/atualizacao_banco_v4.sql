-- ==============================================================================
-- SCRIPT DE MIGRAÇÃO E ATUALIZAÇÃO DO BANCO DE DADOS (CFO INTELIGENTE B2C)
-- VERSÃO: v4.0.0 - SUPORTE A ODÔMETRO INTRA-TURNO NAS PAUSAS DO MOTORISTA
-- AUTOR: Gemini Notebook (SRE & Database Architect)
-- DATA: 2026-08-29
-- ==============================================================================

BEGIN;

-- 1. REGISTRO DE LOG DE EXECUÇÃO DA MIGRAÇÃO
DO $$
BEGIN
    RAISE NOTICE 'Iniciando migração de banco de dados para suporte de odômetro nas pausas v4...';
END $$;

-- 2. ADIÇÃO DE COLUNAS 'km_inicio' E 'km_fim' NA TABELA DE PAUSAS
ALTER TABLE public.pausas_turno ADD COLUMN IF NOT EXISTS km_inicio NUMERIC(10,2);
ALTER TABLE public.pausas_turno ADD COLUMN IF NOT EXISTS km_fim NUMERIC(10,2);

-- 3. REGISTRO LGPD DE AUDITORIA STRUCTURAL
INSERT INTO public.lgpd_logs (
    motorista_id, 
    acao_realizada, 
    ip_origem
) 
SELECT 
    id, 
    'UPGRADE_DATABASE_SCHEMA_V4_PAUSAS_KM', 
    '127.0.0.1' 
FROM public.motoristas 
LIMIT 1;

COMMIT;

-- CONFIRMAÇÃO DE SUCESSO DO DUMP
DO $$
BEGIN
    RAISE NOTICE 'Migração v4 concluída com sucesso absoluto.';
END $$;

