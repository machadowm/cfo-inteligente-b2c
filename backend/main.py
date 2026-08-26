import os
import re
import unicodedata
import logging
import orjson
from fastapi import FastAPI, Request, BackgroundTasks, Response
from contextlib import asynccontextmanager
from typing import Any

from services.database_service import DatabaseService
from services.redis_fsm import RedisFSMService
from services.orchestrator_service import OrchestratorService

# Configuração de Logs
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Serializador ORJSON para resposta em microsegundos
class ORJSONResponse(Response):
    media_type = "application/json"
    def render(self, content: Any) -> bytes:
        return orjson.dumps(content)

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("CFO Inteligente B2C: High-Performance Engine Started")
    await DatabaseService.initialize_pool()
    yield
    await DatabaseService.close_pool()
    await RedisFSMService.close_client()

app = FastAPI(title="CFO Peak API", default_response_class=ORJSONResponse, lifespan=lifespan)

# Regex pré-compiladas (Performance de CPU)
RE_CLEAN = re.compile(r'[^a-zA-Z0-9\s]')

def normalizar_fast(texto: str) -> str:
    if not texto: return ""
    nfkd = unicodedata.normalize('NFKD', texto)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return RE_CLEAN.sub('', sem_acento).lower().strip()

@app.post("/webhook/evolution")
async def webhook_v5(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
        data = body.get("data", {})
        key = data.get("key", {})

        if key.get("fromMe"): return {"status": "ignored"}

        remote_jid = key.get("remoteJid", "")
        tenant_id = remote_jid.split("@")
        
        msg = data.get("message", {})
        texto = (msg.get("conversation") or 
                 msg.get("extendedTextMessage", {}).get("text") or 
                 msg.get("imageMessage", {}).get("caption") or "").strip()

        if not texto: return {"status": "empty"}

        # Delegar processamento para background (Resiliência de Rede)
        background_tasks.add_task(
            OrchestratorService.router, 
            tenant_id, remote_jid, texto, key.get("id")
        )

        return {"status": "accepted"}
    except Exception as e:
        logger.error(f"Erro na ingestão: {e}")
        return {"status": "error"}

@app.get("/health")
async def health():
    return {"status": "online", "mode": "peak_performance"}
