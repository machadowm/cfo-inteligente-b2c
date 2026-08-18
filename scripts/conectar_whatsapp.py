#!/usr/bin/env python3
import os
import httpx
import base64
import logging
import asyncio

# Configuração de Observabilidade
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Credenciais lidas do ambiente do seu Ubuntu Server
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "sua_chave_aqui")
INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "cfo_instance")

async def gerar_qrcode_direto():
    """
    Tenta criar a instância na Evolution API solicitando o QR Code na própria resposta.
    Se a instância já existir, força a reconexão para obter um novo QR Code.
    Salva o arquivo gerado diretamente no disco.
    """
    url_create = f"{EVOLUTION_API_URL}/instance/create"
    headers = {
        "apikey": EVOLUTION_API_KEY,
        "Content-Type": "application/json"
    }
    payload = {
        "instanceName": INSTANCE_NAME,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
        "reject_call": False,
        "groups_ignore": True
    }

    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            logger.info(f"A solicitar geração de QR Code direto para a instância: {INSTANCE_NAME}...")
            
            response = await client.post(url_create, json=payload, headers=headers)
            
            # Se a instância já existir (HTTP 403 ou 400 dependendo da versão da Evolution),
            # faz fallback para a rota de connect
            if response.status_code in [400, 403] and "already exists" in response.text.lower():
                logger.warning("Instância já existe. A redirecionar para a rota de conexão (GET)...")
                url_connect = f"{EVOLUTION_API_URL}/instance/connect/{INSTANCE_NAME}"
                response = await client.get(url_connect, headers=headers)

            response.raise_for_status()
            data = response.json()

            # Extração tática do Base64 dependendo do endpoint que respondeu com sucesso
            base64_str = None
            if "qrcode" in data and "base64" in data["qrcode"]:
                base64_str = data["qrcode"]["base64"]
            elif "base64" in data:
                base64_str = data["base64"]

            if not base64_str:
                logger.error(f"O payload da API não retornou um QR Code válido. Resposta: {data}")
                return

            # Tratamento da String (remoção do cabeçalho de Data URI do HTML/Navegador)
            if "," in base64_str:
                base64_str = base64_str.split(",")[1]

            # Conversão para imagem PNG binária e gravação no servidor
            image_data = base64.b64decode(base64_str)
            filename = "qrcode_direto.png"
            
            with open(filename, "wb") as f:
                f.write(image_data)
                
            logger.info(f"[SUCESSO] QR Code gravado localmente como '{filename}'. Leia no WhatsApp antes que expire.")

        except httpx.ReadTimeout:
            logger.error("[TIMEOUT] O WhatsApp demorou demasiado a responder. A rede ou a Evolution API podem estar lentas.")
        except httpx.HTTPStatusError as e:
            logger.error(f"[ERRO HTTP] Falha na comunicação com a API: {e.response.text}")
        except Exception as e:
            logger.exception(f"[FALHA CRÍTICA] Erro interno na geração do QR Code: {str(e)}")

if __name__ == "__main__":
    # Ponto de entrada atómico para rodar o script localmente no terminal
    asyncio.run(gerar_qrcode_direto())
