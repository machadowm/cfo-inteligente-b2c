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
    def _processar_uso_pessoal(
        km_gap: Decimal,
        estoque: dict,
        is_hibrido: bool,
        is_eletrico: bool,
        tipo_comb: str,
    ) -> tuple[Decimal, str]:
        """Debita do cofre virtual o custo de combustível referente a quilometragem de uso pessoal.

        Aplica o mesmo Power Split de três fases (EV → GNV → Líquido) usado em
        `abrir_turno` e `retomar_turno`.  Modifica `estoque` in-place.

        Retorna:
            (custo_total: Decimal, detalhe: str) — custo amortizado e descrição legível.
        """
        custo = Decimal("0.00")
        partes: list[str] = []
        km_restante = km_gap

        # Fase 1 — Elétrico / Híbrido
        if (is_hibrido or is_eletrico) and km_restante > Decimal("0"):
            eletro = estoque["eletricidade"]
            kwh_disp = Decimal(str(eletro.get("kwh", 0.0)))
            custo_bat = Decimal(str(eletro.get("custo_total", 0.0)))
            km_kwh = Decimal(str(eletro.get("km_kwh", 6.5)))
            if kwh_disp > Decimal("0") and km_kwh > Decimal("0"):
                cmp_kwh = custo_bat / kwh_disp
                kwh_q = min(kwh_disp, km_restante / km_kwh)
                c = (kwh_q * cmp_kwh).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                custo += c
                eletro["kwh"]        = float(max(Decimal("0"), kwh_disp - kwh_q).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                eletro["custo_total"] = float(max(Decimal("0"), custo_bat - c).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                km_restante -= kwh_q * km_kwh
                partes.append(f"{float(kwh_q):.2f} kWh (R$ {float(c):.2f})")

        # Fase 2 — GNV
        if tipo_comb == "gnv" and km_restante > Decimal("0"):
            gnv = estoque["gnv"]
            m3_disp = Decimal(str(gnv.get("m3", 0.0)))
            custo_gnv = Decimal(str(gnv.get("custo_total", 0.0)))
            km_m3 = Decimal(str(gnv.get("km_m3", 14.0)))
            if m3_disp > Decimal("0") and km_m3 > Decimal("0"):
                m3_q = min(m3_disp, km_restante / km_m3)
                c = (m3_q * custo_gnv / m3_disp).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                custo += c
                gnv["m3"]        = float(max(Decimal("0"), m3_disp - m3_q).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                gnv["custo_total"] = float(max(Decimal("0"), custo_gnv - c).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                km_restante -= m3_q * km_m3
                partes.append(f"{float(m3_q):.2f} m³ (R$ {float(c):.2f})")

        # Fase 3 — Líquido (Flex / Gasolina)
        if km_restante > Decimal("0") and not is_eletrico and tipo_comb != "gnv":
            liq = estoque["liquido"]
            total_l = Decimal(str(liq.get("litros", 0.0)))
            custo_l = Decimal(str(liq.get("custo_total", 0.0)))
            if total_l > Decimal("0"):
                km_l_gas = Decimal(str(liq.get("km_l_gasolina", 12.0)))
                km_l_eta = Decimal(str(liq.get("km_l_etanol",  8.5)))
                p_gas    = Decimal(str(liq.get("gasolina_proporcao", 1.0)))
                p_eta    = Decimal(str(liq.get("etanol_proporcao",  0.0)))
                km_l_med = (p_gas * km_l_gas) + (p_eta * km_l_eta)
                if km_l_med <= Decimal("0"):
                    km_l_med = Decimal("10.0")
                litros_q = min(total_l, km_restante / km_l_med)
                c = (litros_q * custo_l / total_l).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                custo += c
                novo_g = max(Decimal("0"), Decimal(str(liq.get("gasolina_litros", 0.0))) - litros_q * p_gas)
                novo_e = max(Decimal("0"), Decimal(str(liq.get("etanol_litros",   0.0))) - litros_q * p_eta)
                novo_t = novo_g + novo_e
                liq["gasolina_litros"] = float(novo_g.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                liq["etanol_litros"]   = float(novo_e.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                liq["litros"]          = float(novo_t.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                liq["custo_total"]     = float(max(Decimal("0"), custo_l - c).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
                if novo_t > Decimal("0"):
                    liq["gasolina_proporcao"] = float((novo_g / novo_t).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                    liq["etanol_proporcao"]   = float((novo_e / novo_t).quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP))
                else:
                    liq["gasolina_proporcao"] = 1.0
                    liq["etanol_proporcao"]   = 0.0
                km_restante -= litros_q * km_l_med
                partes.append(f"{float(litros_q):.2f} L (R$ {float(c):.2f})")

        # Fase 4 — Fallback: sem estoque, sem abastecimento registrado
        if km_restante > Decimal("0"):
            c_fb = (km_restante * Decimal("0.48")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            custo += c_fb
            partes.append(f"est. R$ {float(c_fb):.2f} ({float(km_restante):.1f} km s/ estoque)")

        detalhe = " | ".join(partes) if partes else "sem estoque"
        return custo, detalhe

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

                # 2. Busca o km_final do último turno concluído para validar monotonicidade,
                #    e lê o estoque ATUAL do veículo (já incorpora quaisquer abastecimentos
                #    inter-turnos — CMP correto, não o snapshot stale do turno anterior).
                ultimo_km_row = await conn.fetchrow(
                    """
                    SELECT km_final
                    FROM public.turnos
                    WHERE veiculo_id = $1::uuid AND status = 'concluido' AND km_final IS NOT NULL
                    ORDER BY data_fim DESC LIMIT 1;
                    """,
                    veiculo_id,
                )
                veiculo_atual = await conn.fetchrow(
                    "SELECT estoque_financeiro, tipo_combustivel FROM public.veiculos WHERE id = $1::uuid;",
                    veiculo_id,
                )

                km_uso_pessoal = Decimal("0.00")
                custo_uso_pessoal = Decimal("0.00")  # rastreado para a notificação ao motorista
                gap_requer_confirmacao = False        # flag para trava de sanidade (erros de digitação)

                if ultimo_km_row and ultimo_km_row["km_final"] is not None:
                    km_anterior = Decimal(str(ultimo_km_row["km_final"]))
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

                    # Trava de Sanidade: gap acima de 500 km exige confirmação explícita
                    # para evitar que erros de digitação (ex: 1790000) zeram o cofre inteiro.
                    _GAP_LIMITE_CONFIRMACAO = Decimal("500")
                    if km_ini - km_anterior > _GAP_LIMITE_CONFIRMACAO:
                        gap_fmt = f"{float(km_ini - km_anterior):,.0f}".replace(",", ".")
                        return {
                            "sucesso": False,
                            "erro": (
                                f"⚠️ *Gap de Odômetro Elevado!*\\n"
                                f"Você informou *{gap_fmt} km* de uso pessoal desde o último turno.\\n\\n"
                                f"Isso parece muito alto. Confirme o odômetro correto ou "
                                f"use  *!ajustar estoque*  para corrigir o cofre manualmente."
                            ),
                            "tipo_erro": "GAP_ODOMETRO_ELEVADO",
                        }

                    # Processamento de Uso Pessoal se houver gap positivo
                    if km_ini > km_anterior:
                        km_uso_pessoal = km_ini - km_anterior

                        # Lê o estoque ATUAL do veículo (CMP pós-abastecimentos inter-turnos)
                        raw_est = veiculo_atual["estoque_financeiro"] if veiculo_atual else {}
                        estoque: dict = json.loads(raw_est) if isinstance(raw_est, str) else (raw_est or {})
                        estoque = TurnoService._garantir_estrutura_estoque(estoque)
                        meta = estoque["meta"]
                        is_hibrido = bool(meta.get("is_hibrido", False))
                        is_eletrico = bool(meta.get("is_eletrico", False))
                        tipo_comb = (
                            (veiculo_atual["tipo_combustivel"] if veiculo_atual else None)
                            or meta.get("tipo_veiculo", "")
                        ).lower()

                        custo_uso_pessoal, _detalhe = TurnoService._processar_uso_pessoal(
                            km_uso_pessoal, estoque, is_hibrido, is_eletrico, tipo_comb
                        )
                        if custo_uso_pessoal > Decimal("0"):
                            logger.info(
                                f"[abrir_turno] Uso pessoal {float(km_uso_pessoal):.1f} km → "
                                f"R$ {float(custo_uso_pessoal):.2f} | {_detalhe} (motorista={motorista_id})"
                            )

                        # Salva estoque recalculado no banco
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
                    "custo_uso_pessoal": float(custo_uso_pessoal),
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
                           v.escala_trabalho, v.contrato_personalizado, m.meta_mensal_faturamento, m.dias_uteis_mes,
                           COALESCE(m.piso_ganho_km, 2.0) AS piso_ganho_km,
                           COALESCE(m.piso_ganho_hora, 30.0) AS piso_ganho_hora
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

                # FIX #2 — UPDATE idempotente: a cláusula AND status != 'concluido' garante que
                # um segundo request paralelo (debouncing falhou ou reenvio WhatsApp) não insira
                # um segundo snapshot em fechamento_diario. RETURNING id detecta a colisão.
                updated = await conn.fetchval(
                    "UPDATE public.turnos SET km_final = $1, data_fim = $2, status = 'concluido' "
                    "WHERE id = $3::uuid AND status != 'concluido' "
                    "RETURNING id;",
                    km_final_decimal, hora_fim_real, turno_id
                )
                if not updated:
                    return {
                        "sucesso": False,
                        "erro": "⚠️ Este turno já foi encerrado. Verifique com  *status*  se o DRE já foi gerado.",
                        "tipo_erro": "TURNO_JA_CONCLUIDO",
                    }

                # FIX #3 — Fechar com pausa aberta: se houver uma pausa sem fim_pausa,
                # fecha-a agora com o timestamp de fechamento do turno.
                # Sem isso: tempo_pausas usa COALESCE(fim_pausa, CURRENT_TIMESTAMP) e cresce
                # indefinidamente, mas km_pessoal_intra fica 0 — os dois indicadores divergem.
                await conn.execute(
                    """
                    UPDATE public.pausas_turno
                    SET fim_pausa = $1
                    WHERE turno_id = $2::uuid AND fim_pausa IS NULL;
                    """,
                    hora_fim_real, turno_id
                )

                dt_inicio = turno["data_inicio"]
                if dt_inicio.tzinfo is not None:
                    dt_inicio = dt_inicio.astimezone(TZ_BR)

                # Cálculo de tempo operacional e uso pessoal intra-turno
                # FIX #3 aplicado acima: todas as pausas agora têm fim_pausa definido,
                # portanto COALESCE(fim_pausa, CURRENT_TIMESTAMP) é idêntico a fim_pausa.
                tempo_total_min = Decimal(str(int((hora_fim_real - dt_inicio).total_seconds() / 60)))
                pausas_agg = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(EXTRACT(EPOCH FROM (fim_pausa - inicio_pausa))/60), 0)
                            AS tempo_pausas,
                        COALESCE(SUM(
                            CASE WHEN km_fim IS NOT NULL AND km_inicio IS NOT NULL
                                 THEN km_fim - km_inicio ELSE 0 END), 0)
                            AS km_pessoal_intra
                    FROM public.pausas_turno
                    WHERE turno_id = $1::uuid;
                    """,
                    turno_id
                )
                tempo_pausas_min   = Decimal(str(int(pausas_agg["tempo_pausas"]   or 0)))
                km_pessoal_intra   = Decimal(str(pausas_agg["km_pessoal_intra"] or "0.00"))
                tempo_efetivo_min  = max(Decimal("1.00"), tempo_total_min - tempo_pausas_min)
                horas_trabalhadas  = (tempo_efetivo_min / Decimal("60.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

                # Audit alert: pausas > 40% da jornada total sugerem possível manipulação
                if tempo_total_min > 0 and (tempo_pausas_min / tempo_total_min) > Decimal("0.40"):
                    logger.warning(
                        f"[fechar_turno] Pausas representam {float(tempo_pausas_min/tempo_total_min*100):.1f}% "
                        f"da jornada — verificar integridade (turno={turno_id}, motorista={motorista_id})"
                    )

                # km profissional: exclui km rodados durante pausas (uso pessoal intra-turno)
                km_profissional = max(Decimal("0.00"), km_rodados - km_pessoal_intra)

                # 2. LÓGICA DE QUEIMA HÍBRIDA MULTI-SOURCE DE ENERGIA (Power Split)
                # FIX #2 — lê o estoque com FOR UPDATE separado, após o UPDATE do turno,
                # para capturar abastecimentos feitos entre a abertura e o fechamento do turno
                # (inclusive durante pausas) sem depender do snapshot do JOIN inicial.
                veiculo_estoque_row = await conn.fetchrow(
                    "SELECT estoque_financeiro, tipo_combustivel, is_hibrido, is_eletrico "
                    "FROM public.veiculos WHERE id = $1::uuid FOR UPDATE;",
                    veiculo_id
                )
                estoque_raw = veiculo_estoque_row["estoque_financeiro"] if veiculo_estoque_row else turno["estoque_financeiro"]
                estoque = json.loads(estoque_raw) if isinstance(estoque_raw, str) else (estoque_raw or {})
                estoque = TurnoService._garantir_estrutura_estoque(estoque)
                meta = estoque["meta"]

                # Flags escalares têm precedência sobre JSONB.meta (banco de produção possui ambos)
                _vrow = veiculo_estoque_row or turno
                is_hibrido = bool(_vrow.get("is_hibrido") if _vrow.get("is_hibrido") is not None else meta.get("is_hibrido", False))
                is_eletrico = bool(_vrow.get("is_eletrico") if _vrow.get("is_eletrico") is not None else meta.get("is_eletrico", False))
                tipo_comb = (_vrow["tipo_combustivel"] or meta.get("tipo_veiculo", "")).lower()

                custo_combustivel_queimado = Decimal("0.00")
                total_unidades_queimadas_liq = Decimal("0.00")
                total_unidades_queimadas_ele = Decimal("0.00")
                total_unidades_queimadas_gnv = Decimal("0.00")
                detalhe_queima = []

                km_restante = km_profissional

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

                # Se o estoque virtual estava zerado e ainda restou KM para queimar:
                # Amortiza PROPORCIONALMENTE usando o preço real médio do abastecimento do turno.
                # NÃO soma o valor total da nota fiscal — isso seria contabilizar todo o estoque
                # comprado como despesa do dia (violação do regime de competência).
                if km_restante > 0:
                    abt_row = await conn.fetchrow(
                        """
                        SELECT COALESCE(SUM(valor), 0.0000)              AS total_valor,
                               COALESCE(SUM(litros_abastecidos), 0.0000) AS total_litros
                        FROM public.transacoes
                        WHERE motorista_id = $1::uuid AND turno_id = $2::uuid
                          AND categoria = 'combustivel' AND estornado = FALSE;
                        """,
                        motorista_id, turno_id,
                    )
                    abt_valor  = Decimal(str(abt_row["total_valor"]  or "0.00"))
                    abt_litros = Decimal(str(abt_row["total_litros"] or "0.00"))

                    if abt_litros > Decimal("0"):
                        # Preço real médio ponderado dos abastecimentos registrados no turno
                        preco_litro_real = abt_valor / abt_litros

                        # Rendimento da mistura: usa variáveis já calculadas na fase 2.3 se
                        # disponíveis (veículo flex com estoque parcial); caso contrário deriva
                        # do estoque atual — nunca usa `locals()` (frágil e não determinístico).
                        liq_ref = estoque.get("liquido", {})
                        _km_l_gas = Decimal(str(liq_ref.get("km_l_gasolina", 12.0)))
                        _km_l_eta = Decimal(str(liq_ref.get("km_l_etanol",  8.5)))
                        _p_gas    = Decimal(str(liq_ref.get("gasolina_proporcao", 1.0)))
                        _p_eta    = Decimal(str(liq_ref.get("etanol_proporcao",  0.0)))
                        km_l_fallback = (_p_gas * _km_l_gas) + (_p_eta * _km_l_eta)
                        if km_l_fallback <= Decimal("0"):
                            km_l_fallback = Decimal("10.0")

                        litros_necessarios_restantes = km_restante / km_l_fallback
                        custo_estimado = (litros_necessarios_restantes * preco_litro_real).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        custo_combustivel_queimado += custo_estimado
                        # Contabiliza os litros estimados no denominador de km/L para que o
                        # indicador de rendimento reflita a realidade física do turno.
                        total_unidades_queimadas_liq += litros_necessarios_restantes
                        detalhe_queima.append(
                            f"Abastecimento (Proporcional): {float(litros_necessarios_restantes):.2f} L "
                            f"× R$ {float(preco_litro_real):.3f} = R$ {float(custo_estimado):.2f}"
                        )
                    else:
                        # FIX #4 — Fallback SRE: sem estoque e sem abastecimento registrado.
                        # Usa custo por km calibrado por tipo de motorização:
                        #   Elétrico puro → R$ 0,12/km  (≈ 6 km/kWh × R$ 0,72/kWh médio)
                        #   Combustão/híbrido → R$ 0,48/km  (≈ 12 km/L × R$ 5,76/L médio)
                        _custo_km_fb = Decimal("0.12") if is_eletrico else Decimal("0.48")
                        custo_estimado = (km_restante * _custo_km_fb).quantize(
                            Decimal("0.01"), rounding=ROUND_HALF_UP
                        )
                        custo_combustivel_queimado += custo_estimado
                        tipo_fb = "elétrico" if is_eletrico else "combustão"
                        detalhe_queima.append(f"Falta Estoque ({tipo_fb}): R$ {float(custo_estimado):.2f}")

                # Salva o estoque total recalculado
                await conn.execute(
                    "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                    json.dumps(estoque), veiculo_id
                )

                # 3. APURAÇÃO CONTÁBIL E EXTRAÇÃO DE DESPESAS DETALHADAS DO TURNO
                # Scope-clean: filtra SOMENTE por turno_id.  A cláusula "OR turno_id IS NULL"
                # foi removida — ela contabilizava transações órfãs (sem turno) como receita
                # do turno atual, impedindo a trava de faturamento-zero de disparar corretamente.
                financeiro = await conn.fetchrow(
                    "SELECT "
                    "    COALESCE(SUM(CASE WHEN tipo_movimentacao = 'receita' THEN valor ELSE 0 END), 0.0000) as faturamento, "
                    "    COALESCE(SUM(CASE WHEN tipo_movimentacao = 'despesa' AND categoria != 'combustivel' THEN valor ELSE 0 END), 0.0000) as despesas_operacionais, "
                    "    COALESCE(SUM(CASE WHEN tipo_movimentacao = 'despesa' AND categoria = 'combustivel' THEN valor ELSE 0 END), 0.0000) as total_abastecido "
                    "FROM public.transacoes "
                    "WHERE motorista_id = $1::uuid AND turno_id = $2::uuid AND estornado = FALSE;",
                    motorista_id, turno_id
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
                    "WHERE motorista_id = $1::uuid AND turno_id = $2::uuid "
                    "  AND tipo_movimentacao = 'despesa' AND estornado = FALSE "
                    "ORDER BY data_transacao ASC;",
                    motorista_id, turno_id
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
                # Custo de aluguel/contrato: rateado por 6 dias de escala semanal
                custo_fixo_contrato = (custo_aluguel_semanal / Decimal("6.00")).quantize(Decimal("0.02"), rounding=ROUND_HALF_UP)

                # Pro-rata de despesas fixas cadastradas — provisão diária para cada conta
                # Busca também caixa_id e dia_vencimento para aporte e DRE
                despesas_fixas_rows = await conn.fetch(
                    """
                    SELECT nome, valor_pro_rata_diario, caixa_id, dia_vencimento
                    FROM public.despesas_fixas_mensais
                    WHERE motorista_id = $1::uuid AND ativo = TRUE;
                    """,
                    motorista_id
                )
                df_extra = sum(Decimal(str(r["valor_pro_rata_diario"])) for r in despesas_fixas_rows)
                # custo_fixo_total inclui ambos para o cálculo contábil do lucro
                custo_fixo_total = custo_fixo_contrato + df_extra

                # 5. DRE COMPLETO E LÓGICA DE PROVISÃO
                lucro_liquido_real = faturamento_bruto - custo_variavel_total - custo_fixo_total

                # Indicadores de eficiência e produtividade
                ganho_por_km  = (faturamento_bruto / km_rodados)   if km_rodados    > 0 else Decimal("0.00")
                custo_por_km = (custo_variavel_total + custo_fixo_total) / km_rodados if km_rodados > 0 else Decimal("0.00")
                lucro_por_km = (lucro_liquido_real / km_rodados) if km_rodados > 0 else Decimal("0.00")
                ganho_por_hora = (faturamento_bruto / horas_trabalhadas) if horas_trabalhadas > 0 else Decimal("0.00")

                meta_mensal = Decimal(str(turno["meta_mensal_faturamento"] or "12000.00"))
                dias_uteis = int(turno["dias_uteis_mes"] or 26)

                # Rendimento contábil final de Km por Litro / kWh do turno (Ponderado se híbrido)
                # Usa km_profissional para não inflar o rendimento com km de uso pessoal.
                # FIX #7 — inclui GNV para que km_por_unidade seja correto em veículos GNV
                total_unidades_queimadas = (
                    total_unidades_queimadas_liq
                    + total_unidades_queimadas_ele
                    + total_unidades_queimadas_gnv
                )
                km_por_unidade = (km_profissional / total_unidades_queimadas) if total_unidades_queimadas > 0 else Decimal("0.00")

                # 5.1. APORTE AUTOMÁTICO NAS CAIXAS DE PROVISÃO
                # Para cada despesa fixa com caixa vinculada, credita o pro-rata na caixinha.
                # Usa INSERT ... ON CONFLICT para criar a caixa se ainda não existir.
                # Retorna lista de aportes para exibição no DRE.
                aportes_caixas: list[dict] = []
                provisao_descontada_total = Decimal("0.00")

                for df_row in despesas_fixas_rows:
                    pro_rata = Decimal(str(df_row["valor_pro_rata_diario"]))
                    provisao_descontada_total += pro_rata
                    caixa_id_row = df_row["caixa_id"]

                    if caixa_id_row:
                        # Caixa vinculada por UUID — aporte respeitando teto (meta_valor)
                        cx = await conn.fetchrow(
                            "SELECT nome_caixa, saldo_atual, meta_valor "
                            "FROM public.caixas_provisao WHERE id = $1::uuid AND motorista_id = $2::uuid;",
                            str(caixa_id_row), motorista_id,
                        )
                        if cx:
                            saldo_cx   = Decimal(str(cx["saldo_atual"] or "0"))
                            meta_cx    = Decimal(str(cx["meta_valor"])) if cx["meta_valor"] is not None else None
                            # Calcula quanto pode depositar: limitado pelo que falta para a meta
                            if meta_cx is not None and saldo_cx >= meta_cx:
                                aporte_real = Decimal("0.00")  # caixa já cheia — pula
                            elif meta_cx is not None:
                                aporte_real = min(pro_rata, meta_cx - saldo_cx)
                            else:
                                aporte_real = pro_rata  # sem teto — deposita tudo

                            if aporte_real > 0:
                                await conn.execute(
                                    "UPDATE public.caixas_provisao "
                                    "SET saldo_atual = saldo_atual + $1 "
                                    "WHERE id = $2::uuid AND motorista_id = $3::uuid;",
                                    aporte_real, str(caixa_id_row), motorista_id,
                                )
                                meta_atingida = meta_cx is not None and (saldo_cx + aporte_real) >= meta_cx
                                aportes_caixas.append({
                                    "caixa": cx["nome_caixa"],
                                    "aporte": float(aporte_real),
                                    "meta": float(meta_cx) if meta_cx else None,
                                    "saldo_novo": float(saldo_cx + aporte_real),
                                    "meta_atingida": meta_atingida,
                                    "origem": df_row["nome"],
                                    "dia_vencimento": int(df_row["dia_vencimento"]) if df_row["dia_vencimento"] else None,
                                })
                            else:
                                # Caixa cheia — registra no DRE mas sem novo depósito
                                aportes_caixas.append({
                                    "caixa": cx["nome_caixa"],
                                    "aporte": 0.0,
                                    "meta": float(meta_cx) if meta_cx else None,
                                    "saldo_novo": float(saldo_cx),
                                    "meta_atingida": True,
                                    "origem": df_row["nome"],
                                    "dia_vencimento": int(df_row["dia_vencimento"]) if df_row["dia_vencimento"] else None,
                                })
                    else:
                        # Despesa sem caixa vinculada: tenta match pelo nome da despesa
                        # (fallback legado para despesas criadas antes da migração v9)
                        cx_leg = await conn.fetchrow(
                            "SELECT id, nome_caixa, saldo_atual, meta_valor "
                            "FROM public.caixas_provisao "
                            "WHERE motorista_id = $1::uuid AND lower(nome_caixa) = lower($2);",
                            motorista_id, df_row["nome"],
                        )
                        if cx_leg:
                            saldo_cx   = Decimal(str(cx_leg["saldo_atual"] or "0"))
                            meta_cx    = Decimal(str(cx_leg["meta_valor"])) if cx_leg["meta_valor"] is not None else None
                            if meta_cx is not None and saldo_cx >= meta_cx:
                                aporte_real = Decimal("0.00")
                            elif meta_cx is not None:
                                aporte_real = min(pro_rata, meta_cx - saldo_cx)
                            else:
                                aporte_real = pro_rata
                            if aporte_real > 0:
                                await conn.execute(
                                    "UPDATE public.caixas_provisao SET saldo_atual = saldo_atual + $1 "
                                    "WHERE id = $2::uuid;",
                                    aporte_real, str(cx_leg["id"]),
                                )
                            meta_atingida = meta_cx is not None and (saldo_cx + aporte_real) >= meta_cx
                            aportes_caixas.append({
                                "caixa": cx_leg["nome_caixa"],
                                "aporte": float(aporte_real),
                                "meta": float(meta_cx) if meta_cx else None,
                                "saldo_novo": float(saldo_cx + aporte_real),
                                "meta_atingida": meta_atingida,
                                "origem": df_row["nome"],
                                "dia_vencimento": int(df_row["dia_vencimento"]) if df_row["dia_vencimento"] else None,
                            })

                # Persiste o snapshot contábil na tabela fechamento_diario
                # Armazena km_profissional para que médias históricas reflitam apenas serviço real
                await conn.execute(
                    "INSERT INTO public.fechamento_diario ("
                    "    motorista_id, turno_id, faturamento_bruto, custo_variavel_direto, "
                    "    custo_fixo_rateado, lucro_liquido_real, km_rodados, data_fechamento, provisao_descontada"
                    ") VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, CURRENT_DATE, $8);",
                    motorista_id, turno_id, faturamento_bruto, custo_variavel_total,
                    custo_fixo_total, lucro_liquido_real, km_profissional, provisao_descontada_total
                )

                # Média histórica de faturamento diário (últimos 10 fechamentos,
                # excluindo o atual que acabou de ser inserido acima — usa LIMIT 10 OFFSET 1).
                # Usada na projeção mensal do DRE para base realista em vez de meta_diaria.
                hist_fat_row = await conn.fetchrow(
                    """
                    SELECT COALESCE(AVG(NULLIF(faturamento_bruto, 0)), 0) AS media_fat
                    FROM (
                        SELECT faturamento_bruto
                        FROM public.fechamento_diario
                        WHERE motorista_id = $1::uuid
                        ORDER BY data_fechamento DESC
                        LIMIT 10 OFFSET 1
                    ) sub;
                    """,
                    motorista_id,
                )
                media_fat_dia = float(hist_fat_row["media_fat"] or 0.0)

                # FIX #1 — Acumulado de faturamento bruto do mês corrente (excluindo o turno
                # que acabou de ser fechado, para não duplicar — usa transacoes do mês).
                # Necessário para que a projeção mensal e o déficit de meta no DRE usem
                # o faturamento real acumulado, não o faturamento de apenas um turno.
                fat_mes_row = await conn.fetchrow(
                    """
                    SELECT COALESCE(SUM(valor), 0) AS fat_mes
                    FROM public.transacoes
                    WHERE motorista_id = $1::uuid
                      AND tipo_movimentacao = 'receita'
                      AND estornado = FALSE
                      AND date_trunc('month', data_transacao AT TIME ZONE 'America/Sao_Paulo')
                          = date_trunc('month', CURRENT_DATE);
                    """,
                    motorista_id,
                )
                fat_bruto_mes = float(fat_mes_row["fat_mes"] or 0.0)

            return {
                "sucesso": True,
                "turno_id": turno_id,
                "data_inicio": dt_inicio.strftime('%d/%m/%Y %H:%M'),
                "data_fim": hora_fim_real.strftime('%d/%m/%Y %H:%M'),
                "km_inicial": float(km_inicial_decimal),
                "km_final": float(km_final_decimal),
                "km_rodados": float(km_rodados),
                "km_profissional": float(km_profissional),
                "km_pessoal_intra": float(km_pessoal_intra),
                "tempo_total_min": int(tempo_total_min),
                "tempo_pausas_min": int(tempo_pausas_min),
                "tempo_efetivo_min": int(tempo_efetivo_min),
                "horas_trabalhadas": float(horas_trabalhadas),
                "faturamento_bruto": float(faturamento_bruto),
                "custo_combustivel_queimado": float(custo_combustivel_queimado),
                "total_abastecido_turno": float(total_abastecido_turno),
                "outras_despesas_variaveis": float(outras_despesas_variaveis),
                "custo_variavel": float(custo_variavel_total),
                # FIX #5 — separados para evitar duplicação no DRE:
                # custo_fixo_contrato = só aluguel/pro-rata contratual (linha visível no DRE)
                # provisao_descontada = rateio das despesas fixas (linha separada no DRE)
                # custo_fixo_rateado  = soma dos dois (usado no lucro_liquido_real)
                "custo_fixo_contrato": float(custo_fixo_contrato),
                "custo_fixo_rateado": float(custo_fixo_total),
                "lucro_liquido_real": float(lucro_liquido_real),
                "ganho_por_km": float(ganho_por_km),
                "custo_por_km": float(custo_por_km),
                "lucro_por_km": float(lucro_por_km),
                "ganho_por_hora": float(ganho_por_hora),
                "km_por_litro": float(km_por_unidade),
                "meta_mensal": float(meta_mensal),
                "dias_uteis": dias_uteis,
                "piso_ganho_km": float(turno["piso_ganho_km"]),
                "piso_ganho_hora": float(turno["piso_ganho_hora"]),
                "locadora": turno["locadora"] or "Localiza Zarp",
                "custo_aluguel_semanal": float(turno["custo_aluguel_semanal"] or 1020.85),
                "escala_trabalho": turno["escala_trabalho"] or "De quarta a segunda (6 dias)",
                "franquia_km_semanal": float(turno["franquia_km_semanal"] or 1505.0),
                "valor_km_excedente": float(turno["valor_km_excedente"] or 0.75),
                "contrato_personalizado": bool(turno["contrato_personalizado"]),
                "detalhe_queima": " | ".join(detalhe_queima),
                "despesas_detalhadas": despesas_detalhadas,
                "aportes_caixas": aportes_caixas,
                "provisao_descontada": float(provisao_descontada_total),
                "media_fat_dia": media_fat_dia,
                "fat_bruto_mes": fat_bruto_mes,  # FIX #1 — acumulado mensal para projeção correta
            }

        except Exception as e:
            logger.exception("Falha na consolidação diária do turno.")
            return {"sucesso": False, "erro": f"Erro interno de processamento: {e}", "tipo_erro": "ERRO_INTERNO"}

    @staticmethod
    async def pausar_turno(motorista_id: str, km_pausa: Optional[float] = None) -> Dict[str, Any]:
        """Aplica interrupção operacional na jornada.

        Se `km_pausa` for fornecido, registra a âncora de odômetro no início da pausa.
        Isso permite que `retomar_turno` calcule os km de uso pessoal intra-turno
        e os debite silenciosamente do cofre virtual.
        """
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # Limita a espera pelo lock a 5 s. Se outro worker estiver a processar
                # uma pausa simultânea, evita que a conexão fique suspensa indefinidamente
                # e esgote o pool (max_size=30) em cargas de múltiplos motoristas.
                await conn.execute("SET LOCAL lock_timeout = '5s';")
                turno = await conn.fetchrow(
                    """SELECT id, status, km_inicial FROM public.turnos
                       WHERE motorista_id = $1::uuid AND status IN ('em_andamento', 'ABERTO')
                       ORDER BY data_inicio DESC LIMIT 1
                       FOR UPDATE;""",
                    motorista_id
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em andamento para pausar."}

                turno_id = str(turno["id"])

                # Validação de envelope: km de pausa deve estar dentro do intervalo do turno
                km_dec = None
                if km_pausa is not None:
                    km_dec = Decimal(str(km_pausa)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    km_inicial_turno = Decimal(str(turno["km_inicial"]))
                    if km_dec < km_inicial_turno:
                        return {
                            "sucesso": False,
                            "erro": (
                                f"⚠ Odômetro de pausa ({float(km_dec):,.0f} km) é menor que o "
                                f"km inicial do turno ({float(km_inicial_turno):,.0f} km). "
                                f"Verifique o valor e tente novamente."
                            ),
                            "tipo_erro": "KM_PAUSA_INVALIDO",
                        }

                await conn.execute("UPDATE public.turnos SET status = 'em_pausa' WHERE id = $1::uuid;", turno_id)
                await conn.execute(
                    "INSERT INTO public.pausas_turno (turno_id, motivo, inicio_pausa, km_inicio) "
                    "VALUES ($1::uuid, 'Pausa Operacional', $2, $3);",
                    turno_id, agora_brasil(), km_dec
                )
            return {"sucesso": True, "km_pausa_registrado": km_pausa is not None}
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    @staticmethod
    async def retomar_turno(motorista_id: str, km_retomada: Optional[float] = None) -> Dict[str, Any]:
        """Finaliza a pausa aberta.

        Se `km_retomada` for fornecido, registra a âncora de odômetro de fim de pausa,
        calcula o gap de uso pessoal intra-turno e debita do cofre virtual via Power Split.
        """
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                await conn.execute("SET LOCAL lock_timeout = '5s';")
                turno = await conn.fetchrow(
                    """SELECT id, status, km_inicial FROM public.turnos
                       WHERE motorista_id = $1::uuid AND status = 'em_pausa'
                       ORDER BY data_inicio DESC LIMIT 1
                       FOR UPDATE;""",
                    motorista_id
                )
                if not turno:
                    return {"sucesso": False, "erro": "❌ Não encontramos nenhuma jornada em pausa registrada no momento."}

                turno_id = str(turno["id"])

                # Validação de envelope: km de retomada deve estar dentro do intervalo do turno
                km_dec = None
                if km_retomada is not None:
                    km_dec = Decimal(str(km_retomada)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                    km_inicial_turno = Decimal(str(turno["km_inicial"]))
                    if km_dec < km_inicial_turno:
                        return {
                            "sucesso": False,
                            "erro": (
                                f"⚠ Odômetro de retomada ({float(km_dec):,.0f} km) é menor que o "
                                f"km inicial do turno ({float(km_inicial_turno):,.0f} km). "
                                f"Verifique o valor e tente novamente."
                            ),
                            "tipo_erro": "KM_RETOMADA_INVALIDO",
                        }

                await conn.execute("UPDATE public.turnos SET status = 'em_andamento' WHERE id = $1::uuid;", turno_id)
                await conn.execute(
                    "UPDATE public.pausas_turno SET fim_pausa = $1, km_fim = $2 "
                    "WHERE turno_id = $3::uuid AND fim_pausa IS NULL;",
                    agora_brasil(), km_dec, turno_id
                )

                custo_intra = Decimal("0.00")
                detalhe_intra = ""
                if km_retomada is not None:
                    # Verifica se a pausa recém-fechada tem âncora de início para calcular gap
                    pausa_row = await conn.fetchrow(
                        "SELECT km_inicio FROM public.pausas_turno "
                        "WHERE turno_id = $1::uuid AND fim_pausa IS NOT NULL AND km_fim IS NOT NULL "
                        "ORDER BY inicio_pausa DESC LIMIT 1;",
                        turno_id
                    )
                    if pausa_row and pausa_row["km_inicio"] is not None:
                        km_gap = km_dec - Decimal(str(pausa_row["km_inicio"]))
                        if km_gap > Decimal("0"):
                            veiculo_row = await conn.fetchrow(
                                "SELECT id, estoque_financeiro, tipo_combustivel FROM public.veiculos "
                                "WHERE motorista_id = $1::uuid AND ativo = TRUE ORDER BY created_at DESC LIMIT 1;",
                                motorista_id
                            )
                            if veiculo_row:
                                raw = veiculo_row["estoque_financeiro"]
                                estoque: dict = json.loads(raw) if isinstance(raw, str) else (raw or {})
                                estoque = TurnoService._garantir_estrutura_estoque(estoque)
                                meta = estoque["meta"]
                                is_hibrido = bool(meta.get("is_hibrido", False))
                                is_eletrico = bool(meta.get("is_eletrico", False))
                                tipo_comb = (veiculo_row["tipo_combustivel"] or meta.get("tipo_veiculo", "")).lower()
                                custo_intra, detalhe_intra = TurnoService._processar_uso_pessoal(
                                    km_gap, estoque, is_hibrido, is_eletrico, tipo_comb
                                )
                                await conn.execute(
                                    "UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE id = $2::uuid;",
                                    json.dumps(estoque), str(veiculo_row["id"])
                                )
                                logger.info(
                                    f"[retomar_turno] Uso pessoal intra-turno {float(km_gap):.1f} km → "
                                    f"R$ {float(custo_intra):.2f} | {detalhe_intra} (motorista={motorista_id})"
                                )

            return {
                "sucesso": True,
                "km_retomada_registrado": km_retomada is not None,
                "custo_uso_pessoal_intra": float(custo_intra),
                "detalhe_uso_pessoal": detalhe_intra,
            }
        except Exception as e:
            return {"sucesso": False, "erro": str(e)}

    @staticmethod
    async def verificar_transacoes_turno(motorista_id: str) -> int:
        """Conta receitas vinculadas ao turno ativo (Read-Only).

        A trava de faturamento zero deve disparar quando não há RECEITA registrada,
        independentemente de existirem despesas (ex: abastecimento sem corrida).
        Contar despesas como "evidência de faturamento" é semanticamente incorreto —
        o motorista pode ter abastecido e não trabalhou; o DRE resultaria em lucro
        negativo sem qualquer confirmação humana.

        Fail-Safe: retorna 0 em caso de erro para acionar a confirmação,
        nunca fechar silenciosamente (elimina a vulnerabilidade "Fail-Open").
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

                # Conta apenas RECEITAS vinculadas ao turno.
                # Despesas (abastecimento, alimentação, etc.) não são evidência de faturamento.
                row = await conn.fetchrow(
                    "SELECT COUNT(*) as total FROM public.transacoes "
                    "WHERE motorista_id = $1::uuid AND turno_id = $2::uuid "
                    "AND tipo_movimentacao = 'receita' AND estornado = FALSE;",
                    motorista_id, turno_id
                )
                return int(row["total"]) if row else 0
        except Exception as e:
            # Fail-Safe: retorna 0 para que o sistema acione a confirmação de faturamento
            # zerado em vez de fechar o turno sem validação humana.
            logger.error(f"[TurnoService] Erro crítico na trava de fechamento (motorista={motorista_id}): {e}")
            return 0
