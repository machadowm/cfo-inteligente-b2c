import os
import re
import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Any, Optional
import asyncpg
from services.database_service import DatabaseService

logger = logging.getLogger(__name__)

class TransacaoService:
    """
    Serviço Financeiro (Ledger) de Alta Precisão (Decimal).
    Oferece suporte a carregamento e abastecimento dinâmico de veículos Híbridos,
    Flex de tanque único (blend homogêneo) e recargas elétricas (incluindo carregamento solar em casa com custo zero).
    """
    _TIPOS_MOVIMENTACAO_VALIDOS = {"receita", "despesa"}
    _PRECO_MEDIO_LITRO_FALLBACK = Decimal("5.85")
    _PRECO_MEDIO_KWH_FALLBACK = Decimal("1.20")

    @staticmethod
    def _normalizar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        return tipo_movimentacao.strip().lower()

    @staticmethod
    def _validar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        tipo_normalizado = TransacaoService._normalizar_tipo_movimentacao(tipo_movimentacao)
        if tipo_normalizado not in TransacaoService._TIPOS_MOVIMENTACAO_VALIDOS:
            raise ValueError("O tipo de movimentação deve ser 'receita' ou 'despesa'.")
        return tipo_normalizado

    @staticmethod
    def _validar_valor(valor: float, permitir_zero: bool = False) -> Decimal:
        try:
            valor_decimal = Decimal(str(valor))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Valor financeiro mal formatado.") from exc

        if permitir_zero:
            if valor_decimal < 0:
                raise ValueError("O valor financeiro não pode ser negativo.")
        else:
            if valor_decimal <= 0:
                raise ValueError("O valor financeiro deve ser estritamente maior que zero.")

        return valor_decimal

    @staticmethod
    def _mapear_erro_postgres(exc: Exception) -> Dict[str, Any]:
        mensagem = str(exc)
        if "PERIODO_FECHADO" in mensagem:
            return {
                "status": "error",
                "message": "⚠️ O período contábil já foi consolidado e trancado pela contabilidade. Não são permitidas alterações retroativas.",
                "error_code": "PERIODO_FECHADO"
            }
        return {
            "status": "error",
            "message": f"Falha na integridade do cofre contábil: {mensagem}",
            "error_code": "ERRO_BANCO"
        }

    @staticmethod
    async def registrar_transacao(
        motorista_id: str,
        tipo_movimentacao: str,
        categoria: str,
        valor: float,
        descricao: Optional[str] = None,
        wpp_msg_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Registra receitas e despesas. Se for abastecimento/recarga (combustível),
        calcula dinamicamente a queima, a mistura no tanque ou carga da bateria sob limites físicos.
        """
        desc_limpa = (descricao or "").lower()
        # Permite valor zero especificamente para recargas solares ou domésticas gratuitas
        is_recarga_gratuita = "solar" in desc_limpa or "casa" in desc_limpa or "gratis" in desc_limpa or "tomada" in desc_limpa
        permitir_zero = categoria.lower() == "combustivel" and is_recarga_gratuita

        try:
            tipo_validado = TransacaoService._validar_tipo_movimentacao(tipo_movimentacao)
            valor_decimal = TransacaoService._validar_valor(valor, permitir_zero=permitir_zero)
        except ValueError as exc:
            return {"status": "error", "message": f"❌ {str(exc)}", "error_code": "VALIDACAO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # 1. Identifica se existe turno ativo para vincular a transação
                turno_ativo = await conn.fetchrow(
                    """
                    SELECT id, veiculo_id FROM public.turnos 
                    WHERE motorista_id = $1::uuid AND status IN ('em_andamento', 'em_pausa', 'ABERTO', 'PAUSADO')
                    ORDER BY data_inicio DESC LIMIT 1;
                    """,
                    motorista_id
                )
                turno_id = str(turno_ativo["id"]) if turno_ativo else None
                veiculo_id = str(turno_ativo["veiculo_id"]) if turno_ativo else None

                # Se for lançamento fora de turno, busca o veículo principal ativo
                if not veiculo_id:
                    veiculo_row = await conn.fetchrow(
                        "SELECT id FROM public.veiculos WHERE motorista_id = $1::uuid AND ativo = TRUE LIMIT 1;",
                        motorista_id
                    )
                    veiculo_id = str(veiculo_row["id"]) if veiculo_row else None

                # 2. SE FOR ABASTECIMENTO OU RECARGA (DESPESA EM COMBUSTÍVEL)
                if tipo_validado == "despesa" and categoria.lower() == "combustivel" and veiculo_id:
                    veiculo = await conn.fetchrow(
                        """
                        SELECT estoque_financeiro, tipo_combustivel, capacidade_tanque, capacidade_bateria, 
                               is_flex, is_hibrido, is_eletrico 
                        FROM public.veiculos WHERE id = $1::uuid;
                        """,
                        veiculo_id
                    )
                    
                    if veiculo:
                        # Limite de capacidade dinâmica recuperada diretamente do banco de dados (com fallbacks seguros)
                        capacidade_tanque = Decimal(str(veiculo["capacidade_tanque"] or "50.00"))
                        capacidade_bateria = Decimal(str(veiculo["capacidade_bateria"] or "30.00"))

                        estoque_raw = veiculo["estoque_financeiro"]
                        estoque = json.loads(estoque_raw) if isinstance(estoque_raw, str) else (estoque_raw or {})
                        
                        # Garante a estrutura de estoque multi-energia unificada (Backward Compatible)
                        if "liquido" not in estoque:
                            estoque["liquido"] = {
                                "litros": 0.0,
                                "custo_total": 0.0,
                                "gasolina_litros": 0.0,
                                "etanol_litros": 0.0,
                                "gasolina_proporcao": 1.0,
                                "etanol_proporcao": 0.0,
                                "km_l_gasolina": 12.0,
                                "km_l_etanol": 8.5
                            }
                        if "eletricidade" not in estoque:
                            estoque["eletricidade"] = {
                                "kwh": 0.0,
                                "custo_total": 0.0,
                                "km_kwh": 6.5
                            }

                        # Determina se o abastecimento é elétrico ou combustível líquido
                        is_eletrico_event = "kwh" in desc_limpa or "recarga" in desc_limpa or "solar" in desc_limpa or "eletrico" in desc_limpa or veiculo["is_eletrico"]
                        
                        if is_eletrico_event:
                            # 2.1. RECARGA ELÉTRICA (BATERIA)
                            dados_comb = estoque["eletricidade"]
                            qtd_atual = Decimal(str(dados_comb.get("kwh", 0.0)))
                            custo_atual = Decimal(str(dados_comb.get("custo_total", 0.0)))

                            # Extrai kWh
                            kwh_novos = Decimal("0.00")
                            kwh_match = re.search(r'(\d+[\.,]?\d*)\s*(?:kwh|kw|quilowatt)', desc_limpa)
                            if kwh_match:
                                kwh_novos = Decimal(kwh_match.group(1).replace(',', '.'))
                            else:
                                if is_recarga_gratuita:
                                    kwh_novos = Decimal("15.00") # Fallback carga solar média de casa
                                else:
                                    kwh_novos = (valor_decimal / TransacaoService._PRECO_MEDIO_KWH_FALLBACK).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                            novo_volume = qtd_atual + kwh_novos
                            if novo_volume > capacidade_bateria:
                                vazio_disponivel = capacidade_bateria - qtd_atual
                                return {
                                    "status": "error",
                                    "message": f"⚠️ *Capacidade da Bateria Excedida!*\nUma recarga de *{kwh_novos:.2f} kWh* excede a capacidade (*{capacidade_bateria:.1f} kWh*).\n\n"
                                               f"Sua bateria já possui *{qtd_atual:.1f} kWh*. Você pode adicionar no máximo *{vazio_disponivel:.2f} kWh*.",
                                    "error_code": "EXCEDE_CAPACIDADE"
                                }

                            novo_custo = custo_atual + valor_decimal
                            dados_comb["kwh"] = float(novo_volume.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                            dados_comb["custo_total"] = float(novo_custo.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                        else:
                            # 2.2. ABASTECIMENTO LÍQUIDO (GASOLINA/ETANOL NO TANQUE ÚNICO)
                            dados_comb = estoque["liquido"]
                            qtd_atual = Decimal(str(dados_comb.get("litros", 0.0)))
                            custo_atual = Decimal(str(dados_comb.get("custo_total", 0.0)))

                            # Extrai litros
                            litros_novos = Decimal("0.00")
                            litros_match = re.search(r'(\d+[\.,]?\d*)\s*(?:litros|litro|l)', desc_limpa)
                            if litros_match:
                                litros_novos = Decimal(litros_match.group(1).replace(',', '.'))
                            else:
                                litros_novos = (valor_decimal / TransacaoService._PRECO_MEDIO_LITRO_FALLBACK).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                            novo_volume = qtd_atual + litros_novos
                            if novo_volume > capacidade_tanque:
                                vazio_disponivel = capacidade_tanque - qtd_atual
                                return {
                                    "status": "error",
                                    "message": f"⚠️ *Capacidade do Tanque Excedida!*\nO abastecimento de *{litros_novos:.2f} L* excede a capacidade (*{capacidade_tanque:.1f} L*).\n\n"
                                               f"Seu tanque já possui *{qtd_atual:.1f} L*. Você pode adicionar no máximo *{vazio_disponivel:.2f} L*.",
                                    "error_code": "EXCEDE_CAPACIDADE"
                                }

                            # Determina se é Gasolina, Etanol ou Mistura padrão Flex
                            is_gasolina = "gasolina" in desc_limpa or "gas" in desc_limpa
                            is_etanol = "etanol" in desc_limpa or "alcool" in desc_limpa or "alc" in desc_limpa

                            gas_atual = Decimal(str(dados_comb.get("gasolina_litros", qtd_atual if veiculo["tipo_combustivel"].lower() == "gasolina" else 0.0)))
                            eta_atual = Decimal(str(dados_comb.get("etanol_litros", qtd_atual if veiculo["tipo_combustivel"].lower() == "etanol" else 0.0)))

                            if is_gasolina:
                                gas_atual += litros_novos
                            elif is_etanol:
                                eta_atual += litros_novos
                            else:
                                # Caso não especificado em veículo Flex, divide meio a meio
                                if veiculo["is_flex"] or veiculo["is_hibrido"]:
                                    gas_atual += litros_novos / 2
                                    eta_atual += litros_novos / 2
                                elif veiculo["tipo_combustivel"].lower() == "etanol":
                                    eta_atual += litros_novos
                                else:
                                    gas_atual += litros_novos

                            total_calculado = gas_atual + eta_atual
                            
                            # Atualiza as proporções químicas exatas no tanque
                            if total_calculado > 0:
                                dados_comb["gasolina_proporcao"] = float((gas_atual / total_calculado).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                                dados_comb["etanol_proporcao"] = float((eta_atual / total_calculado).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                            else:
                                dados_comb["gasolina_proporcao"] = 1.0
                                dados_comb["etanol_proporcao"] = 0.0

                            dados_comb["gasolina_litros"] = float(gas_atual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                            dados_comb["etanol_litros"] = float(eta_atual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                            dados_comb["litros"] = float(total_calculado.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                            dados_comb["custo_total"] = float((custo_atual + valor_decimal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                        # Salva o estoque recalculado de volta no veículo
                        await conn.execute(
                            "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                            json.dumps(estoque), veiculo_id
                        )

                # 3. Insere a transação no Ledger de forma idempotente
                query_insert = """
                    INSERT INTO public.transacoes (
                        motorista_id, turno_id, veiculo_id, tipo_movimentacao, categoria, valor, descricao, wpp_msg_id
                    )
                    VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8)
                    ON CONFLICT (wpp_msg_id) DO NOTHING
                    RETURNING id, data_transacao;
                """
                row = await conn.fetchrow(
                    query_insert,
                    motorista_id,
                    turno_id,
                    veiculo_id,
                    tipo_validado,
                    categoria,
                    valor_decimal,
                    descricao,
                    wpp_msg_id
                )

                if row is None:
                    logger.warning(f"Lançamento duplicado evitado por idempotência contábil (MsgID: {wpp_msg_id}).")
                    return {
                        "status": "duplicate",
                        "message": "⚠️ Esse lançamento já foi guardado anteriormente no cofre contábil.",
                        "error_code": "DUPLICADA"
                    }

                msg_valor_formatado = f"R$ {float(valor_decimal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                return {
                    "status": "success",
                    "message": f"✅ Lançamento de *{msg_valor_formatado}* guardado no cofre! 🛡️",
                    "transacao_id": str(row["id"]),
                    "turno_id": turno_id,
                    "data_transacao": row["data_transacao"]
                }

        except asyncpg.PostgresError as exc:
            logger.exception("Exceção relacional no PostgreSQL ao registrar transação.")
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def estornar_transacao(motorista_id: str, transacao_id: str) -> Dict[str, Any]:
        """Aplica inversão lógica de lançamento (Soft-Delete) preservando a trilha de auditoria contábil."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE public.transacoes
                    SET estornado = TRUE
                    WHERE id = $1::uuid AND motorista_id = $2::uuid AND estornado = FALSE
                    RETURNING id, tipo_movimentacao, valor;
                    """,
                    transacao_id, motorista_id
                )

                if row is None:
                    return {
                        "status": "error",
                        "message": "❌ Lançamento não encontrado, não pertence ao seu perfil ou já foi estornado.",
                        "error_code": "TRANSACAO_INEXISTENTE"
                    }

                valor_estornado = float(row["valor"])
                tipo = str(row["tipo_movimentacao"]).capitalize()
                return {
                    "status": "success",
                    "message": f"🔄 Estorno concluído! {tipo} de *R$ {valor_estornado:,.2f}* anulada com sucesso no cofre.".replace(",", "X").replace(".", ",").replace("X", ".")
                }
        except asyncpg.PostgresError as exc:
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def obter_resumo_diario(motorista_id: str, data_referencia_iso: str) -> Dict[str, Any]:
        """Calcula o DRE parcial rápido do dia de forma altamente performática em nível de banco de dados."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'), 0.0000) AS total_receitas,
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'), 0.0000) AS total_despesas
                    FROM public.transacoes
                    WHERE motorista_id = $1::uuid
                      AND estornado = FALSE
                      AND DATE(data_transacao AT TIME ZONE 'America/Sao_Paulo') = $2::date;
                    """,
                    motorista_id, data_referencia_iso
                )

                receitas = Decimal(str(row["total_receitas"]))
                despesas = Decimal(str(row["total_despesas"]))
                saldo_liquido = receitas - despesas

                return {
                    "status": "success",
                    "data": data_referencia_iso,
                    "financeiro": {
                        "receitas": float(receitas),
                        "despesas": float(despesas),
                        "saldo_liquido": float(saldo_liquido)
                    }
                }
        except Exception as exc:
            return {"status": "error", "message": f"Falha ao consolidar o extrato diário: {exc}"}

