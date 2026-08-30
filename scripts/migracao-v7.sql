BEGIN;

-- 1. Garante que as colunas legadas existem temporariamente para evitar falhas se rodar em banco limpo
ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS capacidade_tanque NUMERIC(10,2);
ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS capacidade_bateria_kwh NUMERIC(10,2);
ALTER TABLE veiculos ADD COLUMN IF NOT EXISTS capacidade_gnv_m3 NUMERIC(10,2);

-- 2. Migração de dados segura: Move capacidades físicas para dentro do JSONB 'estoque_financeiro'
-- Preserva os valores de estoque, custos e eficiências já existentes nas chaves do JSONB
UPDATE veiculos
SET estoque_financeiro = jsonb_build_object(
    'liquido', jsonb_build_object(
        'unidades', COALESCE((estoque_financeiro->'liquido'->>'unidades')::numeric, 0.00),
        'capacidade_nominal', COALESCE(capacidade_tanque, 45.00),
        'custo_total', COALESCE((estoque_financeiro->'liquido'->>'custo_total')::numeric, 0.00),
        'cmp', COALESCE((estoque_financeiro->'liquido'->>'cmp')::numeric, 0.00),
        'km_unidade', COALESCE((estoque_financeiro->'liquido'->>'km_unidade')::numeric, 10.00)
    ),
    'eletricidade', jsonb_build_object(
        'unidades', COALESCE((estoque_financeiro->'eletricidade'->>'unidades')::numeric, 0.00),
        'capacidade_nominal', COALESCE(capacidade_bateria_kwh, 40.00),
        'custo_total', COALESCE((estoque_financeiro->'eletricidade'->>'custo_total')::numeric, 0.00),
        'cmp', COALESCE((estoque_financeiro->'eletricidade'->>'cmp')::numeric, 0.00),
        'km_unidade', COALESCE((estoque_financeiro->'eletricidade'->>'km_unidade')::numeric, 6.00)
    ),
    'gnv', jsonb_build_object(
        'unidades', COALESCE((estoque_financeiro->'gnv'->>'unidades')::numeric, 0.00),
        'capacidade_nominal', COALESCE(capacidade_gnv_m3, 15.00),
        'custo_total', COALESCE((estoque_financeiro->'gnv'->>'custo_total')::numeric, 0.00),
        'cmp', COALESCE((estoque_financeiro->'gnv'->>'cmp')::numeric, 0.00),
        'km_unidade', COALESCE((estoque_financeiro->'gnv'->>'km_unidade')::numeric, 14.00)
    )
);

-- 3. Expurgo Contábil: Remove as colunas físicas obsoletas para higienizar o schema
ALTER TABLE veiculos DROP COLUMN IF EXISTS capacidade_tanque;
ALTER TABLE veiculos DROP COLUMN IF EXISTS capacidade_bateria_kwh;
ALTER TABLE veiculos DROP COLUMN IF EXISTS capacidade_gnv_m3;

-- 4. Otimização de Performance: Recria índice GIN composto para consultas rápidas na matriz energética
DROP INDEX IF EXISTS idx_veiculos_estoque_gin;
CREATE INDEX idx_veiculos_estoque_gin ON veiculos USING GIN (estoque_financeiro);

-- 5. Atualização de Metadados: Garante as colunas e chaves de idempotência na tabela de transações
ALTER TABLE transacoes ADD COLUMN IF NOT EXISTS descricao TEXT;
ALTER TABLE transacoes ADD COLUMN IF NOT EXISTS wpp_msg_id VARCHAR(255);

DO $$ 
BEGIN 
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint 
        WHERE conname IN ('unique_wpp_msg_id', 'transacoes_wpp_msg_id_key')
    ) THEN
        ALTER TABLE transacoes ADD CONSTRAINT unique_wpp_msg_id UNIQUE (wpp_msg_id);
    END IF;
END $$;

COMMIT;
