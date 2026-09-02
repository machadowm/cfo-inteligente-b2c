"""
ReminderService — Lembretes proativos de turno via WhatsApp.

Executa dois gatilhos periódicos sem dependências externas (asyncio nativo):

  • PAUSA_PROLONGADA : turno em 'em_pausa' há > 90 min sem interação recente.
  • TURNO_ZUMBI     : turno 'em_andamento' aberto há > 12 h sem interação recente.

Idempotência: cada lembrete grava uma chave Redis com TTL igual ao intervalo mínimo
entre reenvios, evitando spam mesmo em múltiplos workers ou reinicializações.

Bypass de interação: se o motorista enviou qualquer mensagem nos últimos 15 minutos
(detectado pela chave buffer_msg: no Redis), o lembrete é suprimido.
"""

import asyncio
import logging
import os
from datetime import datetime, timedelta

import httpx
import pytz

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService

logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://cfo_evolution:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")

# ── Configuração de limites ───────────────────────────────────────────────────
_PAUSA_LIMITE_MIN       = 90        # minutos em pausa antes de lembrar
_ZUMBI_LIMITE_H         = 12        # horas de turno aberto antes de lembrar
_BYPASS_INTERACAO_MIN   = 15        # ignora lembrete se motorista interagiu neste intervalo
_TTL_REMINDER_PAUSA_S   = 60 * 60   # reenvio mínimo: 1 h
_TTL_REMINDER_ZUMBI_S   = 60 * 120  # reenvio mínimo: 2 h
_TTL_REMINDER_VENC_S    = 60 * 60 * 20  # vencimento: reenvio mínimo 20 h (1× por dia)
_TTL_BAIXA_AUTO_S       = 60 * 60 * 24 * 32  # baixa automática: idempotência de ~1 mês
_INTERVALO_LOOP_S       = 300       # varredura a cada 5 min


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
    """Verifica se existe buffer de mensagem ativo (motorista interagiu recentemente)."""
    client = await RedisFSMService.get_client()
    # buffer_msg: tem TTL = janela de debouncing (4 s por default).
    # Para bypass de lembrete usamos a chave de erros como proxy de atividade recente —
    # ela tem TTL de 15 min e é resetada em toda interação bem-sucedida.
    # Alternativa mais direta: chave last_seen: gravada abaixo.
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
        tenant_id = row["telefone"]
        motorista_id = row["motorista_id"]
        minutos = int(row["minutos_pausa"])
        remote_jid = f"{tenant_id}@s.whatsapp.net"

        if await _interagiu_recentemente(tenant_id):
            continue
        if await _ja_foi_lembrado(tenant_id, "pausa"):
            continue

        horas = minutos // 60
        mins  = minutos % 60
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
        tenant_id = row["telefone"]
        motorista_id = row["motorista_id"]
        horas = round(float(row["horas_abertas"]), 1)
        remote_jid = f"{tenant_id}@s.whatsapp.net"

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
    """
    _tz = pytz.timezone("America/Sao_Paulo")
    hoje = datetime.now(_tz).date()
    amanha = hoje + timedelta(days=1)
    ultimo_dia_mes = (hoje.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)
    dias_alvo = {
        min(hoje.day, ultimo_dia_mes.day),
        min(amanha.day, ultimo_dia_mes.day),
    }

    try:
        async with DatabaseService.get_connection() as conn:
            # UNNEST expande o array — cada vencimento vira uma linha independente
            rows = await conn.fetch(
                """
                SELECT dfm.id::text          AS despesa_id,
                       dfm.nome,
                       dfm.valor_mensal,
                       venc.dia             AS dia_vencimento,
                       m.id::text           AS motorista_id,
                       m.telefone
                FROM public.despesas_fixas_mensais dfm
                JOIN public.motoristas m ON m.id = dfm.motorista_id
                -- expande cada elemento do array em linha separada
                JOIN LATERAL UNNEST(dfm.dias_vencimento) AS venc(dia) ON TRUE
                WHERE dfm.ativo = TRUE
                  AND LEAST(
                        venc.dia,
                        DATE_PART('day',
                            DATE_TRUNC('month', CURRENT_DATE AT TIME ZONE 'America/Sao_Paulo')
                            + INTERVAL '1 month'
                            - INTERVAL '1 day'
                        )::int
                  ) = ANY($1::int[]);
                """,
                list(dias_alvo),
            )
    except Exception as e:
        logger.error(f"[ReminderService] Erro ao buscar vencimentos: {e}")
        return

    client = await RedisFSMService.get_client()
    for row in rows:
        tenant_id   = row["telefone"]
        dia_venc    = int(row["dia_vencimento"])
        nome        = row["nome"]
        valor       = float(row["valor_mensal"])
        despesa_id  = row["despesa_id"]
        remote_jid  = f"{tenant_id}@s.whatsapp.net"
        # Chave inclui dia_vencimento para disparar independentemente por vencimento
        redis_key   = f"reminder:venc:{tenant_id}:{despesa_id}:{dia_venc}:{hoje.isoformat()}"

        if await client.exists(redis_key):
            continue  # já notificado hoje para este vencimento

        valor_fmt = f"R$ {valor:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        if dia_venc == hoje.day or (hoje.day == ultimo_dia_mes.day and dia_venc > ultimo_dia_mes.day):
            texto = (
                f"📅  *Vencimento HOJE — {nome}*\n\n"
                f"• Valor:  *{valor_fmt}*\n\n"
                f"Se a caixinha já tem saldo suficiente, use:\n"
                f"  👉  `!retirar caixa {nome} {valor:.2f}`\n\n"
                f"_Para ver o saldo atual envie  *!caixas*_"
            )
        else:
            texto = (
                f"⏰  *Vencimento AMANHÃ — {nome}*\n\n"
                f"• Valor:  *{valor_fmt}*\n\n"
                f"Confira o saldo da caixinha antes do vencimento:\n"
                f"  👉  `!caixas`"
            )

        await _enviar(remote_jid, texto)
        await client.set(redis_key, "1", ex=_TTL_REMINDER_VENC_S)
        logger.info(
            f"[ReminderService] Lembrete VENCIMENTO enviado: motorista={row['motorista_id']} "
            f"despesa={nome} dia_venc={dia_venc} hoje={hoje}"
        )


async def _processar_baixas_automaticas() -> None:
    """Executa a baixa automática nas caixas de provisão 1 dia após o vencimento.

    Suporta múltiplos vencimentos por despesa (dias_vencimento INTEGER[]).
    Cada elemento do array é processado independentemente — uma despesa com
    vencimentos nos dias 5 e 20 gera duas baixas por mês.

    A chave de idempotência inclui o dia do vencimento:
      baixa_auto:{motorista}:{despesa}:{dia_venc}:{ano-mês}
    """
    from decimal import Decimal, ROUND_HALF_UP

    _tz = pytz.timezone("America/Sao_Paulo")
    hoje = datetime.now(_tz).date()
    ontem = hoje - timedelta(days=1)
    # Ano-mês de referência para a chave de idempotência (usa o mês de ontem)
    ano_mes_ref = ontem.strftime("%Y-%m")

    try:
        async with DatabaseService.get_connection() as conn:
            rows = await conn.fetch(
                """
                SELECT dfm.id::text          AS despesa_id,
                       dfm.nome,
                       dfm.valor_mensal,
                       venc.dia             AS dia_vencimento,
                       dfm.caixa_id::text    AS caixa_id,
                       cp.saldo_atual,
                       m.id::text            AS motorista_id,
                       m.telefone
                FROM public.despesas_fixas_mensais dfm
                JOIN public.motoristas m ON m.id = dfm.motorista_id
                JOIN public.caixas_provisao cp ON cp.id = dfm.caixa_id
                JOIN LATERAL UNNEST(dfm.dias_vencimento) AS venc(dia) ON TRUE
                WHERE dfm.ativo = TRUE
                  AND dfm.caixa_id IS NOT NULL
                  AND cp.saldo_atual > 0
                  AND LEAST(
                        venc.dia,
                        DATE_PART('day',
                            DATE_TRUNC('month', $1::date AT TIME ZONE 'America/Sao_Paulo')
                            + INTERVAL '1 month'
                            - INTERVAL '1 day'
                        )::int
                  ) = DATE_PART('day', $1::date)::int;
                """,
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

        # Chave de idempotência: 1 baixa por (motorista, despesa, dia_vencimento, ano-mês)
        # Inclui o dia para que vencimentos múltiplos da mesma despesa sejam independentes
        redis_key = f"baixa_auto:{motorista_id}:{despesa_id}:{dia_venc}:{ano_mes_ref}"
        if await client.exists(redis_key):
            continue  # já executado neste mês para este vencimento

        # Quanto retirar: mínimo entre saldo disponível e valor da despesa
        retirada = min(saldo_atual, valor_mensal).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

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

        # Monta notificação
        valor_fmt   = f"R$ {float(valor_mensal):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        retirada_fmt = f"R$ {float(retirada):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        novo_saldo_fmt = f"R$ {float(novo_saldo):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
        faltou = (valor_mensal - retirada).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        if faltou <= Decimal("0.01"):
            # Saldo cobriu 100% — quitado
            texto = (
                f"✅  *Baixa automática — {nome}*\n\n"
                f"• Valor da despesa:  *{valor_fmt}*\n"
                f"• Retirado da caixinha:  *{retirada_fmt}*\n"
                f"• Saldo restante:  *{novo_saldo_fmt}*\n\n"
                f"_Despesa quitada automaticamente. Cofre atualizado!_ 🛡"
            )
        else:
            # Saldo parcial — informa o que falta
            faltou_fmt = f"R$ {float(faltou):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
            texto = (
                f"⚠  *Baixa parcial — {nome}*\n\n"
                f"• Valor da despesa:  *{valor_fmt}*\n"
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
