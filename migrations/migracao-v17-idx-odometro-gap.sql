-- =============================================================================
-- Migração v17 — Índice de Odômetro para Detecção de Anacronismo de Competência
-- =============================================================================
-- Contexto:
--   O TurnoService.abrir_turno e retomar_turno agora executam uma query de
--   range scan em transacoes para detectar abastecimentos que ocorreram no
--   gap entre km_anterior e km_inicial do novo turno (Anacronismo de
--   Competência do Estoque).
--
--   Query crítica em abrir_turno / retomar_turno:
--     SELECT odometro_abastecimento
--     FROM public.transacoes
--     WHERE veiculo_id = $1
--       AND categoria = 'combustivel'
--       AND estornado = FALSE
--       AND odometro_abastecimento IS NOT NULL
--       AND odometro_abastecimento > $2     -- km_anterior
--       AND odometro_abastecimento < $3     -- km_inicial
--     ORDER BY odometro_abastecimento ASC
--     LIMIT 1;
--
--   Sem índice: Sequential Scan em toda a partição de transacoes do veículo.
--   Com índice: Index Range Scan O(log N + k), onde k = registros no gap.
--
-- Índice existente (NÃO cobre esta query):
--   idx_transacoes_recalibracao_telemetria → (veiculo_id, tanque_cheio, data_transacao)
--     — filtro por data_transacao, não por odometro_abastecimento.
--
-- Novo índice:
--   idx_transacoes_odometro_gap — índice parcial em (veiculo_id, odometro_abastecimento)
--   filtrado por estornado = FALSE AND categoria = 'combustivel', que são as
--   condições fixas das queries de detecção de anacronismo.
--   Parcial: exclui as ~80% de linhas que não são combustível ou estão estornadas,
--   mantendo o footprint do índice mínimo.
--
-- Idempotente: CREATE INDEX IF NOT EXISTS — seguro para reexecução.
-- =============================================================================

CREATE INDEX IF NOT EXISTS idx_transacoes_odometro_gap
    ON public.transacoes (veiculo_id, odometro_abastecimento)
    WHERE (
        estornado = FALSE
        AND categoria = 'combustivel'
        AND odometro_abastecimento IS NOT NULL
    );

COMMENT ON INDEX public.idx_transacoes_odometro_gap IS
    'Índice parcial para detecção de abastecimentos dentro do gap de odômetro '
    'inter-turnos (Anacronismo de Competência do Estoque — v17). '
    'Cobre queries de range scan em odometro_abastecimento por veiculo_id '
    'usadas em TurnoService.abrir_turno e TurnoService.retomar_turno.';
