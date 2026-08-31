"""
ProfileService — Raio-X completo do motorista (comando 'perfil' / 'meus dados').

Diferente do 'status', que reflete apenas o turno corrente, o Raio-X consolida:
  - Configurações de metas e contrato vigentes
  - Estoque virtual de combustível/energia no cofre
  - Faturamento e despesas acumulados no mês calendário
  - Histórico de eficiência (km/dia, faturamento/dia, lucro médio) dos últimos fechamentos
  - Projeção de faturamento até o fim do mês com indicador de ritmo vs meta

Melhorias v9 (alinhado ao DRE v9):
  - Histórico: inclui lucro médio/dia, custo médio/dia, margem média (antes só km e fat)
  - Desempenho mensal: exibe lucro real acumulado (fat - desp), progresso visual com barra ASCII
  - Projeção mensal: ritmo atual vs meta, faturamento necessário/dia para fechar a meta
  - Seção de estoque: exibe custo médio ponderado (R$/L ou R$/kWh) quando saldo > 0
  - Alerta de ritmo: avisa quando o motorista está abaixo da meta diária nos últimos 3 turnos

Opera totalmente fora da FSM de turnos — pode ser chamado a qualquer momento.
"""

import json
import logging
from datetime import date as _date
from decimal import Decimal

from services.database_service import DatabaseService
from services.turno_service import TurnoService

logger = logging.getLogger(__name__)


