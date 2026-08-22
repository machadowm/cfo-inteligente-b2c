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
    async def abrir_turno(motorista_id: str, veiculo_id: str, km_inicial: float) -> Dict[str, Any]:
        """
        Abre um novo turno para o motorista com validação rigorosa de monotonicidade do odômetro
        em relação ao último fechamento registrado deste veículo.
        """
        try:
            km_inicial_decimal = TurnoService._validar_km(km_inicial, "km_inicial")
        except ValueError as exc:
            return {"sucesso": False, "erro": f"❌ {str(exc)}", "tipo_erro": "KM_INVALIDO"}

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # 1. Verifica se já existe QUALQUER turno ativo aberto para o motorista
                turno_ativo = await conn.fetchrow(
                    "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa');",
                    motorista_id
                )
                if turno_ativo:
                    return {
                        "sucesso": False,
                        "erro": "⚠️ Você já possui uma jornada em andamento. Encerre o turno atual antes de abrir outro.",
                        "tipo_erro": "TURNO_JA_ATIVO"
                    }

                # 2. Busca o último turno encerrado deste veículo para validar monotonicidade do odômetro
                ultimo_turno = await conn.fetchrow(
                    """
                    SELECT km_final FROM public.turnos
                    WHERE veiculo_id = $1::uuid AND status = 'concluido' AND km_final IS NOT NULL
                    ORDER BY data_fim DESC LIMIT 1;
                    """,
                    veiculo_id
                )

                if ultimo_turno and ultimo_turno["km_final"] is not None:
                    km_final_anterior = Decimal(str(ultimo_turno["km_final"]))
                    if km_inicial_decimal < km_final_anterior:
                        # Retorna erro amigável sem limpar a máquina de estados FSM
                        return {
                            "sucesso": False,
                            "erro": f"⚠️ *Odômetro Divergente!*\nO valor informado (*{float(km_inicial_decimal):.1f} km*) é menor que o odômetro final do último turno deste veículo (*{float(km_final_anterior):.1f} km*).\n\n"
                                   f"Por favor, envie o **valor correto** atual do painel do seu veículo:",
                            "tipo_erro": "ODOMETRO_DIVERGENTE"
                        }

                # 3. Insere o turno com carimbo de tempo oficial do fuso horário brasileiro
                row = await conn.fetchrow(
                    """
                    INSERT INTO public.turnos (motorista_id, veiculo_id, km_inicial, status, data_inicio)
                    VALUES ($1::uuid, $2::uuid, $3, 'ABERTO', $4)
                    RETURNING id, km_inicial, data_inicio;
                    """,
                    motorista_id, veiculo_id, km_inicial_decimal, agora_brasil()
                )

                return {
                    "sucesso": True,
                    "turno_id": str(row["id"]),
                    "km_inicial": float(row["km_inicial"]),
                    "data_inicio": row["data_inicio"]
                }

        except Exception as e:
            logger.exception("Erro crítico ao abrir turno.")
            return {"sucesso": False, "erro": f"Erro interno ao abrir turno: {e}", "tipo_erro": "ERRO_INTERNO"}

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
                           v.estoque_financeiro, v.tipo_combustivel, v.is_flex, v.is_hibrido, v.is_eletrico, v.capacidade_tanque, v.capacidade_bateria,
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
                        "erro": f"⚠️ *Odômetro Final Divergente!*\nO valor informado (*{float(km_final_decimal):.1f} km*) é inferior ao inicial registrado no início do turno (*{float(km_inicial_decimal):.1f} km*).\n\n"
                               f"Por favor, envie o **valor correto** atual do painel do seu veículo:",
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
                
                # Garante chaves padronizadas de estoque
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

                custo_combustivel_queimado = Decimal("0.00")
                total_unidades_queimadas_liq = Decimal("0.00")
                total_unidades_queimadas_ele = Decimal("0.00")
                detalhe_queima = []

                km_restante = km_rodados

                # 2.1. SE FOR HÍBRIDO OU ELÉTRICO: Prioriza consumo da bateria elétrica (EV Mode / Solar CMP)
                if (turno["is_hibrido"] or turno["is_eletrico"]) and km_restante > 0:
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

                # 2.2. CONSUMO DE LÍQUIDO (Se restou KM para queimar ou se é veículo combustão/Flex)
                if km_restante > 0 and not turno["is_eletrico"]:
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
                custo_aluguel_semanal = Decimal(str(turno["custo_aluguel_semanal"] or "1020.85"))
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
                    "SELECT id, status FROM public.turnos WHERE motorista_id = $1::uuid AND status = 'ABERTO' ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em andamento aberta para pausar."}

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
                    "SELECT id, status FROM public.turnos WHERE motorista_id = $1::uuid AND status = 'em_pausa' ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em pausa registrada no momento."}

                turno_id = str(turno["id"])
                await conn.execute("UPDATE public.turnos SET status = 'ABERTO' WHERE id = $1::uuid;", turno_id)
                await conn.execute(
                    "UPDATE public.pausas_turno SET fim_pausa = $1 WHERE turno_id = $2::uuid AND fim_pausa IS NULL;",
                    agora_brasil(), turno_id
                )
            return {"sucesso": True}
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    @staticmethod
    async def verificar_transacoes_turno(motorista_id: str) -> int:
        """Verifica se o motorista registrou lançamentos (faturamento ou despesa) durante a jornada atual (Read-Only)."""
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno = await conn.fetchrow(
                    "SELECT id, data_inicio FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id
                )
                if not turno:
                    return 0

                turno_id = str(turno["id"])
                dt_inicio = turno["data_inicio"]

                row = await conn.fetchrow(
                    "SELECT COUNT(*) as total FROM public.transacoes "
                    "WHERE motorista_id = $1::uuid AND (turno_id = $2::uuid OR (turno_id IS NULL AND data_transacao >= $3)) AND estornado = FALSE;",
                    motorista_id, turno_id, dt_inicio
                )
                return int(row["total"]) if row else 0
        except Exception:
            return 1 # Fallback conservador para evitar loop

