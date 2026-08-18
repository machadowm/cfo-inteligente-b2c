import logging
from decimal import Decimal, InvalidOperation
from typing import Any, Dict

import asyncpg

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class TransacaoService:
    """Serviço financeiro com idempotência e tratamento de período consolidado."""

    _TIPOS_MOVIMENTACAO_VALIDOS = {"receita", "despesa"}
    _TIMEZONE_NEGOCIO = "America/Sao_Paulo"

    @staticmethod
    def _normalizar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        return tipo_movimentacao.strip().lower()

    @staticmethod
    def _validar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        tipo_normalizado = TransacaoService._normalizar_tipo_movimentacao(
            tipo_movimentacao
        )
        if tipo_normalizado not in TransacaoService._TIPOS_MOVIMENTACAO_VALIDOS:
            raise ValueError(
                "O tipo de movimentação deve ser 'receita' ou 'despesa'."
            )
        return tipo_normalizado

    @staticmethod
    def _validar_valor(valor: float) -> Decimal:
        try:
            valor_decimal = Decimal(str(valor))
        except InvalidOperation as exc:
            raise ValueError("Valor financeiro mal formatado.") from exc

        if valor_decimal <= 0:
            raise ValueError(
                "O valor financeiro deve ser estritamente maior que zero."
            )

        return valor_decimal

    @staticmethod
    def _mapear_erro_postgres(exc: Exception) -> Dict[str, Any]:
        mensagem = str(exc)
        if "PERIODO_FECHADO" in mensagem:
            return {
                "status": "error",
                "message": (
                    "O período contabilístico já foi consolidado e não permite alterações."
                ),
                "error_code": "PERIODO_FECHADO",
            }

        return {
            "status": "error",
            "message": "Falha ao processar a transação.",
            "error_code": "ERRO_BANCO",
        }

    @staticmethod
    async def registrar_transacao(
        motorista_id: str,
        tipo_movimentacao: str,
        categoria: str,
        valor: float,
        descricao: str,
        wpp_msg_id: str,
    ) -> Dict[str, Any]:
        try:
            tipo_movimentacao_validado = (
                TransacaoService._validar_tipo_movimentacao(tipo_movimentacao)
            )
            valor_decimal = TransacaoService._validar_valor(valor)
        except ValueError as exc:
            return {"status": "error", "message": str(exc), "error_code": "VALIDACAO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno_id = await conn.fetchval(
                    """
                    SELECT id
                    FROM turnos
                    WHERE motorista_id = $1::uuid
                      AND status IN ('ABERTO', 'PAUSADO')
                    ORDER BY data_inicio DESC
                    LIMIT 1
                    """,
                    motorista_id,
                )

                row = await conn.fetchrow(
                    """
                    INSERT INTO transacoes (
                        motorista_id,
                        turno_id,
                        tipo_movimentacao,
                        categoria,
                        valor,
                        descricao,
                        wpp_msg_id
                    )
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7)
                    ON CONFLICT (wpp_msg_id) DO NOTHING
                    RETURNING id, data_transacao
                    """,
                    motorista_id,
                    turno_id,
                    tipo_movimentacao_validado,
                    categoria,
                    valor_decimal,
                    descricao,
                    wpp_msg_id,
                )

                if row is None:
                    logger.warning(
                        "Tentativa duplicada de transação. motorista_id=%s wpp_msg_id=%s",
                        motorista_id,
                        wpp_msg_id,
                    )
                    return {
                        "status": "duplicate",
                        "message": "Transação já registada. Ignorada por idempotência.",
                        "error_code": "DUPLICADA",
                    }

                return {
                    "status": "success",
                    "message": "Transação registada com sucesso.",
                    "transacao_id": str(row["id"]),
                    "turno_id": str(turno_id) if turno_id else None,
                    "data_transacao": row["data_transacao"],
                }
        except asyncpg.PostgresError as exc:
            logger.exception("Erro PostgreSQL ao registrar transação.")
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def estornar_transacao(
        motorista_id: str,
        transacao_id: str,
    ) -> Dict[str, Any]:
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE transacoes
                    SET estornado = TRUE
                    WHERE id = $1::uuid
                      AND motorista_id = $2::uuid
                      AND estornado = FALSE
                    RETURNING id, tipo_movimentacao, valor
                    """,
                    transacao_id,
                    motorista_id,
                )

                if row is None:
                    return {
                        "status": "error",
                        "message": (
                            "Transação não encontrada, não pertence ao motorista, "
                            "ou já estornada."
                        ),
                        "error_code": "TRANSACAO_NAO_ENCONTRADA",
                    }

                return {
                    "status": "success",
                    "message": (
                        f"{row['tipo_movimentacao'].capitalize()} de "
                        f"R$ {row['valor']} estornada com sucesso."
                    ),
                }
        except asyncpg.PostgresError as exc:
            logger.exception("Erro PostgreSQL ao estornar transação.")
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def obter_resumo_diario(
        motorista_id: str,
        data_referencia_iso: str,
    ) -> Dict[str, Any]:
        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
            row = await conn.fetchrow(
                f"""
                SELECT
                    COALESCE(
                        SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'),
                        0
                    ) AS total_receitas,
                    COALESCE(
                        SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'),
                        0
                    ) AS total_despesas
                FROM transacoes
                WHERE motorista_id = $1::uuid
                  AND estornado = FALSE
                  AND DATE(
                        data_transacao AT TIME ZONE '{TransacaoService._TIMEZONE_NEGOCIO}'
                  ) = $2::date
                """,
                motorista_id,
                data_referencia_iso,
            )

            receitas = Decimal(str(row["total_receitas"]))
            despesas = Decimal(str(row["total_despesas"]))
            saldo_liquido = receitas - despesas

            return {
                "status": "success",
                "message": "Resumo diário calculado com sucesso.",
                "data": data_referencia_iso,
                "financeiro": {
                    "receitas": float(receitas),
                    "despesas": float(despesas),
                    "saldo_liquido": float(saldo_liquido),
                },
            }