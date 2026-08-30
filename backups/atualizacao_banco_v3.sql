-- ==============================================================================
-- SCRIPT DE MIGRAÇÃO E ATUALIZAÇÃO DO BANCO DE DADOS (CFO INTELIGENTE B2C)
-- VERSÃO: v3.0.0 - TELEMETRIA DE ABASTECIMENTO E RECARGAS SOLARES/DOMÉSTICAS
-- AUTOR: Gemini Notebook (SRE & Database Architect)
-- DATA: 2026-08-26
-- ==============================================================================

BEGIN;

-- 1. REGISTRO DE LOG DE EXECUÇÃO DA MIGRAÇÃO
DO $$
BEGIN
    RAISE NOTICE 'Iniciando migração de banco de dados para a suíte de telemetria v3...';
END $$;

-- 2. ADIÇÃO DA COLUNA 'tanque_cheio' NA TABELA DE TRANSAÇÕES
-- Esta coluna serve como flag/gatilho para disparar o cálculo do método
-- "Virtual Full-to-Full" para recalibrar o rendimento real (Km/L ou Km/kWh) do veículo.
ALTER TABLE public.transacoes 
ADD COLUMN IF NOT EXISTS tanque_cheio BOOLEAN DEFAULT FALSE NOT NULL;

-- 3. AJUSTE DO CHECK CONSTRAINT DE VALOR MÍNIMO DE TRANSAÇÃO
-- O constraint original 'transacoes_valor_check' exige que o valor seja estritamente maior que 0.
-- Para suportar recargas elétricas gratuitas (ex: energia solar em casa ou pontos de carregamento públicos gratuitos),
-- precisamos permitir que transações de abastecimento/combustível tenham valor igual a R$ 0,00.
-- Removemos o constraint antigo e adicionamos o novo com suporte a valor zero.

ALTER TABLE public.transacoes 
DROP CONSTRAINT IF EXISTS transacoes_valor_check;

ALTER TABLE public.transacoes 
ADD CONSTRAINT transacoes_valor_check CHECK (valor >= (0)::numeric);

-- 4. CRIAÇÃO DE ÍNDICE DE PERFORMANCE PARA BUSCAS DE RECALIBRAÇÃO
-- Para recalibrar o rendimento, o TransacaoService executa uma busca reversa pelas transações
-- com 'tanque_cheio = TRUE' e calcula a soma de litros do intervalo.
-- Este índice parcial otimiza a query para O(log N) em vez de um Sequential Scan completo.
CREATE INDEX IF NOT EXISTS idx_transacoes_recalibracao_telemetria
ON public.transacoes (veiculo_id, tanque_cheio, data_transacao)
WHERE (estornado = FALSE AND categoria = 'combustivel');

-- 5. VERIFICAÇÃO E CORREÇÃO DE INTEGRIDADE DE VALORES DE METADADOS NOS VEÍCULOS
-- Garante que o JSONB 'estoque_financeiro' de todos os veículos existentes possui a
-- estrutura de metadados padrão configurada para evitar erros de deserialização 'KeyError' no Python.
UPDATE public.veiculos
SET estoque_financeiro = '{"meta": {"tipo_veiculo": "gasolina", "is_flex": false, "is_hibrido": false, "is_eletrico": false, "capacidade_tanque_l": 50.0, "capacidade_bateria_kwh": 0.0, "qtd_tanques": 1}, "liquido": {"litros": 0.0, "custo_total": 0.0, "gasolina_litros": 0.0, "etanol_litros": 0.0, "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0, "km_l_gasolina": 12.0, "km_l_etanol": 8.5}, "eletricidade": {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5}, "gnv": {"m3": 0.0, "custo_total": 0.0, "km_m3": 14.0}}'::jsonb
WHERE estoque_financeiro IS NULL OR NOT (estoque_financeiro ? 'meta');

-- 6. AUDITORIA: INSERÇÃO AUTOMÁTICA DE EVENTO NA TABELA DE LOGS LGPD
-- Registra a alteração estrutural para fins de compliance e governança.
INSERT INTO public.lgpd_logs (
    motorista_id, 
    acao_realizada, 
    ip_origem
) 
SELECT 
    id, 
    'UPGRADE_DATABASE_SCHEMA_V3', 
    '127.0.0.1' 
FROM public.motoristas 
LIMIT 1;

COMMIT;

-- CONFIRMAÇÃO DE SUCESSO DO DUMP
DO $$
BEGIN
    RAISE NOTICE 'Migração v3 concluída com sucesso absoluto. Banco de dados CFO Inteligente atualizado.';
END $$;

