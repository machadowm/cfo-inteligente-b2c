import asyncio
import os
import logging
import orjson
from fastapi import FastAPI, Request, BackgroundTasks, Response
from typing import Any
from contextlib import asynccontextmanager

from schemas import WebhookPayload
from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.orchestrator_service import OrchestratorService
from services.reminder_service import loop_lembretes

# Logs de Observabilidade
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class ORJSONResponse(Response):
    media_type = "application/json"
    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Initializing CFO Inteligente B2C Backend stack (Peak Performance Ingestion Gateway)...")
    await DatabaseService.initialize_pool()
    reminder_task = asyncio.create_task(loop_lembretes())
    yield
    reminder_task.cancel()
    try:
        await reminder_task
    except asyncio.CancelledError:
        pass
    logger.info("Draining connections graciosamente...")
    await DatabaseService.close_pool()
    try:
        redis_client = await RedisFSMService.get_client()
        await redis_client.aclose()
    except Exception as e:
        logger.error(f"Falha ao fechar conexão com Redis: {e}")

app = FastAPI(
    title="CFO Inteligente B2C API",
    description="SaaS financeiro e ERP logístico conversacional de fricção zero via WhatsApp - Ingestão de Alta Performance",
    version="5.0.0",
    default_response_class=ORJSONResponse,
    lifespan=lifespan
)

@app.get("/health")
@app.get("/health_check")
async def health_check():
    """Endpoint de verificação de saúde do microsserviço."""
    return {"status": "healthy", "engine": "FastAPI & PostgreSQL 15 Bank-Grade", "performance": "peak"}

async def process_webhook_background(payload: WebhookPayload, background_tasks: BackgroundTasks):
    try:
        data = payload.data
        key = data.key

        if key.from_me:
            return

        remote_jid = key.remote_jid
        tenant_id = remote_jid.split("@")[0] if remote_jid else "unknown"
        wpp_msg_id = key.id

        # Capturar mensagem de texto de forma resiliente
        message = data.message
        texto_bruto = (
            message.conversation or
            (message.extended_text_message.get("text") if message.extended_text_message else None) or
            (message.image_message.get("caption") if message.image_message else None) or
            ""
        ).strip()

        if not texto_bruto or tenant_id == "unknown":
            return

        # Ativar status digitando para reter atenção do motorista
        background_tasks.add_task(OrchestratorService.router, tenant_id, remote_jid, texto_bruto, wpp_msg_id)
    except Exception as e:
        logger.error(f"Erro no processamento em background do webhook: {e}")

@app.post("/webhooks/evolution")
@app.post("/webhook/evolution")
@app.post("/api/v1/webhook/whatsapp")
@app.post("/webhook/whatsapp")
async def evolution_webhook_routing(payload: WebhookPayload, background_tasks: BackgroundTasks):
    """
    Roteador de Webhook de Ingestão Unificada de Alta Performance.
    Valida e recebe payloads via Pydantic v2 de forma não bloqueante e delega para background tasks.
    """
    background_tasks.add_task(process_webhook_background, payload, background_tasks)
    return {"status": "accepted", "performance": "async_task_queued"}
