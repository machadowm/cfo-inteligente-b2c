import re
import json
import logging
import hashlib
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Dict, Any, Optional

import asyncpg

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)


class TransacaoService:
    """
    Ledger Financeiro de Alta Precisão (Decimal).

    Implementa a Dicotomia Estoque vs. Queima (Regime de Caixa para abastecimentos
    e Regime de Competência para queima no fechamento do turno):
      - Abastecimento: converte o valor pago em volume físico e atualiza o JSONB
        'estoque_financeiro' com recálculo de CMP. O valor NÃO entra no DRE como
        despesa imediata.
      - Consumo: amortização calculada no fechamento do turno por TurnoService.

    Suporte multi-energia: tanque único Flex (blend gasolina/etanol), elétrico (kWh),
    GNV (m³) e híbrido (EV Priority).
    """

    _TIPOS_MOVIMENTACAO_VALIDOS = {"receita", "despesa"}
    _PRECO_MEDIO_LITRO_FALLBACK = Decimal("5.85")
    _PRECO_MEDIO_KWH_FALLBACK = Decimal("1.20")
    _PRECO_MEDIO_M3_GNV_FALLBACK = Decimal("4.50")

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _normalizar_tipo(tipo: str) -> str:
        return tipo.strip().lower()

    @staticmethod
    def _validar_tipo(tipo: str) -> str:
        t = TransacaoService._normalizar_tipo(tipo)
        if t not in TransacaoService._TIPOS_MOVIMENTACAO_VALIDOS:
            raise ValueError("Tipo de movimentação deve ser 'receita' ou 'despesa'.")
        return t

    @staticmethod
    def _validar_valor(valor: float, permitir_zero: bool = False) -> Decimal:
        try:
            d = Decimal(str(valor))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("Valor financeiro mal formatado.") from exc
        if permitir_zero:
            if d < Decimal("0"):
                raise ValueError("O valor financeiro não pode ser negativo.")
        else:
            if d <= Decimal("0"):
                raise ValueError("O valor financeiro deve ser maior que zero.")
        return d

    @staticmethod
    def _garantir_estrutura_estoque(estoque: dict) -> dict:
        """
        Garante que todas as chaves estruturais do JSONB existam.
        A sub-chave 'meta' contém os dados físicos e de motorização do veículo
        (capacidades, flags de tipo) — eliminando a necessidade de colunas
        dedicadas na tabela veiculos.
        """
        if "meta" not in estoque:
            estoque["meta"] = {
                "tipo_veiculo": "gasolina",
                "is_flex": False,
                "is_hibrido": False,
                "is_eletrico": False,
                "capacidade_tanque_l": 50.0,
                "capacidade_bateria_kwh": 0.0,
                "qtd_tanques": 1,
            }
        if "liquido" not in estoque:
            estoque["liquido"] = {
                "litros": 0.0,
                "custo_total": 0.0,
                "gasolina_litros": 0.0,
                "etanol_litros": 0.0,
                "gasolina_proporcao": 1.0,
                "etanol_proporcao": 0.0,
                "km_l_gasolina": 12.0,
                "km_l_etanol": 8.5,
            }
        if "eletricidade" not in estoque:
            estoque["eletricidade"] = {
                "kwh": 0.0,
                "custo_total": 0.0,
                "km_kwh": 6.5,
            }
        if "gnv" not in estoque:
            estoque["gnv"] = {
                "m3": 0.0,
                "custo_total": 0.0,
                "km_m3": 14.0,
            }
        return estoque

    @staticmethod
    def _mapear_erro_postgres(exc: Exception) -> Dict[str, Any]:
        msg = str(exc)
        if "PERIODO_FECHADO" in msg:
            return {
                "status": "error",
                "message": "⚠️ O período contábil já foi consolidado. Não são permitidas alterações retroativas.",
                "error_code": "PERIODO_FECHADO",
            }
        return {
            "status": "error",
            "message": f"Falha na integridade do cofre contábil: {msg}",
            "error_code": "ERRO_BANCO",
        }

    # ------------------------------------------------------------------ público

    @staticmethod
    async def registrar_transacao(
        motorista_id: str,
        tipo_movimentacao: str,
        categoria: str,
        valor: float,
        descricao: Optional[str] = None,
        wpp_msg_id: Optional[str] = None,
        litros_informados: Optional[float] = None,
        preco_por_litro: Optional[float] = None,
        odometro_abastecimento: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        Registra uma receita ou despesa no ledger.

        Para despesas do tipo 'combustivel':
          1. Identifica o sub-estoque correto (líquido, elétrico ou GNV).
          2. Valida a Barreira de Estanqueidade Física (não ultrapassa capacidade do tanque/bateria).
          3. Usa litros_informados se fornecido; caso contrário extrai da descrição ou estima via CMP.
          4. Executa o blend Flex proporcional no tanque único homogêneo.
          5. Recalcula o CMP da energia após a mistura.
          6. Persiste o JSONB atualizado no registro do veículo.

        Campos opcionais de abastecimento:
          - litros_informados: volume exato abastecido informado pelo motorista.
          - preco_por_litro: preço unitário pago, salvo na transação para rastreabilidade.
          - odometro_abastecimento: leitura do odômetro no momento do abastecimento.

        O valor pago NÃO é lançado como custo no DRE — permanece como ativo circulante
        (estoque de energia) até a queima proporcional no fechamento do turno.

        Idempotência: wpp_msg_id nunca será NULL — se vier vazio, gera hash SHA-256
        determinístico para blindar contra retentativas duplicadas de rede.
        """
        desc_limpa = (descricao or "").lower()
        is_recarga_gratuita = any(
            w in desc_limpa for w in ("solar", "casa", "gratis", "tomada", "gratuito")
        )
        e_combustivel = categoria.lower() == "combustivel"
        permitir_zero = e_combustivel and is_recarga_gratuita

        # Garante wpp_msg_id único — ON CONFLICT (wpp_msg_id) não dispara para NULL em SQL
        if not wpp_msg_id:
            raw = f"{motorista_id}|{tipo_movimentacao}|{categoria}|{valor}|{desc_limpa}"
            wpp_msg_id = "hash:" + hashlib.sha256(raw.encode()).hexdigest()[:32]

        try:
            tipo_validado = TransacaoService._validar_tipo(tipo_movimentacao)
            valor_decimal = TransacaoService._validar_valor(valor, permitir_zero=permitir_zero)
        except ValueError as exc:
            return {"status": "error", "message": f"❌ {exc}", "error_code": "VALIDACAO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:

                # ------------------------------------------------------------------
                # 1. Resolve turno ativo (para vinculação da transação)
                # ------------------------------------------------------------------
                turno_row = await conn.fetchrow(
                    """
                    SELECT id, veiculo_id
                    FROM public.turnos
                    WHERE motorista_id = $1::uuid
                      AND status IN ('em_andamento', 'em_pausa')
                    ORDER BY data_inicio DESC
                    LIMIT 1;
                    """,
                    motorista_id,
                )
                turno_id = str(turno_row["id"]) if turno_row else None
                veiculo_id = str(turno_row["veiculo_id"]) if turno_row else None

                # Fora de turno: busca veículo principal ativo
                if not veiculo_id:
                    vei_row = await conn.fetchrow(
                        "SELECT id FROM public.veiculos WHERE motorista_id = $1::uuid AND ativo = TRUE LIMIT 1;",
                        motorista_id,
                    )
                    veiculo_id = str(vei_row["id"]) if vei_row else None

                # ------------------------------------------------------------------
                # 2. Lógica de abastecimento / recarga de energia
                # ------------------------------------------------------------------
                if tipo_validado == "despesa" and e_combustivel and veiculo_id:
                    veiculo = await conn.fetchrow(
                        "SELECT estoque_financeiro, tipo_combustivel FROM public.veiculos WHERE id = $1::uuid;",
                        veiculo_id,
                    )

                    if veiculo:
                        raw_est = veiculo["estoque_financeiro"]
                        estoque: dict = (
                            json.loads(raw_est) if isinstance(raw_est, str) else (raw_est or {})
                        )
                        estoque = TransacaoService._garantir_estrutura_estoque(estoque)

                        # Capacidades e flags de motorização lidos do JSONB (sub-chave 'meta')
                        meta = estoque["meta"]
                        cap_tanque = Decimal(str(meta.get("capacidade_tanque_l", 50.0)))
                        cap_bateria = Decimal(str(meta.get("capacidade_bateria_kwh", 0.0)))
                        tipo_comb = (veiculo["tipo_combustivel"] or meta.get("tipo_veiculo", "gasolina")).lower()

                        # Determina a fonte de energia do evento
                        is_evento_eletrico = (
                            any(w in desc_limpa for w in ("kwh", "recarga", "solar", "eletrico", "tomada", "casa"))
                            or bool(meta.get("is_eletrico", False))
                        )
                        is_evento_gnv = (
                            "gnv" in desc_limpa
                            or "gas natural" in desc_limpa
                            or tipo_comb == "gnv"
                        ) and not is_evento_eletrico

                        if is_evento_eletrico:
                            # -------------------------------------------------------
                            # 2-A  RECARGA ELÉTRICA (BATERIA)
                            # -------------------------------------------------------
                            eletro = estoque["eletricidade"]
                            kwh_atual = Decimal(str(eletro.get("kwh", 0.0)))
                            custo_atual = Decimal(str(eletro.get("custo_total", 0.0)))

                            kwh_novos = Decimal("0.00")
                            m = re.search(r"(\d+[\.,]?\d*)\s*(?:kwh|kw\b|quilowatt)", desc_limpa)
                            if m:
                                kwh_novos = Decimal(m.group(1).replace(",", "."))
                            elif is_recarga_gratuita:
                                kwh_novos = Decimal("15.00")  # Fallback: carga solar doméstica média
                            else:
                                kwh_novos = (
                                    valor_decimal / TransacaoService._PRECO_MEDIO_KWH_FALLBACK
                                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                            novo_total_kwh = kwh_atual + kwh_novos
                            if novo_total_kwh > cap_bateria:
                                livre = cap_bateria - kwh_atual
                                return {
                                    "status": "error",
                                    "message": (
                                        f"⚠️ *Capacidade da Bateria Excedida!*\n"
                                        f"Uma recarga de *{kwh_novos:.2f} kWh* ultrapassa a capacidade máxima "
                                        f"(*{cap_bateria:.1f} kWh*).\n\n"
                                        f"Bateria atual: *{kwh_atual:.1f} kWh*. "
                                        f"Espaço livre: *{livre:.2f} kWh*."
                                    ),
                                    "error_code": "EXCEDE_CAPACIDADE",
                                }

                            eletro["kwh"] = float(
                                novo_total_kwh.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )
                            eletro["custo_total"] = float(
                                (custo_atual + valor_decimal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )

                        elif is_evento_gnv:
                            # -------------------------------------------------------
                            # 2-B  ABASTECIMENTO GNV (m³)
                            # -------------------------------------------------------
                            gnv = estoque["gnv"]
                            m3_atual = Decimal(str(gnv.get("m3", 0.0)))
                            custo_atual = Decimal(str(gnv.get("custo_total", 0.0)))

                            m3_novos = Decimal("0.00")
                            m = re.search(r"(\d+[\.,]?\d*)\s*(?:m3|m³|metro)", desc_limpa)
                            if m:
                                m3_novos = Decimal(m.group(1).replace(",", "."))
                            else:
                                m3_novos = (
                                    valor_decimal / TransacaoService._PRECO_MEDIO_M3_GNV_FALLBACK
                                ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                            gnv["m3"] = float(
                                (m3_atual + m3_novos).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )
                            gnv["custo_total"] = float(
                                (custo_atual + valor_decimal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )

                        else:
                            # -------------------------------------------------------
                            # 2-C  ABASTECIMENTO LÍQUIDO (TANQUE ÚNICO FLEX/GASOLINA/ETANOL)
                            # -------------------------------------------------------
                            liq = estoque["liquido"]
                            litros_atual = Decimal(str(liq.get("litros", 0.0)))
                            custo_atual = Decimal(str(liq.get("custo_total", 0.0)))

                            litros_novos = Decimal("0.00")
                            if litros_informados is not None and litros_informados > 0:
                                # Valor exato fornecido pelo motorista via fluxo guiado
                                litros_novos = Decimal(str(litros_informados)).quantize(
                                    Decimal("0.001"), rounding=ROUND_HALF_UP
                                )
                            else:
                                # Tenta extrair da descrição livre; \bl\b evita "lava", "legal", etc.
                                m = re.search(r"(\d+[\.,]?\d*)\s*(?:litros|litro|\bl\b)", desc_limpa)
                                if m:
                                    litros_novos = Decimal(m.group(1).replace(",", "."))
                                else:
                                    litros_novos = (
                                        valor_decimal / TransacaoService._PRECO_MEDIO_LITRO_FALLBACK
                                    ).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                            novo_total_litros = litros_atual + litros_novos
                            if novo_total_litros > cap_tanque:
                                livre = cap_tanque - litros_atual
                                return {
                                    "status": "error",
                                    "message": (
                                        f"⚠️ *Capacidade do Tanque Excedida!*\n"
                                        f"O abastecimento de *{litros_novos:.2f} L* ultrapassa a capacidade máxima "
                                        f"(*{cap_tanque:.1f} L*).\n\n"
                                        f"Tanque atual: *{litros_atual:.1f} L*. "
                                        f"Espaço livre: *{livre:.2f} L*."
                                    ),
                                    "error_code": "EXCEDE_CAPACIDADE",
                                }

                            # Alquimia Flex: classifica o combustível pelo descritor
                            # \bgas\b para não casar "gastei", "gastos", etc.
                            is_gasolina = "gasolina" in desc_limpa or bool(re.search(r"\bgas\b", desc_limpa))
                            is_etanol = (
                                "etanol" in desc_limpa
                                or bool(re.search(r"\balcool\b", desc_limpa))
                                or bool(re.search(r"\balc\b", desc_limpa))
                            )

                            gas_atual = Decimal(
                                str(liq.get("gasolina_litros", float(litros_atual) if tipo_comb == "gasolina" else 0.0))
                            )
                            eta_atual = Decimal(
                                str(liq.get("etanol_litros", float(litros_atual) if tipo_comb == "etanol" else 0.0))
                            )

                            if is_gasolina:
                                gas_atual += litros_novos
                            elif is_etanol:
                                eta_atual += litros_novos
                            else:
                                # Combustível não especificado em veículo Flex: divide 50/50
                                if meta.get("is_flex", False) or meta.get("is_hibrido", False):
                                    metade = litros_novos / Decimal("2")
                                    gas_atual += metade
                                    eta_atual += metade
                                elif tipo_comb == "etanol":
                                    eta_atual += litros_novos
                                else:
                                    gas_atual += litros_novos

                            total_calc = gas_atual + eta_atual

                            # Recalcula proporções exatas do blend homogêneo
                            if total_calc > Decimal("0"):
                                liq["gasolina_proporcao"] = float(
                                    (gas_atual / total_calc).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                                )
                                liq["etanol_proporcao"] = float(
                                    (eta_atual / total_calc).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                                )
                            else:
                                liq["gasolina_proporcao"] = 1.0
                                liq["etanol_proporcao"] = 0.0

                            liq["gasolina_litros"] = float(
                                gas_atual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )
                            liq["etanol_litros"] = float(
                                eta_atual.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )
                            liq["litros"] = float(
                                total_calc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )
                            # Novo CMP = (Custo Total Antigo + Valor Novo) / Volume Total Novo
                            liq["custo_total"] = float(
                                (custo_atual + valor_decimal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                            )

                        # Persiste o estoque recalculado no JSONB do veículo
                        await conn.execute(
                            "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                            json.dumps(estoque),
                            veiculo_id,
                        )

                # ------------------------------------------------------------------
                # 3. Insere a transação no ledger (idempotência via wpp_msg_id)
                # ------------------------------------------------------------------
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.transacoes
                        (motorista_id, turno_id, veiculo_id, tipo_movimentacao, categoria, valor, descricao,
                         wpp_msg_id, litros_abastecidos, preco_por_litro, odometro_abastecimento)
                    VALUES
                        ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8, $9, $10, $11)
                    ON CONFLICT (wpp_msg_id) DO NOTHING
                    RETURNING id, data_transacao;
                    """,
                    motorista_id,
                    turno_id,
                    veiculo_id,
                    tipo_validado,
                    categoria,
                    valor_decimal,
                    descricao,
                    wpp_msg_id,
                    float(litros_informados) if litros_informados is not None else None,
                    float(preco_por_litro) if preco_por_litro is not None else None,
                    float(odometro_abastecimento) if odometro_abastecimento is not None else None,
                )

                if row is None:
                    logger.warning("Lançamento duplicado bloqueado por idempotência (MsgID: %s).", wpp_msg_id)
                    return {
                        "status": "duplicate",
                        "message": "⚠️ Esse lançamento já foi guardado anteriormente no cofre contábil.",
                        "error_code": "DUPLICADA",
                    }

                val_fmt = f"R$ {float(valor_decimal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                return {
                    "status": "success",
                    "message": f"✅ Lançamento de *{val_fmt}* guardado no cofre! 🛡️",
                    "transacao_id": str(row["id"]),
                    "turno_id": turno_id,
                    "data_transacao": row["data_transacao"],
                }

        except asyncpg.PostgresError as exc:
            logger.exception("Exceção PostgreSQL ao registrar transação.")
            return TransacaoService._mapear_erro_postgres(exc)

    # ------------------------------------------------------------------ extras

    @staticmethod
    async def estornar_transacao(motorista_id: str, transacao_id: str) -> Dict[str, Any]:
        """Soft-delete lógico de um lançamento preservando a trilha de auditoria contábil."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                row = await conn.fetchrow(
                    """
                    UPDATE public.transacoes
                    SET estornado = TRUE
                    WHERE id = $1::uuid AND motorista_id = $2::uuid AND estornado = FALSE
                    RETURNING id, tipo_movimentacao, valor;
                    """,
                    transacao_id,
                    motorista_id,
                )
                if row is None:
                    return {
                        "status": "error",
                        "message": "❌ Lançamento não encontrado, não pertence ao seu perfil ou já foi estornado.",
                        "error_code": "TRANSACAO_INEXISTENTE",
                    }
                val = float(row["valor"])
                tipo = str(row["tipo_movimentacao"]).capitalize()
                val_fmt = f"R$ {val:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                return {
                    "status": "success",
                    "message": f"🔄 Estorno concluído! {tipo} de *{val_fmt}* anulada com sucesso no cofre.",
                }
        except asyncpg.PostgresError as exc:
            return TransacaoService._mapear_erro_postgres(exc)

    @staticmethod
    async def obter_resumo_diario(motorista_id: str, data_referencia_iso: str) -> Dict[str, Any]:
        """DRE parcial rápido do dia calculado inteiramente em nível de banco de dados."""
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
                    motorista_id,
                    data_referencia_iso,
                )
                receitas = Decimal(str(row["total_receitas"]))
                despesas = Decimal(str(row["total_despesas"]))
                return {
                    "status": "success",
                    "data": data_referencia_iso,
                    "financeiro": {
                        "receitas": float(receitas),
                        "despesas": float(despesas),
                        "saldo_liquido": float(receitas - despesas),
                    },
                }
        except Exception as exc:
            return {"status": "error", "message": f"Falha ao consolidar extrato diário: {exc}"}
