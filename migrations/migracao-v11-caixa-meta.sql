-- =============================================================================
-- Migração v11 — Caixa-Meta: teto de provisionamento por caixinha
-- =============================================================================
-- Adiciona coluna meta_valor (nullable) em caixas_provisao.
-- NULL = acumulação livre (comportamento anterior preservado).
-- Quando preenchido, o aporte automático no fechamento de turno é limitado
-- ao valor que falta para completar a meta.
--
-- Uso (a partir da pasta migrations/):
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c < migracao-v11-caixa-meta.sql
-- =============================================================================

BEGIN;

ALTER TABLE public.caixas_provisao
    ADD COLUMN IF NOT EXISTS meta_valor numeric(14,4) DEFAULT NULL;

-- Log LGPD
INSERT INTO public.lgpd_logs (motorista_id, acao_realizada, ip_origem, data_evento)
VALUES (
    '00000000-0000-0000-0000-000000000000',
    'migracao_v11_caixa_meta',
    'migration_script',
    NOW()
);

COMMIT;
