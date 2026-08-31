-- ============================================================
-- Migração v9 — Caixas de Provisão: vínculo estrutural
-- ============================================================
-- Aplique no banco vivo com:
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c < scripts/migracao-v9-caixas.sql
-- Seguro para re-execução (IF NOT EXISTS / IF EXISTS em todo ALTER).
-- ============================================================

BEGIN;

-- 1. Adiciona caixa_id em despesas_fixas_mensais (nullable — despesas sem caixa vinculada são válidas)
ALTER TABLE public.despesas_fixas_mensais
    ADD COLUMN IF NOT EXISTS caixa_id uuid REFERENCES public.caixas_provisao(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_despesas_fixas_caixa
    ON public.despesas_fixas_mensais (caixa_id)
    WHERE caixa_id IS NOT NULL;

-- 2. Garante que provisao_descontada existe em fechamento_diario (já existe no init.sql, mas
--    bancos migrados de versões anteriores podem não ter a coluna)
ALTER TABLE public.fechamento_diario
    ADD COLUMN IF NOT EXISTS provisao_descontada numeric(14,4) NOT NULL DEFAULT 0.0000;

-- 3. Log de auditoria
INSERT INTO public.lgpd_logs (motorista_id, acao_realizada, ip_origem)
SELECT id, 'UPGRADE_SCHEMA_V9_CAIXAS_PROVISAO', '127.0.0.1'
FROM public.motoristas
ON CONFLICT DO NOTHING;

COMMIT;
