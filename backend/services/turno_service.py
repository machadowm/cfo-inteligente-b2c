from decimal import Decimal, InvalidOperation
from typing import Any, Dict, Optional

from services.database_service import DatabaseService


class TurnoService:
    """Serviço responsável pela gestão de turnos operacionais."""

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
    async def _buscar_turno_aberto_ou_pausado(
        motorista_id: str,
    ) -> Optional[Dict[str, Any]]:
        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            row = await conn.fetchrow(
                """
                SELECT id, veiculo_id, km_inicial, km_final, status, data_inicio, data_fim
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
    ) -> Dict[str, Any]:
        try:
            km_inicial_decimal = TurnoService._validar_km(km_inicial, "km_inicial")
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "error_code": "KM_INVALIDO"}

        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            turno_ativo_id = await conn.fetchval(
                """
                SELECT id
                FROM turnos
                WHERE motorista_id = $1::uuid
                  AND status IN ('ABERTO', 'PAUSADO')
                LIMIT 1
                """,
                motorista_id,
            )

            if turno_ativo_id:
                return {
                    "status": "error",
                    "message": "Já existe um turno aberto para este motorista.",
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
    async def pausar_turno(motorista_id: str) -> Dict[str, Any]:
        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE turnos
                SET status = 'PAUSADO'
                WHERE motorista_id = $1::uuid
                  AND status = 'ABERTO'
                RETURNING id
                """,
                motorista_id,
            )

            if row is None:
                return {
                    "status": "error",
                    "message": "Nenhum turno aberto encontrado para pausar.",
                    "error_code": "TURNO_INEXISTENTE",
                }

            return {
                "status": "success",
                "message": "⏸️ Turno pausado. Bom descanso!",
                "turno_id": str(row["id"]),
            }

    @staticmethod
    async def retomar_turno(motorista_id: str) -> Dict[str, Any]:
        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            row = await conn.fetchrow(
                """
                UPDATE turnos
                SET status = 'ABERTO'
                WHERE motorista_id = $1::uuid
                  AND status = 'PAUSADO'
                RETURNING id
                """,
                motorista_id,
            )

            if row is None:
                return {
                    "status": "error",
                    "message": "Nenhum turno pausado encontrado para retomar.",
                    "error_code": "TURNO_PAUSADO_INEXISTENTE",
                }

            return {
                "status": "success",
                "message": "▶️ Turno retomado! Bora faturar!",
                "turno_id": str(row["id"]),
            }

    @staticmethod
    async def obter_status_turno(motorista_id: str) -> Dict[str, Any]:
        turno = await TurnoService._buscar_turno_aberto_ou_pausado(motorista_id)

        if not turno:
            return {
                "status": "success",
                "message": "Nenhum turno ativo no momento.",
                "turno": None,
            }

        return {
            "status": "success",
            "message": (
                f"Turno {turno['status'].lower()} desde {turno['data_inicio']}."
            ),
            "turno": {
                "turno_id": str(turno["id"]),
                "veiculo_id": str(turno["veiculo_id"]) if turno["veiculo_id"] else None,
                "status": turno["status"],
                "km_inicial": float(turno["km_inicial"]),
                "data_inicio": turno["data_inicio"],
            },
        }

    @staticmethod
    async def fechar_turno_com_dre(
        motorista_id: str,
        km_final: float,
    ) -> Dict[str, Any]:
        try:
            km_final_decimal = TurnoService._validar_km(km_final, "km_final")
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "error_code": "KM_INVALIDO"}

        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
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
                    "message": "Nenhum turno aberto encontrado para fechamento.",
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

            km_rodados = Decimal(str(turno_fechado["km_final"])) - Decimal(
                str(turno_fechado["km_inicial"])
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
                    COALESCE(
                        SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'),
                        0
                    ) AS faturamento_bruto,
                    COALESCE(
                        SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'),
                        0
                    ) AS custo_variavel
                FROM transacoes
                WHERE turno_id = $1::uuid
                  AND estornado = FALSE
                """,
                turno["id"],
            )

            faturamento_bruto = float(agregados["faturamento_bruto"])
            custo_variavel = float(agregados["custo_variavel"])
            custo_fixo_rateado = 0.0
            lucro_liquido_real = faturamento_bruto - custo_variavel - custo_fixo_rateado

            tempo_total = turno_fechado["data_fim"] - turno_fechado["data_inicio"]
            tempo_total_min = max(int(tempo_total.total_seconds() // 60), 0)
            horas_trabalhadas = tempo_total_min / 60 if tempo_total_min > 0 else 0.0

            return {
                "status": "success",
                "message": "Turno fechado com sucesso.",
                "turno_id": str(turno_fechado["id"]),
                "data_inicio": turno_fechado["data_inicio"],
                "data_fim": turno_fechado["data_fim"],
                "km_inicial": float(turno_fechado["km_inicial"]),
                "km_final": float(turno_fechado["km_final"]),
                "km_rodados": float(km_rodados),
                "tempo_total_min": tempo_total_min,
                "horas_trabalhadas": horas_trabalhadas,
                "faturamento_bruto": faturamento_bruto,
                "custo_variavel": custo_variavel,
                "custo_fixo_rateado": custo_fixo_rateado,
                "lucro_liquido_real": lucro_liquido_real,
                "meta_mensal": 0.0,
                "dias_uteis": 1,
                "despesas_detalhadas": [
                    {
                        "categoria": row["categoria"],
                        "descricao_original": row["descricao_original"],
                        "valor": float(row["valor"]),
                    }
                    for row in despesas
                ],
            }