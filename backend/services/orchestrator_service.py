import os
import re
import json
import httpx
import logging
import unicodedata
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.transacao_service import TransacaoService
from services.turno_service import TurnoService
from services.help_service import HelpService
from services.parametros_service import ParametrosService
from services.profile_service import ProfileService
from services.reminder_service import registrar_interacao

# Configuração de Logger
logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://cfo_evolution:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")

# --- Funções Auxiliares de Formatação e Utilitários ---

def normalizar_texto(texto: str) -> str:
    """Sanitiza strings removendo acentos e pontuações para reconhecimento de intenção."""
    if not texto: return ""
    nfkd = unicodedata.normalize('NFKD', texto)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r'[^a-zA-Z0-9\s]', '', sem_acento).lower().strip()

def _detectar_tipo_combustivel(texto_limpo: str) -> Optional[str]:
    """
    Detecta o tipo de combustível explicitamente mencionado na mensagem normalizada.
    Retorna 'gasolina' | 'etanol' | 'gnv' | 'eletrico' | None.
    None significa que o tipo não foi especificado — o TransacaoService usará o fallback
    baseado no combustível principal do veículo.
    """
    if any(w in texto_limpo for w in ["gnv", "gas natural", "m3"]):
        return "gnv"
    if any(w in texto_limpo for w in ["kwh", "recarga", "eletric", "solar", "tomada"]):
        return "eletrico"
    if any(w in texto_limpo for w in ["etanol", "alcool", "alc"]):
        return "etanol"
    if any(w in texto_limpo for w in ["gasolin", "gasolina", "comum", "aditivada"]):
        return "gasolina"
    return None

def converter_para_float(texto_valor: str) -> float:
    """Parser monetário robusto e resiliente a padrões regionais brasileiros (ex: R$ 1.500,50 -> 1500.50)."""
    try:
        limpo = texto_valor.upper().replace("R$", "").replace("REAIS", "").strip()
        limpo = re.sub(r'[^\d.,]', '', limpo)
        if "," in limpo and "." in limpo:
            if limpo.find(".") < limpo.find(","):  # Padrão brasileiro: 1.000,50
                limpo = limpo.replace(".", "").replace(",", ".")
            else:  # Padrão americano: 1,000.50
                limpo = limpo.replace(",", "")
        elif "," in limpo:
            limpo = limpo.replace(",", ".")
        return float(limpo)
    except ValueError:
        return 0.0

def formatar_relatorio_parcial(nome_motorista: str, info: dict) -> str:
    """Gera o status parcial do turno atualizado com as regras contratuais e do DRE."""
    aluguel_diario = info["custo_aluguel_semanal"] / 6.0
    franquia_diaria = info["franquia_km_semanal"] / 7.0
    meta_diaria = info["meta_mensal"] / info["dias_uteis"]
    piso_km   = info.get("piso_ganho_km",   2.0)
    piso_hora = info.get("piso_ganho_hora", 30.0)
    piso_km_fmt   = f"{piso_km:.2f}".replace(".", ",")
    piso_hora_fmt = f"{piso_hora:.2f}".replace(".", ",")
    return (
        f"📥  *DADOS PARCIAIS DO TURNO DE HOJE ({info['data_turno']})* \n\n"
        f"• Início:  *{info['data_inicio_hora']}* \n"
        f"• KM Inicial:  *{info['km_inicial']:,.1f} km* \n".replace(",", ".") +
        f"• Combustível Lançado:  *R$ {info['total_abastecido']:.2f}* \n\n"
        f"⚙  *CUSTOS DO CONTRATO ({info['locadora']})* \n\n"
        f"• Escala:  *{info['escala_trabalho']}* \n"
        f"• Aluguel Diário:  *R$ {aluguel_diario:.2f}*  (semanal R$ {info['custo_aluguel_semanal']:.2f} / 6d)\n"
        f"• Franquia Recomendada:  *{franquia_diaria:.0f} km/dia* \n"
        f"• KM Excedente:  *R$ {info['valor_km_excedente']:.2f}/km* \n\n"
        f"🎯  *METAS DE HOJE* \n\n"
        f"• Meta Diária:  *R$ {meta_diaria:.2f}* \n"
        f"• Piso Indicado:  *R$ {piso_km_fmt}/km*  |  *R$ {piso_hora_fmt}/h* \n\n"
        f"🔮 Para encerrar a sua jornada e emitir o DRE, envie:  *'fechar [KM final]'"
    )

def _formatar_sugestao_recalibracao(s: dict) -> str:
    """Monta a mensagem de sugestão de recalibração Full-to-Full para envio ao motorista.

    Formato separado da resposta de abastecimento para não misturar confirmação
    financeira (ok, guardado) com alerta de engenharia (ajuste de parâmetro).
    """
    km_real_fmt = f"{s['km_l_real']:.2f}".replace(".", ",")
    km_cfg_fmt  = f"{s['km_l_configurado']:.2f}".replace(".", ",")
    div_fmt     = f"{s['divergencia_pct']:.1f}".replace(".", ",")
    km_int_fmt  = f"{s['km_intervalo']:.0f}".replace(".", ".")
    lit_fmt     = f"{s['litros_intervalo']:.1f}".replace(".", ",")
    sinal       = s["sinal"]
    label       = s["param_label"]
    param       = s["param_nome"]
    return (
        f"📐  *Recalibração Full-to-Full Detectada!*\n\n"
        f"Nos últimos  *{km_int_fmt} km*  ({lit_fmt} L consumidos), seu carro fez:\n"
        f"  {sinal}  *{km_real_fmt} km/L*  (rendimento real medido)\n"
        f"  📋  *{km_cfg_fmt} km/L*  (parâmetro cadastrado para {label})\n\n"
        f"Divergência:  *{div_fmt}%*\n\n"
        f"Quer atualizar para o valor real? Envie:\n"
        f"  👉  `!alterar {param} {km_real_fmt}`"
    )


def _nota_qualidade_dados(km_por_unidade: float, detalhe_queima: str) -> str:
    """Retorna uma nota educativa quando os dados de entrada sugerem baixa qualidade.

    Só emite aviso quando o rendimento calculado está fora dos limites físicos plausíveis,
    indicando que o motorista pode ter informado preço, litros ou odômetro incorretamente.
    Não emite nada quando os dados parecem normais — zero poluição visual nos turnos corretos.

    Limites calibrados por tipo de veículo detectado no detalhe_queima:
      Elétrico puro  → kWh: valores < 3.0 ou > 15.0 são suspeitos
      Combustão/híb. → km/L: valores < 5.0 ou > 25.0 são suspeitos
    """
    if km_por_unidade <= 0:
        return ""

    # Elétrico puro: unidade é km/kWh
    if "kWh" in detalhe_queima and "Combustão" not in detalhe_queima:
        if km_por_unidade < 3.0 or km_por_unidade > 15.0:
            return (
                f"⚠  _Rendimento de  *{km_por_unidade:.2f} km/kWh*  fora do esperado "
                f"(normal: 3–15 km/kWh). Isso pode indicar km do painel digitado errado "
                f"ou quantidade de kWh imprecisa no registro de recarga. "
                f"Se quiser corrigir, envie  *!ajustar estoque kwh [valor]* ._\n\n"
            )
        return ""

    # Combustão / híbrido: unidade é km/L
    if km_por_unidade < 5.0:
        return (
            f"⚠  _Rendimento de  *{km_por_unidade:.2f} km/L*  abaixo do esperado "
            f"(normal: 5–20 km/L). Possíveis causas: preço por litro informado abaixo do real, "
            f"litros abastecidos a menos do registrado ou km inicial do turno muito alto. "
            f"Abasteça com tanque cheio no próximo turno para o cofre se recalibrar automaticamente._\n\n"
        )
    if km_por_unidade > 25.0:
        return (
            f"⚠  _Rendimento de  *{km_por_unidade:.2f} km/L*  acima do esperado "
            f"(normal: 5–20 km/L). Possível causa: preço por litro informado acima do real "
            f"ou estoque do cofre com saldo residual baixo de turnos anteriores. "
            f"Abasteça com tanque cheio para recalibrar._\n\n"
        )
    return ""


