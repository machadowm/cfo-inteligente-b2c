"""
ProfileService — Raio-X completo do motorista (comando 'perfil' / 'meus dados').

Diferente do 'status', que reflete apenas o turno corrente, o Raio-X consolida:
  - Configurações de metas e contrato vigentes
  - Estoque virtual de combustível/energia no cofre
  - Faturamento e despesas acumulados no mês calendário
  - Histórico de eficiência (km/L ou km/kWh) dos últimos fechamentos

Opera totalmente fora da FSM de turnos — pode ser chamado a qualquer momento.
"""

import json
import logging
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

                # ── 1. Metas do motorista ───────────────────────────────────
                m = await conn.fetchrow(
                    """
                    SELECT meta_mensal_faturamento, dias_uteis_mes
                    FROM public.motoristas
                    WHERE id = $1::uuid;
                    """,
                    motorista_id,
                )

                # ── 2. Veículo ativo + estoque JSONB ───────────────────────
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

                # ── 3. Acumulado do mês — receitas e despesas separadas ────
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

                # ── 4. Média de eficiência — últimos 10 fechamentos ────────
                hist = await conn.fetchrow(
                    """
                    SELECT
                        COALESCE(AVG(NULLIF(km_rodados, 0)), 0)        AS media_km,
                        COALESCE(AVG(NULLIF(faturamento_bruto, 0)), 0) AS media_fat,
                        COUNT(*)                                        AS qtd_turnos
                    FROM (
                        SELECT km_rodados, faturamento_bruto
                        FROM public.fechamento_diario
                        WHERE motorista_id = $1::uuid
                        ORDER BY data_fechamento DESC
                        LIMIT 10
                    ) sub;
                    """,
                    motorista_id,
                )

            # ── Extração e normalização do estoque JSONB ──────────────────
            estoque_raw = v["estoque_financeiro"] if v else {}
            if isinstance(estoque_raw, str):
                estoque_raw = json.loads(estoque_raw)
            estoque = TurnoService._garantir_estrutura_estoque(estoque_raw or {})

            liq     = estoque.get("liquido", {})
            ele     = estoque.get("eletricidade", {})
            gnv_e   = estoque.get("gnv", {})

            litros  = Decimal(str(liq.get("litros", 0.0)))
            kwh     = Decimal(str(ele.get("kwh", 0.0)))
            m3      = Decimal(str(gnv_e.get("m3", 0.0)))

            # ── Cálculos financeiros ───────────────────────────────────────
            meta_mensal   = Decimal(str(m["meta_mensal_faturamento"] or "12000.00"))
            dias_uteis    = int(m["dias_uteis_mes"] or 26)
            meta_diaria   = (meta_mensal / Decimal(str(dias_uteis))).quantize(Decimal("0.01"))

            fat_bruto     = Decimal(str(fat_row["fat_bruto"]  or 0))
            desp_total    = Decimal(str(fat_row["desp_total"] or 0))
            lucro_parcial = fat_bruto - desp_total
            progresso     = float(fat_bruto / meta_mensal * 100) if meta_mensal > 0 else 0.0

            aluguel_sem   = float(v["custo_aluguel_semanal"] or 1020.85)
            aluguel_dia   = aluguel_sem / 6.0
            franquia_sem  = float(v["franquia_km_semanal"] or 1505.0)

            media_km_dia  = float(hist["media_km"]  or 0)
            media_fat_dia = float(hist["media_fat"] or 0)
            qtd_turnos    = int(hist["qtd_turnos"]  or 0)

            # ── Linha de estoque (exibe apenas fontes com saldo > 0) ───────
            linhas_estoque = []
            if litros > 0:
                linhas_estoque.append(f"⛽  *{litros:.1f} L*  de combustível")
            if kwh > 0:
                linhas_estoque.append(f"🔋  *{kwh:.1f} kWh*  carregados")
            if m3 > 0:
                linhas_estoque.append(f"🟦  *{m3:.1f} m³*  de GNV")
            estoque_str = "\n".join(f"• {l}" for l in linhas_estoque) if linhas_estoque else "• Estoque zerado — abasteça para ativar o rastreio de CMP."

            historico_str = (
                f"• {qtd_turnos} fechamentos analisados\n"
                f"• Média de KM/dia:  *{media_km_dia:.1f} km*\n"
                f"• Média de Faturamento/dia:  *R$ {media_fat_dia:.2f}*"
                if qtd_turnos > 0
                else "• Nenhum fechamento registrado ainda."
            )

            return (
                f"👤  *RAIO-X DO MOTORISTA: {nome_exibicao.upper()}*  🛡\n"
                f"──────────────────────────────\n\n"
                f"🚗  *VEÍCULO ATIVO*\n"
                f"• Modelo:  *{v['modelo']}*  ({v['placa']})\n"
                f"• Contrato:  *{v['locadora'] or 'Não configurado'}*\n"
                f"• Aluguel:  *R$ {aluguel_sem:.2f}/sem*  (≈  *R$ {aluguel_dia:.2f}/dia* )\n"
                f"• Franquia:  *{franquia_sem:.0f} km/sem*  (≈  *{franquia_sem / 7:.0f} km/dia* )\n\n"
                f"⛽  *ESTOQUE NO COFRE (Virtual)*\n"
                f"{estoque_str}\n"
                f"_(Usado para calcular o Lucro Real no fechamento do turno)_\n\n"
                f"📊  *DESEMPENHO DO MÊS ATUAL*\n"
                f"• Receitas:  *R$ {fat_bruto:.2f}*\n"
                f"• Despesas:  *R$ {desp_total:.2f}*\n"
                f"• Lucro Parcial:  *R$ {lucro_parcial:.2f}*\n"
                f"• Meta Mensal:  *R$ {meta_mensal:.2f}*  →  *{progresso:.1f}%* atingido\n\n"
                f"🎯  *CONFIGURAÇÕES ATUAIS*\n"
                f"• Meta Diária:  *R$ {meta_diaria:.2f}*\n"
                f"• Dias Úteis/Mês:  *{dias_uteis} dias*\n\n"
                f"📈  *HISTÓRICO RECENTE* (últimos {qtd_turnos if qtd_turnos else '—'} turnos)\n"
                f"{historico_str}\n\n"
                f"💡  _Quer ajustar algo?_\n"
                f"  `!alterar meta mensal 12000`\n"
                f"  `!alterar aluguel 1020`   ou   `!parametros`"
            )

        except Exception as exc:
            logger.error(f"[ProfileService] Erro ao gerar Raio-X (motorista={motorista_id}): {exc}")
            return "❌ Não consegui carregar seus dados agora. Tente novamente em instantes."
