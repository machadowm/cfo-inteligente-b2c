import os
import re
import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Any, Optional
import asyncpg
from services.database_service import DatabaseService

# Configuração do Logger de Observabilidade para Rastreabilidade SRE
logger = logging.getLogger(__name__)

class TransacaoService:
    """
    Serviço Financeiro (Ledger) de Alta Precisão (Bank-Grade).
    
    Implementa a lógica de telemetria e recalibração dinâmica para veículos Híbridos, 
    Flex e Elétricos. Gerencia estoques químicos e elétricos via JSONB, garantindo 
    idempotência contábil e integridade aritmética com arredondamento ROUND_HALF_UP.
    """

    # Constantes de Validação e Fallbacks Financeiros
    _TIPOS_MOVIMENTACAO_VALIDOS = {"receita", "despesa"}
    _PRECO_MEDIO_LITRO_FALLBACK = Decimal("5.85")
    _PRECO_MEDIO_KWH_FALLBACK = Decimal("1.20")

    @staticmethod
    def _normalizar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        """Sanitiza strings para normalização de domínio."""
        return tipo_movimentacao.strip().lower()

    @staticmethod
    def _validar_tipo_movimentacao(tipo_movimentacao: str) -> str:
        """Garante a integridade do domínio de movimentação."""
        tipo_normalizado = TransacaoService._normalizar_tipo_movimentacao(tipo_movimentacao)
        if tipo_normalizado not in TransacaoService._TIPOS_MOVIMENTACAO_VALIDOS:
            raise ValueError("O tipo de movimentação deve ser 'receita' ou 'despesa'.")
        return tipo_normalizado

    @staticmethod
    def _validar_valor(valor: float, permitir_zero: bool = False) -> Decimal:
        """
        Converte e valida valores financeiros para precisão Decimal.
        Utiliza strings como intermediário para evitar imprecisões de ponto flutuante IEEE 754.
        """
        try:
            # Arredondamento para 2 casas decimais (Bank-Grade)
            valor_decimal = Decimal(str(valor)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
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
        """Tradução de exceções relacionais para mensagens de negócio (Domain Driven Errors)."""
        mensagem = str(exc)
        if "PERIODO_FECHADO" in mensagem:
            return {
                "status": "error",
                "message": "⚠ O período contábil já foi consolidado e trancado pela contabilidade. Não são permitidas alterações retroativas.",
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
        Registra movimentações financeiras com interceptação de telemetria.
        Realiza recalibração atômica de tanques/baterias em eventos de abastecimento.
        """
        desc_limpa = (descricao or "").lower()
        
        # Detecção de gratuidade baseada no Ground Truth (Solar, Casa, Grátis, Tomada)
        is_recarga_gratuita = any(term in desc_limpa for term in ["solar", "casa", "gratis", "tomada"])
        permitir_zero = categoria.lower() == "combustivel" and is_recarga_gratuita

        try:
            tipo_validado = TransacaoService._validar_tipo_movimentacao(tipo_movimentacao)
            valor_decimal = TransacaoService._validar_valor(valor, permitir_zero=permitir_zero)
        except ValueError as exc:
            return {"status": "error", "message": f"❌ {str(exc)}", "error_code": "VALIDACAO"}

        try:
            # get_tenant_connection injeta RLS e abre transação atômica implicitamente
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # 1. Identificação de Turno Ativo
                turno_ativo = await conn.fetchrow(
                    """
                    SELECT id, veiculo_id FROM public.turnos 
                    WHERE motorista_id = $1::uuid AND status IN ('em_andamento', 'em_pausa', 'ABERTO', 'PAUSADO')
                    ORDER BY data_inicio DESC LIMIT 1;
                    """, motorista_id
                )
                
                turno_id = str(turno_ativo["id"]) if turno_ativo else None
                veiculo_id = str(turno_ativo["veiculo_id"]) if turno_ativo else None

                if not veiculo_id:
                    veiculo_row = await conn.fetchrow(
                        "SELECT id FROM public.veiculos WHERE motorista_id = $1::uuid AND ativo = TRUE LIMIT 1;", 
                        motorista_id
                    )
                    veiculo_id = str(veiculo_row["id"]) if veiculo_row else None

                # 2. Telemetria e Recalibração de Combustível
                if tipo_validado == "despesa" and categoria.lower() == "combustivel" and veiculo_id:
                    veiculo = await conn.fetchrow(
                        """SELECT estoque_financeiro, tipo_combustivel,
                                  is_hibrido, is_eletrico, is_flex, capacidade_bateria
                           FROM public.veiculos WHERE id = $1::uuid;""",
                        veiculo_id
                    )
                    
                    if veiculo:
                        estoque_raw = veiculo["estoque_financeiro"]
                        estoque = json.loads(estoque_raw) if isinstance(estoque_raw, str) else (estoque_raw or {})
                        
                        # Bloco de Retrocompatibilidade (Backward Compatibility) - GROUND TRUTH
                        if "meta" not in estoque:
                            estoque["meta"] = {
                                "tipo_veiculo": veiculo["tipo_combustivel"] or "gasolina",
                                "is_flex": "flex" in (veiculo["tipo_combustivel"] or "").lower(),
                                "is_hibrido": "hibrido" in (veiculo["tipo_combustivel"] or "").lower(),
                                "is_eletrico": "eletrico" in (veiculo["tipo_combustivel"] or "").lower(),
                                "capacidade_tanque_l": 50.0,
                                "capacidade_bateria_kwh": 30.0,
                                "qtd_tanques": 1
                            }
                        if "liquido" not in estoque:
                            estoque["liquido"] = {
                                "litros": 0.0, "custo_total": 0.0, "gasolina_litros": 0.0, "etanol_litros": 0.0,
                                "gasolina_proporcao": 1.0, "etanol_proporcao": 0.0, "km_l_gasolina": 12.0, "km_l_etanol": 8.5
                            }
                        if "eletricidade" not in estoque:
                            estoque["eletricidade"] = {"kwh": 0.0, "custo_total": 0.0, "km_kwh": 6.5}
                        
                        meta = estoque["meta"]
                        capacidade_tanque = Decimal(str(meta.get("capacidade_tanque_l", "50.0")))
                        capacidade_bateria = Decimal(str(meta.get("capacidade_bateria_kwh", "30.0")))
                        tipo_veiculo = (veiculo["tipo_combustivel"] or meta.get("tipo_veiculo", "gasolina")).lower()

                        # Detecção de Energia (Regex Expandida conforme Ground Truth)
                        is_eletrico_event = (
                            "kwh" in desc_limpa or "recarga" in desc_limpa or "solar" in desc_limpa or
                            "eletrico" in desc_limpa or bool(meta.get("is_eletrico", False))
                        )
                        is_gnv_event = (
                            "gnv" in desc_limpa or "gas natural" in desc_limpa or
                            "m3" in desc_limpa or tipo_veiculo == "gnv"
                        )

                        # 2.1. Recarga Elétrica
                        if is_eletrico_event:
                            dados_elet = estoque["eletricidade"]
                            qtd_atual = Decimal(str(dados_elet.get("kwh", 0.0)))
                            
                            kwh_match = re.search(r'(\d+[.,]?\d*)\s*(?:kwh|kw|quilowatt)', desc_limpa)
                            if kwh_match:
                                kwh_novos = Decimal(kwh_match.group(1).replace(',', '.'))
                            else:
                                if is_recarga_gratuita:
                                    kwh_novos = Decimal("15.00") # Fallback Ground Truth para carga solar/doméstica
                                else:
                                    kwh_novos = (valor_decimal / TransacaoService._PRECO_MEDIO_KWH_FALLBACK)

                            novo_volume = (qtd_atual + kwh_novos).quantize(Decimal("0.01"), ROUND_HALF_UP)
                            if novo_volume > capacidade_bateria:
                                vazio_disponivel = capacidade_bateria - qtd_atual
                                return {
                                    "status": "error",
                                    "message": (
                                        f"⚠ *Capacidade da Bateria Excedida!* \nUma recarga de *{kwh_novos:.2f} kWh* "
                                        f"excede a capacidade (*{capacidade_bateria:.1f} kWh*).\n\n"
                                        f"Sua bateria já possui *{qtd_atual:.1f} kWh*. Você pode adicionar no máximo *{vazio_disponivel:.2f} kWh*."
                                    ),
                                    "error_code": "EXCEDE_CAPACIDADE"
                                }
                            
                            dados_elet["kwh"] = float(novo_volume)
                            dados_elet["custo_total"] = float((Decimal(str(dados_elet["custo_total"])) + valor_decimal).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            estoque["eletricidade"] = dados_elet

                        # 2.2. Abastecimento GNV (m³)
                        elif is_gnv_event:
                            if "gnv" not in estoque:
                                estoque["gnv"] = {"m3": 0.0, "custo_total": 0.0, "km_m3": 14.0}
                            dados_gnv = estoque["gnv"]
                            m3_atual = Decimal(str(dados_gnv.get("m3", 0.0)))

                            m3_match = re.search(r'(\d+[.,]?\d*)\s*(?:m3|metros? cubicos?|m³)', desc_limpa)
                            if m3_match:
                                m3_novos = Decimal(m3_match.group(1).replace(',', '.'))
                            else:
                                # Preço médio de referência GNV: ~3,50/m³
                                m3_novos = (valor_decimal / Decimal("3.50")).quantize(Decimal("0.01"), ROUND_HALF_UP)

                            dados_gnv["m3"] = float((m3_atual + m3_novos).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            dados_gnv["custo_total"] = float((Decimal(str(dados_gnv["custo_total"])) + valor_decimal).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            estoque["gnv"] = dados_gnv

                        # 2.3. Abastecimento Líquido (Mix Químico Flex/Gasolina/Etanol)
                        else:
                            dados_liq = estoque["liquido"]
                            qtd_atual = Decimal(str(dados_liq.get("litros", 0.0)))
                            
                            litros_match = re.search(r'(\d+[.,]?\d*)\s*(?:litros|litro|l)', desc_limpa)
                            if litros_match:
                                litros_novos = Decimal(litros_match.group(1).replace(',', '.'))
                            else:
                                litros_novos = (valor_decimal / TransacaoService._PRECO_MEDIO_LITRO_FALLBACK)

                            novo_vol_liq = (qtd_atual + litros_novos).quantize(Decimal("0.01"), ROUND_HALF_UP)
                            if novo_vol_liq > capacidade_tanque:
                                vazio_disponivel = capacidade_tanque - qtd_atual
                                return {
                                    "status": "error",
                                    "message": (
                                        f"⚠ *Capacidade do Tanque Excedida!* \nO abastecimento de *{litros_novos:.2f} L* "
                                        f"excede a capacidade (*{capacidade_tanque:.1f} L*).\n\n"
                                        f"Seu tanque já possui *{qtd_atual:.1f} L*. Você pode adicionar no máximo *{vazio_disponivel:.2f} L*."
                                    ),
                                    "error_code": "EXCEDE_CAPACIDADE"
                                }

                            # Lógica de Mix Químico Refinada
                            is_gasolina = "gasolina" in desc_limpa or "gas" in desc_limpa
                            is_etanol = "etanol" in desc_limpa or "alcool" in desc_limpa or "alc" in desc_limpa
                            
                            gas_litros = Decimal(str(dados_liq.get("gasolina_litros", 0.0)))
                            eta_litros = Decimal(str(dados_liq.get("etanol_litros", 0.0)))

                            if is_gasolina: 
                                gas_litros += litros_novos
                            elif is_etanol: 
                                eta_litros += litros_novos
                            else: 
                                # Fallback Flex/Híbrido (50/50) ou combustível principal do veículo
                                if bool(meta.get("is_flex", False)) or bool(meta.get("is_hibrido", False)):
                                    gas_litros += litros_novos / 2
                                    eta_litros += litros_novos / 2
                                elif veiculo["tipo_combustivel"].lower() == "etanol":
                                    eta_litros += litros_novos
                                else:
                                    gas_litros += litros_novos

                            total_tanque = gas_litros + eta_litros
                            if total_tanque > 0:
                                # Precisão de 4 casas para proporção (Engenharia Química)
                                dados_liq["gasolina_proporcao"] = float((gas_litros / total_tanque).quantize(Decimal("0.0001"), ROUND_HALF_UP))
                                dados_liq["etanol_proporcao"] = float((eta_litros / total_tanque).quantize(Decimal("0.0001"), ROUND_HALF_UP))
                            
                            dados_liq["gasolina_litros"] = float(gas_litros.quantize(Decimal("0.01"), ROUND_HALF_UP))
                            dados_liq["etanol_litros"] = float(eta_litros.quantize(Decimal("0.01"), ROUND_HALF_UP))
                            dados_liq["litros"] = float(total_tanque.quantize(Decimal("0.01"), ROUND_HALF_UP))
                            dados_liq["custo_total"] = float((Decimal(str(dados_liq["custo_total"])) + valor_decimal).quantize(Decimal("0.01"), ROUND_HALF_UP))
                            estoque["liquido"] = dados_liq

                        # Persistência Atômica da recalibração no JSONB
                        await conn.execute(
                            "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                            json.dumps(estoque), veiculo_id
                        )

                # 3. Inserção no Livro-Razão (Idempotência via wpp_msg_id)
                query_insert = """
                INSERT INTO public.transacoes (
                    motorista_id, turno_id, veiculo_id, tipo_movimentacao, categoria, valor, descricao, wpp_msg_id
                ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8)
                ON CONFLICT (wpp_msg_id) DO NOTHING
                RETURNING id, data_transacao;
                """
                row = await conn.fetchrow(
                    query_insert, motorista_id, turno_id, veiculo_id, tipo_validado, categoria, valor_decimal, descricao, wpp_msg_id
                )

                if row is None:
                    logger.warning(f"Lançamento duplicado ignorado: {wpp_msg_id}")
                    return {
                        "status": "duplicate", 
                        "message": "⚠ Esse lançamento já foi guardado anteriormente no cofre contábil.",
                        "error_code": "DUPLICADA"
                    }

                valor_fmt = f"R$ {float(valor_decimal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                return {
                    "status": "success",
                    "message": f"✅ Lançamento de *{valor_fmt}* guardado no cofre! 🛡",
                    "transacao_id": str(row["id"]),
                    "data_transacao": row["data_transacao"]
                }

        except asyncpg.PostgresError as exc:
            logger.exception("Erro crítico de banco de dados.")
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def estornar_transacao(motorista_id: str, transacao_id: str) -> Dict[str, Any]:
        """Realiza a inversão lógica de lançamento preservando auditoria contábil."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE public.transacoes SET estornado = TRUE 
                    WHERE id = $1::uuid AND motorista_id = $2::uuid AND estornado = FALSE
                    RETURNING id, valor, tipo_movimentacao;
                    """, transacao_id, motorista_id
                )
                if row is None:
                    return {"status": "error", "message": "❌ Lançamento não localizado ou já estornado."}
                
                valor_estornado = f"R$ {float(row['valor']):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                tipo = str(row["tipo_movimentacao"]).capitalize()
                return {
                    "status": "success", 
                    "message": f"🔄 Estorno concluído! {tipo} de *{valor_estornado}* anulada com sucesso no cofre."
                }
        except asyncpg.PostgresError as exc:
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def obter_resumo_diario(motorista_id: str, data_iso: str) -> Dict[str, Any]:
        """Agregação SQL para cálculo de saldo líquido com timezone Brasília."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    SELECT 
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'), 0.0000) AS total_receitas,
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'), 0.0000) AS total_despesas
                    FROM public.transacoes
                    WHERE motorista_id = $1::uuid AND estornado = FALSE 
                    AND DATE(data_transacao AT TIME ZONE 'America/Sao_Paulo') = $2::date;
                    """, motorista_id, data_iso
                )
                
                receitas = Decimal(str(row["total_receitas"]))
                despesas = Decimal(str(row["total_despesas"]))
                
                return {
                    "status": "success",
                    "data": data_iso,
                    "financeiro": {
                        "receitas": float(receitas),
                        "despesas": float(despesas),
                        "saldo_liquido": float(receitas - despesas)
                    }
                }
        except Exception as exc:
            logger.error(f"Erro ao obter resumo: {exc}")
            return {"status": "error", "message": f"Falha ao consolidar extrato: {exc}"}
