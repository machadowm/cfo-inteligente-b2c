"""
ProfileService — Raio-X completo do motorista (comando 'perfil' / 'meus dados').

Diferente do 'status', que reflete apenas o turno corrente, o Raio-X consolida:
  - Configurações de metas e contrato vigentes
  - Turno em andamento agora (se houver): km, tempo, faturamento parcial
  - Estoque virtual de combustível/energia: CMP, proporção mix, autonomia estimada
  - Faturamento e despesas acumulados no mês calendário
  - Histórico de eficiência: médias, melhor/pior turno, tendência últimos 5 turnos
  - Projeção de faturamento até o fim do mês com indicador de ritmo vs meta
  - Alertas de piso (km e hora) nos últimos 5 turnos
  - Alertas de manutenção próximos (baseado em regras cadastradas)
  - Despesas com vencimento hoje/amanhã destacadas
  - Caixas com próximo vencimento e projeção de dias para completar

Opera totalmente fora da FSM de turnos — pode ser chamado a qualquer momento.
"""

import json
import logging
import calendar as _cal
from datetime import date as _date, timedelta as _timedelta
from decimal import Decimal, ROUND_HALF_UP

from services.database_service import DatabaseService
from services.turno_service import TurnoService

logger = logging.getLogger(__name__)

# ── Limiar mínimo de volume para exibir CMP confiável ─────────────────────────
# Abaixo disso o arredondamento acumulado distorce o custo unitário.
_LIMIAR_CMP_L   = Decimal("5.0")
_LIMIAR_CMP_KWH = Decimal("3.0")
_LIMIAR_CMP_M3  = Decimal("2.0")


def _fmt_brl(valor: float) -> str:
    """Formata float para padrão monetário BR: 1234.56 → R$ 1.234,56"""
    return f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _barra(pct: float, blocos: int = 10) -> str:
    """Barra ASCII proporcional. pct em 0–100."""
    cheios = min(blocos, int(min(pct, 100.0) / (100.0 / blocos)))
    return "█" * cheios + "░" * (blocos - cheios)


