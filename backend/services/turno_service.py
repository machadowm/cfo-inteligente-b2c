import json
import logging
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from datetime import datetime
from typing import Dict, Any, Optional

import pytz
import asyncpg

from services.database_service import DatabaseService

logger = logging.getLogger(__name__)

TZ_BR = pytz.timezone("America/Sao_Paulo")


def agora_brasil() -> datetime:
    """Retorna o timestamp corrente sincronizado no fuso de Brasília (America/Sao_Paulo)."""
    return datetime.now(TZ_BR)


class TurnoService:
    """
    Motor de Queima, Competência e DRE.

    Implementa o Algoritmo Power Split Híbrido (EV Priority):
      1. Prioriza consumo da bateria elétrica (kWh) nos veículos Elétrico/Híbrido.
      2. Redireciona KMs excedentes para o tanque único Flex com rendimento médio ponderado.
      3. Suporte a GNV como fonte alternativa de combustão.
      4. Em estoque zerado, recorre a estimativa proporcional via ledger do turno.

    A amortização do custo de combustível é calculada pelo CMP (Custo Médio Ponderado)
    real acumulado no JSONB 'estoque_financeiro', garantindo aderência ao Regime de
    Competência no fechamento do DRE.
    """

    # ------------------------------------------------------------------ helpers

    @staticmethod
    def _validar_km(valor_km: float, campo: str) -> Decimal:
        try:
            km = Decimal(str(valor_km))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"O valor de {campo} está mal formatado.") from exc
        if km < Decimal("0"):
            raise ValueError(f"O valor de {campo} não pode ser negativo.")
        return km

    @staticmethod
    def _garantir_estrutura_estoque(estoque: dict) -> dict:
        """Retrocompatibilidade: adiciona chaves ausentes sem sobrescrever dados existentes."""
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

    # ------------------------------------------------------------------ público

    @staticmethod
    async def abrir_turno(motorista_id: str, veiculo_id: str, km_inicial: float) -> Dict[str, Any]:
        """
        Abre um novo turno com validação de monotonicidade estrita do odômetro
        em relação ao último fechamento registrado para este veículo.
        """
        try:
            km_ini = TurnoService._validar_km(km_inicial, "km_inicial")
        except ValueError as exc:
            return {"sucesso": False, "erro": f"❌ {exc}", "tipo_erro": "KM_INVALIDO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:

                # Garante que não há turno em aberto
                turno_ativo = await conn.fetchrow(
                    "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa');",
                    motorista_id,
                )
                if turno_ativo:
                    return {
                        "sucesso": False,
                        "erro": "⚠️ Você já possui uma jornada em andamento. Encerre o turno atual antes de abrir outro.",
                        "tipo_erro": "TURNO_JA_ATIVO",
                    }

                # Valida monotonicidade contra o odômetro final do turno anterior
                ultimo = await conn.fetchrow(
                    """
                    SELECT km_final FROM public.turnos
                    WHERE veiculo_id = $1::uuid AND status = 'concluido' AND km_final IS NOT NULL
                    ORDER BY data_fim DESC LIMIT 1;
                    """,
                    veiculo_id,
                )
                if ultimo and ultimo["km_final"] is not None:
                    km_anterior = Decimal(str(ultimo["km_final"]))
                    if km_ini < km_anterior:
                        km_ini_fmt = f"{float(km_ini):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        km_ant_fmt = f"{float(km_anterior):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        return {
                            "sucesso": False,
                            "erro": (
                                f"⚠️ *Odômetro Divergente!*\n"
                                f"O valor informado (*{km_ini_fmt} km*) é menor que o odômetro final do último turno "
                                f"(*{km_ant_fmt} km*).\n\n"
                                f"Por favor, envie o *valor correto* atual do painel do seu veículo:"
                            ),
                            "tipo_erro": "ODOMETRO_DIVERGENTE",
                        }

                row = await conn.fetchrow(
                    """
                    INSERT INTO public.turnos (motorista_id, veiculo_id, km_inicial, status, data_inicio)
                    VALUES ($1::uuid, $2::uuid, $3, 'ABERTO', $4)
                    RETURNING id, km_inicial, data_inicio;
                    """,
                    motorista_id, veiculo_id, km_ini, agora_brasil(),
                )
                return {
                    "sucesso": True,
                    "turno_id": str(row["id"]),
                    "km_inicial": float(row["km_inicial"]),
                    "data_inicio": row["data_inicio"],
                }

        except Exception as exc:
            logger.exception("Erro crítico ao abrir turno.")
            return {"sucesso": False, "erro": f"Erro interno ao abrir turno: {exc}", "tipo_erro": "ERRO_INTERNO"}

    @staticmethod
    async def fechar_turno_com_dre(motorista_id: str, km_final: float) -> Dict[str, Any]:
        """
        Encerra o turno ativo e executa o algoritmo completo de fechamento contábil:

          1. Valida monotonicidade estrita do odômetro (km_final >= km_inicial).
          2. Calcula tempo operacional total e efetivo (bruto menos pausas acumuladas).
          3. Power Split Híbrido (EV Priority):
               - Fase 1: Queima da bateria elétrica (kWh) até esgotar autonomia ou km_restante.
               - Fase 2: Queima do GNV (m³) se configurado.
               - Fase 3: Queima do tanque único Flex com rendimento médio ponderado do blend.
               - Fase 4: Fallback proporcional via ledger do turno se estoque JSONB zerado.
          4. Atualiza o JSONB 'estoque_financeiro' do veículo com os volumes residuais.
          5. Apura o DRE: faturamento bruto, custos variáveis (pista + queima amortizada),
             rateio pro-rata de custo fixo contratual e lucro líquido real.
          6. Persiste snapshot contábil na tabela 'fechamento_diario'.
        """
        try:
            km_fin = TurnoService._validar_km(km_final, "km_final")
        except ValueError as exc:
            return {"sucesso": False, "erro": f"❌ {exc}", "tipo_erro": "KM_INVALIDO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:

                # ---------------------------------------------------------------
                # 1. Resgata dados do turno ativo, veículo e motorista
                # ---------------------------------------------------------------
                turno = await conn.fetchrow(
                    """
                    SELECT
                        t.id, t.km_inicial, t.data_inicio,
                        v.id AS veiculo_id,
                        v.estoque_financeiro, v.tipo_combustivel,
                        v.is_flex, v.is_hibrido, v.is_eletrico,
                        v.capacidade_tanque, v.capacidade_bateria,
                        v.locadora, v.custo_aluguel_semanal, v.franquia_km_semanal,
                        v.valor_km_excedente, v.escala_trabalho, v.contrato_personalizado,
                        m.meta_mensal_faturamento, m.dias_uteis_mes
                    FROM public.turnos t
                    JOIN public.veiculos v ON v.id = t.veiculo_id
                    JOIN public.motoristas m ON m.id = t.motorista_id
                    WHERE t.motorista_id = $1::uuid
                      AND t.status IN ('ABERTO', 'em_andamento', 'em_pausa')
                    ORDER BY t.data_inicio DESC
                    LIMIT 1;
                    """,
                    motorista_id,
                )

                if not turno:
                    return {
                        "sucesso": False,
                        "erro": "⚠️ Nenhum turno ativo em andamento foi localizado para este motorista.",
                        "tipo_erro": "NENHUM_TURNO_ATIVO",
                    }

                turno_id = str(turno["id"])
                veiculo_id = str(turno["veiculo_id"])
                km_ini = Decimal(str(turno["km_inicial"]))

                # Validação de monotonicidade estrita do odômetro final
                if km_fin < km_ini:
                    km_fin_fmt = f"{float(km_fin):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    km_ini_fmt = f"{float(km_ini):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    return {
                        "sucesso": False,
                        "erro": (
                            f"⚠️ *Odômetro Final Divergente!*\n"
                            f"O valor informado (*{km_fin_fmt} km*) é inferior ao odômetro inicial "
                            f"(*{km_ini_fmt} km*).\n\n"
                            f"Por favor, envie o *valor correto* atual do painel do seu veículo:"
                        ),
                        "tipo_erro": "ODOMETRO_DIVERGENTE",
                    }

                km_rodados = km_fin - km_ini
                hora_fim = agora_brasil()

                # Marca o turno como concluído imediatamente (antes das queries analíticas)
                await conn.execute(
                    "UPDATE public.turnos SET km_final = $1, data_fim = $2, status = 'concluido' WHERE id = $3::uuid;",
                    km_fin, hora_fim, turno_id,
                )

                dt_inicio = turno["data_inicio"]
                if dt_inicio.tzinfo is not None:
                    dt_inicio = dt_inicio.astimezone(TZ_BR)

                # ---------------------------------------------------------------
                # 2. Cálculo de tempo operacional efetivo (bruto - pausas)
                # ---------------------------------------------------------------
                tempo_total_min = max(
                    Decimal("1"),
                    Decimal(str(int((hora_fim - dt_inicio).total_seconds() / 60))),
                )
                pausas_val = await conn.fetchval(
                    """
                    SELECT COALESCE(
                        SUM(EXTRACT(EPOCH FROM (COALESCE(fim_pausa, CURRENT_TIMESTAMP) - inicio_pausa)) / 60),
                        0
                    )
                    FROM public.pausas_turno
                    WHERE turno_id = $1::uuid;
                    """,
                    turno_id,
                )
                tempo_pausas_min = Decimal(str(int(pausas_val or 0)))
                tempo_efetivo_min = max(Decimal("1"), tempo_total_min - tempo_pausas_min)
                horas_trabalhadas = (tempo_efetivo_min / Decimal("60")).quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )

                # ---------------------------------------------------------------
                # 3. Power Split Híbrido — Queima Multi-Energia (EV Priority)
                # ---------------------------------------------------------------
                raw_est = turno["estoque_financeiro"]
                estoque: dict = json.loads(raw_est) if isinstance(raw_est, str) else (raw_est or {})
                estoque = TurnoService._garantir_estrutura_estoque(estoque)

                custo_combustivel_queimado = Decimal("0.00")
                unidades_queimadas_liq = Decimal("0.00")
                unidades_queimadas_ele = Decimal("0.00")
                unidades_queimadas_gnv = Decimal("0.00")
                detalhe_queima: list[str] = []
                km_restante = km_rodados

                # --- 3.1 Fase Elétrica (EV Priority) ---
                if (turno["is_hibrido"] or turno["is_eletrico"]) and km_restante > Decimal("0"):
                    eletro = estoque["eletricidade"]
                    kwh_disp = Decimal(str(eletro.get("kwh", 0.0)))
                    custo_bat = Decimal(str(eletro.get("custo_total", 0.0)))
                    km_kwh = Decimal(str(eletro.get("km_kwh", 6.5)))

                    if kwh_disp > Decimal("0") and km_kwh > Decimal("0"):
                        cmp_kwh = custo_bat / kwh_disp
                        kwh_necessarios = km_restante / km_kwh
                        kwh_queimados = min(kwh_disp, kwh_necessarios)
                        custo_bat_queimado = (kwh_queimados * cmp_kwh).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )

                        custo_combustivel_queimado += custo_bat_queimado
                        unidades_queimadas_ele += kwh_queimados
                        km_restante -= (kwh_queimados * km_kwh)

                        eletro["kwh"] = float(
                            max(Decimal("0"), kwh_disp - kwh_queimados).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        )
                        eletro["custo_total"] = float(
                            max(Decimal("0"), custo_bat - custo_bat_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        )
                        detalhe_queima.append(
                            f"Elétrico: {float(kwh_queimados):.1f} kWh (R$ {float(custo_bat_queimado):.2f})"
                        )

                # --- 3.2 Fase GNV ---
                tipo_comb = (turno["tipo_combustivel"] or "").lower()
                if tipo_comb == "gnv" and km_restante > Decimal("0"):
                    gnv = estoque["gnv"]
                    m3_disp = Decimal(str(gnv.get("m3", 0.0)))
                    custo_gnv = Decimal(str(gnv.get("custo_total", 0.0)))
                    km_m3 = Decimal(str(gnv.get("km_m3", 14.0)))

                    if m3_disp > Decimal("0") and km_m3 > Decimal("0"):
                        cmp_m3 = custo_gnv / m3_disp
                        m3_necessarios = km_restante / km_m3
                        m3_queimados = min(m3_disp, m3_necessarios)
                        custo_gnv_queimado = (m3_queimados * cmp_m3).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )

                        custo_combustivel_queimado += custo_gnv_queimado
                        unidades_queimadas_gnv += m3_queimados
                        km_restante -= (m3_queimados * km_m3)

                        gnv["m3"] = float(
                            max(Decimal("0"), m3_disp - m3_queimados).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        )
                        gnv["custo_total"] = float(
                            max(Decimal("0"), custo_gnv - custo_gnv_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        )
                        detalhe_queima.append(
                            f"GNV: {float(m3_queimados):.1f} m³ (R$ {float(custo_gnv_queimado):.2f})"
                        )

                # --- 3.3 Fase Combustão Líquida (Flex) ---
                if km_restante > Decimal("0") and not turno["is_eletrico"] and tipo_comb != "gnv":
                    liq = estoque["liquido"]
                    total_litros = Decimal(str(liq.get("litros", 0.0)))
                    custo_liq = Decimal(str(liq.get("custo_total", 0.0)))

                    if total_litros > Decimal("0"):
                        km_l_gas = Decimal(str(liq.get("km_l_gasolina", 12.0)))
                        km_l_eta = Decimal(str(liq.get("km_l_etanol", 8.5)))
                        p_gas = Decimal(str(liq.get("gasolina_proporcao", 1.0)))
                        p_eta = Decimal(str(liq.get("etanol_proporcao", 0.0)))

                        # Rendimento médio ponderado do blend atual no tanque único
                        km_l_medio = (p_gas * km_l_gas) + (p_eta * km_l_eta)
                        if km_l_medio <= Decimal("0"):
                            km_l_medio = Decimal("10.0")

                        litros_necessarios = km_restante / km_l_medio
                        litros_queimados = min(total_litros, litros_necessarios)

                        # Queima proporcional entre gasolina e etanol do blend
                        gas_queimado = litros_queimados * p_gas
                        eta_queimado = litros_queimados * p_eta

                        # Amortização pelo CMP do tanque
                        cmp_liq = custo_liq / total_litros
                        custo_liq_queimado = (litros_queimados * cmp_liq).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )

                        custo_combustivel_queimado += custo_liq_queimado
                        unidades_queimadas_liq += litros_queimados
                        km_restante -= (litros_queimados * km_l_medio)

                        novo_gas = max(Decimal("0"), Decimal(str(liq.get("gasolina_litros", 0.0))) - gas_queimado)
                        novo_eta = max(Decimal("0"), Decimal(str(liq.get("etanol_litros", 0.0))) - eta_queimado)
                        novo_total = novo_gas + novo_eta

                        liq["gasolina_litros"] = float(novo_gas.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        liq["etanol_litros"] = float(novo_eta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        liq["litros"] = float(novo_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        liq["custo_total"] = float(
                            max(Decimal("0"), custo_liq - custo_liq_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        )
                        # Recalcula proporções do blend residual
                        if novo_total > Decimal("0"):
                            liq["gasolina_proporcao"] = float(
                                (novo_gas / novo_total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                            )
                            liq["etanol_proporcao"] = float(
                                (novo_eta / novo_total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)
                            )
                        else:
                            liq["gasolina_proporcao"] = 1.0
                            liq["etanol_proporcao"] = 0.0

                        detalhe_queima.append(
                            f"Combustão: {float(litros_queimados):.1f} L (R$ {float(custo_liq_queimado):.2f})"
                        )

                # --- 3.4 Fallback: estoque JSONB zerado — estimativa proporcional via ledger ---
                if km_restante > Decimal("0"):
                    abast_val = await conn.fetchval(
                        """
                        SELECT COALESCE(SUM(valor), 0.0000)
                        FROM public.transacoes
                        WHERE motorista_id = $1::uuid AND turno_id = $2::uuid
                          AND categoria = 'combustivel' AND estornado = FALSE;
                        """,
                        motorista_id, turno_id,
                    )
                    abast_total = Decimal(str(abast_val or "0.00"))

                    if abast_total > Decimal("0"):
                        # Estima proporção do custo com base na km restante vs km total
                        prop = (km_restante / km_rodados) if km_rodados > Decimal("0") else Decimal("1")
                        prop = min(Decimal("1"), prop)
                        custo_est = (abast_total * prop).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        custo_combustivel_queimado += custo_est
                        logger.warning(
                            "[Fallback CMP] Motorista %s: estoque JSONB zerado com %.1f km restantes. "
                            "Estimativa proporcional: R$ %.2f (%.0f%% de R$ %.2f).",
                            motorista_id, float(km_restante),
                            float(custo_est), float(prop) * 100, float(abast_total),
                        )
                        detalhe_queima.append(f"Estimativa Combustível: R$ {float(custo_est):.2f}")
                    else:
                        # Nenhum abastecimento registrado — custo por km padrão de mercado
                        custo_est = (km_restante * Decimal("0.48")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        custo_combustivel_queimado += custo_est
                        detalhe_queima.append(f"Custo Estimado (sem estoque): R$ {float(custo_est):.2f}")

                # Persiste o estoque atualizado
                await conn.execute(
                    "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                    json.dumps(estoque), veiculo_id,
                )

                # ---------------------------------------------------------------
                # 4. Apuração contábil do DRE
                # ---------------------------------------------------------------
                fin = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(CASE WHEN tipo_movimentacao = 'receita' THEN valor ELSE 0 END), 0.0000) AS faturamento,
                        COALESCE(SUM(CASE WHEN tipo_movimentacao = 'despesa' AND categoria != 'combustivel' THEN valor ELSE 0 END), 0.0000) AS outras_despesas,
                        COALESCE(SUM(CASE WHEN tipo_movimentacao = 'despesa' AND categoria = 'combustivel' THEN valor ELSE 0 END), 0.0000) AS total_abastecido
                    FROM public.transacoes
                    WHERE motorista_id = $1::uuid
                      AND (turno_id = $2::uuid OR (turno_id IS NULL AND data_transacao >= $3))
                      AND estornado = FALSE;
                    """,
                    motorista_id, turno_id, dt_inicio,
                )

                faturamento_bruto = Decimal(str(fin["faturamento"]))
                outras_despesas = Decimal(str(fin["outras_despesas"]))
                total_abastecido_turno = Decimal(str(fin["total_abastecido"]))

                # Custo variável total = despesas de pista + custo amortizado de queima
                custo_variavel_total = outras_despesas + custo_combustivel_queimado

                # Detalhamento individual das despesas para o DRE
                despesas_lista = await conn.fetch(
                    """
                    SELECT categoria, valor, descricao
                    FROM public.transacoes
                    WHERE motorista_id = $1::uuid
                      AND (turno_id = $2::uuid OR (turno_id IS NULL AND data_transacao >= $3))
                      AND tipo_movimentacao = 'despesa' AND estornado = FALSE
                    ORDER BY data_transacao ASC;
                    """,
                    motorista_id, turno_id, dt_inicio,
                )
                despesas_detalhadas = [
                    {
                        "categoria": d["categoria"],
                        "descricao_original": d["descricao"] or d["categoria"].replace("_", " ").capitalize(),
                        "valor": float(d["valor"]),
                    }
                    for d in despesas_lista
                ]

                # ---------------------------------------------------------------
                # 5. Custo Fixo Contratual Pro-Rata (Aluguel / Locadora)
                # ---------------------------------------------------------------
                aluguel_semanal = Decimal(str(turno["custo_aluguel_semanal"] or "1020.85"))
                custo_fixo_rateado = (aluguel_semanal / Decimal("6")).quantize(
                    Decimal("0.02"), rounding=ROUND_HALF_UP
                )
                df_extra = await conn.fetchval(
                    "SELECT COALESCE(SUM(valor_pro_rata_diario), 0.0000) FROM public.despesas_fixas_mensais WHERE motorista_id = $1::uuid AND ativo = TRUE;",
                    motorista_id,
                )
                custo_fixo_total = custo_fixo_rateado + Decimal(str(df_extra or "0.00"))

                # ---------------------------------------------------------------
                # 6. DRE Final
                # ---------------------------------------------------------------
                lucro_liquido = faturamento_bruto - custo_variavel_total - custo_fixo_total

                ganho_por_km = (faturamento_bruto / km_rodados) if km_rodados > Decimal("0") else Decimal("0")
                custo_por_km = ((custo_variavel_total + custo_fixo_total) / km_rodados) if km_rodados > Decimal("0") else Decimal("0")
                lucro_por_km = (lucro_liquido / km_rodados) if km_rodados > Decimal("0") else Decimal("0")
                ganho_por_hora = (faturamento_bruto / horas_trabalhadas) if horas_trabalhadas > Decimal("0") else Decimal("0")

                meta_mensal = Decimal(str(turno["meta_mensal_faturamento"] or "12000.00"))
                dias_uteis = int(turno["dias_uteis_mes"] or 26)

                total_unidades = unidades_queimadas_liq + unidades_queimadas_ele + unidades_queimadas_gnv
                km_por_unidade = (km_rodados / total_unidades) if total_unidades > Decimal("0") else Decimal("0")

                # Persiste snapshot contábil
                await conn.execute(
                    """
                    INSERT INTO public.fechamento_diario
                        (motorista_id, turno_id, faturamento_bruto, custo_variavel_direto,
                         custo_fixo_rateado, lucro_liquido_real, km_rodados, data_fechamento)
                    VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, CURRENT_DATE);
                    """,
                    motorista_id, turno_id,
                    faturamento_bruto, custo_variavel_total,
                    custo_fixo_total, lucro_liquido, km_rodados,
                )

            return {
                "sucesso": True,
                "turno_id": turno_id,
                "data_inicio": dt_inicio.strftime("%d/%m/%Y %H:%M"),
                "data_fim": hora_fim.strftime("%d/%m/%Y %H:%M"),
                "km_inicial": float(km_ini),
                "km_final": float(km_fin),
                "km_rodados": float(km_rodados),
                "tempo_total_min": int(tempo_total_min),
                "tempo_pausas_min": int(tempo_pausas_min),
                "tempo_efetivo_min": int(tempo_efetivo_min),
                "horas_trabalhadas": float(horas_trabalhadas),
                "faturamento_bruto": float(faturamento_bruto),
                "custo_combustivel_queimado": float(custo_combustivel_queimado),
                "total_abastecido_turno": float(total_abastecido_turno),
                "outras_despesas_variaveis": float(outras_despesas),
                "custo_variavel": float(custo_variavel_total),
                "custo_fixo_rateado": float(custo_fixo_total),
                "lucro_liquido_real": float(lucro_liquido),
                "ganho_por_km": float(ganho_por_km),
                "custo_por_km": float(custo_por_km),
                "lucro_por_km": float(lucro_por_km),
                "ganho_por_hora": float(ganho_por_hora),
                "km_por_litro": float(km_por_unidade),
                "meta_mensal": float(meta_mensal),
                "dias_uteis": dias_uteis,
                "locadora": turno["locadora"] or "Localiza Zarp",
                "escala_trabalho": turno["escala_trabalho"] or "De quarta a segunda (6 dias)",
                "franquia_km_semanal": float(turno["franquia_km_semanal"] or 1505.0),
                "valor_km_excedente": float(turno["valor_km_excedente"] or 0.75),
                "contrato_personalizado": bool(turno["contrato_personalizado"]),
                "detalhe_queima": " | ".join(detalhe_queima),
                "despesas_detalhadas": despesas_detalhadas,
            }

        except Exception as exc:
            logger.exception("Falha na consolidação diária do turno.")
            return {"sucesso": False, "erro": f"Erro interno de processamento: {exc}", "tipo_erro": "ERRO_INTERNO"}

    @staticmethod
    async def pausar_turno(motorista_id: str) -> Dict[str, Any]:
        """Aplica interrupção operacional na jornada de trabalho e registra na tabela pausas_turno."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno = await conn.fetchrow(
                    "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento') ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id,
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em andamento aberta para pausar."}

                turno_id = str(turno["id"])
                await conn.execute("UPDATE public.turnos SET status = 'em_pausa' WHERE id = $1::uuid;", turno_id)
                await conn.execute(
                    "INSERT INTO public.pausas_turno (turno_id, motivo, inicio_pausa) VALUES ($1::uuid, 'Pausa Operacional', $2);",
                    turno_id, agora_brasil(),
                )
            return {"sucesso": True}
        except Exception as exc:
            return {"sucesso": False, "erro": str(exc)}

    @staticmethod
    async def retomar_turno(motorista_id: str) -> Dict[str, Any]:
        """Finaliza a pausa aberta do turno e seta status 'em_andamento'."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno = await conn.fetchrow(
                    "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status = 'em_pausa' ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id,
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em pausa registrada no momento."}

                turno_id = str(turno["id"])
                await conn.execute("UPDATE public.turnos SET status = 'em_andamento' WHERE id = $1::uuid;", turno_id)
                await conn.execute(
                    "UPDATE public.pausas_turno SET fim_pausa = $1 WHERE turno_id = $2::uuid AND fim_pausa IS NULL;",
                    agora_brasil(), turno_id,
                )
            return {"sucesso": True}
        except Exception as exc:
            return {"sucesso": False, "erro": str(exc)}

    @staticmethod
    async def verificar_transacoes_turno(motorista_id: str) -> int:
        """Verifica se há lançamentos no turno ativo (Read-Only). Retorna a contagem ou 1 em caso de erro."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno = await conn.fetchrow(
                    "SELECT id, data_inicio FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id,
                )
                if not turno:
                    return 0
                turno_id = str(turno["id"])
                dt_inicio = turno["data_inicio"]
                row = await conn.fetchrow(
                    """
                    SELECT COUNT(*) AS total FROM public.transacoes
                    WHERE motorista_id = $1::uuid
                      AND (turno_id = $2::uuid OR (turno_id IS NULL AND data_transacao >= $3))
                      AND estornado = FALSE;
                    """,
                    motorista_id, turno_id, dt_inicio,
                )
                return int(row["total"]) if row else 0
        except Exception:
            return 1  # Fallback conservador: não fecha sem aviso
