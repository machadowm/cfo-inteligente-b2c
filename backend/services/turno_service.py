from decimal import Decimal, InvalidOperation
from typing import Any

from services.database_service import DatabaseService


class TurnoService:
    """Gestão operacional de turnos com pausa, retomada e fechamento diário."""

    @staticmethod
    def _validar_km(valor_km: float, campo: str) -> Decimal:
        try:
            km_decimal = Decimal(str(valor_km))
        except InvalidOperation as exc:
            raise ValueError(f"O valor de {campo} está mal formatado.") from exc

        if km_decimal < 0:
            raise ValueError(f"O valor de {campo} não pode ser negativo.")

        return km_decimal

    @staticmethod
    async def _buscar_turno_ativo(
        motorista_id: str,
        conn,
    ) -> dict[str, Any] | None:
        row = await conn.fetchrow(
            """
            SELECT id, motorista_id, veiculo_id, status, km_inicial, km_final,
                   km_uso_pessoal, data_inicio, data_fim
            FROM turnos
            WHERE motorista_id = $1::uuid
              AND status IN ('ABERTO', 'PAUSADO')
            ORDER BY data_inicio DESC
            LIMIT 1
            """,
            motorista_id,
        )
        return dict(row) if row else None

    @staticmethod
    async def abrir_turno(
        motorista_id: str,
        veiculo_id: str,
        km_inicial: float,
    ) -> dict[str, Any]:
        try:
            km_inicial_decimal = TurnoService._validar_km(km_inicial, "km_inicial")
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "error_code": "KM_INVALIDO"}

        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            turno_ativo = await TurnoService._buscar_turno_ativo(motorista_id, conn)
            if turno_ativo:
                return {
                    "status": "error",
                    "message": "Já existe um turno ativo para este motorista.",
                    "error_code": "TURNO_JA_ATIVO",
                }

            row = await conn.fetchrow(
                """
                INSERT INTO turnos (
                    motorista_id,
                    veiculo_id,
                    km_inicial,
                    status,
                    data_inicio
                )
                VALUES ($1::uuid, $2::uuid, $3, 'ABERTO', NOW())
                RETURNING id, data_inicio, km_inicial
                """,
                motorista_id,
                veiculo_id,
                km_inicial_decimal,
            )

            return {
                "status": "success",
                "message": "Turno aberto com sucesso.",
                "turno_id": str(row["id"]),
                "data_inicio": row["data_inicio"],
                "km_inicial": float(row["km_inicial"]),
            }

    @staticmethod
    async def pausar_turno(
        motorista_id: str,
        motivo: str | None = None,
    ) -> dict[str, Any]:
        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            turno = await conn.fetchrow(
                """
                SELECT id
                FROM turnos
                WHERE motorista_id = $1::uuid
                  AND status = 'ABERTO'
                ORDER BY data_inicio DESC
                LIMIT 1
                """,
                motorista_id,
            )

            if turno is None:
                return {
                    "status": "error",
                    "message": "Nenhum turno aberto encontrado para pausar.",
                    "error_code": "TURNO_INEXISTENTE",
                }

            await conn.execute(
                """
                UPDATE turnos
                SET status = 'PAUSADO'
                WHERE id = $1::uuid
                """,
                turno["id"],
            )

            await conn.execute(
                """
                INSERT INTO pausas_turno (
                    motorista_id,
                    turno_id,
                    motivo,
                    data_inicio
                )
                VALUES ($1::uuid, $2::uuid, $3, NOW())
                """,
                motorista_id,
                turno["id"],
                motivo,
            )

            return {
                "status": "success",
                "message": "⏸️ Turno pausado. Bom descanso!",
                "turno_id": str(turno["id"]),
            }

    @staticmethod
    async def retomar_turno(motorista_id: str) -> dict[str, Any]:
        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            turno = await conn.fetchrow(
                """
                SELECT id
                FROM turnos
                WHERE motorista_id = $1::uuid
                  AND status = 'PAUSADO'
                ORDER BY data_inicio DESC
                LIMIT 1
                """,
                motorista_id,
            )

            if turno is None:
                return {
                    "status": "error",
                    "message": "Nenhum turno pausado encontrado para retomar.",
                    "error_code": "TURNO_PAUSADO_INEXISTENTE",
                }

            await conn.execute(
                """
                UPDATE turnos
                SET status = 'ABERTO'
                WHERE id = $1::uuid
                """,
                turno["id"],
            )

            await conn.execute(
                """
                UPDATE pausas_turno
                SET data_fim = NOW()
                WHERE turno_id = $1::uuid
                  AND data_fim IS NULL
                """,
                turno["id"],
            )

            return {
                "status": "success",
                "message": "▶️ Turno retomado! Bora faturar!",
                "turno_id": str(turno["id"]),
            }

    @staticmethod
    async def obter_status_turno(motorista_id: str) -> dict[str, Any]:
        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            turno = await TurnoService._buscar_turno_ativo(motorista_id, conn)

            if not turno:
                return {
                    "status": "success",
                    "message": "Nenhum turno ativo no momento.",
                    "turno": None,
                }

            pausas_abertas = await conn.fetchval(
                """
                SELECT COUNT(1)
                FROM pausas_turno
                WHERE turno_id = $1::uuid
                  AND data_fim IS NULL
                """,
                turno["id"],
            )

            return {
                "status": "success",
                "message": f"Turno {turno['status'].lower()} desde {turno['data_inicio']}.",
                "turno": {
                    "turno_id": str(turno["id"]),
                    "veiculo_id": str(turno["veiculo_id"]) if turno["veiculo_id"] else None,
                    "status": turno["status"],
                    "km_inicial": float(turno["km_inicial"]),
                    "data_inicio": turno["data_inicio"],
                    "pausas_abertas": int(pausas_abertas or 0),
                },
            }

    @staticmethod
    async def fechar_turno_com_dre(
        motorista_id: str,
        km_final: float,
    ) -> dict[str, Any]:
        try:
            km_final_decimal = TurnoService._validar_km(km_final, "km_final")
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "error_code": "KM_INVALIDO"}

        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            motorista = await conn.fetchrow(
                """
                SELECT id, nome, meta_mensal_faturamento, dias_uteis_mes
                FROM motoristas
                WHERE id = $1::uuid
                LIMIT 1
                """,
                motorista_id,
            )

            turno = await conn.fetchrow(
                """
                SELECT id, km_inicial, data_inicio, veiculo_id
                FROM turnos
                WHERE motorista_id = $1::uuid
                  AND status IN ('ABERTO', 'PAUSADO')
                ORDER BY data_inicio DESC
                LIMIT 1
                """,
                motorista_id,
            )

            if turno is None:
                return {
                    "status": "error",
                    "message": "Nenhum turno ativo encontrado para fechamento.",
                    "error_code": "TURNO_INEXISTENTE",
                }

            km_inicial_decimal = Decimal(str(turno["km_inicial"]))
            if km_final_decimal < km_inicial_decimal:
                return {
                    "status": "error",
                    "message": "O km final não pode ser menor que o km inicial.",
                    "error_code": "ODOMETRO_DIVERGENTE",
                }

            turno_fechado = await conn.fetchrow(
                """
                UPDATE turnos
                SET km_final = $2,
                    status = 'FECHADO',
                    data_fim = NOW()
                WHERE id = $1::uuid
                RETURNING id, km_inicial, km_final, data_inicio, data_fim
                """,
                turno["id"],
                km_final_decimal,
            )

            await conn.execute(
                """
                UPDATE pausas_turno
                SET data_fim = NOW()
                WHERE turno_id = $1::uuid
                  AND data_fim IS NULL
                """,
                turno["id"],
            )

            despesas = await conn.fetch(
                """
                SELECT categoria, descricao AS descricao_original, valor
                FROM transacoes
                WHERE turno_id = $1::uuid
                  AND tipo_movimentacao = 'despesa'
                  AND estornado = FALSE
                ORDER BY data_transacao ASC
                """,
                turno["id"],
            )

            agregados = await conn.fetchrow(
                """
                SELECT
                    COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'), 0) AS faturamento_bruto,
                    COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'), 0) AS custo_variavel
                FROM transacoes
                WHERE turno_id = $1::uuid
                  AND estornado = FALSE
                """,
                turno["id"],
            )

            faturamento_bruto = Decimal(str(agregados["faturamento_bruto"]))
            custo_variavel = Decimal(str(agregados["custo_variavel"]))
            custo_fixo_rateado = Decimal("0.00")
            provisao_descontada = Decimal("0.00")
            lucro_liquido_real = (
                faturamento_bruto
                - custo_variavel
                - custo_fixo_rateado
                - provisao_descontada
            )

            km_rodados = Decimal(str(turno_fechado["km_final"])) - Decimal(
                str(turno_fechado["km_inicial"])
            )

            tempo_total = turno_fechado["data_fim"] - turno_fechado["data_inicio"]
            tempo_total_min = max(int(tempo_total.total_seconds() // 60), 0)
            horas_trabalhadas = round(tempo_total_min / 60, 2) if tempo_total_min > 0 else 0.0
            data_referencia = turno_fechado["data_fim"].date()

            await conn.execute(
                """
                INSERT INTO fechamento_diario (
                    motorista_id,
                    turno_id,
                    faturamento_bruto,
                    custo_variavel_direto,
                    custo_fixo_rateado,
                    provisao_descontada,
                    lucro_liquido_real,
                    km_rodados,
                    data_referencia
                )
                VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (turno_id) DO NOTHING
                """,
                motorista_id,
                turno["id"],
                faturamento_bruto,
                custo_variavel,
                custo_fixo_rateado,
                provisao_descontada,
                lucro_liquido_real,
                km_rodados,
                data_referencia,
            )

            return {
                "status": "success",
                "message": "Turno fechado com sucesso.",
                "turno_id": str(turno_fechado["id"]),
                "motorista_nome": motorista["nome"] if motorista else None,
                "data_inicio": turno_fechado["data_inicio"],
                "data_fim": turno_fechado["data_fim"],
                "km_inicial": float(turno_fechado["km_inicial"]),
                "km_final": float(turno_fechado["km_final"]),
                "km_rodados": float(km_rodados),
                "tempo_total_min": tempo_total_min,
                "horas_trabalhadas": horas_trabalhadas,
                "faturamento_bruto": float(faturamento_bruto),
                "custo_variavel": float(custo_variavel),
                "custo_fixo_rateado": float(custo_fixo_rateado),
                "provisao_descontada": float(provisao_descontada),
                "lucro_liquido_real": float(lucro_liquido_real),
                "meta_mensal": float(motorista["meta_mensal_faturamento"]) if motorista else 0.0,
                "dias_uteis": int(motorista["dias_uteis_mes"]) if motorista else 1,
                "despesas_detalhadas": [
                    {
                        "categoria": row["categoria"],
                        "descricao_original": row["descricao_original"],
                        "valor": float(row["valor"]),
                    }
                    for row in despesas
                ],
            }