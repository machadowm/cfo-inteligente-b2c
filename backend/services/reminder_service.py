"""
ReminderService — Lembretes proativos de turno e automação de caixa via WhatsApp.

Executa rotinas periódicas sem dependências externas (asyncio nativo):

  • PAUSA_PROLONGADA    : turno em 'em_pausa' há > 90 min sem interação recente.
  • TURNO_ZUMBI        : turno 'em_andamento' aberto há > 12 h sem interação recente.
  • VENCIMENTOS        : notifica sobre despesas que vencem hoje/amanhã com UX
                         adaptativa ao saldo real da caixinha de provisão.
  • BAIXAS_AUTOMATICAS : executa baixa automática de caixas 1 dia após o vencimento.

Idempotência: cada evento grava uma chave Redis com TTL, evitando spam em múltiplos
workers ou reinicializações.

Bypass de interação: se o motorista enviou qualquer mensagem nos últimos 15 minutos
(detectado pela chave last_seen: no Redis), os lembretes de turno são suprimidos.

Calendário imune a timezone drift: todos os cálculos de data e de teto de mês
(último dia do mês, clamp de dia 29/30/31) são realizados em Python com
pytz/America/Sao_Paulo e injetados como parâmetros escalares na query SQL.
Isso evita que funções nativas do PostgreSQL (DATE_TRUNC, CURRENT_DATE) operem
em UTC e produzam resultados divergentes entre 21h e 23h59 no fuso de Brasília.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP

import httpx
import pytz

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService

logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://cfo_evolution:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")

# ── Configuração de limites ───────────────────────────────────────────────────
_PAUSA_LIMITE_MIN       = 90            # minutos em pausa antes de lembrar
_ZUMBI_LIMITE_H         = 12            # horas de turno aberto antes de lembrar
_BYPASS_INTERACAO_MIN   = 15            # ignora lembrete se motorista interagiu neste intervalo
_TTL_REMINDER_PAUSA_S   = 60 * 60       # reenvio mínimo: 1 h
_TTL_REMINDER_ZUMBI_S   = 60 * 120      # reenvio mínimo: 2 h
_TTL_REMINDER_VENC_S    = 60 * 60 * 20  # vencimento: reenvio mínimo 20 h (1× por dia)
_TTL_BAIXA_AUTO_S       = 60 * 60 * 24 * 32  # baixa automática: idempotência de ~1 mês
_INTERVALO_LOOP_S       = 300           # varredura a cada 5 min


async def _enviar(remote_jid: str, texto: str) -> None:
    """Envia mensagem via Evolution API — idêntico ao enviar_whatsapp do orchestrator."""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{EVOLUTION_API_URL}/message/sendText/cfo_bot",
                headers={"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"},
                json={"number": remote_jid, "text": texto},
                timeout=10.0,
            )
            r.raise_for_status()
    except Exception as e:
        logger.error(f"[ReminderService] Falha ao enviar para {remote_jid}: {e}")


async def _ja_foi_lembrado(tenant_id: str, tipo: str) -> bool:
    """Retorna True se um lembrete deste tipo já foi enviado dentro do TTL."""
    client = await RedisFSMService.get_client()
    return await client.exists(f"reminder:{tipo}:{tenant_id}") == 1


async def _marcar_lembrado(tenant_id: str, tipo: str, ttl: int) -> None:
    """Grava chave de idempotência com TTL."""
    client = await RedisFSMService.get_client()
    await client.set(f"reminder:{tipo}:{tenant_id}", "1", ex=ttl)


async def _interagiu_recentemente(tenant_id: str) -> bool:
    """Verifica se existe chave last_seen: ativa (motorista interagiu recentemente)."""
    client = await RedisFSMService.get_client()
    return await client.exists(f"last_seen:{tenant_id}") == 1


async def _processar_pausas_prolongadas() -> None:
    """Verifica todos os turnos em pausa e dispara lembrete se necessário."""
    try:
        async with DatabaseService.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT t.motorista_id::text, m.telefone,
                       EXTRACT(EPOCH FROM (NOW() - MAX(p.inicio_pausa))) / 60 AS minutos_pausa
                FROM public.turnos t
                JOIN public.motoristas m ON m.id = t.motorista_id
                JOIN public.pausas_turno p ON p.turno_id = t.id AND p.fim_pausa IS NULL
                WHERE t.status = 'em_pausa'
                GROUP BY t.motorista_id, m.telefone
                HAVING EXTRACT(EPOCH FROM (NOW() - MAX(p.inicio_pausa))) / 60 > $1;
                """,
                _PAUSA_LIMITE_MIN,
            )
    except Exception as e:
        logger.error(f"[ReminderService] Erro ao buscar pausas prolongadas: {e}")
        return

    for row in rows:
        tenant_id    = row["telefone"]
        motorista_id = row["motorista_id"]
        minutos      = int(row["minutos_pausa"])
        remote_jid   = f"{tenant_id}@s.whatsapp.net"

        if await _interagiu_recentemente(tenant_id):
            continue
        if await _ja_foi_lembrado(tenant_id, "pausa"):
            continue

        horas     = minutos // 60
        mins      = minutos % 60
        tempo_str = f"{horas}h{mins:02d}min" if horas else f"{mins}min"

        texto = (
            f"⏸ Seu turno está em pausa há  *{tempo_str}* .\n\n"
            f"Se já voltou a trabalhar, envie  *'retomar'*  para que o seu "
            f"faturamento por hora seja calculado corretamente no DRE!"
        )
        await _enviar(remote_jid, texto)
        await _marcar_lembrado(tenant_id, "pausa", _TTL_REMINDER_PAUSA_S)
        logger.info(
            f"[ReminderService] Lembrete PAUSA enviado: motorista={motorista_id} "
            f"pausa={tempo_str}"
        )


