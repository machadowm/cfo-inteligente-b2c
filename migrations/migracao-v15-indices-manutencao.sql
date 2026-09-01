-- =============================================================================
-- Migração v15 — Índices de suporte para o módulo de manutenção preventiva
-- =============================================================================
-- O init.sql já possui:
--   idx_regras_veiculo   ON regras_manutencao(veiculo_id)
--   idx_historico_veiculo ON historico_manutencao(veiculo_id)
--
-- Esta migração adiciona os índices necessários para os padrões de acesso do
-- ManutencaoService que não são cobertos pelos índices acima:
--
--   1. idx_regras_veiculo_ativo
--      Todas as queries do serviço filtram ativo = TRUE além de veiculo_id.
--      Índice composto elimina filter scan sobre linhas inativas.
--
--   2. idx_historico_regra_km
--      A query de último odômetro por regra usa:
--        WHERE veiculo_id = $1 AND regra_id = $2
--        ORDER BY km_execucao DESC LIMIT 1
--      Índice composto com km_execucao DESC permite index-only scan para o LIMIT 1.
--
--   3. idx_historico_transacao
--      A FK historico_manutencao_transacao_id_fkey (ON DELETE RESTRICT) requer
--      índice para que o DELETE em transacoes não faça full scan no histórico.
--      PostgreSQL não cria índice de FK automaticamente.
--
-- Todos os comandos usam IF NOT EXISTS — idempotentes em re-execução.
-- =============================================================================

BEGIN;

-- 1. Regras ativas por veículo (padrão de acesso mais frequente do serviço)
CREATE INDEX IF NOT EXISTS idx_regras_veiculo_ativo
    ON public.regras_manutencao USING btree (veiculo_id, ativo)
    WHERE ativo = TRUE;

-- 2. Histórico: último km por (veiculo, regra) — suporta o ORDER BY km_execucao DESC LIMIT 1
CREATE INDEX IF NOT EXISTS idx_historico_regra_km
    ON public.historico_manutencao USING btree (veiculo_id, regra_id, km_execucao DESC);

-- 3. Histórico: FK de transacao_id sem índice explícito no init.sql
CREATE INDEX IF NOT EXISTS idx_historico_transacao
    ON public.historico_manutencao USING btree (transacao_id)
    WHERE transacao_id IS NOT NULL;

COMMIT;
