-- =============================================================================
-- Migração v12 — Backfill meta_valor nas caixas vinculadas a despesas fixas
-- =============================================================================
-- Preenche meta_valor = valor_mensal da despesa fixa vinculada para todas as
-- caixas_provisao que ainda estão sem meta (meta_valor IS NULL).
-- Caixas sem despesa vinculada (ex: Manutenção Corretiva, Amortização IPVA)
-- permanecem sem meta — acumulação livre.
--
-- Uso:
--   docker exec -i cfo_postgres psql -U admin -d cfo_b2c < migracao-v12-caixa-meta-backfill.sql
-- =============================================================================

BEGIN;

UPDATE public.caixas_provisao cp
SET meta_valor = dfm.valor_mensal
FROM public.despesas_fixas_mensais dfm
WHERE dfm.caixa_id = cp.id
  AND dfm.ativo = TRUE
  AND cp.meta_valor IS NULL;

-- Log LGPD
INSERT INTO public.lgpd_logs (motorista_id, acao_realizada, ip_origem, data_evento)
VALUES (
    '00000000-0000-0000-0000-000000000000',
    'migracao_v12_caixa_meta_backfill',
    'migration_script',
    NOW()
);

COMMIT;
