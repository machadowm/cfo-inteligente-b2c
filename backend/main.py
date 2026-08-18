import os
import re
import unicodedata
import httpx
import logging
from fastapi import FastAPI, Request, BackgroundTasks
from contextlib import asynccontextmanager

# Importação dos serviços centrais da aplicação
from services.redis_fsm import RedisFSMService
from services.database_service import DatabaseService
from services.turno_service import TurnoService
from services.transacao_service import TransacaoService

# ---------------------------------------------------------
# Passo 1: Configuração de Observabilidade (Logging)
# ---------------------------------------------------------
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configurações do ambiente para a Evolution API
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://evolution-api:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "sua_chave_aqui")
INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "cfo_instance")

# ---------------------------------------------------------
# Passo 2: Gestão do Ciclo de Vida da Aplicação
# ---------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Gere o arranque e o encerramento seguro dos serviços dependentes."""
    logger.info("A inicializar os pools de conexões (PostgreSQL e Redis)...")
    
    # CORREÇÃO: Utilizando a função correta do database_service.py
    await DatabaseService.initialize_pool()
    
    # Salvaguarda: Executa o init_redis apenas se já o tiveres criado no ficheiro redis_fsm.py
    if hasattr(RedisFSMService, 'init_redis'):
        await RedisFSMService.init_redis()
        
    # Ponto de execução da aplicação
    yield
    
    logger.info("A encerrar recursos de forma graciosa...")
    await DatabaseService.close_pool()
    
    if hasattr(RedisFSMService, 'close_redis'):
        await RedisFSMService.close_redis()

app = FastAPI(title="CFO Inteligente B2C API", version="1.1.0", lifespan=lifespan)

# ---------------------------------------------------------
# Passo 3: Funções Utilitárias e de Integração
# ---------------------------------------------------------
def normalizar_texto(texto: str) -> str:
    """Remove acentos e caracteres especiais para facilitar a leitura da IA e FSM."""
    if not texto:
        return ""
    nfkd = unicodedata.normalize('NFKD', texto)
    sem_acento = "".join([c for c in nfkd if not unicodedata.combining(c)])
    return re.sub(r'[^a-zA-Z0-9\s]', '', sem_acento).lower().strip()

async def enviar_mensagem_whatsapp(telefone: str, texto: str):
    """Comunica com a Evolution API para enviar a resposta de volta ao motorista."""
    url = f"{EVOLUTION_API_URL}/message/sendText/{INSTANCE_NAME}"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "number": telefone,
        "text": texto
    }

    # Tolerância a falhas implementada no envio (Mock de DLQ Lógico via Log)
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            logger.info(f"Mensagem enviada com sucesso para {telefone}.")
        except httpx.RequestError as e:
            logger.error(f"[DLQ] Erro de rede ao comunicar com Evolution API para {telefone}: {e}")
        except httpx.HTTPStatusError as e:
            logger.error(f"[DLQ] Evolution API rejeitou o payload para {telefone}. Status: {e.response.status_code}")

# ---------------------------------------------------------
# Passo 4: Webhooks e Endpoints
# ---------------------------------------------------------
@app.post("/webhook/whatsapp")
async def webhook_whatsapp(request: Request, background_tasks: BackgroundTasks):
    """Recebe e processa as mensagens enviadas pelos motoristas."""
    body = await request.json()

    try:
        event = body.get("event")
        # Garante que só processamos mensagens novas (upsert)
        if event != "messages.upsert":
            return {"status": "ignored", "reason": "event_not_upsert"}

        data = body.get("data", {})
        key = data.get("key", {})
        
        # Ignora mensagens enviadas pelo próprio bot
        if key.get("fromMe", False):
            return {"status": "ignored", "reason": "message_from_me"}

        remote_jid = key.get("remoteJid", "")
        driver_phone = remote_jid.split("@")[0]

        message = data.get("message", {})
        texto_msg = message.get("conversation") or message.get("extendedTextMessage", {}).get("text", "")

        if not texto_msg:
            return {"status": "ignored", "reason": "no_text_content"}

        # Operação Atómica para resolução do Motorista (Tenant)
        driver_id = await DatabaseService.get_or_create_driver_by_phone(driver_phone)
        if not driver_id:
            logger.error(f"Falha ao resolver Tenant ID para o número {driver_phone}.")
            return {"status": "error", "reason": "driver_resolution_failed"}

        # Gestão de Estado Finito e Buffer de Mensagens no Redis
        estado_atual = await RedisFSMService.get_state(driver_id)
        await RedisFSMService.buffer_message(driver_id, texto_msg)

        texto_limpo = normalizar_texto(texto_msg)
        logger.info(f"Processamento de webhook. Tenant: {driver_id}, Estado: {estado_atual}")

        # Resposta imediata delegada para processamento em background (evita Timeouts da API)
        resposta_ia = f"Motorista {driver_phone}, registei a sua entrada: '{texto_msg}'. A aguardar processamento de IA."
        background_tasks.add_task(enviar_mensagem_whatsapp, driver_phone, resposta_ia)

        return {"status": "success", "driver_id": driver_id}

    except Exception as e:
        logger.exception(f"Erro crítico no processamento do webhook: {e}")
        # Mantém a integridade e evita retry loops infinitos da API
        return {"status": "error", "detail": "Internal Server Error"}

@app.get("/health")
async def health_check():
    """Endpoint de monitorização do serviço."""
    return {"status": "healthy", "service": "cfo-inteligente-backend"}