async def _processar_turnos_zumbi() -> None:
    """Verifica turnos em andamento há mais de 12 h e dispara alerta de fechamento."""
    try:
        async with DatabaseService.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT t.motorista_id::text, m.telefone,
                       EXTRACT(EPOCH FROM (NOW() - t.data_inicio)) / 3600 AS horas_abertas
                FROM public.turnos t
                JOIN public.motoristas m ON m.id = t.motorista_id
                WHERE t.status IN ('em_andamento', 'ABERTO')
                  AND t.data_inicio < NOW() - ($1 * INTERVAL '1 hour');
                """,
                _ZUMBI_LIMITE_H,
            )
    except Exception as e:
        logger.error(f"[ReminderService] Erro ao buscar turnos zumbi: {e}")
        return

    for row in rows:
        tenant_id    = row["telefone"]
        motorista_id = row["motorista_id"]
        horas        = round(float(row["horas_abertas"]), 1)
        remote_jid   = f"{tenant_id}@s.whatsapp.net"

        if await _interagiu_recentemente(tenant_id):
            continue
        if await _ja_foi_lembrado(tenant_id, "zumbi"):
            continue

        texto = (
            f"⏰ Seu turno está aberto há  *{horas:.0f}h* .\n\n"
            f"Se terminou o dia, envie  *'fechar'*  com o odômetro atual para gerar "
            f"o seu DRE e garantir que o Lucro Real seja apurado corretamente."
        )
        await _enviar(remote_jid, texto)
        await _marcar_lembrado(tenant_id, "zumbi", _TTL_REMINDER_ZUMBI_S)
        logger.info(
            f"[ReminderService] Lembrete ZUMBI enviado: motorista={motorista_id} "
            f"horas_abertas={horas}"
        )


async def _processar_vencimentos_despesas() -> None:
    """Avisa motoristas sobre despesas fixas que vencem hoje ou amanhã.

    Suporta múltiplos vencimentos por despesa (dias_vencimento INTEGER[]).
    Cada elemento do array gera uma notificação independente, controlada por
    chave Redis com TTL de 20 h: reminder:venc:{tenant}:{despesa_id}:{dia}:{data}.

    Calendário calculado integralmente em Python (pytz/America/Sao_Paulo) e
    injetado na query como parâmetros escalares, eliminando a dependência de
    DATE_TRUNC/CURRENT_DATE no Postgres e o risco de timezone drift.

    UX adaptativa: lê o saldo real da caixinha vinculada e ramifica a mensagem
    em três estados — saldo suficiente, saldo parcial e caixinha zerada — para
    evitar que o sistema sugira retiradas impossíveis ao motorista.
    """
    _tz = pytz.timezone("America/Sao_Paulo")
    hoje  = datetime.now(_tz).date()
    amanha = hoje + timedelta(days=1)

    # Último dia do mês corrente calculado em Python — usado como teto de clamp
    # para despesas com vencimento nos dias 29, 30 ou 31 (ex: fev. só tem 28/29)
    ultimo_dia_mes = (hoje.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    # Dias de interesse: usamos os valores brutos (não clamped) aqui porque o
    # LEAST no SQL já aplica o clamp ao comparar venc.dia.  Se usássemos os valores
    # clamped no set Python, poderíamos descartar despesas com dia_vencimento > ultimo_dia
    # que deveriam ser tratadas como vencendo no último dia do mês.
    dias_alvo = {hoje.day, amanha.day}

    try:
        async with DatabaseService.get_connection() as conn:
            # $1 = lista de dias alvo (hoje e amanhã, valores brutos)
            # $2 = último dia do mês calculado em Python (teto do LEAST)
            # LEFT JOIN com caixas_provisao para ler o saldo real e calibrar UX
            rows = await conn.fetch(
                """
                SELECT dfm.id::text                    AS despesa_id,
                       dfm.nome,
                       dfm.valor_mensal,
                       array_length(dfm.dias_vencimento, 1) AS qtd_vencimentos,
                       venc.dia                        AS dia_vencimento,
                       m.id::text                      AS motorista_id,
                       m.telefone,
                       COALESCE(cp.saldo_atual, 0)     AS saldo_atual
                FROM public.despesas_fixas_mensais dfm
                JOIN public.motoristas m ON m.id = dfm.motorista_id
                -- Junta com a caixinha vinculada para leitura de saldo (pode ser NULL)
                LEFT JOIN public.caixas_provisao cp ON cp.id = dfm.caixa_id
                -- Expande cada elemento do array em linha separada
                JOIN LATERAL UNNEST(dfm.dias_vencimento) AS venc(dia) ON TRUE
                WHERE dfm.ativo = TRUE
                  AND LEAST(venc.dia, $2::int) = ANY($1::int[]);
                """,
                list(dias_alvo),
                int(ultimo_dia_mes.day),
            )
    except Exception as e:
        logger.error(f"[ReminderService] Erro ao buscar vencimentos: {e}")
        return

    client = await RedisFSMService.get_client()
    for row in rows:
        tenant_id    = row["telefone"]
        dia_venc     = int(row["dia_vencimento"])
        nome         = row["nome"]
        valor_mensal = Decimal(str(row["valor_mensal"]))
        saldo_atual  = Decimal(str(row["saldo_atual"]))
        despesa_id   = row["despesa_id"]
        remote_jid   = f"{tenant_id}@s.whatsapp.net"

        # Valor da parcela deste vencimento específico:
        # Se a despesa tem N vencimentos/mês, cada parcela = valor_mensal / N.
        # Ex: cartão R$ 800 com venc. dias 5 e 20 → cada parcela = R$ 400.
        # Ex: empréstimo R$ 650 pago por 13 dias → cada parcela = R$ 50/dia.
        # Se há apenas 1 vencimento, valor_parcela == valor_mensal (sem divisão).
        qtd_venc      = max(1, int(row["qtd_vencimentos"] or 1))
        valor_parcela = (valor_mensal / Decimal(str(qtd_venc))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        eh_multiplo   = qtd_venc > 1

        # Chave inclui dia_vencimento para disparar independentemente por vencimento
        redis_key = f"reminder:venc:{tenant_id}:{despesa_id}:{dia_venc}:{hoje.isoformat()}"
        if await client.exists(redis_key):
            continue  # já notificado hoje para este vencimento

        valor_fmt   = f"R$ {float(valor_mensal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        parcela_fmt = f"R$ {float(valor_parcela):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        saldo_fmt   = f"R$ {float(saldo_atual):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

        # Linha de contexto de parcela — formulada de forma legível para qualquer N:
        # ≤6 vencimentos: "Parcela (2× por mês): R$ 400,00"
        # ≥7 vencimentos (regime diário/semanal): "Pagamento diário: R$ 50,00"
        if not eh_multiplo:
            linha_parcela = ""
        elif qtd_venc <= 6:
            linha_parcela = f"• Parcela ({qtd_venc}× por mês):  *{parcela_fmt}*\n"
        else:
            linha_parcela = f"• Valor por pagamento ({qtd_venc} parcelas/mês):  *{parcela_fmt}*\n"

        # Valor efetivo a cobrar neste vencimento
        valor_cobrar = valor_parcela

        # Resolve se o vencimento clamped corresponde a hoje ou amanhã.
        # dia_venc pode ser 31 num mês de 30 dias — o LEAST no SQL já retornou 30,
        # então comparamos o dia clamped (não o raw) com hoje.day / amanha.day.
        dia_venc_efetivo = min(dia_venc, ultimo_dia_mes.day)
        is_hoje = dia_venc_efetivo == hoje.day

        # Data de vencimento formatada em BR para exibição nas mensagens
        data_venc_obj = hoje if is_hoje else amanha
        data_venc_fmt = data_venc_obj.strftime("%d/%m/%Y")

        # ── UX Adaptativa — 3 estados baseados no saldo real da caixinha ──────
        if is_hoje:
            if saldo_atual >= valor_cobrar:
                # Caso A: saldo cobre a parcela — oferece o comando de retirada
                texto = (
                    f"📅  *Vencimento HOJE ({data_venc_fmt}) — {nome}*\n\n"
                    f"• Valor mensal:  *{valor_fmt}*\n"
                    + linha_parcela +
                    f"• Saldo na caixinha:  *{saldo_fmt}*  ✅\n\n"
                    f"Tudo provisionado! Para registrar a saída:\n"
                    f"  👉  `!retirar caixa {nome} {float(valor_cobrar):.2f}`\n\n"
                    f"_Se não retirar agora, a baixa automática executa na virada do dia._"
                )
            elif saldo_atual > Decimal("0"):
                # Caso B: saldo parcial — aponta déficit e oferece retirada do disponível
                faltando     = (valor_cobrar - saldo_atual).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                faltando_fmt = f"R$ {float(faltando):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                texto = (
                    f"⚠️  *Vencimento HOJE ({data_venc_fmt}) — {nome}*\n\n"
                    f"• Valor mensal:  *{valor_fmt}*\n"
                    + linha_parcela +
                    f"• Saldo na caixinha:  *{saldo_fmt}*  (parcial 🚨)\n"
                    f"• Déficit:  *{faltando_fmt}*\n\n"
                    f"O saldo provisionado não cobre a parcela. Complemente o pagamento externamente.\n"
                    f"Para resgatar o saldo disponível:\n"
                    f"  👉  `!retirar caixa {nome} {float(saldo_atual):.2f}`"
                )
            else:
                # Caso C: caixinha zerada — apenas alerta
                texto = (
                    f"🚨  *Vencimento HOJE ({data_venc_fmt}) — {nome}*\n\n"
                    f"• Valor mensal:  *{valor_fmt}*\n"
                    + linha_parcela +
                    f"• Saldo na caixinha:  *R$ 0,00*  ❌\n\n"
                    f"Não há reserva para cobrir esta parcela.\n"
                    f"Realize o pagamento com recursos externos."
                )
        else:
            # Vencimento amanhã
            if saldo_atual >= valor_cobrar:
                texto = (
                    f"⏰  *Vencimento AMANHÃ ({data_venc_fmt}) — {nome}*\n\n"
                    f"• Valor mensal:  *{valor_fmt}*\n"
                    + linha_parcela +
                    f"• Saldo na caixinha:  *{saldo_fmt}*  ✅\n\n"
                    f"Provisionamento em dia! Amanhã lembrarei você de fazer a retirada."
                )
            elif saldo_atual > Decimal("0"):
                faltando     = (valor_cobrar - saldo_atual).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
                faltando_fmt = f"R$ {float(faltando):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
                texto = (
                    f"⏰  *Vencimento AMANHÃ ({data_venc_fmt}) — {nome}*\n\n"
                    f"• Valor mensal:  *{valor_fmt}*\n"
                    + linha_parcela +
                    f"• Saldo na caixinha:  *{saldo_fmt}*  (parcial ⚠️)\n"
                    f"• Faltam:  *{faltando_fmt}*\n\n"
                    f"Complete a caixinha antes do fechamento do dia para cobertura total."
                )
            else:
                texto = (
                    f"⏰  *Vencimento AMANHÃ ({data_venc_fmt}) — {nome}*\n\n"
                    f"• Valor mensal:  *{valor_fmt}*\n"
                    + linha_parcela +
                    f"• Saldo na caixinha:  *R$ 0,00*  ❌\n\n"
                    f"Sem provisão para esta parcela. Prepare recursos externos para quitá-la amanhã.\n"
                    f"_Para ver o saldo de todas as caixas:  *!caixas*_"
                )

        await _enviar(remote_jid, texto)
        await client.set(redis_key, "1", ex=_TTL_REMINDER_VENC_S)
        logger.info(
            f"[ReminderService] Lembrete VENCIMENTO enviado: motorista={row['motorista_id']} "
            f"despesa={nome} dia_venc={dia_venc} (efetivo={dia_venc_efetivo}) hoje={hoje}"
        )


async def _processar_baixas_automaticas() -> None:
    """Executa a baixa automática nas caixas de provisão 1 dia após o vencimento.

    Suporta múltiplos vencimentos por despesa (dias_vencimento INTEGER[]).
    Cada elemento do array é processado independentemente — uma despesa com
    vencimentos nos dias 5 e 20 gera duas baixas por mês.

    A chave de idempotência inclui o dia do vencimento:
      baixa_auto:{motorista}:{despesa}:{dia_venc}:{ano-mês}

    Calendário calculado inteiramente em Python (ontem, último_dia_mes_ontem)
    e injetado como parâmetros escalares ($1, $2) para blindar a query de
    funções nativas temporais do Postgres sujeitas ao fuso do servidor.
    """
    _tz = pytz.timezone("America/Sao_Paulo")
    hoje  = datetime.now(_tz).date()
    ontem = hoje - timedelta(days=1)

    # Teto de calendário do mês de ontem (necessário quando ontem = último dia do mês)
    ultimo_dia_mes_ontem = (ontem.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)

    # Ano-mês de referência para a chave de idempotência (usa o mês de ontem)
    ano_mes_ref = ontem.strftime("%Y-%m")

    try:
        async with DatabaseService.get_connection() as conn:
            # $1 = último dia do mês de ontem (teto do LEAST — imune ao fuso do DB)
            # $2 = dia numérico de ontem (dia do vencimento a processar)
            rows = await conn.fetch(
                """
                SELECT dfm.id::text                    AS despesa_id,
                       dfm.nome,
                       dfm.valor_mensal,
                       array_length(dfm.dias_vencimento, 1) AS qtd_vencimentos,
                       venc.dia                        AS dia_vencimento,
                       dfm.caixa_id::text              AS caixa_id,
                       dfm.parcelas_totais,
                       dfm.parcelas_pagas,
                       dfm.valor_total,
                       dfm.frequencia_dias,
                       cp.saldo_atual,
                       m.id::text                      AS motorista_id,
                       m.telefone
                FROM public.despesas_fixas_mensais dfm
                JOIN public.motoristas m ON m.id = dfm.motorista_id
                JOIN public.caixas_provisao cp ON cp.id = dfm.caixa_id
                JOIN LATERAL UNNEST(dfm.dias_vencimento) AS venc(dia) ON TRUE
                WHERE dfm.ativo = TRUE
                  AND dfm.caixa_id IS NOT NULL
                  AND cp.saldo_atual > 0
                  AND (dfm.parcelas_totais IS NULL OR dfm.parcelas_pagas < dfm.parcelas_totais)
                  AND (dfm.data_inicio IS NULL OR dfm.data_inicio <= $3::date)
                  AND LEAST(venc.dia, $1::int) = $2::int;
                """,
                int(ultimo_dia_mes_ontem.day),
                int(ontem.day),
                ontem,
            )
    except Exception as e:
        logger.error(f"[ReminderService] Erro ao buscar baixas automáticas: {e}")
        return

    client = await RedisFSMService.get_client()

    for row in rows:
        tenant_id    = row["telefone"]
        motorista_id = row["motorista_id"]
        despesa_id   = row["despesa_id"]
        dia_venc     = int(row["dia_vencimento"])
        caixa_id     = row["caixa_id"]
        nome         = row["nome"]
        valor_mensal = Decimal(str(row["valor_mensal"]))
        saldo_atual  = Decimal(str(row["saldo_atual"]))
        remote_jid   = f"{tenant_id}@s.whatsapp.net"

        # Valor base da parcela deste vencimento específico (mesmo critério dos lembretes)
        qtd_venc           = max(1, int(row["qtd_vencimentos"] or 1))
        valor_parcela_base = (valor_mensal / Decimal(str(qtd_venc))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        # Absorção de resíduo de centavos na última parcela de parcelamento
        _pt = row.get("parcelas_totais")
        _pp = int(row.get("parcelas_pagas") or 0)
        _val_tot_raw = row.get("valor_total")
        if _pt is not None and (_pp + 1) >= _pt and _val_tot_raw:
            val_total_dec = Decimal(str(_val_tot_raw))
            # Resíduo é absorvido na última parcela para fechar 100% do contrato
            valor_parcela = (val_total_dec - (Decimal(str(_pt - 1)) * valor_parcela_base)).quantize(
                Decimal("0.01"), rounding=ROUND_HALF_UP
            )
            if valor_parcela <= 0:
                valor_parcela = valor_parcela_base
        else:
            valor_parcela = valor_parcela_base

        # Linha de contexto de parcela na notificação de baixa
        if qtd_venc <= 1:
            linha_parcela_baixa = ""
        elif qtd_venc <= 6:
            parcela_fmt_b = f"R$ {float(valor_parcela):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            linha_parcela_baixa = f"• Parcela ({qtd_venc}× por mês):  *{parcela_fmt_b}*\n"
        else:
            parcela_fmt_b = f"R$ {float(valor_parcela):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            linha_parcela_baixa = f"• Valor por pagamento ({qtd_venc} parcelas/mês):  *{parcela_fmt_b}*\n"

        # Chave de idempotência: 1 baixa por (motorista, despesa, dia_vencimento, ano-mês)
        # Inclui o dia para que vencimentos múltiplos da mesma despesa sejam independentes
        redis_key = f"baixa_auto:{motorista_id}:{despesa_id}:{dia_venc}:{ano_mes_ref}"
        if await client.exists(redis_key):
            continue  # já executado neste mês para este vencimento

        # Quanto retirar: mínimo entre saldo disponível e valor da parcela
        retirada = min(saldo_atual, valor_parcela).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                # Baixa atômica com checagem de saldo (guarda contra race condition)
                novo_saldo_row = await conn.fetchrow(
                    """
                    UPDATE public.caixas_provisao
                    SET saldo_atual = saldo_atual - $1
                    WHERE id = $2::uuid
                      AND motorista_id = $3::uuid
                      AND saldo_atual >= $1
                    RETURNING saldo_atual;
                    """,
                    retirada, caixa_id, motorista_id,
                )
                if not novo_saldo_row:
                    # Race condition: saldo mudou entre o SELECT e o UPDATE — ignora
                    logger.warning(
                        f"[ReminderService] Baixa automática abortada (saldo insuficiente no momento do UPDATE): "
                        f"motorista={motorista_id} despesa={nome}"
                    )
                    continue
                novo_saldo = Decimal(str(novo_saldo_row["saldo_atual"]))
        except Exception as e:
            logger.error(
                f"[ReminderService] Erro ao executar baixa automática: "
                f"motorista={motorista_id} despesa={nome}: {e}"
            )
            continue

        # Marca idempotência antes de enviar (mesmo que o envio falhe, não repete a baixa)
        await client.set(redis_key, "1", ex=_TTL_BAIXA_AUTO_S)

        # ── Incrementa parcelas_pagas e auto-desativa se exauriu ──────────
        _pt = row.get("parcelas_totais")
        _pp = int(row.get("parcelas_pagas") or 0)
        _nova_pp = _pp + 1
        _exaurida = _pt is not None and _nova_pp >= _pt
        try:
            async with DatabaseService.get_tenant_connection(motorista_id) as conn:
                if _exaurida:
                    # Última parcela paga — desativa a despesa e dispara limpeza da caixa
                    await conn.execute(
                        "UPDATE public.despesas_fixas_mensais "
                        "SET parcelas_pagas = $1, ativo = FALSE "
                        "WHERE id = $2::uuid AND motorista_id = $3::uuid;",
                        _nova_pp, despesa_id, motorista_id,
                    )
                    # Caixa vazia após a última baixa → exclui automaticamente
                    if novo_saldo <= Decimal("0.01") and caixa_id:
                        await conn.execute(
                            "DELETE FROM public.caixas_provisao "
                            "WHERE id = $1::uuid AND saldo_atual <= 0.01;",
                            caixa_id,
                        )
                    logger.info(
                        f"[ReminderService] Despesa exaurida e desativada: motorista={motorista_id} "
                        f"despesa={nome!r} parcelas={_nova_pp}/{_pt}"
                    )
                else:
                    await conn.execute(
                        "UPDATE public.despesas_fixas_mensais "
                        "SET parcelas_pagas = $1 "
                        "WHERE id = $2::uuid AND motorista_id = $3::uuid;",
                        _nova_pp, despesa_id, motorista_id,
                    )
        except Exception as _e:
            # Falha no incremento não deve impedir o envio da notificação
            logger.error(
                f"[ReminderService] Erro ao incrementar parcelas_pagas: "
                f"motorista={motorista_id} despesa={nome}: {_e}"
            )

        # Monta notificação
        valor_fmt      = f"R$ {float(valor_mensal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        retirada_fmt   = f"R$ {float(retirada):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        novo_saldo_fmt = f"R$ {float(novo_saldo):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        faltou         = (valor_parcela - retirada).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if faltou <= Decimal("0.01"):
            # Saldo cobriu 100% da parcela — quitado
            _tag_encerramento = (
                f"\n\n🏁  *Parcelamento concluído!* Todas as {_pt} parcelas foram quitadas. "
                f"A despesa foi desativada automaticamente."
                if _exaurida and _pt and _pt > 1 else
                f"\n\n🏁  *Despesa única quitada!* Desativada automaticamente."
                if _exaurida else ""
            )
            _prog = f"  (parcela *{_nova_pp}/{_pt}*)" if _pt and not _exaurida else ""
            texto = (
                f"✅  *Baixa automática — {nome}*{_prog}\n\n"
                f"• Valor mensal:  *{valor_fmt}*\n"
                + linha_parcela_baixa +
                f"• Retirado da caixinha:  *{retirada_fmt}*\n"
                f"• Saldo restante:  *{novo_saldo_fmt}*\n\n"
                f"_Parcela quitada automaticamente. Cofre atualizado!_ 🛡"
                + _tag_encerramento
            )
        else:
            # Saldo parcial — informa o que falta para cobrir a parcela
            faltou_fmt = f"R$ {float(faltou):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            texto = (
                f"⚠  *Baixa parcial — {nome}*\n\n"
                f"• Valor mensal:  *{valor_fmt}*\n"
                + linha_parcela_baixa +
                f"• Retirado (saldo disponível):  *{retirada_fmt}*\n"
                f"• Ainda falta:  *{faltou_fmt}*\n\n"
                f"_A caixinha não tinha saldo suficiente. "
                f"Verifique e complete o pagamento manualmente._"
            )

        await _enviar(remote_jid, texto)
        logger.info(
            f"[ReminderService] Baixa automática executada: motorista={motorista_id} "
            f"despesa={nome!r} retirada=R${float(retirada):.2f} novo_saldo=R${float(novo_saldo):.2f}"
        )


async def _processar_vencimentos_semanais() -> None:
    """Avisa motoristas sobre despesas semanais que vencem hoje ou amanhã.

    Opera sobre despesas com recorrencia_tipo = 'semanal' e dias_semana INTEGER[].
    Usa isoweekday() (1=Segunda … 7=Domingo) para comparar com os dias cadastrados.
    Compartilha a mesma UX adaptativa de saldo da função mensal — 3 estados:
    saldo suficiente, saldo parcial, caixinha zerada.
    Chave de idempotência inclui o isoweekday para disparar 1× por semana por vencimento.
    """
    _tz   = pytz.timezone("America/Sao_Paulo")
    hoje  = datetime.now(_tz).date()
    amanha = hoje + timedelta(days=1)

    hoje_iso   = hoje.isoweekday()          # 1=Seg … 7=Dom
    amanha_iso = amanha.isoweekday()

    try:
        async with DatabaseService.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT dfm.id::text                    AS despesa_id,
                       dfm.nome,
                       dfm.valor_mensal,
                       array_length(dfm.dias_semana, 1) AS qtd_dias_semana,
                       venc.dia                        AS dia_semana,
                       m.id::text                      AS motorista_id,
                       m.telefone,
                       COALESCE(cp.saldo_atual, 0)     AS saldo_atual
                FROM public.despesas_fixas_mensais dfm
                JOIN public.motoristas m ON m.id = dfm.motorista_id
                LEFT JOIN public.caixas_provisao cp ON cp.id = dfm.caixa_id
                JOIN LATERAL UNNEST(dfm.dias_semana) AS venc(dia) ON TRUE
                WHERE dfm.ativo = TRUE
                  AND dfm.recorrencia_tipo = 'semanal'
                  AND venc.dia = ANY($1::int[]);
                """,
                [hoje_iso, amanha_iso],
            )
    except Exception as e:
        logger.error(f"[ReminderService] Erro ao buscar vencimentos semanais: {e}")
        return

    _DIAS_FULL = {1: "Segunda", 2: "Terça", 3: "Quarta", 4: "Quinta",
                  5: "Sexta", 6: "Sábado", 7: "Domingo"}

    client = await RedisFSMService.get_client()
    for row in rows:
        tenant_id    = row["telefone"]
        dia_semana   = int(row["dia_semana"])
        nome         = row["nome"]
        valor_mensal = Decimal(str(row["valor_mensal"]))
        saldo_atual  = Decimal(str(row["saldo_atual"]))
        despesa_id   = row["despesa_id"]
        remote_jid   = f"{tenant_id}@s.whatsapp.net"

        # Parcela semanal = valor_mensal / 4 (4 semanas por mês)
        qtd_dias = max(1, int(row["qtd_dias_semana"] or 1))
        valor_parcela = (valor_mensal / Decimal("4") / Decimal(str(qtd_dias))).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )

        # Chave de idempotência: 1× por semana por dia cadastrado
        # Usa o isoformat da data atual para garantir 1 disparo por dia calendário
        redis_key = f"reminder:venc_sem:{tenant_id}:{despesa_id}:{dia_semana}:{hoje.isoformat()}"
        if await client.exists(redis_key):
            continue

        is_hoje     = dia_semana == hoje_iso
        dia_label   = _DIAS_FULL.get(dia_semana, str(dia_semana))
        data_venc   = hoje if is_hoje else amanha
        data_fmt    = data_venc.strftime("%d/%m/%Y")

        valor_fmt   = f"R$ {float(valor_mensal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        parcela_fmt = f"R$ {float(valor_parcela):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        saldo_fmt   = f"R$ {float(saldo_atual):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        cabecalho   = f"{'HOJE' if is_hoje else 'AMANHÃ'} ({data_fmt}) — {nome}"
        emoji_h     = "📅" if is_hoje else "⏰"

        linha_freq  = f"• Recorrência:  *toda  {dia_label}*  ·  parcela  *{parcela_fmt}*\n"

        if saldo_atual >= valor_parcela:
            texto = (
                f"{emoji_h}  *Vencimento {cabecalho}*\n\n"
                f"• Valor mensal:  *{valor_fmt}*\n"
                f"{linha_freq}"
                f"• Saldo na caixinha:  *{saldo_fmt}*  ✅\n\n"
                f"Tudo provisionado! Para registrar a saída:\n"
                f"  👉  `!retirar caixa {nome} {float(valor_parcela):.2f}`\n\n"
                f"_Se não retirar agora, a baixa automática executa na virada do dia._"
            )
        elif saldo_atual > Decimal("0"):
            faltando     = (valor_parcela - saldo_atual).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            faltando_fmt = f"R$ {float(faltando):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            texto = (
                f"{emoji_h}  *Vencimento {cabecalho}*\n\n"
                f"• Valor mensal:  *{valor_fmt}*\n"
                f"{linha_freq}"
                f"• Saldo na caixinha:  *{saldo_fmt}*  (parcial 🚨)\n"
                f"• Déficit:  *{faltando_fmt}*\n\n"
                f"Complete o pagamento externamente. Para resgatar o disponível:\n"
                f"  👉  `!retirar caixa {nome} {float(saldo_atual):.2f}`"
            )
        else:
            texto = (
                f"{emoji_h}  *Vencimento {cabecalho}*\n\n"
                f"• Valor mensal:  *{valor_fmt}*\n"
                f"{linha_freq}"
                f"• Saldo na caixinha:  *R$ 0,00*  ❌\n\n"
                f"Não há reserva para cobrir esta parcela.\n"
                f"Realize o pagamento com recursos externos."
            )

        await _enviar(remote_jid, texto)
        await client.set(redis_key, "1", ex=_TTL_REMINDER_VENC_S)
        logger.info(
            f"[ReminderService] Lembrete VENCIMENTO SEMANAL enviado: motorista={row['motorista_id']} "
            f"despesa={nome} dia_semana={dia_semana} ({dia_label}) hoje={hoje}"
        )