class ProfileService:
    """Gera o relatório de Raio-X completo do motorista em um único bloco de texto."""

    @staticmethod
    async def gerar_raiox_completo(motorista_id: str, nome_exibicao: str) -> str:
        """
        Consolida dados de múltiplas fontes e retorna a mensagem formatada pronta para envio.
        Todas as queries são executadas dentro de uma única conexão de tenant para minimizar
        round-trips e garantir consistência RLS.
        """
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:

                # ── 1. Metas e configurações do motorista ─────────────────────
                m = await conn.fetchrow(
                    """
                    SELECT meta_mensal_faturamento, dias_uteis_mes,
                           COALESCE(piso_ganho_km,   2.0)  AS piso_ganho_km,
                           COALESCE(piso_ganho_hora, 30.0) AS piso_ganho_hora
                    FROM public.motoristas
                    WHERE id = $1::uuid;
                    """,
                    motorista_id,
                )

                # ── 2. Veículo selecionado + estoque JSONB ────────────────────
                v = await conn.fetchrow(
                    """
                    SELECT modelo, placa, tipo_combustivel,
                           estoque_financeiro,
                           locadora, custo_aluguel_semanal, franquia_km_semanal,
                           valor_km_excedente,
                           COALESCE(dias_trabalho_semana, 6) AS dias_trabalho_semana,
                           is_hibrido, is_eletrico, id AS veiculo_id
                    FROM public.veiculos
                    WHERE motorista_id = $1::uuid AND ativo = TRUE
                    ORDER BY selecionado DESC, created_at DESC LIMIT 1;
                    """,
                    motorista_id,
                )
                veiculo_id = str(v["veiculo_id"]) if v else None

                # ── 3. Turno em andamento agora (se houver) ───────────────────
                turno_ativo = await conn.fetchrow(
                    """
                    SELECT t.id, t.km_inicial, t.data_inicio, t.status,
                           COALESCE(SUM(tx.valor) FILTER (WHERE tx.tipo_movimentacao = 'receita'), 0) AS fat_parcial,
                           COALESCE(SUM(tx.valor) FILTER (WHERE tx.tipo_movimentacao = 'despesa'), 0) AS desp_parcial,
                           COALESCE(SUM(tx.litros_abastecidos) FILTER (WHERE tx.categoria = 'combustivel'), 0) AS litros_abast_turno,
                           COUNT(tx.id) FILTER (WHERE tx.tipo_movimentacao = 'receita')               AS qtd_corridas
                    FROM public.turnos t
                    LEFT JOIN public.transacoes tx
                        ON tx.turno_id = t.id AND tx.estornado = FALSE
                    WHERE t.motorista_id = $1::uuid
                      AND t.status IN ('em_andamento', 'em_pausa', 'ABERTO')
                    GROUP BY t.id, t.km_inicial, t.data_inicio, t.status
                    ORDER BY t.data_inicio DESC LIMIT 1;
                    """,
                    motorista_id,
                )

                # ── 4. Acumulado do mês corrente — exclui turno aberto ────────
                fat_row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'), 0) AS fat_bruto,
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'), 0) AS desp_total
                    FROM public.transacoes t
                    WHERE t.motorista_id = $1::uuid
                      AND t.estornado = FALSE
                      AND date_trunc('month', t.data_transacao) = date_trunc('month', CURRENT_DATE)
                      AND (
                          t.turno_id IS NULL
                          OR t.turno_id NOT IN (
                              SELECT id FROM public.turnos
                              WHERE motorista_id = $1::uuid
                                AND status IN ('em_andamento', 'em_pausa', 'ABERTO')
                          )
                      );
                    """,
                    motorista_id,
                )

                # ── 5. Histórico: últimos 10 fechamentos do mês (fallback: geral) ──
                hist_mes = await conn.fetch(
                    """
                    SELECT faturamento_bruto, km_rodados, lucro_liquido_real,
                           custo_variavel_direto, custo_fixo_rateado, data_fechamento
                    FROM public.fechamento_diario
                    WHERE motorista_id = $1::uuid
                      AND date_trunc('month', data_fechamento AT TIME ZONE 'America/Sao_Paulo')
                          = date_trunc('month', CURRENT_DATE)
                    ORDER BY data_fechamento DESC
                    LIMIT 10;
                    """,
                    motorista_id,
                )
                _hist_is_mes = len(hist_mes) >= 3
                if not _hist_is_mes:
                    hist_mes = await conn.fetch(
                        """
                        SELECT faturamento_bruto, km_rodados, lucro_liquido_real,
                               custo_variavel_direto, custo_fixo_rateado, data_fechamento
                        FROM public.fechamento_diario
                        WHERE motorista_id = $1::uuid
                        ORDER BY data_fechamento DESC
                        LIMIT 10;
                        """,
                        motorista_id,
                    )

                # ── 6. Alertas de manutenção: regras ativas do veículo ────────
                alertas_manut = []
                if veiculo_id:
                    # Lê km do último turno concluído para calcular proximidade
                    km_atual_row = await conn.fetchrow(
                        """
                        SELECT km_final FROM public.turnos
                        WHERE veiculo_id = $1::uuid AND status = 'concluido'
                          AND km_final IS NOT NULL
                        ORDER BY data_fim DESC LIMIT 1;
                        """,
                        veiculo_id,
                    )
                    km_atual_veiculo = Decimal(str(km_atual_row["km_final"])) if km_atual_row else None

                    if km_atual_veiculo:
                        regras_manut = await conn.fetch(
                            """
                            SELECT rm.tipo_servico, rm.intervalo_km, rm.aviso_previo_km,
                                   COALESCE(MAX(hm.km_execucao), 0) AS ultimo_km
                            FROM public.regras_manutencao rm
                            LEFT JOIN public.historico_manutencao hm ON hm.regra_id = rm.id
                            WHERE rm.veiculo_id = $1::uuid AND rm.ativo = TRUE
                            GROUP BY rm.id, rm.tipo_servico, rm.intervalo_km, rm.aviso_previo_km
                            ORDER BY rm.tipo_servico;
                            """,
                            veiculo_id,
                        )
                        for r in regras_manut:
                            ultimo = Decimal(str(r["ultimo_km"]))
                            proximo = ultimo + Decimal(str(r["intervalo_km"]))
                            restante = proximo - km_atual_veiculo
                            aviso = Decimal(str(r["aviso_previo_km"]))
                            if restante <= aviso:
                                if restante <= 0:
                                    alertas_manut.append(
                                        f"🔴 *{r['tipo_servico']}* — VENCIDA!  "
                                        f"({abs(int(restante))} km atrasada)"
                                    )
                                else:
                                    alertas_manut.append(
                                        f"🟡 *{r['tipo_servico']}* — faltam  *{int(restante)} km*"
                                    )

                # ── 7. Despesas fixas ─────────────────────────────────────────
                despesas_fixas = await conn.fetch(
                    """
                    SELECT nome, valor_mensal, valor_pro_rata_diario, dias_vencimento
                    FROM public.despesas_fixas_mensais
                    WHERE motorista_id = $1::uuid AND ativo = TRUE
                    ORDER BY valor_mensal DESC;
                    """,
                    motorista_id,
                )

                # ── 8. Caixas de provisão ─────────────────────────────────────
                caixas = await conn.fetch(
                    """
                    SELECT cp.nome_caixa, cp.saldo_atual, cp.meta_valor,
                           COALESCE(SUM(dfm.valor_pro_rata_diario), 0) AS aporte_diario
                    FROM public.caixas_provisao cp
                    LEFT JOIN public.despesas_fixas_mensais dfm
                        ON dfm.caixa_id = cp.id AND dfm.ativo = TRUE
                    WHERE cp.motorista_id = $1::uuid
                    GROUP BY cp.id, cp.nome_caixa, cp.saldo_atual, cp.meta_valor
                    ORDER BY cp.nome_caixa;
                    """,
                    motorista_id,
                )

            # =================================================================
            # PROCESSAMENTO — tudo abaixo é Python puro, sem acesso ao banco
            # =================================================================

            hoje      = _date.today()
            hoje_dia  = hoje.day
            amanha_dia = (hoje + _timedelta(days=1)).day

            # ── Metas e configurações ──────────────────────────────────────────
            meta_mensal  = Decimal(str(m["meta_mensal_faturamento"] or "12000.00"))
            dias_uteis   = int(m["dias_uteis_mes"] or 26)
            piso_km      = float(m["piso_ganho_km"])
            piso_hora    = float(m["piso_ganho_hora"])
            meta_diaria  = (meta_mensal / Decimal(str(dias_uteis))).quantize(Decimal("0.01"))

            # ── Contrato do veículo ────────────────────────────────────────────
            aluguel_sem      = float(v["custo_aluguel_semanal"] or 1020.85) if v else 1020.85
            _dias_sem        = int(v["dias_trabalho_semana"] or 6) if v else 6
            aluguel_dia      = aluguel_sem / _dias_sem
            _locadora        = (v["locadora"] or "").lower() if v else ""
            _is_proprio      = _locadora in ("proprietario", "quitado", "financiado")
            franquia_sem     = float(v["franquia_km_semanal"] or 0.0) if v and not _is_proprio else 0.0
            km_excedente     = float(v["valor_km_excedente"] or 0.0) if v else 0.0

            # ── Acumulado do mês ──────────────────────────────────────────────
            fat_bruto     = Decimal(str(fat_row["fat_bruto"]  or 0))
            desp_total    = Decimal(str(fat_row["desp_total"] or 0))
            lucro_parcial = fat_bruto - desp_total
            progresso_pct = float(fat_bruto / meta_mensal * 100) if meta_mensal > 0 else 0.0

            # ── Projeção mensal ───────────────────────────────────────────────
            _dias_no_mes         = _cal.monthrange(hoje.year, hoje.month)[1]
            dias_uteis_restantes = max(0, round(dias_uteis * (1 - hoje.day / _dias_no_mes)))

            # Médias do histórico
            if hist_mes:
                fats  = [float(r["faturamento_bruto"] or 0) for r in hist_mes]
                kms   = [float(r["km_rodados"] or 0) for r in hist_mes if r["km_rodados"] > 0]
                lucros = [float(r["lucro_liquido_real"] or 0) for r in hist_mes]
                custos = [float((r["custo_variavel_direto"] or 0) + (r["custo_fixo_rateado"] or 0)) for r in hist_mes]

                media_fat_dia   = sum(fats)   / len(fats)
                media_km_dia    = sum(kms)    / len(kms)   if kms   else 0.0
                media_lucro_dia = sum(lucros) / len(lucros)
                media_custo_dia = sum(custos) / len(custos)
                qtd_turnos      = len(hist_mes)

                # Melhor e pior turno do histórico
                melhor_fat = max(fats)
                pior_fat   = min(fats)

                # Tendência: compara média dos últimos 3 vs 3 anteriores (quando há ≥6)
                tendencia_str = ""
                if len(fats) >= 6:
                    recentes   = sum(fats[:3]) / 3
                    anteriores = sum(fats[3:6]) / 3
                    diff_pct   = (recentes - anteriores) / anteriores * 100 if anteriores > 0 else 0
                    if diff_pct >= 5:
                        tendencia_str = f"📈 _Tendência: +{diff_pct:.0f}% vs período anterior_"
                    elif diff_pct <= -5:
                        tendencia_str = f"📉 _Tendência: {diff_pct:.0f}% vs período anterior_"
                    else:
                        tendencia_str = f"➡️ _Tendência: estável_"

                # Alertas de piso (últimos 5 turnos)
                ultimos5 = hist_mes[:5]
                abaixo_piso_fat = sum(1 for r in ultimos5 if float(r["faturamento_bruto"] or 0) < float(meta_diaria))
                # piso por km: media_km_dia vs piso_km × horas estimadas (usa proxy: fat / receita por hora estimada)
                # usamos apenas o faturamento bruto vs meta diária como proxy de piso
            else:
                media_fat_dia = media_km_dia = media_lucro_dia = media_custo_dia = 0.0
                qtd_turnos    = 0
                melhor_fat = pior_fat = 0.0
                tendencia_str = ""
                abaixo_piso_fat = 0

            margem_media = (media_lucro_dia / media_fat_dia * 100.0) if media_fat_dia > 0 else 0.0

            projecao_mensal    = float(fat_bruto) + (media_fat_dia * dias_uteis_restantes) if media_fat_dia > 0 and dias_uteis_restantes > 0 else float(fat_bruto)
            deficit_meta       = max(0.0, float(meta_mensal) - float(fat_bruto))
            fat_necessario_dia = deficit_meta / dias_uteis_restantes if dias_uteis_restantes > 0 else 0.0

            # ── Estoque virtual ────────────────────────────────────────────────
            estoque_raw = v["estoque_financeiro"] if v else {}
            if isinstance(estoque_raw, str):
                estoque_raw = json.loads(estoque_raw)
            estoque = TurnoService._garantir_estrutura_estoque(estoque_raw or {})
            meta_e  = estoque.get("meta", {})

            liq   = estoque.get("liquido", {})
            ele   = estoque.get("eletricidade", {})
            gnv_e = estoque.get("gnv", {})

            litros = Decimal(str(liq.get("litros", 0.0)))
            kwh    = Decimal(str(ele.get("kwh", 0.0)))
            m3     = Decimal(str(gnv_e.get("m3", 0.0)))

            linhas_estoque = []
            if litros > 0:
                custo_liq  = Decimal(str(liq.get("custo_total", 0.0)))
                km_l_gas   = Decimal(str(liq.get("km_l_gasolina", 12.0)))
                km_l_eta   = Decimal(str(liq.get("km_l_etanol",   8.5)))
                p_gas      = Decimal(str(liq.get("gasolina_proporcao", 1.0)))
                p_eta      = Decimal(str(liq.get("etanol_proporcao",  0.0)))
                km_l_med   = (p_gas * km_l_gas + p_eta * km_l_eta) or Decimal("10.0")
                autonomia  = (litros * km_l_med).quantize(Decimal("1"), rounding=ROUND_HALF_UP)

                # Mix de combustível (só exibe se flex, ou seja, proporção mista relevante)
                mix_str = ""
                if p_gas > Decimal("0.05") and p_eta > Decimal("0.05"):
                    mix_str = f"  ·  mix {float(p_gas * 100):.0f}% gas / {float(p_eta * 100):.0f}% eta"

                if custo_liq > 0:
                    cmp = custo_liq / litros
                    if litros >= _LIMIAR_CMP_L:
                        cmp_str = f"  ·  CMP  *R$ {float(cmp):.3f}/L*  ·  total R$ {float(custo_liq):.2f}"
                    else:
                        cmp_str = f"  ·  R$ {float(custo_liq):.2f} total — _reabasteça em breve_"
                else:
                    cmp_str = ""

                linhas_estoque.append(
                    f"⛽  *{float(litros):.1f} L*{mix_str}{cmp_str}\n"
                    f"   _Autonomia estimada: ~{int(autonomia)} km_"
                )

            if kwh > 0:
                custo_ele = Decimal(str(ele.get("custo_total", 0.0)))
                km_kwh    = Decimal(str(ele.get("km_kwh", 6.5)))
                autonomia_e = (kwh * km_kwh).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if custo_ele > 0:
                    cmp_str = (
                        f"  ·  CMP  *R$ {float(custo_ele / kwh):.3f}/kWh*"
                        if kwh >= _LIMIAR_CMP_KWH else f"  ·  R$ {float(custo_ele):.2f} total"
                    )
                else:
                    cmp_str = "  ·  _solar/gratuito_"
                linhas_estoque.append(
                    f"🔋  *{float(kwh):.1f} kWh*{cmp_str}\n"
                    f"   _Autonomia estimada: ~{int(autonomia_e)} km_"
                )

            if m3 > 0:
                custo_gnv = Decimal(str(gnv_e.get("custo_total", 0.0)))
                km_m3     = Decimal(str(gnv_e.get("km_m3", 14.0)))
                autonomia_g = (m3 * km_m3).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
                if custo_gnv > 0:
                    cmp_str = (
                        f"  ·  CMP  *R$ {float(custo_gnv / m3):.3f}/m³*"
                        if m3 >= _LIMIAR_CMP_M3 else f"  ·  R$ {float(custo_gnv):.2f} total"
                    )
                else:
                    cmp_str = ""
                linhas_estoque.append(
                    f"🟦  *{float(m3):.1f} m³*  GNV{cmp_str}\n"
                    f"   _Autonomia estimada: ~{int(autonomia_g)} km_"
                )

            estoque_str = (
                "\n".join(f"• {l}" for l in linhas_estoque)
                if linhas_estoque
                else "• Estoque zerado — abasteça para ativar o rastreio de CMP."
            )

            # ── Turno ativo agora ──────────────────────────────────────────────
            secao_turno_ativo = ""
            if turno_ativo:
                from datetime import datetime as _dt
                import pytz as _pytz
                _tz_br = _pytz.timezone("America/Sao_Paulo")
                inicio  = turno_ativo["data_inicio"]
                if hasattr(inicio, "astimezone"):
                    inicio = inicio.astimezone(_tz_br)
                agora_br = _dt.now(_tz_br)
                minutos_turno = int((agora_br - inicio).total_seconds() / 60)
                h_t = minutos_turno // 60
                m_t = minutos_turno % 60
                tempo_str = f"{h_t}h{m_t:02d}min" if h_t else f"{m_t}min"

                fat_p   = float(turno_ativo["fat_parcial"]  or 0)
                desp_p  = float(turno_ativo["desp_parcial"] or 0)
                lucro_p = fat_p - desp_p
                corridas = int(turno_ativo["qtd_corridas"] or 0)
                status_emoji = "⏸" if turno_ativo["status"] == "em_pausa" else "🟢"

                # Ritmo: faturamento por hora no turno atual vs meta diária
                fat_por_hora = (fat_p / (minutos_turno / 60)) if minutos_turno > 30 else 0
                meta_hora_equiv = float(meta_diaria) / 8  # meta horária estimada (8h)
                ritmo_emoji = "🔥" if fat_por_hora >= meta_hora_equiv else ("⚠️" if fat_por_hora > 0 else "")

                secao_turno_ativo = (
                    f"{status_emoji}  *TURNO EM ANDAMENTO* \n"
                    f"• Início:  *{inicio.strftime('%H:%M')}*  ·  Duração: *{tempo_str}*\n"
                    f"• Corridas:  *{corridas}*  ·  Faturado:  *{_fmt_brl(fat_p)}* {ritmo_emoji}\n"
                    f"• Custos no turno:  *{_fmt_brl(desp_p)}*  ·  Lucro parcial:  *{_fmt_brl(lucro_p)}*\n"
                    + (f"• Ritmo:  *{_fmt_brl(fat_por_hora)}/h*  (meta ≈ {_fmt_brl(meta_hora_equiv)}/h)\n" if fat_por_hora > 0 else "")
                    + f"\n"
                )

            # ── Seção desempenho mensal ────────────────────────────────────────
            _barra_progresso = _barra(progresso_pct)

            if dias_uteis_restantes > 0 and float(fat_bruto) > 0:
                if fat_necessario_dia <= 0:
                    linha_projecao = f"• 🎯 *Meta mensal atingida!* Parabéns!\n"
                else:
                    linha_projecao = (
                        f"• Projeção (ritmo atual):  *{_fmt_brl(projecao_mensal)}*\n"
                        f"• Precisa por dia para a meta:  *{_fmt_brl(fat_necessario_dia)}*\n"
                    )
            else:
                linha_projecao = ""

            # Alerta de ritmo baseado nos últimos 3 fechamentos
            alerta_ritmo = ""
            if hist_mes:
                ultimos3 = hist_mes[:3]
                n_abaixo = sum(1 for r in ultimos3 if Decimal(str(r["faturamento_bruto"] or 0)) < meta_diaria)
                if n_abaixo == 3:
                    alerta_ritmo = (
                        f"\n⚠️  *Atenção ao Ritmo!*  Últimos 3 turnos abaixo da meta diária "
                        f"de  *{_fmt_brl(float(meta_diaria))}* . Avalie horário ou plataforma."
                    )
                elif n_abaixo == 2:
                    alerta_ritmo = (
                        f"\n📉  _2 dos últimos 3 turnos abaixo da meta diária ({_fmt_brl(float(meta_diaria))})._"
                    )

            # ── Seção histórico ────────────────────────────────────────────────
            if qtd_turnos > 0:
                historico_str = (
                    f"• {qtd_turnos} fechamentos analisados\n"
                    f"• KM médio/dia:  *{media_km_dia:.1f} km*\n"
                    f"• Faturamento médio/dia:  *{_fmt_brl(media_fat_dia)}*\n"
                    f"• Custo médio/dia:  *{_fmt_brl(media_custo_dia)}*\n"
                    f"• Lucro médio/dia:  *{_fmt_brl(media_lucro_dia)}*  (margem *{margem_media:.1f}%*)\n"
                    + (f"• Melhor turno:  *{_fmt_brl(melhor_fat)}*  ·  Pior:  *{_fmt_brl(pior_fat)}*\n" if qtd_turnos >= 2 else "")
                    + (f"• {tendencia_str}\n" if tendencia_str else "")
                )
            else:
                historico_str = "• Nenhum fechamento registrado ainda."

            # ── Seção manutenções ──────────────────────────────────────────────
            secao_manut = ""
            if alertas_manut:
                secao_manut = (
                    f"🔧  *ALERTAS DE MANUTENÇÃO* \n"
                    + "\n".join(f"• {a}" for a in alertas_manut)
                    + "\n\n"
                )

            # ── Seção despesas fixas ───────────────────────────────────────────
            if despesas_fixas:
                total_df = sum(float(r["valor_pro_rata_diario"]) for r in despesas_fixas)
                linhas_df = []
                for r in despesas_fixas:
                    dias_venc = list(r["dias_vencimento"] or [1])
                    qtd_v     = len(dias_venc)
                    mensal    = float(r["valor_mensal"])
                    diario    = float(r["valor_pro_rata_diario"])

                    # Alerta de vencimento próximo
                    tag_venc = ""
                    if hoje_dia in dias_venc:
                        tag_venc = "  🚨 *VENCE HOJE*"
                    elif amanha_dia in dias_venc:
                        tag_venc = "  ⏰ _vence amanhã_"

                    if qtd_v == 1:
                        venc_str = f"📅 dia *{dias_venc[0]}*"
                    elif qtd_v <= 6:
                        venc_str = "📅 dias " + " e ".join(f"*{d}*" for d in dias_venc)
                    else:
                        parcela = mensal / qtd_v
                        venc_str = f"📅 {qtd_v}× (dias *{dias_venc[0]}*–*{dias_venc[-1]}* · ≈ {_fmt_brl(parcela)}/parcela)"

                    linhas_df.append(
                        f"• {r['nome']}:  *{_fmt_brl(mensal)}/mês*"
                        f"  (≈ {_fmt_brl(diario)}/dia)  {venc_str}{tag_venc}"
                    )
                linhas_df.append(f"\n• *Total pro-rata diário: {_fmt_brl(total_df)}*")
                despesas_fixas_str = "\n".join(linhas_df)
            else:
                despesas_fixas_str = (
                    "• Nenhuma despesa fixa cadastrada.\n"
                    "_Use  *!adicionar despesa seguro 180 26*_"
                )

            # ── Seção caixas de provisão ──────────────────────────────────────
            if caixas:
                total_saldo = sum(float(r["saldo_atual"]) for r in caixas)
                linhas_cx   = []
                for r in caixas:
                    saldo_cx = float(r["saldo_atual"])
                    meta_cx  = float(r["meta_valor"]) if r["meta_valor"] is not None else None
                    aporte   = float(r["aporte_diario"])

                    if meta_cx is not None:
                        pct      = min(100.0, saldo_cx / meta_cx * 100)
                        barra_cx = _barra(pct)
                        if saldo_cx >= meta_cx:
                            linhas_cx.append(
                                f"• {r['nome_caixa']}:  ✅  *{_fmt_brl(saldo_cx)} / {_fmt_brl(meta_cx)}* — Meta atingida!\n"
                                f"  [{barra_cx}]"
                            )
                        else:
                            falta     = meta_cx - saldo_cx
                            dias_meta = f"  ·  ~{falta / aporte:.0f} turnos" if aporte > 0 else ""
                            linhas_cx.append(
                                f"• {r['nome_caixa']}:  *{_fmt_brl(saldo_cx)} / {_fmt_brl(meta_cx)}*  ({pct:.0f}%)\n"
                                f"  [{barra_cx}]  _Faltam {_fmt_brl(falta)}{dias_meta}_"
                            )
                    else:
                        aporte_str = f"  _(+{_fmt_brl(aporte)}/turno)_" if aporte > 0 else ""
                        linhas_cx.append(f"• {r['nome_caixa']}:  *{_fmt_brl(saldo_cx)}*{aporte_str}")

                linhas_cx.append(f"\n*Total reservado: {_fmt_brl(total_saldo)}*")
                caixas_str = "\n".join(linhas_cx)
            else:
                caixas_str = (
                    "• Nenhuma caixa criada ainda.\n"
                    "_Use  *!criar caixa pneu 500*  para criar com meta._"
                )

            # ── Linha de contrato do veículo ──────────────────────────────────
            if _is_proprio:
                _linha_contrato = f"• Custo (pro-rata diário):  *{_fmt_brl(aluguel_dia)}*\n\n"
            else:
                franquia_dia = franquia_sem / 7.0
                _linha_contrato = (
                    f"• Aluguel:  *{_fmt_brl(aluguel_sem)}/sem*  (≈ *{_fmt_brl(aluguel_dia)}/dia* · {_dias_sem}d/sem)\n"
                    f"• Franquia:  *{franquia_sem:.0f} km/sem*  (≈ *{franquia_dia:.0f} km/dia*)"
                    + (f"  ·  excedente *{_fmt_brl(km_excedente)}/km*" if km_excedente > 0 else "")
                    + "\n\n"
                )

            # ── Montagem final ─────────────────────────────────────────────────
            return (
                f"👤  *PARCEIRO DO PAINEL — {nome_exibicao.upper()}*  🛡\n"
                f"──────────────────────────────\n\n"
                + secao_turno_ativo
                + f"🚗  *VEÍCULO ATIVO* \n"
                f"• Modelo:  *{v['modelo']}*  ({v['placa']})\n"
                f"• Contrato:  *{v['locadora'] or 'Não configurado'}*\n"
                + _linha_contrato
                + secao_manut
                + f"⛽  *ESTOQUE NO COFRE* \n"
                f"{estoque_str}\n\n"
                f"📊  *DESEMPENHO DO MÊS ATUAL* \n"
                f"• Receitas:  *{_fmt_brl(float(fat_bruto))}*\n"
                f"• Despesas:  *{_fmt_brl(float(desp_total))}*\n"
                f"• Lucro Real Acumulado:  *{_fmt_brl(float(lucro_parcial))}*\n"
                f"• Meta Mensal:  *{_fmt_brl(float(meta_mensal))}*\n"
                f"• Progresso: [{_barra_progresso}]  *{progresso_pct:.1f}%*\n"
                + (f"• Dias Úteis Restantes (est.):  *{dias_uteis_restantes} dias*\n" if dias_uteis_restantes > 0 else "")
                + linha_projecao
                + alerta_ritmo
                + f"\n\n🎯  *METAS E PISOS* \n"
                f"• Meta Diária:  *{_fmt_brl(float(meta_diaria))}*  ({dias_uteis} dias úteis/mês)\n"
                f"• Piso por KM:  *{_fmt_brl(piso_km)}/km*   Piso por Hora:  *{_fmt_brl(piso_hora)}/h*\n\n"
                f"📌  *DESPESAS FIXAS MENSAIS* \n"
                f"{despesas_fixas_str}\n\n"
                f"📦  *CAIXAS DE PROVISÃO* \n"
                f"{caixas_str}\n\n"
                f"📈  *HISTÓRICO RECENTE*  "
                f"({'mês atual — ' if _hist_is_mes else 'histórico geral — '}"
                f"{qtd_turnos if qtd_turnos else '—'} turnos)\n"
                f"{historico_str}\n"
                f"\n💡  _Quer ajustar algo?_\n"
                f"  *!alterar meta mensal 12000*   *!alterar piso km 2,50*\n"
                f"  *!adicionar despesa seguro 180 26*   *!caixas*   *!veiculos*"
            )

        except Exception as exc:
            logger.error(f"[ProfileService] Erro ao gerar Raio-X (motorista={motorista_id}): {exc}")
            return "❌ Não consegui carregar seus dados agora. Tente novamente em instantes."