def formatar_relatorio_fechamento_dre(nome_motorista: str, res: dict) -> str:
    """Formata o DRE Executivo Diário consolidando tempo operacional e indicadores.

    Estrutura v10:
    - Seção 1: Resumo Operacional (horário, tempo, km com uso pessoal)
    - Seção 2: DRE — custos variáveis detalhados + custo fixo contratual + provisão
    - Seção 3: Indicadores de Performance (por km, por hora, rendimento, meta)
    - Seção 4: Projeção Mensal (dias restantes, ritmo atual vs. meta)
    - Seção 5: Caixas de Provisão — aportes do turno com vencimentos próximos destacados
    - Alertas de piso de performance e nota de qualidade de dados
    - Rodapé de configuração de contrato (apenas quando não personalizado)
    """
    from datetime import date as _date

    # ── 1. Tempo operacional ───────────────────────────────────────────────────
    horas = int(res["tempo_total_min"] // 60)
    minutos = int(res["tempo_total_min"] % 60)
    duracao_str = f"{horas}h {minutos}min" if horas > 0 else f"{minutos} min"

    tempo_efetivo_min = res.get("tempo_efetivo_min", res["tempo_total_min"])
    tempo_pausas_min  = res.get("tempo_pausas_min", 0)
    horas_ef   = int(tempo_efetivo_min // 60)
    minutos_ef = int(tempo_efetivo_min % 60)
    duracao_efetiva_str = f"{horas_ef}h {minutos_ef}min" if horas_ef > 0 else f"{minutos_ef} min"

    if tempo_pausas_min > 0:
        horas_p   = int(tempo_pausas_min // 60)
        minutos_p = int(tempo_pausas_min % 60)
        pausa_str = f"{horas_p}h {minutos_p}min" if horas_p > 0 else f"{minutos_p} min"
        linha_tempo_pausas = (
            f"• Tempo Total da Jornada:  *{duracao_str}*  _(pausa: {pausa_str})_\n"
            f"• Tempo Efetivo ao Volante:  *{duracao_efetiva_str}* \n"
        )
    else:
        linha_tempo_pausas = f"• Tempo ao Volante:  *{duracao_str}* \n"

    # ── KM ────────────────────────────────────────────────────────────────────
    km_rodados       = res["km_rodados"] if res["km_rodados"] > 0 else 1.0
    km_profissional  = res.get("km_profissional", km_rodados)
    km_pessoal_intra = res.get("km_pessoal_intra", 0.0)

    linha_km_pessoal = ""
    if km_pessoal_intra > 0:
        linha_km_pessoal = (
            f"• KM Profissional (serviço):  *{km_profissional:,.1f} km* \n".replace(",", ".") +
            f"• KM Uso Pessoal (pausa auditada):  *{km_pessoal_intra:,.1f} km*  _(amortizado)_\n".replace(",", ".")
        )

    # ── 2. Financeiro ─────────────────────────────────────────────────────────
    horas_trab = res["horas_trabalhadas"] if res["horas_trabalhadas"] > 0 else 1.0
    fat              = res["faturamento_bruto"]
    c_var            = res["custo_variavel"]
    c_fixo_contrato  = res["custo_fixo_rateado"]          # já inclui aluguel + despesas fixas
    provisao         = res.get("provisao_descontada", 0.0)
    lucro            = res["lucro_liquido_real"]

    margem_contribuicao = fat - c_var
    # Margem líquida sobre o que sobra após todos os custos (excluindo provisão — é reserva, não perda)
    base_margem = fat - c_var - c_fixo_contrato
    margem_lucro = (base_margem / fat * 100.0) if fat > 0 else 0.0

    # ── Detalhamento de custos variáveis ─────────────────────────────────────
    despesas          = res.get("despesas_detalhadas", [])
    custo_queima      = res.get("custo_combustivel_queimado", 0.0)
    total_abastecido  = res.get("total_abastecido_turno", 0.0)
    detalhe_queima_raw = res.get("detalhe_queima", "")

    # Rendimento energético — label dinâmico
    if "kWh" in detalhe_queima_raw and "Combustão" not in detalhe_queima_raw:
        label_rendimento = "km/kWh ⚡"
    elif "kWh" in detalhe_queima_raw:
        label_rendimento = "km/L (híb.) ⚡🔥"
    else:
        label_rendimento = "km/L ⛽"

    # Abastecimento = entrada de estoque (CMP) — a queima proporcional é o custo real.
    # Filtramos 'combustivel' das despesas para evitar dupla-contagem visual.
    despesas_operacionais = [d for d in despesas if d.get("categoria") != "combustivel"]

    lista_despesas_str = ""
    if custo_queima > 0:
        detalhe_fmt = f"\n     _{detalhe_queima_raw}_" if detalhe_queima_raw else ""
        lista_despesas_str += f" -  *Queima {label_rendimento.split()[0]}* :  *R$ {custo_queima:.2f}*{detalhe_fmt}\n"
        # Mostra também o total abastecido vs. custo queimado para transparência de estoque
        if total_abastecido > 0 and abs(total_abastecido - custo_queima) > 0.01:
            lista_despesas_str += f"     _(abastecido R$ {total_abastecido:.2f} → queimado R$ {custo_queima:.2f})_\n"
    for d in despesas_operacionais:
        desc = d.get("descricao_original") or d.get("categoria", "geral")
        val  = float(d.get("valor", 0.0))
        lista_despesas_str += f" -  *{desc}* :  *R$ {val:.2f}* \n"

    if lista_despesas_str:
        lista_despesas_str = "• Detalhes:\n" + lista_despesas_str
    else:
        lista_despesas_str = "• Nenhuma despesa variável neste turno.\n"

    # ── Linha de resultado ────────────────────────────────────────────────────
    if lucro < 0:
        linha_resultado = (
            f"🔴  *RESULTADO: R$ {lucro:.2f}*  _(Prejuízo)_\n"
        )
    else:
        linha_resultado = f"💰  *LUCRO LÍQUIDO REAL: R$ {lucro:.2f}* \n"

    # ── Linha de provisão no DRE (reserva real, não perda) ───────────────────
    linha_provisao = ""
    if provisao > 0:
        linha_provisao = f"• (≡) Provisão Despesas Fixas:  *R$ {provisao:.2f}*  _(reservado nas caixas)_\n"

    # ── 3. Indicadores de eficiência ─────────────────────────────────────────
    km_por_unidade       = res.get("km_por_litro", 0.0)
    faturamento_por_km   = fat   / km_rodados
    custo_por_km         = (c_var + c_fixo_contrato) / km_rodados
    lucro_por_km         = lucro / km_rodados
    faturamento_por_hora = fat   / horas_trab
    lucro_por_hora       = lucro / horas_trab

    # ── Pisos de performance ──────────────────────────────────────────────────
    piso_km   = res.get("piso_ganho_km",   2.0)
    piso_hora = res.get("piso_ganho_hora", 30.0)
    alertas_piso: list[str] = []
    if fat > 0:
        if km_rodados > 1 and faturamento_por_km < piso_km:
            alertas_piso.append(
                f"⚠  _Ganho/km  *R$ {faturamento_por_km:.2f}*  abaixo do piso "
                f"(R$ {piso_km:.2f}/km). Avalie plataforma ou horário._"
            )
        if horas_trab > 0.5 and faturamento_por_hora < piso_hora:
            alertas_piso.append(
                f"⚠  _Ganho/h  *R$ {faturamento_por_hora:.2f}*  abaixo do piso "
                f"(R$ {piso_hora:.2f}/h). Muitas viagens curtas ou pico fraco._"
            )
    secao_alertas_piso = ("\n".join(alertas_piso) + "\n\n") if alertas_piso else ""

    # ── 4. Meta diária e projeção mensal ─────────────────────────────────────
    meta_mensal  = res["meta_mensal"]
    dias_uteis   = res["dias_uteis"]
    meta_diaria  = meta_mensal / dias_uteis if dias_uteis > 0 else meta_mensal
    perc_meta    = (fat / meta_diaria * 100.0) if meta_diaria > 0 else 0.0

    hoje = _date.today()
    # Dias úteis restantes no mês: escala linear sobre dias corridos
    dias_uteis_restantes = max(0, round(dias_uteis * (1 - hoje.day / 30)))
    # Projeção usa meta_diaria × dias restantes (não fat do dia — que pode ser atípico)
    projecao_mensal       = fat + (meta_diaria * dias_uteis_restantes)
    deficit_meta          = max(0.0, meta_mensal - fat)
    fat_diario_necessario = deficit_meta / dias_uteis_restantes if dias_uteis_restantes > 0 else 0.0

    if fat > 0 and dias_uteis_restantes > 0:
        secao_projecao = (
            f"📅  *4. PROJEÇÃO MENSAL* \n"
            f"• Meta Mensal:  *R$ {meta_mensal:,.2f}* \n".replace(",", ".") +
            f"• Dias Úteis Restantes (est.):  *{dias_uteis_restantes} dias* \n"
            f"• Projeção ao ritmo da meta:  *R$ {projecao_mensal:,.2f}* \n".replace(",", ".") +
            (
                f"• Faturamento/dia necessário para a meta:  *R$ {fat_diario_necessario:.2f}* \n"
                if fat_diario_necessario > 0 else
                f"• 🎯 Você já ultrapassou a meta mensal! Parabéns!\n"
            ) +
            f"• Atingimento hoje:  *{perc_meta:.1f}%*  da meta diária (R$ {meta_diaria:.2f})\n\n"
        )
    else:
        secao_projecao = ""

    # ── 5. Caixas de provisão ─────────────────────────────────────────────────
    aportes_caixas      = res.get("aportes_caixas", [])
    secao_caixas = ""
    if aportes_caixas:
        hoje_dia  = hoje.day
        amanha_dia = hoje_dia + 1  # simplicidade; edge-case fim de mês não gera falso positivo
        linhas_aportes = ""
        for ap in aportes_caixas:
            # Detecta vencimento próximo via campo 'dia_vencimento' (adicionado nas despesas fixas)
            dia_venc = ap.get("dia_vencimento")
            tag_venc = ""
            if dia_venc is not None:
                if dia_venc == hoje_dia:
                    tag_venc = "  📅 *VENCE HOJE*"
                elif dia_venc == amanha_dia:
                    tag_venc = "  ⏰ _vence amanhã_"

            if ap.get("meta_atingida") and ap["aporte"] == 0.0:
                linhas_aportes += f" -  *{ap['caixa']}* :  ✅ Meta atingida! (R$ {ap['saldo_novo']:.2f}){tag_venc}\n"
            elif ap.get("meta_atingida"):
                linhas_aportes += f" -  *{ap['caixa']}* :  +R$ {ap['aporte']:.2f}  ✅ Meta atingida!{tag_venc}\n"
            elif ap.get("meta") is not None:
                pct = min(100.0, ap["saldo_novo"] / ap["meta"] * 100)
                linhas_aportes += (
                    f" -  *{ap['caixa']}* :  +R$ {ap['aporte']:.2f}  "
                    f"_(R$ {ap['saldo_novo']:.2f} / R$ {ap['meta']:.2f}  {pct:.0f}%)_{tag_venc}\n"
                )
            else:
                linhas_aportes += f" -  *{ap['caixa']}* :  +R$ {ap['aporte']:.2f}{tag_venc}\n"

        secao_caixas = (
            f"📦  *5. CAIXAS DE PROVISÃO* \n"
            f"• Aportes deste turno:\n"
            + linhas_aportes
            + f"• Total provisionado hoje:  *R$ {provisao:.2f}*\n"
            f"_(Envie  *!caixas*  para ver saldos completos)_\n\n"
        )

    # ── Rodapé de configuração de contrato ────────────────────────────────────
    rodape_sugestao = ""
    if not res.get("contrato_personalizado", False):
        aluguel_diario_real = res.get("custo_aluguel_semanal", 1020.85) / 6.0
        rodape_sugestao = (
            f"\n\n_{nome_motorista}, este cálculo usou o custo padrão "
            f"(R$ {aluguel_diario_real:.2f}/dia). Para precisão total, configure seu contrato:_\n\n"
            "1⃣  *Alugado*  (Zarp, Movida, Mottu...):\n"
            "👉  *atualizar contrato [Locadora] [Aluguel Semanal] [Franquia KM]*\n"
            "_Ex: atualizar contrato Zarp 1020 1500_\n\n"
            "2⃣  *Próprio Quitado* :\n"
            "👉  *atualizar contrato Proprietario [Manutenção/dia] 0*\n\n"
            "3⃣  *Financiado* :\n"
            "👉  *atualizar contrato Financiado [Pro-Rata/dia] 0*"
        )

    return (
        f"🏁  *FECHAMENTO — DRE EXECUTIVO DIÁRIO* \n"
        f"👤  *{nome_motorista}* \n"
        f"──────────────────────────────\n\n"
        # ── Seção 1 ──
        f"⏱  *1. RESUMO OPERACIONAL* \n"
        f"• Horário:  *{res['data_inicio']}*  →  *{res['data_fim']}* \n"
        + linha_tempo_pausas
        + f"• Odômetro:  *{res['km_inicial']:,.1f}*  →  *{res['km_final']:,.1f} km* \n".replace(",", ".")
        + f"• Distância Rodada:  *{km_rodados:,.1f} km* \n".replace(",", ".")
        + linha_km_pessoal
        + "\n"
        # ── Seção 2 ──
        + f"📊  *2. DRE — DEMONSTRATIVO DE RESULTADO* \n"
        + f"• (+) Faturamento Bruto:  *R$ {fat:.2f}* \n"
        + f"• (-) Custos Variáveis:\n"
        + lista_despesas_str
        + f"   *Subtotal Variável: R$ {c_var:.2f}* \n"
        + f"• (=) Margem de Contribuição:  *R$ {margem_contribuicao:.2f}* \n"
        + f"• (-) Custo Fixo Contratual:  *R$ {c_fixo_contrato:.2f}*"
        + f"  _({res.get('locadora', 'contrato')})_\n"
        + linha_provisao
        + f"──────────────────────────────\n"
        + linha_resultado
        + f"📈 Margem Líquida:  *{margem_lucro:.1f}%* \n\n"
        # ── Seção 3 ──
        + f"🎯  *3. INDICADORES DE PERFORMANCE* \n"
        + f"• Faturamento/km:  *R$ {faturamento_por_km:.2f}/km* \n"
        + f"• Custo total/km:  *R$ {custo_por_km:.2f}/km* \n"
        + f"• Lucro/km:  *R$ {lucro_por_km:.2f}/km* \n"
        + f"• Faturamento/hora:  *R$ {faturamento_por_hora:.2f}/h* \n"
        + f"• Lucro/hora:  *R$ {lucro_por_hora:.2f}/h* \n"
        + f"• Rendimento ({label_rendimento}):  *{km_por_unidade:.2f}* \n\n"
        + secao_alertas_piso
        + secao_projecao
        + secao_caixas
        + _nota_qualidade_dados(km_por_unidade, res.get("detalhe_queima", ""))
        + f"🛡  *Cofre Contábil Atualizado! Bom descanso, {nome_motorista}!*"
        + rodape_sugestao
    )

async def enviar_whatsapp(remote_jid: str, texto: str):
    """Envia uma mensagem de texto de volta ao WhatsApp usando o gateway Evolution API."""
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "text": texto},
                timeout=10.0
            )
            response.raise_for_status()
    except Exception as e:
        logger.error(f"[Evolution API] Falha ao enviar mensagem de texto para {remote_jid}: {e}")

async def enviar_status_digitando(remote_jid: str):
    """Sinaliza no WhatsApp que o chatbot está escrevendo (Presence composing) para reter atenção."""
    try:
        async with httpx.AsyncClient() as client:
            await client.post(
                f"{EVOLUTION_API_URL}/chat/sendPresence/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "presence": "composing", "delay": 2000},
                timeout=5.0
            )
    except Exception as e:
        logger.error(f"[Evolution API] Falha ao simular presença para {remote_jid}: {e}")

async def registrar_erro_e_verificar_escape(remote_jid: str, tenant_id: str, fsm_key: str, msg_erro: str) -> bool:
    """Registra erro consecutivo no Redis e força o escape se atingir 3 falhas."""
    erros = await RedisFSMService.incrementar_erros_consecutivos(tenant_id)
    if erros >= 3:
        await RedisFSMService.limpar_buffer(fsm_key)
        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
        mensagem_escape = (
            "⚠  *Múltiplos Erros Consecutivos!* \n"
            "Seu fluxo atual foi interrompido e limpo para evitar travamento.\n\n"
            "Por favor, envie um dos comandos rápidos ou valores livres para começar de novo:\n"
            "🟢  *Iniciar*  (ou 'iniciar 1399')\n"
            "🏁  *Fechar*  (ou 'fechar 1450')\n"
            "⏸  *Pausar*  /  *Retomar* \n"
            "📊  *Status*  (resumo do dia)\n"
            "💰  *[Valor]* (ex: 'ganhei 100' ou 'gastei 40 almoço')"
        )
        await enviar_whatsapp(remote_jid, mensagem_escape)
        return True
    await enviar_whatsapp(remote_jid, msg_erro)
    return False

def _montar_resposta_abertura_turno(nome: str, km: float, res: dict) -> str:
    """Monta a mensagem de confirmação de abertura de turno.

    Quando há uso pessoal (odometer gap), informa o motorista de forma
    transparente sobre a amortização que foi realizada no cofre virtual.
    """
    km_fmt = f"{km:,.1f}".replace(",", ".")
    km_uso = res.get("km_uso_pessoal", 0.0)
    custo_uso = res.get("custo_uso_pessoal", 0.0)

    base = f"🚀 Turno aberto! Odômetro inicial registrado em  *{km_fmt} km* . Boa jornada, {nome}!"

    if km_uso > 0:
        km_uso_fmt = f"{km_uso:,.1f}".replace(",", ".")
        custo_fmt = f"R$ {custo_uso:.2f}"
        base += (
            f"\n\n🛣️  *Uso Pessoal Auditado*\n"
            f"• {km_uso_fmt} km registrados fora de serviço desde o último turno.\n"
            f"• Custo amortizado do cofre:  *{custo_fmt}*  (CMP calculado pelo estoque atual).\n"
            f"_O Lucro Real dos próximos turnos já está protegido._"
        )

    return base

# --- Orquestrador de Mensagens ---

class OrchestratorService:
    # Janela de debouncing em segundos. Mensagens enviadas em rajada dentro
    # desta janela são bufferizadas e processadas como um único evento.
    _DEBOUNCE_JANELA_SEGUNDOS: int = 4

    @staticmethod
    async def router(tenant_id: str, remote_jid: str, texto_bruto: str, wpp_msg_id: Optional[str] = None, **kwargs):
        """
        Orquestrador Central do Fluxo Contábil e de Onboarding.
        Remove 100% da sobrecarga do FastAPI Event Loop rodando de forma assíncrona.
        Implementa debouncing de mensagens via Redis para aglutinação de rajadas.
        """
        # Resiliência de Interface: Tolera chamadas legadas com nomes de parâmetro variáveis
        wpp_msg_id = wpp_msg_id or kwargs.get("wpp_id") or kwargs.get("wpp_msg_id") or "unknown"

        # --- DEBOUNCING: Acumula a mensagem no buffer e aguarda a janela fechar ---
        mensagens_buffer = await RedisFSMService.acumular_mensagem(
            tenant_id, texto_bruto, OrchestratorService._DEBOUNCE_JANELA_SEGUNDOS
        )
        # Se há mais de uma mensagem no buffer, esta não é a última da rajada:
        # descarta silenciosamente e aguarda o processamento da mensagem final.
        if len(mensagens_buffer) > 1:
            return

        # Reconstrói o texto consolidado caso múltiplas mensagens tenham chegado
        # e esta seja a última (o buffer já terá expirado para as anteriores).
        # Na prática com janela de 4s, chegamos aqui com exatamente 1 mensagem.
        texto_bruto = " ".join(mensagens_buffer) if mensagens_buffer else texto_bruto

        texto_limpo = normalizar_texto(texto_bruto)

        # Registra atividade do motorista — suprime lembretes por _BYPASS_INTERACAO_MIN minutos
        await registrar_interacao(tenant_id)

        # 1. Bypass de Cache de Perfil no Redis para Velocidade Absoluta
        motorista = await RedisFSMService.obter_perfil_cache(tenant_id)
        if not motorista:
            motorista_record = await DatabaseService.buscar_motorista_por_telefone(tenant_id)
            if motorista_record:
                motorista = dict(motorista_record)
                await RedisFSMService.cache_perfil_motorista(tenant_id, motorista)

        # =========================================================================
        # FLUXO DE ONBOARDING CONVERSACIONAL (FRICÇÃO ZERO)
        # =========================================================================
        if not motorista:
            fsm_key = f"onboard:{tenant_id}"
            estado_atual = await RedisFSMService.obter_estado(fsm_key)

            if estado_atual == "IDLE" or not estado_atual:
                await RedisFSMService.definir_estado(fsm_key, "AGUARDANDO_NOME")
                await enviar_whatsapp(
                    remote_jid,
                    "Ei, tudo bem? 👋 Seja bem-vindo ao  *Parceiro do Painel* ! 🚗\n\n"
                    "Aqui você controla seus ganhos, gastos e lucro real direto pelo WhatsApp — sem planilha, sem complicação.\n\n"
                    "Vamos criar seu perfil rapidinho! Como você se chama?"
                )
                return

            elif estado_atual == "AGUARDANDO_NOME":
                if len(texto_bruto) < 3:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key, "Hmm, parece curto demais. 😅 Me manda seu nome completo:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_VEICULO|name:{texto_bruto}")
                await enviar_whatsapp(
                    remote_jid,
                    f"Que nome bonito,  *{texto_bruto}* ! 😄\n\n"
                    "Agora me fala: qual é o modelo do seu veículo de trabalho? (Ex:  *Honda CG 160* ,  *HB20* ,  *Gol 1.6* )"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_VEICULO"):
                nome = estado_atual.split("|name:")[1] if "|name:" in estado_atual else "Motorista"
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_CATEGORIA_VEICULO|name:{nome}|veiculo:{texto_bruto}")
                await enviar_whatsapp(
                    remote_jid,
                    f"Boa,  *{texto_bruto}*  anotado! 🚗🏍\n\n"
                    "Ele é um  *Carro*  ou uma  *Moto* ?\n\n"
                    "👉  *Carro*\n"
                    "👉  *Moto*"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_CATEGORIA_VEICULO"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                cat_input = texto_bruto.lower().strip()
                # Aceita variações naturais (carro, moto, automóvel, motocicleta, etc.)
                if any(w in cat_input for w in ["carro", "automovel", "auto", "carro", "sedan", "hatch", "suv", "van", "pickup", "caminhonete"]):
                    categoria_veiculo = "carro"
                elif any(w in cat_input for w in ["moto", "motocicleta", "bike", "scooter", "motoneta"]):
                    categoria_veiculo = "moto"
                else:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "Não entendi bem. 😅 Responda  *Carro*  ou  *Moto* :"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_COMBUSTIVEL|name:{nome}|veiculo:{veiculo}|categoria:{categoria_veiculo}")
                await enviar_whatsapp(
                    remote_jid,
                    f"Entendido! ⛽ Qual é o combustível do  *{veiculo}* ?\n\n"
                    "👉  *Gasolina*\n"
                    "👉  *Etanol*\n"
                    "👉  *Flex*  _(aceita os dois)_\n"
                    "👉  *Hibrido*\n"
                    "👉  *Eletrico*\n"
                    "👉  *GNV*"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_COMBUSTIVEL"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                # categoria_veiculo pode não existir em estados Redis gravados antes desta versão
                categoria_veiculo = next((p.split("categoria:")[1] for p in partes if p.startswith("categoria:")), "carro")
                comb_input = texto_bruto.lower().replace("é", "e").replace("í", "i").strip()
                combustiveis_suportados = ["gasolina", "etanol", "flex", "hibrido", "eletrico", "gnv"]
                if comb_input not in combustiveis_suportados:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "Esse combustível não reconheci. 😅 Tenta uma dessas opções:\n"
                        "*Gasolina, Etanol, Flex, Hibrido, Eletrico* ou *GNV*"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_PLACA|name:{nome}|veiculo:{veiculo}|categoria:{categoria_veiculo}|combustivel:{comb_input}")
                await enviar_whatsapp(
                    remote_jid,
                    "Ótimo! Agora me passa a  *placa*  do veículo. 🔡\n"
                    "_(Ex: ABC1234 ou ABC1D23)_"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_PLACA"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                categoria_veiculo = next((p.split("categoria:")[1] for p in partes if p.startswith("categoria:")), "carro")
                combustivel = next((p.split("combustivel:")[1] for p in partes if p.startswith("combustivel:")), "gasolina")
                placa_limpa = re.sub(r'[^A-Za-z0-9]', '', texto_bruto).upper()
                if len(placa_limpa) != 7:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key,
                        "Hmm, a placa precisa ter 7 caracteres (ex:  *ABC1234* ). Tenta de novo:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_CAPACIDADE_TANQUE|name:{nome}|veiculo:{veiculo}|categoria:{categoria_veiculo}|combustivel:{combustivel}|placa:{placa_limpa}")
                await enviar_whatsapp(
                    remote_jid,
                    "Quase lá! ⛽ Qual a  *capacidade do tanque*  em litros?\n"
                    "_(Se for elétrico puro, manda  *0* )_"
                )
                return

            elif estado_atual.startswith("AGUARDANDO_CAPACIDADE_TANQUE"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                categoria_veiculo = next((p.split("categoria:")[1] for p in partes if p.startswith("categoria:")), "carro")
                combustivel = next((p.split("combustivel:")[1] for p in partes if p.startswith("combustivel:")), "gasolina")
                placa = next((p.split("placa:")[1] for p in partes if p.startswith("placa:")), "")
                tanque_val = converter_para_float(texto_bruto)
                if tanque_val < 0:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key, "Valor negativo não funciona aqui. 😅 Manda a capacidade em litros:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                if combustivel in ["hibrido", "eletrico"]:
                    await RedisFSMService.definir_estado(fsm_key, f"AGUARDANDO_CAPACIDADE_BATERIA|name:{nome}|veiculo:{veiculo}|categoria:{categoria_veiculo}|combustivel:{combustivel}|placa:{placa}|tanque:{tanque_val}")
                    await enviar_whatsapp(remote_jid, "🔋 E qual a capacidade da bateria em  *kWh* ? (Ex:  *30* )")
                    return
                else:
                    await enviar_whatsapp(remote_jid, "⚙  *A preparar o seu cofre contábil... Só um segundo!*")
                    try:
                        motorista_uuid = await DatabaseService.registrar_novo_motorista(
                            telefone=tenant_id, nome=nome, veiculo_modelo=veiculo, combustivel=combustivel, placa=placa
                        )
                        # Defaults de rendimento calibrados por categoria de veículo.
                        # Motos têm rendimento ~3× superior a carros — usar o default de
                        # carro causaria rejeição silenciosa no sanity check Full-to-Full.
                        _km_l_gas = 35.0 if categoria_veiculo == "moto" else 12.0
                        _km_l_eta = 24.5 if categoria_veiculo == "moto" else 8.5
                        async with DatabaseService.get_tenant_connection(motorista_uuid) as conn:
                            estoque_dict = {
                                "meta": {
                                    "tipo_veiculo": combustivel,
                                    "categoria_veiculo": categoria_veiculo,
                                    "is_flex": bool(combustivel == "flex"),
                                    "is_hibrido": False,
                                    "is_eletrico": False,
                                    "capacidade_tanque_l": float(tanque_val),
                                    "capacidade_bateria_kwh": 0.0,
                                    "qtd_tanques": 1
                                },
                                "liquido": {
                                    "litros": 0.0,
                                    "custo_total": 0.0,
                                    "gasolina_litros": 0.0,
                                    "etanol_litros": 0.0,
                                    "gasolina_proporcao": 1.0,
                                    "etanol_proporcao": 0.0,
                                    "km_l_gasolina": _km_l_gas,
                                    "km_l_etanol": _km_l_eta
                                },
                                "eletricidade": {
                                    "kwh": 0.0,
                                    "custo_total": 0.0,
                                    "km_kwh": 6.5
                                },
                                "gnv": {
                                    "m3": 0.0,
                                    "custo_total": 0.0,
                                    "km_m3": 14.0
                                }
                            }
                            await conn.execute(
                                """
                                UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE motorista_id = $2::uuid AND ativo = TRUE;
                                """, json.dumps(estoque_dict), motorista_uuid
                            )
                        await RedisFSMService.limpar_buffer(fsm_key)
                        await enviar_whatsapp(
                            remote_jid,
                            f"Tudo pronto,  *{nome}* ! 🎉\n\n"
                            f"Seu cofre está ativo para o  *{veiculo}*  ({placa}).\n\n"
                            "Sempre que iniciar seu dia, manda  *iniciar*  com o km do painel.\n"
                            "Ex:  *iniciar 45230*"
                        )
                    except Exception as e:
                        logger.error(f"Falha ao salvar onboarding no banco: {e}")
                        await RedisFSMService.limpar_buffer(fsm_key)
                    return

            elif estado_atual.startswith("AGUARDANDO_CAPACIDADE_BATERIA"):
                partes = estado_atual.split("|")
                nome = partes[1].split("name:")[1]
                veiculo = partes[2].split("veiculo:")[1]
                categoria_veiculo = next((p.split("categoria:")[1] for p in partes if p.startswith("categoria:")), "carro")
                combustivel = next((p.split("combustivel:")[1] for p in partes if p.startswith("combustivel:")), "hibrido")
                placa = next((p.split("placa:")[1] for p in partes if p.startswith("placa:")), "")
                tanque_val = float(next((p.split("tanque:")[1] for p in partes if p.startswith("tanque:")), "0"))
                bateria_val = converter_para_float(texto_bruto)
                if bateria_val < 0:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_key, "Valor negativo não funciona aqui. 😅 Manda a capacidade em kWh:"
                    )
                    return
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                await enviar_whatsapp(remote_jid, "⚙ Configurando seu cofre... um segundo!")
                try:
                    motorista_uuid = await DatabaseService.registrar_novo_motorista(
                        telefone=tenant_id, nome=nome, veiculo_modelo=veiculo, combustivel=combustivel, placa=placa
                    )
                    # Híbridos/elétricos são sempre carros no contexto de apps de frete
                    _km_l_gas = 35.0 if categoria_veiculo == "moto" else 12.0
                    _km_l_eta = 24.5 if categoria_veiculo == "moto" else 8.5
                    async with DatabaseService.get_tenant_connection(motorista_uuid) as conn:
                        estoque_dict = {
                            "meta": {
                                "tipo_veiculo": combustivel,
                                "categoria_veiculo": categoria_veiculo,
                                "is_flex": bool(combustivel == "flex"),
                                "is_hibrido": bool(combustivel == "hibrido"),
                                "is_eletrico": bool(combustivel == "eletrico"),
                                "capacidade_tanque_l": float(tanque_val),
                                "capacidade_bateria_kwh": float(bateria_val),
                                "qtd_tanques": 1
                            },
                            "liquido": {
                                "litros": 0.0,
                                "custo_total": 0.0,
                                "gasolina_litros": 0.0,
                                "etanol_litros": 0.0,
                                "gasolina_proporcao": 1.0,
                                "etanol_proporcao": 0.0,
                                "km_l_gasolina": _km_l_gas,
                                "km_l_etanol": _km_l_eta
                            },
                            "eletricidade": {
                                "kwh": 0.0,
                                "custo_total": 0.0,
                                "km_kwh": 6.5
                            },
                            "gnv": {
                                "m3": 0.0,
                                "custo_total": 0.0,
                                "km_m3": 14.0
                            }
                        }
                        await conn.execute(
                            """
                            UPDATE public.veiculos SET estoque_financeiro = $1::jsonb WHERE motorista_id = $2::uuid AND ativo = TRUE;
                            """, json.dumps(estoque_dict), motorista_uuid
                        )
                    await RedisFSMService.limpar_buffer(fsm_key)
                    await enviar_whatsapp(
                        remote_jid,
                        f"Tudo pronto,  *{nome}* ! 🎉\n\n"
                        f"Seu cofre está ativo para o  *{veiculo}*  ({placa}).\n\n"
                        "Sempre que iniciar seu dia, manda  *iniciar*  com o km do painel.\n"
                        "Ex:  *iniciar 45230*"
                    )
                except Exception as e:
                    logger.error(f"Falha ao salvar onboarding híbrido no banco: {e}")
                    await RedisFSMService.limpar_buffer(fsm_key)
                return

        # =========================================================================
        # MOTORISTAS CADASTRADOS: PROCESSO CONVERSACIONAL COM FSM DE TURNO
        # =========================================================================
        motorista_id = str(motorista["id"])
        fsm_turno_key = f"turno_flow:{tenant_id}"

        # =========================================================================
        # INTERCEPTOR DE COMANDOS ADMINISTRATIVOS (prefixo '!')
        # Executa antes de qualquer lógica de turno para dar precedência absoluta
        # aos ajustes de parâmetro.  Exemplos: !alterar meta mensal 12000
        # =========================================================================
        if texto_bruto.strip().startswith("!"):
            resposta_param = await ParametrosService.processar(motorista_id, tenant_id, texto_bruto)
            if resposta_param is not None:
                await enviar_whatsapp(remote_jid, resposta_param)
                return
            # Prefixo '!' sem padrão reconhecido → cai no fluxo normal com ajuda contextual

        # AJUDA CONTEXTUAL (FSM-aware)
        # Tópicos explícitos ("ajuda metas", "ajuda contrato"...) → ajuda estática por tópico.
        # "ajuda" sem tópico → ajuda contextual baseada no estado atual da FSM.
        palavras_ajuda = ["ajuda", "help", "socorro", "como", "explicar"]
        if any(w in texto_limpo for w in palavras_ajuda):
            partes = texto_limpo.split()
            topico_explicito = None
            for i, palavra in enumerate(partes):
                if palavra in palavras_ajuda and i + 1 < len(partes):
                    pot = partes[i + 1]
                    if pot in ["metas", "contrato", "lancamentos", "turno", "parametros", "perfil", "geral"]:
                        topico_explicito = pot
                        break
            if topico_explicito:
                # Tópico específico solicitado → resposta estática detalhada
                resposta_ajuda = HelpService.obter_ajuda(topico_explicito)
            else:
                # Sem tópico → lê o estado FSM atual e entrega ajuda relevante ao momento
                estado_para_ajuda = await RedisFSMService.obter_estado(fsm_turno_key)
                # Resolve o status real do turno no DB quando a FSM Redis está vazia
                # (turno em_andamento não grava estado na FSM — a FSM só existe para fluxos guiados)
                if not estado_para_ajuda:
                    async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                        turno_db = await conn.fetchrow(
                            "SELECT status FROM public.turnos WHERE motorista_id = $1::uuid "
                            "AND status IN ('ABERTO', 'em_andamento', 'em_pausa') "
                            "ORDER BY data_inicio DESC LIMIT 1;",
                            motorista_id,
                        )
                    estado_para_ajuda = turno_db["status"] if turno_db else None
                fsm_onboard_key = f"onboard:{tenant_id}"
                estado_onboard = await RedisFSMService.obter_estado(fsm_onboard_key)
                resposta_ajuda = HelpService.obter_ajuda_contextual(estado_para_ajuda, estado_onboard)
            await enviar_whatsapp(remote_jid, resposta_ajuda)
            return

        estado_turno = await RedisFSMService.obter_estado(fsm_turno_key)

        # Pre-parse de intenções
        tem_intencao_inicio = any(w in texto_limpo for w in ["iniciar", "comecar", "abrir", "bora", "turno", "inicio"])
        tem_intencao_fim = any(w in texto_limpo for w in ["encerrar", "fechar", "finalizar", "terminar", "fim"])
        tem_intencao_pausa = any(w in texto_limpo for w in ["pausa", "pausar", "pause", "pausei", "almocar"])
        tem_intencao_retomada = any(w in texto_limpo for w in ["retomar", "voltar", "voltei", "continuar", "retom"])
        # Detecta lançamentos de RECEITA (aciona auto-resume se turno estiver em pausa)
        _PALAVRAS_RECEITA = [
            'recebi', 'ganhei', 'faturei', 'corrida', 'uber', 'indrive', 'faturamento', 'ganho',
            'gorjeta', 'gorjet', 'bico', 'freela', 'freelance', 'salario', 'salário', 'pagamento',
            'bonus', 'bônus', 'comissao', 'comissão', 'clt', 'extra', 'renda', 'entrada',
        ]
        # '99' e 'pj' precisam de word boundary — são curtos demais para substring matching
        # (ex: "ganhei 99" → '99' bate no valor; "pj" bate em "hoje trabalhei")
        _PALAVRAS_RECEITA_WB = [r'\b99\b', r'\bpj\b']
        is_intencao_receita = (
            any(w in texto_limpo for w in _PALAVRAS_RECEITA)
            or any(re.search(p, texto_limpo) for p in _PALAVRAS_RECEITA_WB)
        )
        is_status = any(t in texto_limpo for t in ['status', 'resumo', 'como estou', 'parcial', 'relatorio'])
        is_perfil = any(t in texto_limpo for t in ['perfil', 'meus dados', 'minha conta', 'raio x', 'raiox', 'configuracoes', 'meu perfil'])
        is_contrato = "atualizar contrato" in texto_limpo

        # 1. ATUALIZAÇÃO CONTRATUAL EM TEMPO REAL
        if is_contrato:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            partes = texto_bruto.split()
            try:
                locadora = partes[2] if len(partes) > 2 else "Localiza Zarp"
                aluguel_input = converter_para_float(partes[3]) if len(partes) > 3 else 1020.85
                franquia = converter_para_float(partes[4]) if len(partes) > 4 else 1505.00
                
                if locadora.lower() in ["proprietario", "quitado", "financiado"]:
                    aluguel_semanal = aluguel_input * 6.0
                else:
                    aluguel_semanal = aluguel_input
                    
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    await conn.execute(
                        """
                        UPDATE public.veiculos SET locadora = $1, custo_aluguel_semanal = $2, franquia_km_semanal = $3, contrato_personalizado = TRUE
                        WHERE motorista_id = $4::uuid AND ativo = TRUE;
                        """, locadora, aluguel_semanal, franquia, motorista_id
                    )
                await enviar_whatsapp(remote_jid, f"✅ Contrato atualizado com sucesso para  *{locadora}*! Aluguel rateado recalculado e cofre adaptado. 🛡")
            except Exception as e:
                logger.error(f"Erro ao atualizar contrato: {e}")
                await enviar_whatsapp(remote_jid, "⚠ Formato inválido. Use ex: *'atualizar contrato Zarp 1020.85 1505' *")
            return

        # 2. RAIO-X COMPLETO DO PERFIL (off-shift)
        if is_perfil:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            nome = motorista.get("nome_social") or motorista.get("nome", "Motorista")
            resposta = await ProfileService.gerar_raiox_completo(motorista_id, nome)
            await enviar_whatsapp(remote_jid, resposta)
            return

        # 3. EMISSÃO DE REPORT PARCIAL/STATUS (in-shift)
        if is_status:
            await RedisFSMService.limpar_buffer(fsm_turno_key)
            try:
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    turno_ativo = await conn.fetchrow(
                        """
                        SELECT t.id, t.km_inicial, t.data_inicio, v.locadora, v.custo_aluguel_semanal, v.franquia_km_semanal, v.valor_km_excedente, v.escala_trabalho, m.meta_mensal_faturamento, m.dias_uteis_mes, COALESCE(m.piso_ganho_km, 2.0) AS piso_ganho_km, COALESCE(m.piso_ganho_hora, 30.0) AS piso_ganho_hora
                        FROM public.turnos t 
                        JOIN public.veiculos v ON v.id = t.veiculo_id 
                        JOIN public.motoristas m ON m.id = t.motorista_id 
                        WHERE t.motorista_id = $1::uuid AND t.status IN ('ABERTO', 'em_andamento', 'em_pausa') 
                        ORDER BY t.data_inicio DESC LIMIT 1;
                        """, motorista_id
                    )
                    if turno_ativo:
                        turno_id = str(turno_ativo["id"])
                        dt_inicio = turno_ativo["data_inicio"]
                        tx = await conn.fetchrow(
                            """
                            SELECT COALESCE(SUM(valor), 0.00) as abastecido FROM public.transacoes 
                            WHERE motorista_id = $1::uuid AND turno_id = $2::uuid AND categoria = 'combustivel' AND estornado = FALSE;
                            """, motorista_id, turno_id
                        )
                        info_turno = {
                            "data_turno": dt_inicio.strftime('%d/%m/%Y'),
                            "data_inicio_hora": dt_inicio.strftime('%H:%M'),
                            "km_inicial": float(turno_ativo["km_inicial"]),
                            "locadora": turno_ativo["locadora"] or "Localiza Zarp",
                            "custo_aluguel_semanal": float(turno_ativo["custo_aluguel_semanal"] or 1020.85),
                            "franquia_km_semanal": float(turno_ativo["franquia_km_semanal"] or 1505.0),
                            "valor_km_excedente": float(turno_ativo["valor_km_excedente"] or 0.75),
                            "escala_trabalho": turno_ativo["escala_trabalho"] or "De quarta a segunda (6 dias)",
                            "meta_mensal": float(turno_ativo["meta_mensal_faturamento"] or 12000.0),
                            "dias_uteis": int(turno_ativo["dias_uteis_mes"] or 26),
                            "piso_ganho_km":   float(turno_ativo["piso_ganho_km"]),
                            "piso_ganho_hora": float(turno_ativo["piso_ganho_hora"]),
                            "total_abastecido": float(tx["abastecido"])
                        }
                        resposta = formatar_relatorio_parcial(motorista["nome"], info_turno)
                    else:
                        resposta = "Você ainda não abriu um turno hoje. 😊 Manda  *iniciar*  com o km do painel para começar!"
            except Exception as e:
                logger.error(f"Erro ao obter status: {e}")
                resposta = "❌ Ocorreu um erro interno de banco de dados ao buscar seu status."
            await enviar_whatsapp(remote_jid, resposta)
            return

        # Extração de Odômetro (KM)
        # Opera sobre texto_bruto (antes da normalização) para preservar ':' e 'h'
        # que marcam expressões de horário/tempo — ex: "12:30", "8h45", "1h30min".
        # Remove do candidato qualquer sequência numérica que faça parte dessas expressões
        # para evitar que "fechar 12:30" vire km=1230 após o normalizar_texto remover ':'.
        _texto_sem_horas = re.sub(
            r'\d{1,2}:\d{2}'           # HH:MM  (ex: "12:30", "8:05")
            r'|\d{1,3}\s*h\s*\d{0,3}'  # XhYY   (ex: "1h30", "8h", "1h30min")
            r'|\d{1,3}\s*(?:min|hora)', # Xmin / Xhora
            '', texto_bruto, flags=re.IGNORECASE
        )
        numeros = re.findall(r'\d+', _texto_sem_horas.replace('.', '').replace(',', ''))
        _candidatos_km = [float(n) for n in numeros if float(n) > 100]
        km_encontrado = _candidatos_km[0] if _candidatos_km else None
        # Marca se o km veio de mensagem com intenção inequívoca (único candidato numérico).
        # Usado para suprimir o aviso de descarte em falsos positivos de texto genérico,
        # evitando poluição visual quando o número foi capturado de frase longa.
        _km_intencao_explicita = len(_candidatos_km) == 1

        # =========================================================================
        # 3. INTERCEPÇÃO DA FSM DE JORNADA (Precedência Absoluta)
        # =========================================================================

        # --- FSM DE ABASTECIMENTO GUIADO (4 passos) ----------------------------
        # Estado serializado como: ABASTECIMENTO_<PASSO>|<chave>:<valor>|...
        # TTL: 10 minutos (600s) — abandono de fluxo limpo automaticamente.
        _ABT_TTL = 600

        if estado_turno and estado_turno.startswith("ABASTECIMENTO_"):
            passo = estado_turno.split("|")[0]  # ex: "ABASTECIMENTO_PRECO"

            # Extrai params já acumulados do estado
            params: Dict[str, str] = {}
            for parte in estado_turno.split("|")[1:]:
                if ":" in parte:
                    k, v = parte.split(":", 1)
                    params[k] = v

            if passo == "ABASTECIMENTO_VALOR":
                # Aguarda o valor total gasto no abastecimento
                total = converter_para_float(texto_bruto)
                if total <= 0:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_turno_key,
                        "Não consegui ler o valor. 😅 Manda o total gasto (ex:  *150,00* ):"
                    )
                    return
                desc = params.get("desc", "Abastecimento")
                # Propaga o tipo de combustível detectado na mensagem inicial (se houver)
                comb_detectado = params.get("comb", "") or _detectar_tipo_combustivel(texto_limpo) or ""
                novo_estado = f"ABASTECIMENTO_PRECO|total:{total}|desc:{desc}|comb:{comb_detectado}"
                await RedisFSMService.definir_estado(fsm_turno_key, novo_estado, ex_seconds=_ABT_TTL)
                await enviar_whatsapp(
                    remote_jid,
                    f"Ótimo!  *R$ {total:,.2f}*  registrado.\nQual foi o  *preço por litro*  cobrado? (Ex:  *5,85* )".replace(",", "X").replace(".", ",").replace("X", ".")
                )
                return

            elif passo == "ABASTECIMENTO_PRECO":
                # Aguarda preço unitário (ex: 5,859 ou 5.86)
                preco = converter_para_float(texto_bruto)
                if preco <= 0:
                    await registrar_erro_e_verificar_escape(
                        remote_jid, tenant_id, fsm_turno_key,
                        "Não entendi o preço. 😅 Manda o valor por litro (ex:  *5,85* ):"
                    )
                    return
                total = float(params.get("total", "0"))
                litros = round(total / preco, 3) if preco > 0 else 0.0
                litros_fmt = f"{litros:.2f}".replace(".", ",")
                novo_estado = (
                    f"ABASTECIMENTO_ODOMETRO|total:{total}|preco:{preco}|litros:{litros}"
                    f"|desc:{params.get('desc', '')}|comb:{params.get('comb', '')}"
                )
                await RedisFSMService.definir_estado(fsm_turno_key, novo_estado, ex_seconds=_ABT_TTL)
                await enviar_whatsapp(
                    remote_jid,
                    f"Calculei  *{litros_fmt} litros*  abastecidos ao preço de  *R$ {preco:.3f}/L* . 👍\n"
                    f"_Esse preço define o custo real do combustível no cofre — se errar aqui, o lucro do turno vai sair torto._\n\n"
                    f"Qual o  *km do painel*  agora? (Ex:  *179500* )\n"
                    f"_(Ou manda  *pular*  — mas sem o km não consigo calibrar seu consumo real)_"
                )
                return

            elif passo == "ABASTECIMENTO_ODOMETRO":
                # Aguarda odômetro ou "pular"
                odometro: Optional[float] = None
                if any(w in texto_limpo for w in ["pular", "nao", "nao sei", "skip"]):
                    odometro = None
                else:
                    odometro = converter_para_float(texto_bruto)
                    if odometro <= 0:
                        await registrar_erro_e_verificar_escape(
                            remote_jid, tenant_id, fsm_turno_key,
                            "Km inválido. Manda o valor do painel (ex:  *179500* ) ou  *pular*  para ignorar:"
                        )
                        return
                total   = float(params.get("total",  "0"))
                preco   = float(params.get("preco",  "0"))
                litros  = float(params.get("litros", "0"))
                desc    = params.get("desc", "Abastecimento guiado")
                novo_estado = (
                    f"ABASTECIMENTO_TANQUE_CHEIO"
                    f"|total:{total}|preco:{preco}|litros:{litros}"
                    f"|odo:{odometro if odometro else ''}|desc:{desc}"
                    f"|comb:{params.get('comb', '')}"
                )
                await RedisFSMService.definir_estado(fsm_turno_key, novo_estado, ex_seconds=_ABT_TTL)
                await enviar_whatsapp(
                    remote_jid,
                    "O tanque ficou  *cheio* ? 🔋\n\n"
                    "👉  *Sim*  — eu reseto e ancora o cofre na capacidade real do tanque. "
                    "É a forma mais precisa de calcular seu consumo. Recomendo sempre que puder!\n"
                    "👉  *Não*  — adiciono só os litros abastecidos ao saldo atual.\n\n"
                    "_Essa resposta afeta diretamente o km/L que aparece no seu DRE._"
                )
                return

            elif passo == "ABASTECIMENTO_TANQUE_CHEIO":
                tanque_cheio = any(w in texto_limpo for w in ["sim", "s", "cheio", "yes", "completo"])
                total   = float(params.get("total",  "0"))
                preco   = float(params.get("preco",  "0"))
                litros  = float(params.get("litros", "0"))
                odo_str = params.get("odo", "")
                odometro = float(odo_str) if odo_str else None
                desc    = params.get("desc", "Abastecimento guiado")
                # Recupera o tipo de combustível propagado pela FSM (pode ser vazio se não detectado)
                comb_fsm = params.get("comb", "") or None

                await RedisFSMService.limpar_buffer(fsm_turno_key)
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                res_tx = await TransacaoService.registrar_transacao(
                    motorista_id=motorista_id,
                    tipo_movimentacao='despesa',
                    categoria='combustivel',
                    valor=total,
                    descricao=desc,
                    wpp_msg_id=wpp_msg_id,
                    litros_abastecidos=litros,
                    preco_por_litro=preco,
                    odometro_abastecimento=odometro,
                    tanque_cheio=tanque_cheio,
                    tipo_combustivel_abastecido=comb_fsm,
                )
                if res_tx.get("status") == "success":
                    valor_fmt = f"R$ {total:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    litros_fmt = f"{litros:.2f}".replace(".", ",")
                    preco_fmt  = f"R$ {preco:.3f}".replace(".", ",")
                    odo_fmt    = f"  |  Odômetro:  *{int(odometro):,} km*".replace(",", ".") if odometro else ""
                    if res_tx.get("self_healed"):
                        resposta = (
                            f"⛽  *Abastecimento registrado!*\n\n"
                            f"• Valor:  *{valor_fmt}*\n"
                            f"• Volume:  *{litros_fmt} L*\n"
                            f"• Preço/L:  *{preco_fmt}*"
                            f"{odo_fmt}\n\n"
                            f"🔧 Cofre recalibrado — tanque zerado e recarregado com a capacidade real do veículo. Tudo certinho! 🛡"
                        )
                    else:
                        cheio_str = "  ✅ Tanque cheio registrado!" if tanque_cheio else ""
                        resposta = (
                            f"⛽  *Abastecimento registrado!*\n\n"
                            f"• Valor:  *{valor_fmt}*\n"
                            f"• Volume:  *{litros_fmt} L*\n"
                            f"• Preço/L:  *{preco_fmt}*"
                            f"{odo_fmt}{cheio_str}\n\n"
                            f"Cofre atualizado! 🛡"
                        )
                elif res_tx.get("status") == "duplicate":
                    resposta = "Esse lançamento já estava guardado no cofre. 👍"
                else:
                    resposta = f"❌ Não consegui registrar: {res_tx.get('message')}"
                await enviar_whatsapp(remote_jid, resposta)
                # Envia sugestão de recalibração em mensagem separada para não poluir
                # a confirmação de abastecimento com dados de engenharia.
                sugestao = res_tx.get("sugestao_recalibracao") if res_tx.get("status") == "success" else None
                if sugestao:
                    await enviar_whatsapp(remote_jid, _formatar_sugestao_recalibracao(sugestao))
                return

        # 3.0. Declaração retroativa de pausa (motorista respondendo ao aviso de jornada longa)
        if estado_turno and estado_turno.startswith("AGUARDANDO_DECLARACAO_PAUSA"):
            km_final = float(estado_turno.split("|km:")[1])

            # Interpreta "não" / "nao" / "nenhuma" como ausência de pausa
            sem_pausa = any(w in texto_limpo for w in ["nao", "nenhuma", "nenhum", "sem pausa", "nao parei", "nunca"])

            minutos_declarados: Optional[int] = None
            if not sem_pausa:
                # Parseia expressões de tempo: "1h30", "1h30min", "45min", "45m", "2h", "30"
                m = re.search(r'(\d+)\s*h\s*(\d+)', texto_limpo)         # 1h30, 1h 30
                if m:
                    minutos_declarados = int(m.group(1)) * 60 + int(m.group(2))
                else:
                    m = re.search(r'(\d+)\s*(?:h\b|hora)', texto_limpo)  # 2h, 2 horas
                    if m:
                        minutos_declarados = int(m.group(1)) * 60
                    else:
                        m = re.search(r'(\d+)\s*(?:min\b|m\b)', texto_limpo)  # 45min, 45m
                        if m:
                            minutos_declarados = int(m.group(1))
                        else:
                            # Número puro ≤ 480 interpretado como minutos (≤ 8h de pausa)
                            m = re.search(r'^\s*(\d+)\s*$', texto_bruto.strip())
                            if m and int(m.group(1)) <= 480:
                                minutos_declarados = int(m.group(1))

            if not sem_pausa and minutos_declarados is None:
                # Resposta não reconhecida: pede novamente sem consumir o estado
                await enviar_whatsapp(
                    remote_jid,
                    "Não entendi bem. 😅 Me diz o tempo total de pausa (ex:  *1h30* ,  *45min* ,  *2h* ) "
                    "ou manda  *não*  se não parou."
                )
                return

            await RedisFSMService.limpar_buffer(fsm_turno_key)

            if minutos_declarados and minutos_declarados > 0:
                # Insere pausa retroativa: cobre [dt_inicio_turno, dt_inicio_turno + duração]
                try:
                    from datetime import timezone as _tz
                    async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                        turno_row = await conn.fetchrow(
                            "SELECT id, data_inicio FROM public.turnos "
                            "WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') "
                            "ORDER BY data_inicio DESC LIMIT 1;",
                            motorista_id
                        )
                        if turno_row:
                            dt_inicio = turno_row["data_inicio"]
                            if dt_inicio.tzinfo is None:
                                dt_inicio = dt_inicio.replace(tzinfo=_tz.utc)
                            duracao_turno = datetime.now(_tz.utc) - dt_inicio
                            duracao_turno_min = int(duracao_turno.total_seconds() / 60)

                            # ── SRE CLAMP ────────────────────────────────────────────────────────
                            # Uma pausa não pode ser maior ou igual à duração total da jornada.
                            # Limitamos a (duração - 5 min) para garantir que horas_trabalhadas > 0
                            # e que fim_pausa nunca ultrapasse o timestamp de fechamento.
                            _min_teto = max(1, duracao_turno_min - 5)
                            if minutos_declarados > _min_teto:
                                logger.warning(
                                    f"[DECLARACAO_PAUSA] Clamp aplicado: declarado={minutos_declarados}min "
                                    f"teto={_min_teto}min (jornada={duracao_turno_min}min) motorista={motorista_id}"
                                )
                                minutos_declarados = _min_teto
                            # ────────────────────────────────────────────────────────────────────

                            # Posiciona a pausa retroativa no meio da jornada
                            meio_jornada = dt_inicio + duracao_turno / 2
                            fim_pausa = meio_jornada + timedelta(minutes=minutos_declarados)
                            await conn.execute(
                                "INSERT INTO public.pausas_turno "
                                "(turno_id, motivo, inicio_pausa, fim_pausa) "
                                "VALUES ($1::uuid, 'Pausa Declarada Retroativamente', $2, $3);",
                                str(turno_row["id"]), meio_jornada, fim_pausa
                            )
                            logger.info(
                                f"[DECLARACAO_PAUSA] Motorista {motorista_id}: {minutos_declarados}min "
                                f"de pausa retroativa inserida no turno {turno_row['id']}"
                            )
                except Exception as e:
                    logger.error(f"[DECLARACAO_PAUSA] Erro ao inserir pausa retroativa: {e}")
                    # Falha ao gravar a pausa não deve bloquear o fechamento; apenas loga.

                horas_fmt  = minutos_declarados // 60
                minutos_fmt = minutos_declarados % 60
                tempo_str  = f"{horas_fmt}h{minutos_fmt:02d}min" if horas_fmt else f"{minutos_fmt}min"
                await enviar_whatsapp(
                    remote_jid,
                    f"✅  *{tempo_str}*  de pausa anotada!\n"
                    f"📊 Gerando seu DRE agora..."
                )
            else:
                await enviar_whatsapp(remote_jid, "📊 Sem pausa registrada. Gerando seu DRE...")

            res = await TurnoService.fechar_turno_com_dre(motorista_id, km_final)
            if res["sucesso"]:
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res)
            else:
                resposta = res.get("erro", "❌ Erro ao gerar DRE.")
            await enviar_whatsapp(remote_jid, resposta)
            return

        if estado_turno in ["AGUARDANDO_KM_INICIAL", "AGUARDANDO_KM_FINAL"] or (estado_turno and estado_turno.startswith("AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO")):
            km_digitado = converter_para_float(texto_bruto)

            # 3.1. Estado de Confirmação de Fechamento sem Lançamentos
            if estado_turno.startswith("AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO"):
                if any(term in texto_limpo for term in ["confirmar", "confirm", "sim", "confir", "ok", "isso", "certeza"]):
                    km_final = float(estado_turno.split("|km:")[1])
                    # Guarda de Idempotência: confirma que o turno ainda está ativo antes de fechar.
                    # Evita duplos fechamentos caso o estado Redis fique obsoleto.
                    async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                        turno_confirmacao = await conn.fetchrow(
                            "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;",
                            motorista_id
                        )
                    if not turno_confirmacao:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        await enviar_whatsapp(remote_jid, "Não encontrei um turno aberto. Talvez ele já tenha sido fechado antes. 🙂")
                        return
                    # Verifica se o motorista inseriu receitas pelo bypass antes de confirmar.
                    # Evita a mensagem contraditória "faturamento zerado" quando há lançamentos.
                    receitas_agora = await TurnoService.verificar_transacoes_turno(motorista_id)
                    if receitas_agora == 0:
                        await enviar_whatsapp(remote_jid, "📊 Confirmado! Fechando o turno com faturamento zerado...")
                    else:
                        await enviar_whatsapp(remote_jid, "📊 Ótimo! Processando seus lançamentos e gerando o DRE...")
                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km_final)
                    await RedisFSMService.limpar_buffer(fsm_turno_key)
                    resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res) if res["sucesso"] else res['erro']
                    await enviar_whatsapp(remote_jid, resposta)
                else:
                    palavras_chave_financeiras = ['recebi', 'ganhei', 'faturei', 'corrida', 'uber', '99', 'gastei', 'paguei', 'despesa', 'combustiv', 'gasolin', 'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado']
                    is_fin_event = any(w in texto_limpo for w in palavras_chave_financeiras)
                    if is_fin_event and km_digitado > 0:
                        is_desp = any(w in texto_limpo for w in ['gastei', 'paguei', 'despesa', 'combustiv', 'gasolin', 'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado'])
                        cat_event = 'combustivel' if any(c in texto_limpo for c in ['gasolin', 'posto', 'combustiv', 'etanol', 'recarga', 'kwh', 'tomada']) else 'geral'
                        tipo_event = 'despesa' if is_desp else 'receita'
                        await TransacaoService.registrar_transacao(motorista_id, tipo_event, cat_event, km_digitado, texto_bruto, wpp_msg_id)
                        resposta = f"✅  *R$ {km_digitado:.2f}*  guardado! Manda mais lançamentos ou responde  *Confirmar*  para fechar o DRE:"
                    else:
                        resposta = "Ok! Se quiser registrar algo, manda o valor com uma descrição (ex:  *ganhei 150* ) ou responde  *Confirmar*  para fechar:"
                    await enviar_whatsapp(remote_jid, resposta)
                return

            # 3.2. Estados de Odômetro Pendente
            if km_digitado > 100:
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if not veiculo:
                    await enviar_whatsapp(remote_jid, "Não encontrei um veículo ativo no seu cadastro. Fala com o suporte! 🙏")
                    return

                if estado_turno == "AGUARDANDO_KM_INICIAL":
                    res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_digitado)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                        resposta = _montar_resposta_abertura_turno(motorista["nome"], km_digitado, res)
                    else:
                        tipo_erro = res.get("tipo_erro", "")
                        if tipo_erro == "TURNO_JA_ATIVO":
                            await RedisFSMService.limpar_buffer(fsm_turno_key)
                            await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                            resposta = res['erro']
                        elif tipo_erro == "GAP_ODOMETRO_ELEVADO":
                            # Gap suspeito: mantém estado para nova tentativa mas não conta como erro
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                            await enviar_whatsapp(remote_jid, res['erro'])
                            return
                        else:
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                            await registrar_erro_e_verificar_escape(remote_jid, tenant_id, fsm_turno_key, res['erro'])
                            return
                    await enviar_whatsapp(remote_jid, resposta)

                elif estado_turno == "AGUARDANDO_KM_FINAL":
                    async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                        active_turno_row = await conn.fetchrow("SELECT id FROM public.turnos WHERE motorista_id = $1::uuid AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;", motorista_id)
                        if active_turno_row:
                            total_tx = await TurnoService.verificar_transacoes_turno(motorista_id)
                            if total_tx == 0:
                                await RedisFSMService.definir_estado(fsm_turno_key, f"AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO|km:{km_digitado}")
                                # Auditoria: registra o acionamento da trava para rastreio de padrões
                                await RedisFSMService.registrar_audit_trava_zero(tenant_id, km_digitado)
                                logger.warning(f"[TRAVA_ZERO] Motorista {motorista_id} tentou fechar turno sem lançamentos (km={km_digitado})")
                                resposta = (
                                    "⚠  *Ei, espera!*  Não achei nenhum lançamento neste turno.\n\n"
                                    "Tem certeza que o faturamento de hoje foi  *R$ 0,00* ?\n\n"
                                    "Se sim, manda  *Confirmar* . Se não, manda o valor de uma corrida ou gasto antes de fechar."
                                )
                                await enviar_whatsapp(remote_jid, resposta)
                                return
                    res = await TurnoService.fechar_turno_com_dre(motorista_id, km_digitado)
                    if res["sucesso"]:
                        await RedisFSMService.limpar_buffer(fsm_turno_key)
                        await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                        resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res)
                    else:
                        tipo_erro = res.get("tipo_erro", "")
                        if tipo_erro != "ODOMETRO_DIVERGENTE":
                            await RedisFSMService.limpar_buffer(fsm_turno_key)
                            await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                            resposta = res['erro']
                        else:
                            await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                            await registrar_erro_e_verificar_escape(remote_jid, tenant_id, fsm_turno_key, res['erro'])
                            return
                    await enviar_whatsapp(remote_jid, resposta)
                return
            else:
                await registrar_erro_e_verificar_escape(remote_jid, tenant_id, fsm_turno_key, "Preciso do número do odômetro do painel. 😊 (ex:  *45230* )")
                return

        # =========================================================================
        # 4. GESTÃO OPERACIONAL DE TURNO DIRETA
        # =========================================================================
        if tem_intencao_inicio:
            if km_encontrado:
                veiculo = await DatabaseService.buscar_veiculo_ativo_do_motorista(motorista_id)
                if not veiculo:
                    await enviar_whatsapp(remote_jid, "Não encontrei um veículo ativo no seu cadastro. Fala com o suporte! 🙏")
                    return
                await enviar_whatsapp(remote_jid, "⏳ Abrindo turno...")
                res = await TurnoService.abrir_turno(motorista_id, str(veiculo["id"]), km_encontrado)
                if res["sucesso"]:
                    await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                    resposta = _montar_resposta_abertura_turno(motorista["nome"], km_encontrado, res)
                else:
                    tipo_erro = res.get("tipo_erro", "")
                    if tipo_erro == "GAP_ODOMETRO_ELEVADO":
                        await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                        await enviar_whatsapp(remote_jid, res['erro'])
                        return
                    if tipo_erro != "TURNO_JA_ATIVO":
                        await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                    resposta = res['erro']
                await enviar_whatsapp(remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_INICIAL")
                await enviar_whatsapp(remote_jid, "🟢 Qual o km do painel agora? (Ex:  *45230* )")
            return

        if tem_intencao_fim:
            if km_encontrado:
                async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                    turno_row = await conn.fetchrow(
                        "SELECT id, data_inicio FROM public.turnos WHERE motorista_id = $1::uuid "
                        "AND status IN ('ABERTO', 'em_andamento', 'em_pausa') ORDER BY data_inicio DESC LIMIT 1;",
                        motorista_id
                    )
                    if turno_row:
                        total_tx = await TurnoService.verificar_transacoes_turno(motorista_id)
                        if total_tx == 0:
                            await RedisFSMService.definir_estado(fsm_turno_key, f"AGUARDANDO_CONFIRMACAO_ZERO_TRANSACAO|km:{km_encontrado}")
                            await RedisFSMService.registrar_audit_trava_zero(tenant_id, km_encontrado)
                            logger.warning(f"[TRAVA_ZERO] Motorista {motorista_id} tentou fechar turno sem lançamentos (km={km_encontrado})")
                            await enviar_whatsapp(remote_jid, (
                                "⚠  *Ei, espera!*  Não achei nenhum lançamento neste turno.\n\n"
                                "Tem certeza que o faturamento de hoje foi  *R$ 0,00* ?\n\n"
                                "Se sim, manda  *Confirmar* . Se não, manda o valor de uma corrida ou gasto antes de fechar."
                            ))
                            return
                        # Pausa declarativa: jornadas ≥ 6h sem pausas registradas
                        from datetime import timezone
                        dt_inicio_turno = turno_row["data_inicio"]
                        agora_utc = datetime.now(timezone.utc)
                        if dt_inicio_turno.tzinfo is None:
                            dt_inicio_turno = dt_inicio_turno.replace(tzinfo=timezone.utc)
                        duracao_h = (agora_utc - dt_inicio_turno).total_seconds() / 3600
                        pausas_count = await conn.fetchval(
                            "SELECT COUNT(*) FROM public.pausas_turno WHERE turno_id = $1::uuid;",
                            str(turno_row["id"])
                        )
                        if duracao_h >= 6 and (pausas_count or 0) == 0:
                            await RedisFSMService.definir_estado(
                                fsm_turno_key, f"AGUARDANDO_DECLARACAO_PAUSA|km:{km_encontrado}"
                            )
                            await enviar_whatsapp(remote_jid, (
                                f"Boa jornada! 👏 Você trabalhou  *{duracao_h:.1f}h*  hoje.\n\n"
                                f"Fez alguma pausa para almoço ou descanso? Me diz o tempo total:\n"
                                f"_(Ex:  *1h30* ,  *45min* ,  *2h* — ou manda  *não*  se não parou)_"
                            ))
                            return
                await enviar_whatsapp(remote_jid, "📊 Gerando seu DRE...")
                res = await TurnoService.fechar_turno_com_dre(motorista_id, km_encontrado)
                resposta = formatar_relatorio_fechamento_dre(motorista["nome"], res) if res["sucesso"] else res['erro']
                await enviar_whatsapp(remote_jid, resposta)
            else:
                await RedisFSMService.definir_estado(fsm_turno_key, "AGUARDANDO_KM_FINAL")
                await enviar_whatsapp(remote_jid, "🏁 Qual o km final no painel agora?")
            return

        if tem_intencao_pausa:
            km_pausa = km_encontrado if km_encontrado and km_encontrado > 100 else None
            res = await TurnoService.pausar_turno(motorista_id, km_pausa=km_pausa)
            # Se o km extraído estava fora do envelope do turno, descarta-o e pausa sem km.
            # Avisa o motorista explicitamente — silenciar seria pior (ele perderia telemetria
            # sem saber, acreditando que o odômetro foi gravado).
            aviso_km_descartado = ""
            if not res["sucesso"] and res.get("tipo_erro") == "KM_PAUSA_INVALIDO":
                logger.warning(f"[pausar_turno] km={km_pausa} fora do envelope — descartado (motorista={motorista_id})")
                # Só avisa se o número veio de mensagem com intenção clara (único candidato).
                # Mensagens com vários números (ex: "na BR 101 e SP 270 pausando")
                # geram falsos positivos silenciosos — o aviso confundiria mais do que ajudaria.
                if _km_intencao_explicita:
                    aviso_km_descartado = (
                        f"\n\n⚠ _O odômetro  *{int(km_pausa):,}*  informado é menor que o km de abertura do turno "
                        f"e foi ignorado. Se quiser registrar o km de pausa, envie  *'pausar NNNNN'*  com o valor correto._"
                    ).replace(",", ".")
                res = await TurnoService.pausar_turno(motorista_id, km_pausa=None)
            if res["sucesso"]:
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                extra = "  _Km anotado para auditoria._" if res.get("km_pausa_registrado") else ""
                resposta = f"⏸ Turno pausado! Descansa bem. Quando voltar, manda  *retomar* .{extra}{aviso_km_descartado}"
            else:
                resposta = f"⚠ {res['erro']}"
            await enviar_whatsapp(remote_jid, resposta)
            return

        if tem_intencao_retomada:
            km_retomada = km_encontrado if km_encontrado and km_encontrado > 100 else None
            res = await TurnoService.retomar_turno(motorista_id, km_retomada=km_retomada)
            # Se o km extraído estava fora do envelope do turno, descarta-o e retoma sem km.
            # Avisa o motorista explicitamente — telemetria de uso pessoal intra-turno depende
            # desse valor; silenciar faria o gap aparecer apenas no fechamento sem aviso prévio.
            aviso_km_descartado = ""
            if not res["sucesso"] and res.get("tipo_erro") == "KM_RETOMADA_INVALIDO":
                logger.warning(f"[retomar_turno] km={km_retomada} fora do envelope — descartado (motorista={motorista_id})")
                if _km_intencao_explicita:
                    aviso_km_descartado = (
                        f"\n\n⚠ _O km  *{int(km_retomada):,}*  informado é menor que o km de abertura — ignorei. "
                        f"Se quiser registrar, manda  *retomar NNNNN*  com o valor correto._"
                    ).replace(",", ".")
                res = await TurnoService.retomar_turno(motorista_id, km_retomada=None)
            if res["sucesso"]:
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                custo_intra = res.get("custo_uso_pessoal_intra", 0.0)
                if custo_intra > 0:
                    detalhe = res.get("detalhe_uso_pessoal", "")
                    resposta = (
                        f"▶ Bem-vindo de volta! Turno retomado. 💪\n\n"
                        f"🛣️  *Km de uso pessoal na pausa auditado*\n"
                        f"• Custo debitado do cofre:  *R$ {custo_intra:.2f}*\n"
                        f"  _{detalhe}_\n"
                        f"_Seu lucro real continua protegido._{aviso_km_descartado}"
                    )
                else:
                    resposta = f"▶ Bem-vindo de volta! Turno retomado. 💪{aviso_km_descartado}"
            else:
                resposta = f"⚠ {res['erro']}"
            await enviar_whatsapp(remote_jid, resposta)
            return

        # =========================================================================
        # 5. LANÇAMENTOS FINANCEIROS LIVRES (Fricção Zero com Trava de Palavra-Chave)
        # =========================================================================
        _PALAVRAS_ABASTECIMENTO = [
            'abastec', 'reabastec', 'gasolin', 'etanol', 'alcool', 'diesel',
            'gnv', 'recarga', 'kwh', 'tomada', 'posto', 'combustiv',
        ]
        palavras_chave_financeiras = [
            'recebi', 'ganhei', 'faturei', 'corrida', 'uber', '99', 'indrive', 'faturamento', 'ganho',
            'gastei', 'paguei', 'despesa', 'bala', 'lava', 'marmita', 'mercado', 'oleo', 'pneu', 'almoco',
        ] + _PALAVRAS_ABASTECIMENTO
        is_intencao_abastecimento = any(w in texto_limpo for w in _PALAVRAS_ABASTECIMENTO)
        is_financeiro = any(w in texto_limpo for w in palavras_chave_financeiras)
        valor_transacao = converter_para_float(texto_bruto)

        # ── ABASTECIMENTO: Fast-path (frase única com valor + preço) ──────────
        # Ex: "abasteci 150 a 5,85" → registra direto sem FSM
        if is_intencao_abastecimento and valor_transacao > 0:
            # Detecta o tipo de combustível na mensagem (elimina fallback 50/50 para Flex)
            tipo_comb_msg = _detectar_tipo_combustivel(texto_limpo)

            # Tenta extrair preço por litro na mesma mensagem
            # Padrão: "a X,XX", "por X,XX", "X,XX/l", "X,XX o litro"
            preco_match = re.search(
                r'(?:a|por|@)\s*([\d]+[.,][\d]{2,3})|'
                r'([\d]+[.,][\d]{2,3})\s*(?:o litro|/l\b|por litro)',
                texto_bruto, re.IGNORECASE
            )
            preco_unitario: Optional[float] = None
            if preco_match:
                raw_preco = preco_match.group(1) or preco_match.group(2)
                preco_unitario = converter_para_float(raw_preco) if raw_preco else None

            if preco_unitario and preco_unitario > 0:
                # Fast-path completo: registra com telemetria (odômetro e tanque_cheio
                # omitidos — usuário pode refinar em outro abastecimento guiado)
                litros = round(valor_transacao / preco_unitario, 3)
                res_tx = await TransacaoService.registrar_transacao(
                    motorista_id=motorista_id,
                    tipo_movimentacao='despesa',
                    categoria='combustivel',
                    valor=valor_transacao,
                    descricao=texto_bruto,
                    wpp_msg_id=wpp_msg_id,
                    litros_abastecidos=litros,
                    preco_por_litro=preco_unitario,
                    tipo_combustivel_abastecido=tipo_comb_msg,
                )
                if res_tx.get("status") == "success":
                    await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                    valor_fmt  = f"R$ {valor_transacao:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                    litros_fmt = f"{litros:.2f}".replace(".", ",")
                    preco_fmt  = f"R$ {preco_unitario:.3f}".replace(".", ",")
                    resposta = (
                        f"⛽  *Abastecimento Registrado!*\n\n"
                        f"• Valor:  *{valor_fmt}*\n"
                        f"• Volume:  *{litros_fmt} L*\n"
                        f"• Preço/L:  *{preco_fmt}*\n\n"
                        f"🛡 Estoque e cofre atualizados!\n"
                        f"_💡 Dica: manda  *abastecer*  antes de registrar para informar também o km do painel. "
                        f"Com o odômetro, eu calibo seu consumo real automaticamente._"
                    )
                elif res_tx.get("status") == "duplicate":
                    resposta = "⚠ Este lançamento já foi guardado anteriormente no cofre contábil."
                else:
                    resposta = f"❌ Falha ao registrar abastecimento:\n_{res_tx.get('message')}_"
                await enviar_whatsapp(remote_jid, resposta)
                # Sugestão de recalibração em mensagem separada (fast-path não tem odômetro,
                # então sugestao_recalibracao será sempre None aqui — guarda por coerência)
                sugestao = res_tx.get("sugestao_recalibracao") if res_tx.get("status") == "success" else None
                if sugestao:
                    await enviar_whatsapp(remote_jid, _formatar_sugestao_recalibracao(sugestao))
                return

            # Tem valor mas falta preço → entra no passo PRECO da FSM
            # Propaga o tipo de combustível detectado para evitar fallback 50/50 ao final
            desc_abt = texto_bruto[:120]
            comb_abt = tipo_comb_msg or ""
            novo_estado = f"ABASTECIMENTO_PRECO|total:{valor_transacao}|desc:{desc_abt}|comb:{comb_abt}"
            await RedisFSMService.definir_estado(fsm_turno_key, novo_estado, ex_seconds=_ABT_TTL)
            valor_fmt = f"R$ {valor_transacao:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            await enviar_whatsapp(
                remote_jid,
                f"⛽  *{valor_fmt}*  anotado! Qual foi o  *preço por litro* ? (Ex:  *5,85* )"
            )
            return

        # ── ABASTECIMENTO: Intenção sem valor → FSM coleta valor primeiro ─────
        if is_intencao_abastecimento and valor_transacao == 0:
            desc_abt = texto_bruto[:120]
            # Captura o tipo de combustível já na mensagem inicial (ex: "vou abastecer etanol")
            comb_abt = _detectar_tipo_combustivel(texto_limpo) or ""
            await RedisFSMService.definir_estado(
                fsm_turno_key,
                f"ABASTECIMENTO_VALOR|desc:{desc_abt}|comb:{comb_abt}",
                ex_seconds=_ABT_TTL
            )
            await enviar_whatsapp(
                remote_jid,
                "⛽ Beleza! Qual foi o  *valor total*  gasto no abastecimento? (Ex:  *150,00* )"
            )
            return

        # ── LANÇAMENTOS FINANCEIROS GERAIS (não-combustível) ──────────────────
        if is_financeiro and valor_transacao > 0:
            is_despesa = any(
                w in texto_limpo for w in [
                    'gastei', 'paguei', 'despesa', 'abastec', 'reabastec', 'combustiv',
                    'gasolin', 'etanol', 'gnv', 'diesel', 'alcool', 'recarga', 'kwh', 'tomada',
                    'posto', 'almoco', 'bala', 'lava', 'marmita', 'mercado', 'oleo', 'pneu',
                ]
            )
            tipo = 'despesa' if is_despesa else 'receita'

            # ── Categoria de DESPESA ──────────────────────────────────────────
            if any(c in texto_limpo for c in ['gasolin', 'etanol', 'gnv', 'diesel', 'alcool', 'reabastec', 'abastec', 'recarga', 'posto', 'solar', 'kwh', 'tomada', 'combustiv']):
                cat = 'combustivel'
            elif any(c in texto_limpo for c in ['almoco', 'marmita', 'lanche', 'refeicao', 'comida', 'rango']):
                cat = 'alimentacao'
            elif any(c in texto_limpo for c in ['lava', 'oleo', 'pneu', 'oficina', 'mecanico', 'manutencao']):
                cat = 'manutencao'
            # ── Categoria de RECEITA ──────────────────────────────────────────
            # '99' requer word boundary — "ganhei 99" vs "ganhei 199" são contextos distintos
            elif tipo == 'receita' and (
                any(c in texto_limpo for c in ['corrida', 'uber', 'indrive', 'passageiro', 'viagem', 'trip'])
                or bool(re.search(r'\b99\b', texto_limpo))
            ):
                cat = 'corrida'
            elif tipo == 'receita' and any(c in texto_limpo for c in ['gorjeta', 'gorjet', 'tip']):
                cat = 'gorjeta'
            elif tipo == 'receita' and any(c in texto_limpo for c in ['bico', 'freela', 'freelance', 'extra', 'servico', 'fiz um']):
                cat = 'bico'
            elif tipo == 'receita' and any(c in texto_limpo for c in ['salario', 'salário', 'clt', 'pj', 'pagamento', 'bonus', 'bônus', 'comissao', 'comissão', 'renda', 'entrada']):
                cat = 'outras_receitas'
            else:
                cat = 'corrida' if tipo == 'receita' else 'geral'

            res_tx = await TransacaoService.registrar_transacao(
                motorista_id=motorista_id, tipo_movimentacao=tipo, categoria=cat,
                valor=valor_transacao, descricao=texto_bruto, wpp_msg_id=wpp_msg_id,
            )
            if res_tx.get("status") == "success":
                await RedisFSMService.limpar_erros_consecutivos(tenant_id)
                valor_fmt_geral = f"R$ {valor_transacao:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                # Confirmação explícita do tipo para eliminar dúvida se foi debitado ou creditado.
                _cat_label = {
                    "combustivel": "combustível", "alimentacao": "alimentação",
                    "manutencao": "manutenção", "corrida": "corrida", "geral": "despesa geral",
                    "gorjeta": "gorjeta", "bico": "bico/freela", "outras_receitas": "outras receitas",
                }.get(cat, cat)
                if tipo == "despesa":
                    resposta = f"✅  *{valor_fmt_geral}*  registrado como  *despesa*  ({_cat_label})! 🛡"
                else:
                    resposta = f"✅  *{valor_fmt_geral}*  registrado como  *receita*  ({_cat_label})! 🛡"

                # ── AUTO-RESUME ──────────────────────────────────────────────────────────
                # Se o turno está em pausa e o motorista acabou de registrar uma RECEITA,
                # retomamos automaticamente — o motorista voltou a trabalhar sem avisar.
                # Isso evita que horas produtivas sejam contadas como pausa no DRE.
                if is_intencao_receita and tipo == "receita":
                    try:
                        async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                            turno_pausado = await conn.fetchval(
                                "SELECT id FROM public.turnos WHERE motorista_id = $1::uuid "
                                "AND status = 'em_pausa' ORDER BY data_inicio DESC LIMIT 1;",
                                motorista_id
                            )
                        if turno_pausado:
                            res_retomada = await TurnoService.retomar_turno(motorista_id)
                            if res_retomada.get("sucesso"):
                                resposta += "\n\n▶ Turno reativado automaticamente — parece que você voltou a trabalhar! 🚀"
                                logger.info(f"[AUTO_RESUME] Turno retomado automaticamente na receita (motorista={motorista_id})")
                            else:
                                logger.warning(f"[AUTO_RESUME] Falha ao retomar turno: {res_retomada.get('erro')} (motorista={motorista_id})")
                    except Exception as _e:
                        logger.error(f"[AUTO_RESUME] Erro ao verificar pausa: {_e}")
                # ─────────────────────────────────────────────────────────────────────────
            else:
                if res_tx.get("status") == "duplicate":
                    resposta = "Esse lançamento já estava guardado no cofre. 👍"
                else:
                    resposta = f"❌ Não consegui guardar:\n_{res_tx.get('message')}_"
            await enviar_whatsapp(remote_jid, resposta)
            return

        # =========================================================================
        # 6. CATCH-ALL (Ajuda Contextual FSM-aware)
        # =========================================================================
        # Reutiliza o mesmo método do bloco de ajuda explícita.
        # O estado_turno já foi lido acima (linha ~740); só precisamos
        # resolver o status do DB quando a FSM Redis está vazia.
        estado_catch = estado_turno
        if not estado_catch:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                turno_catch_row = await conn.fetchrow(
                    "SELECT status FROM public.turnos WHERE motorista_id = $1::uuid "
                    "AND status IN ('ABERTO', 'em_andamento', 'em_pausa') "
                    "ORDER BY data_inicio DESC LIMIT 1;",
                    motorista_id,
                )
            estado_catch = turno_catch_row["status"] if turno_catch_row else None
        resposta_catch = "Hmm, não entendi bem. 😅\n\n" + HelpService.obter_ajuda_contextual(estado_catch)
        await enviar_whatsapp(remote_jid, resposta_catch)
