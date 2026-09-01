-- =============================================================================
-- Migração v13 — dias_trabalho_semana: divisor real da escala semanal
-- =============================================================================
-- Adiciona coluna dias_trabalho_semana (int, NOT NULL DEFAULT 6) em veiculos.
-- Substitui o hardcode /6 que estava espalhado pelo sistema.
-- DEFAULT 6 preserva o comportamento existente (escala Zarp: qua–seg).
--
-- Uso:
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c < migracao-v13-dias-trabalho-semana.sql
-- =============================================================================

BEGIN;

ALTER TABLE public.veiculos
    ADD COLUMN IF NOT EXISTS dias_trabalho_semana integer NOT NULL DEFAULT 6
    CONSTRAINT veiculos_dias_trabalho_semana_check CHECK (dias_trabalho_semana BETWEEN 1 AND 7);

-- Log LGPD
INSERT INTO public.lgpd_logs (motorista_id, acao_realizada, ip_origem, data_evento)
VALUES (
    '00000000-0000-0000-0000-000000000000',
    'migracao_v13_dias_trabalho_semana',
    'migration_script',
    NOW()
);

COMMIT;
