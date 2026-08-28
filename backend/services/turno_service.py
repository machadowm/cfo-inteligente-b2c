import os
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
    Serviço Operacional e Contábil de Turnos.
    Oferece suporte à queima híbrida inteligente de energia veicular (energia solar com custo amortizado + mix combustível líquido Flex),
    cálculo de DRE Executivo com Decimal de alta precisão, timezone seguro e auditoria detalhada de gastos.
    """

    @staticmethod
    def _validar_km(valor_km: float, campo: str) -> Decimal:
        try:
            km_decimal = Decimal(str(valor_km))
        except (InvalidOperation, ValueError) as exc:
            raise ValueError(f"O valor de {campo} está mal formatado.") from exc

        if km_decimal < 0:
            raise ValueError(f"O valor de {campo} não pode ser negativo.")

        return km_decimal

    @staticmethod
    def _garantir_estrutura_estoque(estoque: dict) -> dict:
        """
        Retrocompatibilidade: adiciona chaves ausentes sem sobrescrever dados existentes.
        A sub-chave 'meta' é a fonte única de verdade para flags de motorização e capacidades físicas do veículo — as colunas correspondentes foram removidas da tabela veiculos.
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
    async def abrir_turno(motorista_id: str, veiculo_id: str, km_inicial: float) -> Dict[str, Any]:
        """
        Abre um novo turno para o motorista com validação rigorosa de monotonicidade do odômetro
        em relação ao último fechamento registrado deste veículo.
        E calcula e debita silenciosamente do estoque o consumo de uso pessoal (odometer gap).
        """
        try:
            km_ini = TurnoService._validar_km(km_inicial, "km_inicial")
        except ValueError as exc:
            return {"sucesso": False, "erro": f"❌ {exc}", "tipo_erro": "KM_INVALIDO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # 1. Garante que não há turno em aberto para o motorista
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

                # 2. Busca o último turno encerrado deste veículo para validar monotonicidade do odômetro
                # E extrai as configurações contábeis/físicas de estoque para queima de uso pessoal
                ultimo = await conn.fetchrow(
                    """
                    SELECT t.km_final, v.estoque_financeiro, v.tipo_combustivel
                    FROM public.turnos t
                    JOIN public.veiculos v ON v.id = t.veiculo_id
                    WHERE t.veiculo_id = $1::uuid AND t.status = 'concluido' AND t.km_final IS NOT NULL
                    ORDER BY t.data_fim DESC LIMIT 1;
                    """,
                    veiculo_id,
                )

                km_uso_pessoal = Decimal("0.00")

                if ultimo and ultimo["km_final"] is not None:
                    km_anterior = Decimal(str(ultimo["km_final"]))
                    if km_ini < km_anterior:
                        km_ini_fmt = f"{float(km_ini):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        km_ant_fmt = f"{float(km_anterior):,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")
                        return {
                            "sucesso": False,
                            "erro": (
                                f"⚠️ *Odômetro Divergente!*\\n"
                                f"O valor informado (*{km_ini_fmt} km*) é menor que o odômetro final do último turno "
                                f"(*{km_ant_fmt} km*).\\n\\n"
                                f"Por favor, envie o *valor correto* atual do painel do seu veículo:"
                            ),
                            "tipo_erro": "ODOMETRO_DIVERGENTE",
                        }

                    # Auditoria e processamento de Uso Pessoal se houver odometer gap positivo
                    if km_ini > km_anterior:
                        km_uso_pessoal = km_ini - km_anterior
                        
                        raw_est = ultimo["estoque_financeiro"]
                        estoque: dict = json.loads(raw_est) if isinstance(raw_est, str) else (raw_est or {})
                        estoque = TurnoService._garantir_estrutura_estoque(estoque)
                        meta = estoque["meta"]

                        is_hibrido = bool(meta.get("is_hibrido", False))
                        is_eletrico = bool(meta.get("is_eletrico", False))
                        tipo_comb = (ultimo["tipo_combustivel"] or meta.get("tipo_veiculo", "")).lower()

                        km_restante = km_uso_pessoal

                        # 2.1 Fase Elétrica (EV Priority) se híbrido ou elétrico
                        if (is_hibrido or is_eletrico) and km_restante > Decimal("0"):
                            eletro = estoque["eletricidade"]
                            kwh_disp = Decimal(str(eletro.get("kwh", 0.0)))
                            custo_bat = Decimal(str(eletro.get("custo_total", 0.0)))
                            km_kwh = Decimal(str(eletro.get("km_kwh", 6.5)))
                            
                            if kwh_disp > Decimal("0") and km_kwh > Decimal("0"):
                                cmp_kwh = custo_bat / kwh_disp
                                kwh_necessarios = km_restante / km_kwh
                                kwh_queimados = min(kwh_disp, kwh_necessarios)
                                custo_bat_queimado = (kwh_queimados * cmp_kwh).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                                
                                eletro["kwh"] = float(max(Decimal("0"), kwh_disp - kwh_queimados).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                                eletro["custo_total"] = float(max(Decimal("0"), custo_bat - custo_bat_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                                km_restante -= (kwh_queimados * km_kwh)

                        # 2.2 Fase GNV
                        if tipo_comb == "gnv" and km_restante > Decimal("0"):
                            gnv = estoque["gnv"]
                            m3_disp = Decimal(str(gnv.get("m3", 0.0)))
                            custo_gnv = Decimal(str(gnv.get("custo_total", 0.0)))
                            km_m3 = Decimal(str(gnv.get("km_m3", 14.0)))

                            if m3_disp > Decimal("0") and km_m3 > Decimal("0"):
                                cmp_m3 = custo_gnv / m3_disp
                                m3_necessarios = km_restante / km_m3
                                m3_queimados = min(m3_disp, m3_necessarios)
                                custo_gnv_queimado = (m3_queimados * cmp_m3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                                gnv["m3"] = float(max(Decimal("0"), m3_disp - m3_queimados).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                                gnv["custo_total"] = float(max(Decimal("0"), custo_gnv - custo_gnv_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                                km_restante -= (m3_queimados * km_m3)

                        # 2.3 Fase Combustão Líquida (Flex)
                        if km_restante > Decimal("0") and not is_eletrico and tipo_comb != "gnv":
                            liq = estoque["liquido"]
                            total_litros = Decimal(str(liq.get("litros", 0.0)))
                            custo_liq = Decimal(str(liq.get("custo_total", 0.0)))

                            if total_litros > Decimal("0"):
                                km_l_gas = Decimal(str(liq.get("km_l_gasolina", 12.0)))
                                km_l_eta = Decimal(str(liq.get("km_l_etanol", 8.5)))
                                p_gas = Decimal(str(liq.get("gasolina_proporcao", 1.0)))
                                p_eta = Decimal(str(liq.get("etanol_proporcao", 0.0)))

                                km_l_medio = (p_gas * km_l_gas) + (p_eta * km_l_eta)
                                if km_l_medio <= Decimal("0"):
                                    km_l_medio = Decimal("10.0")

                                litros_necessarios = km_restante / km_l_medio
                                litros_queimados = min(total_litros, litros_necessarios)

                                gas_queimado = litros_queimados * p_gas
                                eta_queimado = litros_queimados * p_eta

                                cmp_liq = custo_liq / total_litros
                                custo_liq_queimado = (litros_queimados * cmp_liq).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                                novo_gas = max(Decimal("0"), Decimal(str(liq.get("gasolina_litros", 0.0))) - gas_queimado)
                                novo_eta = max(Decimal("0"), Decimal(str(liq.get("etanol_litros", 0.0))) - eta_queimado)
                                novo_total = novo_gas + novo_eta

                                liq["gasolina_litros"] = float(novo_gas.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                                liq["etanol_litros"] = float(novo_eta.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                                liq["litros"] = float(novo_total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                                liq["custo_total"] = float(max(Decimal("0"), custo_liq - custo_liq_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))

                                if novo_total > Decimal("0"):
                                    liq["gasolina_proporcao"] = float((novo_gas / novo_total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                                    liq["etanol_proporcao"] = float((novo_eta / novo_total).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                                else:
                                    liq["gasolina_proporcao"] = 1.0
                                    liq["etanol_proporcao"] = 0.0

                        # Salva estoque recalculado silenciosamente no banco
                        await conn.execute(
                            "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                            json.dumps(estoque), veiculo_id,
                        )

                # 3. Insere o turno com o km_uso_pessoal auditado
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.turnos (motorista_id, veiculo_id, km_inicial, km_uso_pessoal, status, data_inicio)
                    VALUES ($1::uuid, $2::uuid, $3, $4, 'em_andamento', $5)
                    RETURNING id, km_inicial, km_uso_pessoal, data_inicio;
                    """,
                    motorista_id, veiculo_id, km_ini, km_uso_pessoal, agora_brasil(),
                )

                return {
                    "sucesso": True,
                    "turno_id": str(row["id"]),
                    "km_inicial": float(row["km_inicial"]),
                    "km_uso_pessoal": float(row["km_uso_pessoal"]),
                    "data_inicio": row["data_inicio"],
                }
        except Exception as exc:
            logger.exception("Erro crítico ao abrir turno.")
            return {"sucesso": False, "erro": f"Erro interno ao abrir turno: {exc}", "tipo_erro": "ERRO_INTERNO"}

    @staticmethod
    async def fechar_turno_com_dre(motorista_id: str, km_final: float) -> Dict[str, Any]:
        """
        Encerra o turno ativo, realiza o Power Split da queima híbrida (Bateria/Eletricidade + Combustível Flex)
        com base no CMP do estoque real, e gera o DRE Executivo do Turno.
        """
        try:
            km_final_decimal = TurnoService._validar_km(km_final, "km_final")
        except ValueError as exc:
            return {"sucesso": False, "erro": f"❌ {str(exc)}", "tipo_erro": "KM_INVALIDO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # 1. Resgata os dados operacionais do turno, veículo e motorista
                turno = await conn.fetchrow(
                    """
                    SELECT t.id, t.km_inicial, t.data_inicio, v.id as veiculo_id,
                           v.estoque_financeiro, v.tipo_combustivel,
                           v.is_hibrido, v.is_eletrico, v.is_flex,
                           v.capacidade_bateria,
                           v.locadora, v.custo_aluguel_semanal, v.franquia_km_semanal, v.valor_km_excedente,
                           v.escala_trabalho, v.contrato_personalizado, m.meta_mensal_faturamento, m.dias_uteis_mes
                    FROM public.turnos t
                    JOIN public.veiculos v ON v.id = t.veiculo_id
                    JOIN public.motoristas m ON m.id = t.motorista_id
                    WHERE t.motorista_id = $1::uuid AND t.status IN ('ABERTO', 'em_andamento', 'em_pausa')
                    ORDER BY t.data_inicio DESC LIMIT 1;
                    """,
                    motorista_id
                )

                if not turno:
                    return {
                        "sucesso": False,
                        "erro": "⚠️ Nenhum turno ativo em andamento foi localizado para este motorista.",
                        "tipo_erro": "NENHUM_TURNO_ATIVO"
                    }

                turno_id = str(turno["id"])
                veiculo_id = str(turno["veiculo_id"])
                km_inicial_decimal = Decimal(str(turno["km_inicial"]))

                # Validação de odômetro final
                if km_final_decimal < km_inicial_decimal:
                    return {
                        "sucesso": False,
                        "erro": f"⚠️ *Odômetro Final Divergente!*\\nO valor informado (*{float(km_final_decimal):.1f} km*) é inferior ao inicial registrado no início do turno (*{float(km_inicial_decimal):.1f} km*).\\n\\n\"\n                               f\"Por favor, envie o **valor correto** atual do painel do seu veículo:",
                        "tipo_erro": "ODOMETRO_DIVERGENTE"
                    }

                km_rodados = km_final_decimal - km_inicial_decimal
                hora_fim_real = agora_brasil()

                # Encerra temporalmente o turno
                await conn.execute(
                    "UPDATE public.turnos SET km_final = $1, data_fim = $2, status = 'concluido' WHERE id = $3::uuid;",
                    km_final_decimal, hora_fim_real, turno_id
                )

                dt_inicio = turno["data_inicio"]
                if dt_inicio.tzinfo is not None:
                    dt_inicio = dt_inicio.astimezone(TZ_BR)

                # Cálculo de tempo operacional
                tempo_total_min = Decimal(str(int((hora_fim_real - dt_inicio).total_seconds() / 60)))
                pausas_row = await conn.fetchval(
                    "SELECT COALESCE(SUM(EXTRACT(EPOCH FROM (COALESCE(fim_pausa, CURRENT_TIMESTAMP) - inicio_pausa))/60), 0) FROM public.pausas_turno WHERE turno_id = $1::uuid;",
                    turno_id
                )
                tempo_pausas_min = Decimal(str(int(pausas_row or 0)))
                tempo_efetivo_min = max(Decimal("1.00"), tempo_total_min - tempo_pausas_min)
                horas_trabalhadas = (tempo_efetivo_min / Decimal("60.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # 2. LÓGICA DE QUEIMA HÍBRIDA MULTI-SOURCE DE ENERGIA (Power Split)
                estoque_raw = turno["estoque_financeiro"]
                estoque = json.loads(estoque_raw) if isinstance(estoque_raw, str) else (estoque_raw or {})
                estoque = TurnoService._garantir_estrutura_estoque(estoque)
                meta = estoque["meta"]

                # Flags escalares têm precedência sobre JSONB.meta (banco de produção possui ambos)
                is_hibrido = bool(turno.get("is_hibrido") if turno.get("is_hibrido") is not None else meta.get("is_hibrido", False))
                is_eletrico = bool(turno.get("is_eletrico") if turno.get("is_eletrico") is not None else meta.get("is_eletrico", False))
                tipo_comb = (turno["tipo_combustivel"] or meta.get("tipo_veiculo", "")).lower()

                custo_combustivel_queimado = Decimal("0.00")
                total_unidades_queimadas_liq = Decimal("0.00")
                total_unidades_queimadas_ele = Decimal("0.00")
                total_unidades_queimadas_gnv = Decimal("0.00")
                detalhe_queima = []

                km_restante = km_rodados

                # 2.1. SE FOR HÍBRIDO OU ELÉTRICO: Prioriza consumo da bateria elétrica (EV Mode / Solar CMP)
                if (is_hibrido or is_eletrico) and km_restante > 0:
                    eletro = estoque["eletricidade"]
                    kwh_disponivel = Decimal(str(eletro.get("kwh", 0.0)))
                    custo_bateria = Decimal(str(eletro.get("custo_total", 0.0)))
                    km_kwh_rendimento = Decimal(str(eletro.get("km_kwh", 6.5)))

                    if kwh_disponivel > 0 and km_kwh_rendimento > 0:
                        # CMP por kWh (Se carregou com solar em casa a custo zero, o custo unitário será menor!)
                        custo_medio_kwh = custo_bateria / kwh_disponivel
                        kwh_necessarios = km_restante / km_kwh_rendimento
                        kwh_queimados = min(kwh_disponivel, kwh_necessarios)
                        
                        custo_queimado_bateria = kwh_queimados * custo_medio_kwh
                        custo_combustivel_queimado += custo_queimado_bateria
                        total_unidades_queimadas_ele += kwh_queimados
                        km_restante -= (kwh_queimados * km_kwh_rendimento)

                        # Updates no dicionário do Redis/JSONB
                        eletro["kwh"] = float((kwh_disponivel - kwh_queimados).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        eletro["custo_total"] = float(max(Decimal("0.00"), custo_bateria - custo_queimado_bateria).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        detalhe_queima.append(f"Elétrico: {float(kwh_queimados):.1f} kWh (R$ {float(custo_queimado_bateria):.2f})")

                # 2.2. CONSUMO DE GNV (fase dedicada — antes do líquido)
                if tipo_comb == "gnv" and km_restante > 0:
                    gnv = estoque["gnv"]
                    m3_disponivel = Decimal(str(gnv.get("m3", 0.0)))
                    custo_gnv = Decimal(str(gnv.get("custo_total", 0.0)))
                    km_m3_rendimento = Decimal(str(gnv.get("km_m3", 14.0)))

                    if m3_disponivel > 0 and km_m3_rendimento > 0:
                        cmp_m3 = custo_gnv / m3_disponivel
                        m3_necessarios = km_restante / km_m3_rendimento
                        m3_queimados = min(m3_disponivel, m3_necessarios)

                        custo_gnv_queimado = (m3_queimados * cmp_m3).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        custo_combustivel_queimado += custo_gnv_queimado
                        total_unidades_queimadas_gnv += m3_queimados
                        km_restante -= (m3_queimados * km_m3_rendimento)

                        gnv["m3"] = float(max(Decimal("0.00"), m3_disponivel - m3_queimados).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        gnv["custo_total"] = float(max(Decimal("0.00"), custo_gnv - custo_gnv_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        detalhe_queima.append(f"GNV: {float(m3_queimados):.2f} m³ (R$ {float(custo_gnv_queimado):.2f})")

                # 2.3. CONSUMO DE LÍQUIDO (Se restou KM para queimar e não é GNV puro nem elétrico puro)
                if km_restante > 0 and not is_eletrico and tipo_comb != "gnv":
                    liq = estoque["liquido"]
                    total_litros = Decimal(str(liq.get("litros", 0.0)))
                    custo_total_liq = Decimal(str(liq.get("custo_total", 0.0)))

                    if total_litros > 0:
                        km_l_gas = Decimal(str(liq.get("km_l_gasolina", 12.0)))
                        km_l_eta = Decimal(str(liq.get("km_l_etanol", 8.5)))
                        p_gas = Decimal(str(liq.get("gasolina_proporcao", 1.0)))
                        p_eta = Decimal(str(liq.get("etanol_proporcao", 0.0)))

                        # Rendimento médio ponderado da mistura no tanque único
                        km_l_medio = (p_gas * km_l_gas) + (p_eta * km_l_eta)
                        if km_l_medio <= 0:
                            km_l_medio = Decimal("10.0")

                        litros_necessarios = km_restante / km_l_medio
                        litros_queimados = min(total_litros, litros_necessarios)

                        # Divide a queima proporcionalmente entre gasolina e etanol do tanque único
                        gas_queimado = litros_queimados * p_gas
                        eta_queimado = litros_queimados * p_eta

                        # Computa amortização pelos respectivos Custos Médios Ponderados (CMP)
                        custo_liquido_queimado = (custo_total_liq / total_litros) * litros_queimados
                        custo_combustivel_queimado += custo_liquido_queimado
                        total_unidades_queimadas_liq += litros_queimados
                        km_restante -= (litros_queimados * km_l_medio)

                        # Atualiza estoques de sub-combustíveis
                        novo_gas_litros = max(Decimal("0.00"), Decimal(str(liq.get("gasolina_litros", 0.0))) - gas_queimado)
                        novo_eta_litros = max(Decimal("0.00"), Decimal(str(liq.get("etanol_litros", 0.0))) - eta_queimado)
                        novo_total_litros = novo_gas_litros + novo_eta_litros

                        liq["gasolina_litros"] = float(novo_gas_litros.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        liq["etanol_litros"] = float(novo_eta_litros.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        liq["litros"] = float(novo_total_litros.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        liq["custo_total"] = float(max(Decimal("0.00"), custo_total_liq - custo_liquido_queimado).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                        
                        # Recalcula as proporções do restante
                        if novo_total_litros > 0:
                            liq["gasolina_proporcao"] = float((novo_gas_litros / novo_total_litros).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                            liq["etanol_proporcao"] = float((novo_eta_litros / novo_total_litros).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                        else:
                            liq["gasolina_proporcao"] = 1.0
                            liq["etanol_proporcao"] = 0.0

                        detalhe_queima.append(f"Combustão: {float(litros_queimados):.1f} L (R$ {float(custo_liquido_queimado):.2f})")

                # Se o estoque virtual estava zerado e ainda restou KM para queimar, usa fallbacks
                if km_restante > 0:
                    total_abastecido_turno_val = await conn.fetchval(
                        "SELECT COALESCE(SUM(valor), 0.0000) FROM public.transacoes WHERE motorista_id = $1::uuid AND turno_id = $2::uuid AND categoria = 'combustivel' AND estornado = FALSE;",
                        motorista_id, turno_id
                    )
                    total_abastecido_turno = Decimal(str(total_abastecido_turno_val or "0.00"))
                    
                    if total_abastecido_turno > 0:
                        custo_estimado = total_abastecido_turno
                        custo_combustivel_queimado += custo_estimado
                        detalhe_queima.append(f"Abastecimento Turno: R$ {float(custo_estimado):.2f}")
                    else:
                        # Média padrão estimada baseada em odômetro para o km excedente sem estoque
                        custo_estimado = (km_restante * Decimal("0.48")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                        custo_combustivel_queimado += custo_estimado
                        detalhe_queima.append(f"Falta Estoque: R$ {float(custo_estimado):.2f}")

                # Salva o estoque total recalculado
                await conn.execute(
                    "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                    json.dumps(estoque), veiculo_id
                )

                # 3. APURAÇÃO CONTÁBIL E EXTRAÇÃO DE DESPESAS DETALHADAS DO TURNO
                financeiro = await conn.fetchrow(
                    "SELECT "
                    "    COALESCE(SUM(CASE WHEN tipo_movimentacao = 'receita' THEN valor ELSE 0 END), 0.0000) as faturamento, "
                    "    COALESCE(SUM(CASE WHEN tipo_movimentacao = 'despesa' AND categoria != 'combustivel' THEN valor ELSE 0 END), 0.0000) as despesas_operacionais, "
                    "    COALESCE(SUM(CASE WHEN tipo_movimentacao = 'despesa' AND categoria = 'combustivel' THEN valor ELSE 0 END), 0.0000) as total_abastecido "
                    "FROM public.transacoes "
                    "WHERE motorista_id = $1::uuid AND (turno_id = $2::uuid OR (turno_id IS NULL AND data_transacao >= $3)) AND estornado = FALSE;",
                    motorista_id, turno_id, dt_inicio
                )

                faturamento_bruto = Decimal(str(financeiro["faturamento"]))
                outras_despesas_variaveis = Decimal(str(financeiro["despesas_operacionais"]))
                total_abastecido_turno = Decimal(str(financeiro["total_abastecido"]))
                
                # Custo variável total da jornada compreende despesas de pista + custo amortizado da queima multi-energia
                custo_variavel_total = outras_despesas_variaveis + custo_combustivel_queimado

                # Busca da listagem detalhada de despesas individuais para transparência de fechamento
                despesas_lista = await conn.fetch(
                    "SELECT categoria, valor, descricao "
                    "FROM public.transacoes "
                    "WHERE motorista_id = $1::uuid AND (turno_id = $2::uuid OR (turno_id IS NULL AND data_transacao >= $3)) "
                    "  AND tipo_movimentacao = 'despesa' AND estornado = FALSE "
                    "ORDER BY data_transacao ASC;",
                    motorista_id, turno_id, dt_inicio
                )

                despesas_detalhadas = []
                for d in despesas_lista:
                    despesas_detalhadas.append({
                        "categoria": d["categoria"],
                        "descricao_original": d["descricao"] or d["categoria"].replace("_", " ").capitalize(),
                        "valor": float(d["valor"])
                    })

                # 4. ENGENHARIA DE CUSTO FIXO CONTRATUAL PRO-RATA (Localiza Zarp fallback)
                custo_aluguel_semanal = Decimal(str(turno["custo_aluguel_semanal" ] or "1020.85"))
                custo_fixo_rateado = (custo_aluguel_semanal / Decimal("6.00")).quantize(Decimal("0.02"), rounding=ROUND_HALF_UP)

                # Pro-rata extra de despesas fixas cadastradas pelo motorista
                df_extra = await conn.fetchval(
                    "SELECT COALESCE(SUM(valor_pro_rata_diario), 0.0000) FROM public.despesas_fixas_mensais WHERE motorista_id = $1::uuid AND ativo = TRUE;",
                    motorista_id
                )
                custo_fixo_total = custo_fixo_rateado + Decimal(str(df_extra or "0.00"))

                # 5. DRE COMPLETO E LÓGICA DE PROVISÃO
                lucro_liquido_real = faturamento_bruto - custo_variavel_total - custo_fixo_total

                # Indicadores de eficiência e produtividade
                ganho_por_km = (faturamento_bruto / km_rodados) if km_rodados > 0 else Decimal("0.00")
                custo_por_km = (custo_variavel_total + custo_fixo_total) / km_rodados if km_rodados > 0 else Decimal("0.00")
                lucro_por_km = (lucro_liquido_real / km_rodados) if km_rodados > 0 else Decimal("0.00")
                ganho_por_hora = (faturamento_bruto / horas_trabalhadas) if horas_trabalhadas > 0 else Decimal("0.00")

                meta_mensal = Decimal(str(turno["meta_mensal_faturamento"] or "12000.00"))
                dias_uteis = int(turno["dias_uteis_mes"] or 26)

                # Rendimento contábil final de Km por Litro / kWh do turno (Ponderado se híbrido)
                total_unidades_queimadas = total_unidades_queimadas_liq + total_unidades_queimadas_ele
                km_por_unidade = (km_rodados / total_unidades_queimadas) if total_unidades_queimadas > 0 else Decimal("0.00")

                # Persiste o snapshot contábil na tabela fechamento_diario
                await conn.execute(
                    "INSERT INTO public.fechamento_diario ("
                    "    motorista_id, turno_id, faturamento_bruto, custo_variavel_direto, "
                    "    custo_fixo_rateado, lucro_liquido_real, km_rodados, data_fechamento"
                    ") VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, CURRENT_DATE);",
                    motorista_id, turno_id, faturamento_bruto, custo_variavel_total,
                    custo_fixo_total, lucro_liquido_real, km_rodados
                )

            return {
                "sucesso": True,
                "turno_id": turno_id,
                "data_inicio": dt_inicio.strftime('%d/%m/%Y %H:%M'),
                "data_fim": hora_fim_real.strftime('%d/%m/%Y %H:%M'),
                "km_inicial": float(km_inicial_decimal),
                "km_final": float(km_final_decimal),
                "km_rodados": float(km_rodados),
                "tempo_total_min": int(tempo_total_min),
                "tempo_pausas_min": int(tempo_pausas_min),
                "tempo_efetivo_min": int(tempo_efetivo_min),
                "horas_trabalhadas": float(horas_trabalhadas),
                "faturamento_bruto": float(faturamento_bruto),
                "custo_combustivel_queimado": float(custo_combustivel_queimado),
                "total_abastecido_turno": float(total_abastecido_turno),
                "outras_despesas_variaveis": float(outras_despesas_variaveis),
                "custo_variavel": float(custo_variavel_total),
                "custo_fixo_rateado": float(custo_fixo_total),
                "lucro_liquido_real": float(lucro_liquido_real),
                "ganho_por_km": float(ganho_por_km),
                "custo_por_km": float(custo_por_km),
                "lucro_por_km": float(lucro_por_km),
                "ganho_por_hora": float(ganho_por_hora),
                "km_por_litro": float(km_por_unidade), # Retorna a média de consumo ponderada do turno
                "meta_mensal": float(meta_mensal),
                "dias_uteis": dias_uteis,
                "locadora": turno["locadora"] or "Localiza Zarp",
                "escala_trabalho": turno["escala_trabalho"] or "De quarta a segunda (6 dias)",
                "franquia_km_semanal": float(turno["franquia_km_semanal"] or 1505.0),
                "valor_km_excedente": float(turno["valor_km_excedente"] or 0.75),
                "contrato_personalizado": bool(turno["contrato_personalizado"]),
                "detalhe_queima": " | ".join(detalhe_queima),
                "despesas_detalhadas": despesas_detalhadas
            }

        except Exception as e:
            logger.exception("Falha na consolidação diária do turno.")
            return {"sucesso": False, "erro": f"Erro interno de processamento: {e}", "tipo_erro": "ERRO_INTERNO"}

    @staticmethod
    async def pausar_turno(motorista_id: str) -> Dict[str, Any]:
        """Aplica interrupção operacional (Pausa) na jornada de trabalho e insere na tabela pausas_turno."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno = await conn.fetchrow(
                    """SELECT id, status FROM public.turnos
                       WHERE motorista_id = $1::uuid AND status IN ('em_andamento', 'ABERTO')
                       ORDER BY data_inicio DESC LIMIT 1;""",
                    motorista_id
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em andamento para pausar."}

                turno_id = str(turno["id"])
                await conn.execute("UPDATE public.turnos SET status = 'em_pausa' WHERE id = $1::uuid;", turno_id)
                await conn.execute(
                    "INSERT INTO public.pausas_turno (turno_id, motivo, inicio_pausa) VALUES ($1::uuid, 'Pausa Operacional', $2);",
                    turno_id, agora_brasil()
                )
            return {"sucesso": True}
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    @staticmethod
    async def retomar_turno(motorista_id: str) -> Dict[str, Any]:
        """Finaliza a pausa aberta do turno ativo, calculando o tempo decorrido no fuso brasileiro."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno = await conn.fetchrow(
                    """SELECT id, status FROM public.turnos
                       WHERE motorista_id = $1::uuid AND status = 'em_pausa'
                       ORDER BY data_inicio DESC LIMIT 1;""",
                    motorista_id
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em pausa registrada no momento."}

                turno_id = str(turno["id"])
                await conn.execute("UPDATE public.turnos SET status = 'em_andamento' WHERE id = $1::uuid;", turno_id)
                await conn.execute(
                    "UPDATE public.pausas_turno SET fim_pausa = $1 WHERE turno_id = $2::uuid AND fim_pausa IS NULL;",
                    agora_brasil(), turno_id
                )
            return {"sucesso": True}
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    @staticmethod
    async def verificar_transacoes_turno(motorista_id: str) -> int:
        """Verifica com rigor se há lançamentos vinculados EXCLUSIVAMENTE ao turno ativo (Read-Only).

        Fail-Safe: em caso de erro retorna 0 para que o sistema prefira acionar a trava
        de confirmação em vez de fechar silenciosamente um turno zerado.
        Isso elimina a vulnerabilidade de "Fail-Open" da versão anterior.
        """
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno = await conn.fetchrow(
                    "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id
                )
                if not turno:
                    return 0  # Sem turno ativo: nada a travar

                turno_id = str(turno["id"])

                # Query purificada: filtra EXCLUSIVAMENTE por turno_id.
                # Removemos o fallback "OR turno_id IS NULL" que contabilizava transações
                # órfãs (sem vínculo com o turno atual), causando contagens espúrias.
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as total FROM public.transacoes "
                    "WHERE motorista_id = $1::uuid AND turno_id = $2::uuid AND estornado = FALSE;",
                    "AND tipo_movimentacao = 'receita' "  # <--- ADICIONAR ESTA LINHA
                    "AND estornado = FALSE;", 
                    motorista_id, turno_id, dt_inicio
                )
                return int(row["total"]) if row else 0
        except Exception as e:
            # Fail-Safe: retorna 0 para que o sistema acione a confirmação de faturamento
            # zerado em vez de fechar o turno sem validação humana.
            logger.error(f"[TurnoService] Erro crítico na trava de fechamento (motorista={motorista_id}): {e}")
            return 0
