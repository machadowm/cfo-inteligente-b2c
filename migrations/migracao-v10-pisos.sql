-- =============================================================================
-- Migração v10 — Pisos de Performance Configuráveis por Motorista
-- =============================================================================
-- Aplica: ADD COLUMN idempotente (IF NOT EXISTS) em public.motoristas
--
-- Novos campos:
--   piso_ganho_km   — Faturamento mínimo por km rodado (padrão R$ 2,00/km)
--   piso_ganho_hora — Faturamento mínimo por hora trabalhada (padrão R$ 30,00/h)
--
-- Uso:
--   psql $DATABASE_URL -f scripts/migracao-v10-pisos.sql
-- =============================================================================

BEGIN;

-- ── Adiciona colunas de piso na tabela motoristas ─────────────────────────────
ALTER TABLE public.motoristas
    ADD COLUMN IF NOT EXISTS piso_ganho_km   numeric(10,4) DEFAULT 2.0000,
    ADD COLUMN IF NOT EXISTS piso_ganho_hora numeric(10,4) DEFAULT 30.0000;

-- ── Preenche motoristas existentes com os padrões (caso o DEFAULT não aplique) ─
UPDATE public.motoristas
    SET piso_ganho_km   = 2.0000  WHERE piso_ganho_km   IS NULL;
UPDATE public.motoristas
    SET piso_ganho_hora = 30.0000 WHERE piso_ganho_hora IS NULL;

-- ── Log de auditoria LGPD ─────────────────────────────────────────────────────
INSERT INTO public.lgpd_logs (acao, detalhes, criado_em)
VALUES (
    'migracao_v10',
    'ADD COLUMN piso_ganho_km e piso_ganho_hora em public.motoristas',
    NOW()
) ON CONFLICT DO NOTHING;

COMMIT;
