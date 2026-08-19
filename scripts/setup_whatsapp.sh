#!/usr/bin/env python3
# ==============================================================================
# Script de Conexão WhatsApp - Geração de QR Code
# Arquitetura Assíncrona Não-Bloqueante (Zero Event-Loop Blocking)
# ==============================================================================

import asyncio
import logging
import os
from pathlib import Path

import httpx
from dotenv import load_dotenv

# Carrega as variáveis de ambiente um nível acima (raiz do projeto)
load_dotenv(dotenv_path="../.env")

logging.basicConfig(
    level=logging.INFO, 
    format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")
INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "cfo_bot")

async def baixar_qrcode():
    """
    Comunica com a Evolution API e descarrega o QR Code sem bloquear o Event Loop.
    """
    url = f"{EVOLUTION_API_URL}/instance/connect/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY}
    
    logger.info(f"A solicitar geração de QR Code para a instância: '{INSTANCE_NAME}'...")

    try:
        # Contexto assíncrono para a requisição HTTP
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            
            payload = response.json()
            
            # Valida se a API retornou a base64 da imagem
            if "base64" not in payload:
                logger.warning("QR Code não encontrado. A instância pode já estar conectada.")
                return

            # Extrai e limpa a string base64 do prefixo "data:image/png;base64,"
            import base64
            base64_data = payload["base64"].split(",")[1]
            image_data = base64.b64decode(base64_data)

            filename = "qrcode_direto.png"
            filepath = Path(filename)
            
            # RESOLUÇÃO ASYNC230: Delega o Disk I/O síncrono para uma Thread separada.
            # O Event Loop continua a rodar sem congelamentos.
            await asyncio.to_thread(filepath.write_bytes, image_data)
            
            logger.info(f"QR Code gravado com sucesso em disco: '{filename}'. Escaneie agora.")

    except httpx.HTTPStatusError as e:
        # Tratamento de Erros HTTP (ex: 401, 403, 404)
        logger.error(f"[ERRO HTTP] Falha na comunicação com a API (Status: {e.response.status_code}): {e.response.text}")
    except Exception:
        # RESOLUÇÃO TRY401: logger.exception sem f-string e sem {e!s}.
        # A stack trace será anexada automaticamente pela engine de logs.
        logger.exception("[FALHA CRÍTICA] Erro interno imprevisto na geração do QR Code.")

if __name__ == "__main__":
    try:
        # Ponto de entrada moderno do asyncio
        asyncio.run(baixar_qrcode())
    except KeyboardInterrupt:
        logger.info("Operação cancelada pelo utilizador.")