class ProfileService:
    """Gera o relatório de Raio-X completo do motorista em um único bloco de texto."""

    @staticmethod
    async def gerar_raiox_completo(motorista_id: str, nome_exibicao: str) -> str:
        """
        Consolida dados de quatro fontes (motoristas, veiculos, transacoes,
        fechamento_diario) e retorna a mensagem formatada pronta para envio.

        Parâmetros:
            motorista_id   — UUID do motorista (chave RLS).
            nome_exibicao  — Nome já resolvido pelo orquestrador (nome_social ?? nome).
        """
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:

                # ── 1. Metas do motorista ──────────────────────────────────────
                m = await conn.fetchrow(
                    """
                    SELECT meta_mensal_faturamento, dias_uteis_mes,
                           COALESCE(piso_ganho_km, 2.0)   AS piso_ganho_km,
                           COALESCE(piso_ganho_hora, 30.0) AS piso_ganho_hora
                    FROM public.motoristas
                    WHERE id = $1::uuid;
                    """,
                    motorista_id,
                )

                # ── 2. Veículo ativo + estoque JSONB ──────────────────────────
                v = await conn.fetchrow(
                    """
                    SELECT modelo, placa, tipo_combustivel,
                           estoque_financeiro,
                           locadora, custo_aluguel_semanal, franquia_km_semanal,
                           is_hibrido, is_eletrico
                    FROM public.veiculos
                    WHERE motorista_id = $1::uuid AND ativo = TRUE
                    ORDER BY created_at DESC LIMIT 1;
                    """,
                    motorista_id,
                )

                # ── 3. Acumulado do mês corrente (receitas e despesas) ─────────
                fat_row = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'receita'), 0) AS fat_bruto,
                        COALESCE(SUM(valor) FILTER (WHERE tipo_movimentacao = 'despesa'), 0) AS desp_total
                    FROM public.transacoes
                    WHERE motorista_id = $1::uuid
                      AND estornado = FALSE
                      AND date_trunc('month', data_transacao) = date_trunc('month', CURRENT_DATE);
                    """,
                    motorista_id,
                )

                # ── 4. Histórico dos últimos 10 fechamentos ────────────────────
                # Inclui lucro e custo para calcular indicadores de eficiência real.
                hist = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(AVG(NULLIF(km_rodados, 0)), 0)          AS media_km,
                        COALESCE(AVG(NULLIF(faturamento_bruto, 0)), 0)   AS media_fat,
                        COALESCE(AVG(lucro_liquido_real), 0)             AS media_lucro,
                        COALESCE(AVG(custo_variavel_direto
                                     + custo_fixo_rateado), 0)           AS media_custo_total,
                        COUNT(*)                                          AS qtd_turnos
                    FROM (
                        SELECT km_rodados, faturamento_bruto,
                               lucro_liquido_real,
                               custo_variavel_direto, custo_fixo_rateado
                        FROM public.fechamento_diario
                        WHERE motorista_id = $1::uuid
                        ORDER BY data_fechamento DESC
                        LIMIT 10
                    ) sub;
                    """,
                    motorista_id,
                )

                # ── 5. Últimos 3 fechamentos para alerta de ritmo ──────────────
                ultimos3 = await conn.fetch(
                    """
                    SELECT faturamento_bruto
                    FROM public.fechamento_diario
                    WHERE motorista_id = $1::uuid
                    ORDER BY data_fechamento DESC
                    LIMIT 3;
                    """,
                    motorista_id,
                )

                # ── 6. Despesas fixas mensais ativas ──────────────────────────
                despesas_fixas = await conn.fetch(
                    """
                    SELECT nome, valor_mensal, valor_pro_rata_diario
                    FROM public.despesas_fixas_mensais
                    WHERE motorista_id = $1::uuid AND ativo = TRUE
                    ORDER BY valor_mensal DESC;
                    """,
                    motorista_id,
                )

                # ── 7. Caixas de provisão ─────────────────────────────────────
                caixas = await conn.fetch(
                    """
                    SELECT cp.nome_caixa, cp.saldo_atual,
                           COALESCE(SUM(dfm.valor_pro_rata_diario), 0) AS aporte_diario
                    FROM public.caixas_provisao cp
                    LEFT JOIN public.despesas_fixas_mensais dfm
                        ON dfm.caixa_id = cp.id AND dfm.ativo = TRUE
                    WHERE cp.motorista_id = $1::uuid
                    GROUP BY cp.id, cp.nome_caixa, cp.saldo_atual
                    ORDER BY cp.saldo_atual DESC;
                    """,
                    motorista_id,
                )

            # ── Extração e normalização do estoque JSONB ──────────────────────
            estoque_raw = v["estoque_financeiro"] if v else {}
            if isinstance(estoque_raw, str):
                estoque_raw = json.loads(estoque_raw)
            estoque = TurnoService._garantir_estrutura_estoque(estoque_raw or {})

            liq   = estoque.get("liquido", {})
            ele   = estoque.get("eletricidade", {})
            gnv_e = estoque.get("gnv", {})

            litros = Decimal(str(liq.get("litros", 0.0)))
            kwh    = Decimal(str(ele.get("kwh", 0.0)))
            m3     = Decimal(str(gnv_e.get("m3", 0.0)))

            # ── Cálculos financeiros ───────────────────────────────────────────
            meta_mensal   = Decimal(str(m["meta_mensal_faturamento"] or "12000.00"))
            dias_uteis    = int(m["dias_uteis_mes"] or 26)
            piso_km       = float(m["piso_ganho_km"])
            piso_hora     = float(m["piso_ganho_hora"])
            meta_diaria   = (meta_mensal / Decimal(str(dias_uteis))).quantize(Decimal("0.01"))

            fat_bruto     = Decimal(str(fat_row["fat_bruto"]  or 0))
            desp_total    = Decimal(str(fat_row["desp_total"] or 0))
            lucro_parcial = fat_bruto - desp_total
            progresso     = float(fat_bruto / meta_mensal * 100) if meta_mensal > 0 else 0.0

            aluguel_sem  = float(v["custo_aluguel_semanal"] or 1020.85)
            aluguel_dia  = aluguel_sem / 6.0
            franquia_sem = float(v["franquia_km_semanal"] or 1505.0)

            media_km_dia    = float(hist["media_km"]         or 0)
            media_fat_dia   = float(hist["media_fat"]        or 0)
            media_lucro_dia = float(hist["media_lucro"]      or 0)
            media_custo_dia = float(hist["media_custo_total"] or 0)
            qtd_turnos      = int(hist["qtd_turnos"]         or 0)
            margem_media    = (media_lucro_dia / media_fat_dia * 100.0) if media_fat_dia > 0 else 0.0

            # ── Barra de progresso ASCII (10 blocos) ──────────────────────────
            blocos_cheios = min(10, int(progresso / 10))
            barra = "█" * blocos_cheios + "░" * (10 - blocos_cheios)

            # ── Projeção mensal ────────────────────────────────────────────────
            hoje = _date.today()
            dias_corridos        = hoje.day
            dias_uteis_restantes = max(0, round(dias_uteis * (1 - dias_corridos / 30)))
            projecao_mensal      = float(fat_bruto) + (media_fat_dia * dias_uteis_restantes) if media_fat_dia > 0 and dias_uteis_restantes > 0 else float(fat_bruto)
            deficit_meta         = max(0.0, float(meta_mensal) - float(fat_bruto))
            fat_necessario_dia   = (deficit_meta / dias_uteis_restantes) if dias_uteis_restantes > 0 else 0.0

            if dias_uteis_restantes > 0 and float(fat_bruto) > 0:
                if fat_necessario_dia <= 0:
                    linha_projecao = f"• 🎯 Meta mensal já atingida! Parabéns!\n"
                else:
                    linha_projecao = (
                        f"• Projeção (mantendo ritmo):  *R$ {projecao_mensal:,.2f}* \n".replace(",", ".") +
                        f"• Precisa faturar/dia para a meta:  *R$ {fat_necessario_dia:.2f}* \n"
                    )
            else:
                linha_projecao = ""

            # ── Alerta de ritmo (últimos 3 turnos vs meta diária) ─────────────
            alerta_ritmo = ""
            if ultimos3 and meta_diaria > 0:
                abaixo = sum(
                    1 for r in ultimos3
                    if Decimal(str(r["faturamento_bruto"] or 0)) < meta_diaria
                )
                if abaixo == 3:
                    alerta_ritmo = (
                        f"\n⚠  *Atenção ao Ritmo!*  Seus últimos 3 turnos ficaram abaixo da meta diária "
                        f"de  *R$ {meta_diaria:.2f}* . Avalie se o horário ou a plataforma está rendendo."
                    )
                elif abaixo == 2:
                    alerta_ritmo = (
                        f"\n📉  _2 dos últimos 3 turnos abaixo da meta diária (R$ {meta_diaria:.2f}). "
                        f"Fique de olho no ritmo!_"
                    )

            # ── Seção de estoque (custo médio ponderado quando disponível) ─────
            # Limiar mínimo para exibir CMP: abaixo disso o arredondamento acumulado
            # de múltiplas queimas distorce o custo residual e o CMP fica enganoso.
            # Exemplo: 1.4 L restante com custo_total = R$ 2.19 → CMP = R$ 1.56/L
            # (matematicamente correto, mas parece errado para o usuário).
            # Abaixo do limiar mostramos só o total em R$ sem o CMP por unidade.
            _LIMIAR_CMP_L   = Decimal("5.0")   # litros mínimos para CMP confiável
            _LIMIAR_CMP_KWH = Decimal("3.0")   # kWh mínimos para CMP confiável
            _LIMIAR_CMP_M3  = Decimal("2.0")   # m³ mínimos para CMP confiável

            linhas_estoque = []
            if litros > 0:
                custo_liq = Decimal(str(liq.get("custo_total", 0.0)))
                if custo_liq > 0:
                    if litros >= _LIMIAR_CMP_L:
                        cmp_str = f" (R$ {float(custo_liq):.2f} total · ≈ R$ {float(custo_liq / litros):.3f}/L)"
                    else:
                        # Saldo residual: mostra só o total, CMP não é confiável
                        cmp_str = f" (R$ {float(custo_liq):.2f} total — _reabasteça em breve_)"
                else:
                    cmp_str = ""
                linhas_estoque.append(f"⛽  *{litros:.1f} L*  de combustível{cmp_str}")
            if kwh > 0:
                custo_ele = Decimal(str(ele.get("custo_total", 0.0)))
                if custo_ele > 0:
                    if kwh >= _LIMIAR_CMP_KWH:
                        cmp_str = f" (R$ {float(custo_ele):.2f} total · ≈ R$ {float(custo_ele / kwh):.3f}/kWh)"
                    else:
                        cmp_str = f" (R$ {float(custo_ele):.2f} total — _recarregue em breve_)"
                else:
                    cmp_str = " (solar/gratuito)"
                linhas_estoque.append(f"🔋  *{kwh:.1f} kWh*  carregados{cmp_str}")
            if m3 > 0:
                custo_gnv = Decimal(str(gnv_e.get("custo_total", 0.0)))
                if custo_gnv > 0:
                    if m3 >= _LIMIAR_CMP_M3:
                        cmp_str = f" (R$ {float(custo_gnv):.2f} total · ≈ R$ {float(custo_gnv / m3):.3f}/m³)"
                    else:
                        cmp_str = f" (R$ {float(custo_gnv):.2f} total — _reabasteça em breve_)"
                else:
                    cmp_str = ""
                linhas_estoque.append(f"🟦  *{m3:.1f} m³*  de GNV{cmp_str}")

            estoque_str = (
                "\n".join(f"• {l}" for l in linhas_estoque)
                if linhas_estoque
                else "• Estoque zerado — abasteça para ativar o rastreio de CMP."
            )

            # ── Seção despesas fixas ───────────────────────────────────────────
            if despesas_fixas:
                total_df = sum(float(r["valor_pro_rata_diario"]) for r in despesas_fixas)
                linhas_df = []
                for r in despesas_fixas:
                    linhas_df.append(
                        f"• {r['nome']}:  *R$ {float(r['valor_mensal']):.2f}/mês*"
                        f"  (≈ R$ {float(r['valor_pro_rata_diario']):.2f}/dia)"
                    )
                linhas_df.append(f"• *Total pro-rata diário: R$ {total_df:.2f}*")
                despesas_fixas_str = "\n".join(linhas_df)
            else:
                despesas_fixas_str = "• Nenhuma despesa fixa cadastrada.\n_Use  *!adicionar despesa <nome> <R$/mês> <dias>*_"

            # ── Seção caixas de provisão ──────────────────────────────────────
            if caixas:
                total_saldo = sum(float(r["saldo_atual"]) for r in caixas)
                linhas_cx = []
                for r in caixas:
                    aporte = float(r["aporte_diario"])
                    aporte_str = f"  _(+R$ {aporte:.2f}/turno)_" if aporte > 0 else ""
                    linhas_cx.append(
                        f"• {r['nome_caixa']}:  *R$ {float(r['saldo_atual']):.2f}*{aporte_str}"
                    )
                linhas_cx.append(f"• *Total reservado: R$ {total_saldo:.2f}*")
                caixas_str = "\n".join(linhas_cx)
            else:
                caixas_str = "• Nenhuma caixa de provisão criada ainda.\n_Elas são criadas automaticamente ao cadastrar uma despesa fixa._"

            # ── Seção histórico ────────────────────────────────────────────────
            if qtd_turnos > 0:
                historico_str = (
                    f"• {qtd_turnos} fechamentos analisados\n"
                    f"• Média de KM/dia:  *{media_km_dia:.1f} km* \n"
                    f"• Faturamento médio/dia:  *R$ {media_fat_dia:.2f}* \n"
                    f"• Custo médio/dia:  *R$ {media_custo_dia:.2f}* \n"
                    f"• Lucro médio/dia:  *R$ {media_lucro_dia:.2f}*  (margem  *{margem_media:.1f}%* )"
                )
            else:
                historico_str = "• Nenhum fechamento registrado ainda."

            # ── Montagem final da mensagem ─────────────────────────────────────
            return (
                f"👤  *PARCEIRO DO PAINEL — {nome_exibicao.upper()}*  🛡\n"
                f"──────────────────────────────\n\n"
                f"🚗  *VEÍCULO ATIVO* \n"
                f"• Modelo:  *{v['modelo']}*  ({v['placa']})\n"
                f"• Contrato:  *{v['locadora'] or 'Não configurado'}* \n"
                f"• Aluguel:  *R$ {aluguel_sem:.2f}/sem*  (≈  *R$ {aluguel_dia:.2f}/dia* )\n"
                f"• Franquia:  *{franquia_sem:.0f} km/sem*  (≈  *{franquia_sem / 7:.0f} km/dia* )\n\n"
                f"⛽  *ESTOQUE NO COFRE (Virtual)* \n"
                f"{estoque_str}\n"
                f"_(Usado para calcular o Lucro Real no fechamento do turno)_\n\n"
                f"📊  *DESEMPENHO DO MÊS ATUAL* \n"
                f"• Receitas:  *R$ {float(fat_bruto):.2f}* \n"
                f"• Despesas:  *R$ {float(desp_total):.2f}* \n"
                f"• Lucro Real Acumulado:  *R$ {float(lucro_parcial):.2f}* \n"
                f"• Meta Mensal:  *R$ {float(meta_mensal):.2f}*\n"
                f"• Progresso: [{barra}]  *{progresso:.1f}%* \n"
                + (f"• Dias Úteis Restantes (est.):  *{dias_uteis_restantes} dias* \n" if dias_uteis_restantes > 0 else "")
                + linha_projecao
                + f"\n🎯  *CONFIGURAÇÕES ATUAIS* \n"
                f"• Meta Diária:  *R$ {float(meta_diaria):.2f}*  ({dias_uteis} dias úteis/mês)\n"
                f"• Piso por KM:  *R$ {piso_km:.2f}/km*   Piso por Hora:  *R$ {piso_hora:.2f}/h* \n\n"
                f"📌  *DESPESAS FIXAS MENSAIS* \n"
                f"{despesas_fixas_str}\n\n"
                f"📦  *CAIXAS DE PROVISÃO* \n"
                f"{caixas_str}\n\n"
                f"📈  *HISTÓRICO RECENTE*  (últimos {qtd_turnos if qtd_turnos else '—'} turnos)\n"
                f"{historico_str}\n"
                + alerta_ritmo
                + f"\n\n💡  _Quer ajustar algo?_\n"
                f"  *!alterar meta mensal 12000*   *!alterar piso km 2,50*\n"
                f"  *!adicionar despesa seguro 180 26*   *!caixas*"
            )

        except Exception as exc:
            logger.error(f"[ProfileService] Erro ao gerar Raio-X (motorista={motorista_id}): {exc}")
            return "❌ Não consegui carregar seus dados agora. Tente novamente em instantes."
