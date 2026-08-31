-- =============================================================================
-- Migração v10 — Pisos de Performance Configuráveis por Motorista
-- =============================================================================
-- Aplica: ADD COLUMN idempotente (IF NOT EXISTS) em public.motoristas
--
-- Novos campos:
--   piso_ganho_km   — Faturamento mínimo por km rodado (padrão R$ 2,00/km)
--   piso_ganho_hora — Faturamento mínimo por hora trabalhada (padrão R$ 30,00/h)
--
-- Uso (a partir da pasta migrations/):
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c < migracao-v10-pisos.sql
-- =============================================================================

BEGIN;

-- ── Adiciona colunas de piso na tabela motoristas ─────────────────────────────
ALTER TABLE public.motoristas
    ADD COLUMN IF NOT EXISTS piso_ganho_km   numeric(10,4) DEFAULT 2.0000,
    ADD COLUMN IF NOT EXISTS piso_ganho_hora numeric(10,4) DEFAULT 30.0000;

-- ── Preenche motoristas existentes com os padrões (caso o DEFAULT não aplique) ─
UPDATE public.motoristas SET piso_ganho_km   = 2.0000  WHERE piso_ganho_km   IS NULL;
UPDATE public.motoristas SET piso_ganho_hora = 30.0000 WHERE piso_ganho_hora IS NULL;

-- ── Log de auditoria LGPD ─────────────────────────────────────────────────────
-- Nota: motorista_id é NOT NULL — usamos um UUID sentinela de sistema (zeros)
-- que representa a operação de migração, não um motorista real.
INSERT INTO public.lgpd_logs (motorista_id, acao_realizada, ip_origem, data_evento)
VALUES (
    '00000000-0000-0000-0000-000000000000',
    'migracao_v10_pisos',
    'migration_script',
    NOW()
);

COMMIT;
