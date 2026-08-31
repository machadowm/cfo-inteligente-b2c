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

import httpx

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


async def loop_lembretes() -> None:
    """
    Loop infinito de varredura. Deve ser iniciado como asyncio.Task no lifespan do FastAPI.
    Cada ciclo dorme _INTERVALO_LOOP_S segundos entre varreduras.
    """
    logger.info(f"[ReminderService] Loop iniciado (intervalo={_INTERVALO_LOOP_S}s).")
    while True:
        await asyncio.sleep(_INTERVALO_LOOP_S)
        try:
            await _processar_pausas_prolongadas()
            await _processar_turnos_zumbi()
        except Exception as e:
            # Nunca deixa o loop morrer por erro pontual
            logger.error(f"[ReminderService] Erro no ciclo de lembretes: {e}")


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