```

---

## 📂 backend/services/redis_fsm.py
**Caminho sugerido:** `backend/services/redis_fsm.py`

```python
import os
import redis.asyncio as redis

REDIS_URL = os.getenv("REDIS_URL", "redis://:cfo_redis_password_2026@cfo_redis:6379/0")

class RedisFSMService:
    """
    Gerenciador de Estado Conversacional (Finite State Machine) e Aglutinação de Mensagens (Debouncing) via Redis 7.
    Adiciona controle de erros consecutivos para prevenção de loops infinitos.
    """
    _client = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        if cls._client is None:
            cls._client = redis.from_url(REDIS_URL, decode_responses=True)
        return cls._client

    @staticmethod
    async def obter_estado(key: str) -> str:
        """Obtém o estado atual da FSM para o tenant."""
        client = await RedisFSMService.get_client()
        estado = await client.get(f"state:{key}")
        return estado if estado else "IDLE"

    @staticmethod
    async def definir_estado(key: str, estado: str, ex_seconds: int = 1800):
        """Define o estado atual da FSM com TTL de segurança (padrão 30 minutos)."""
        client = await RedisFSMService.get_client()
        await client.set(f"state:{key}", estado, ex=ex_seconds)

    @staticmethod
    async def limpar_buffer(key: str):
        """Limpa o estado e os buffers associados à FSM do tenant."""
        client = await RedisFSMService.get_client()
        await client.delete(f"state:{key}")
        await client.delete(f"buffer_msg:{key}")

    @staticmethod
    async def acumular_mensagem(tenant_id: str, texto: str, janela_segundos: int = 4) -> list:
        """
        Implementa o debouncing: acumula mensagens enviadas em rajada pelo motorista
        numa lista do Redis, renovando a janela temporal. Retorna o acumulado.
        """
        client = await RedisFSMService.get_client()
        list_key = f"buffer_msg:{tenant_id}"
        
        async with client.pipeline(transaction=True) as pipe:
            pipe.rpush(list_key, texto)
            pipe.expire(list_key, janela_segundos)
            await pipe.execute()
        
        mensagens = await client.lrange(list_key, 0, -1)
        return mensagens

    @staticmethod
    async def obter_erros_consecutivos(key: str) -> int:
        """Obtém o contador de erros consecutivos para o tenant."""
        client = await RedisFSMService.get_client()
        erros = await client.get(f"errors:{key}")
        return int(erros) if erros else 0

    @staticmethod
    async def incrementar_erros_consecutivos(key: str) -> int:
        """Incrementa o contador de erros consecutivos para o tenant e renova o TTL de 15 minutos."""
        client = await RedisFSMService.get_client()
        erros = await client.incr(f"errors:{key}")
        await client.expire(f"errors:{key}", 900)
        return erros

    @staticmethod
    async def limpar_erros_consecutivos(key: str):
        """Reseta o contador de erros consecutivos do tenant."""
        client = await RedisFSMService.get_client()
        await client.delete(f"errors:{key}")