async def loop_lembretes() -> None:
    """
    Loop infinito de varredura. Deve ser iniciado como asyncio.Task no lifespan do FastAPI.
    Cada ciclo dorme _INTERVALO_LOOP_S segundos entre varreduras.

    O sleep fica no FINAL do ciclo para que a primeira varredura ocorra imediatamente
    na inicialização do container, sem esperar 5 minutos em branco.

    CancelledError não é capturado pelo except Exception (herda de BaseException),
    por isso o relançamento explícito serve apenas para documentar a intenção e
    garantir log de shutdown limpo.
    """
    logger.info(f"[ReminderService] Loop iniciado (intervalo={_INTERVALO_LOOP_S}s).")
    while True:
        try:
            await _processar_pausas_prolongadas()
            await _processar_turnos_zumbi()
            await _processar_vencimentos_despesas()
            await _processar_vencimentos_semanais()
            await _processar_baixas_automaticas()
        except asyncio.CancelledError:
            logger.info("[ReminderService] Loop encerrado graciosamente.")
            raise
        except Exception as e:
            # Nunca deixa o loop morrer por erro pontual de um ciclo
            logger.error(f"[ReminderService] Erro no ciclo de lembretes: {e}")
        try:
            await asyncio.sleep(_INTERVALO_LOOP_S)
        except asyncio.CancelledError:
            logger.info("[ReminderService] Loop encerrado graciosamente (durante sleep).")
            raise


async def registrar_interacao(tenant_id: str) -> None:
    """
    Deve ser chamado no início de cada mensagem processada pelo orchestrator.
    Grava chave last_seen: com TTL de _BYPASS_INTERACAO_MIN para suprimir
    lembretes enquanto o motorista estiver ativo.
    """
    client = await RedisFSMService.get_client()
    await client.set(
        f"last_seen:{tenant_id}", "1", ex=_BYPASS_INTERACAO_MIN * 60
    )