```

---

## 📂 backend/services/help_service.py
**Caminho sugerido:** `backend/services/help_service.py`

```python
class HelpService:
    """
    Provedor estático de informações, tutoriais e documentação de suporte para o motorista.
    Opera de forma stateless, sem alterar o contexto ou o estado da FSM no Redis.
    """
    
    _TEXTOS = {
        "geral": (
            "🤖 *Central de Ajuda CFO Inteligente* 🛡️\n\n"
            "Eu sou o seu CFO Virtual. Entendo comandos em linguagem natural a qualquer momento! "
            "Aqui estão as instruções de uso do sistema:\n\n"
            "🟢 *Para Iniciar Jornada:*\n"
            "Envie: *'Iniciar [KM]'* ou apenas *'Iniciar'*\n"
            "_(Ex: 'Iniciar 13005' ou o bot perguntará seu odômetro)_\n\n"
            "🏁 *Para Encerrar Jornada:*\n"
            "Envie: *'Fechar [KM]'* ou apenas *'Fechar'*\n"
            "_(Ex: 'Fechar 13120' - O DRE diário completo será gerado)_\n\n"
            "⏸️ *Pausas e Intervalos:*\n"
            "Envie: *'Pausa'*, *'Pausar'*, *'Fui Almoçar'* ou *'Retomar'*, *'Voltei'*\n\n"
            "📊 *Resumo Parcial:*\n"
            "Envie: *'Status'*, *'Resumo'* ou *'Parcial'*\n\n"
            "💰 *Lançamentos Livres (Fricção Zero):*\n"
            "• Receitas: *'ganhei 150 na uber'*, *'faturei 80 da 99'*, *'corrida 35 particular'*\n"
            "• Despesas: *'gastei 50 posto'*, *'marmita 22'*, *'paguei 120 mercado'*, *'lava jato 45'*\n\n"
            "Deseja ajuda com um tema específico? Digite:\n"
            "👉 *'Ajuda metas'* - Para entender as metas de faturamento.\n"
            "👉 *'Ajuda contrato'* - Para saber como atualizar aluguel e franquia.\n"
            "👉 *'Ajuda lancamentos'* - Exemplos de registros financeiros."
        ),
        "metas": (
            "🎯 *Ajuda com Metas e Indicadores de Eficiência*\n\n"
            "O CFO Inteligente ajuda você a monitorar sua performance em tempo real com base em metas realistas:\n\n"
            "• *Meta Mensal:* Definida por padrão como *R$ 12.000,00* de faturamento bruto.\n"
            "• *Dias Úteis:* Configurado para *26 dias* de trabalho por mês.\n"
            "• *Meta Diária Recomendada:* O bot calcula automaticamente o valor de *R$ 461,54 por dia trabalhado*.\n\n"
            "Durante a jornada, o sistema audita se seus ganhos parciais estão de acordo com as seguintes métricas:\n"
            "• *Piso de Ganho por KM:* Mínimo de *R$ 2,00 por km rodado*.\n"
            "• *Piso de Ganho por Hora:* Mínimo de *R$ 30,00 por hora trabalhada*.\n\n"
            "Ao fechar o turno, você verá qual percentual da sua meta diária foi atingido! 🚀"
        ),
        "contrato": (
            "⚙️ *Ajuda com Atualização de Contrato (Localiza Zarp, etc.)*\n\n"
            "Se você trocou de carro, mudou de locadora ou o valor do aluguel foi reajustado, você pode atualizar os parâmetros do sistema digitando uma única frase livre:\n\n"
            "👉 *Comando:* _atualizar contrato [Locadora] [Valor Semanal] [Franquia Semanal]_\n"
            "👉 *Exemplo:* _atualizar contrato Zarp 1050 1500_\n\n"
            "O sistema processará as regras contratuais da seguinte forma:\n"
            "• *Custo Fixo Rateado:* Dividirá o valor semanal por 6 (escala padrão de trabalho) para deduzir o aluguel pro-rata diário no seu DRE.\n"
            "• *Franquia de KM Diária:* Dividirá os 1.500 km por 7 dias (214 km/dia) para alertar se você está na média segura de rodagem.\n\n"
            "Se o seu carro for *Próprio* ou *Financiado*, você pode parametrizar a amortização diária (ex: R$ 15,00/dia para custos de depreciação):\n"
            "👉 *Exemplo:* _atualizar contrato Proprietario 90 0_\n"
            "_(R$ 90,00 divididos pela escala de 6 dias úteis resultará em R$ 15,00/dia no DRE)_"
        ),
        "lancamentos": (
            "💰 *Ajuda com Lançamentos Financeiros (Fricção Zero)*\n\n"
            "Você não precisa de comandos rígidos. Escreva exatamente como falaria para um amigo no trânsito:\n\n"
            "🟢 *Registrar Entradas (Ganhos):*\n"
            "• 'ganhei 180 uber'\n"
            "• 'faturei 250 hj na 99'\n"
            "• 'viagem particular de 50 reais'\n"
            "• 'receita de 30 no indrive'\n\n"
            "❌ *Registrar Saídas (Gastos):*\n"
            "• 'gastei 80 no posto de gasolina'\n"
            "• 'paguei 25 de marmita no almoco'\n"
            "• 'lava jato ficou em 45 reais'\n"
            "• 'compras no mercado deu 120'\n"
            "• 'gastei 2 reais de bala para os passageiros'\n\n"
            "🛡️ *Idempotência:* Cada mensagem tem um ID exclusivo. Se o seu sinal cair e o WhatsApp enviar a mesma mensagem duas vezes, o CFO Inteligente bloqueia o segundo registro automaticamente, impedindo faturamentos ou despesas duplicadas no seu caixa!"
        )
    }

    @staticmethod
    def obter_ajuda(topico: str = "geral") -> str:
        """Retorna o texto de ajuda formatado para o tópico correspondente."""
        topico_limpo = topico.lower().strip()
        return HelpService._TEXTOS.get(topico_limpo, HelpService._TEXTOS["geral"])
