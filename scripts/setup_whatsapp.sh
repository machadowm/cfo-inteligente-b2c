#!/usr/bin/env python3
# ==============================================================================
# Script de Provisionamento e Conexão WhatsApp - Evolution API (v2.3.7)
# Arquitetura Assíncrona Não-Bloqueante com Auto-Criação e Registro de Webhook
# Versão v4 - Corrigido erro de sintaxe Python (true -> True)
# ==============================================================================

import asyncio
import base64
import logging
import os
import socket
import subprocess
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

# Configurações lidas do .env com fallbacks de segurança
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")
INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "cfo_bot")
INSTANCE_TOKEN = os.getenv("EVOLUTION_INSTANCE_TOKEN", "meu_token_seguro")

# Webhook apontando para o container do FastAPI dentro da rede do Docker
WEBHOOK_URL = "http://cfo_fastapi:8000/webhooks/evolution"

def resolver_url_api(url_original: str) -> str:
    """
    Analisa se o hostname interno do Docker 'cfo_evolution' é resolvível.
    Caso não seja (execução no Host da VM), chaveia dinamicamente para loopback (127.0.0.1).
    """
    if "cfo_evolution" in url_original:
        try:
            socket.gethostbyname("cfo_evolution")
        except socket.gaierror:
            url_traduzida = url_original.replace("cfo_evolution", "127.0.0.1")
            logger.info(f"⚡ Host do container 'cfo_evolution' não resolvível no host. Chaveando dinamicamente para: {url_traduzida}")
            return url_traduzida
    return url_original

def renderizar_qrcode_terminal(raw_code: str):
    """
    Utiliza o utilitário nativo de sistema 'qrencode' para renderizar o QR Code
    diretamente no terminal em blocos UTF-8 para escaneamento imediato de fricção zero.
    """
    try:
        process = subprocess.Popen(
            ["qrencode", "-t", "UTF8"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=raw_code)
        
        if process.returncode == 0:
            print("\n" + "═" * 50)
            print("🟢 ESCANEIE O QR CODE ABAIXO COM SEU CELULAR:")
            print("═" * 50 + "\n")
            print(stdout)
            print("═" * 50)
            print("🎯 Instância aguardando pareamento...")
            print("═" * 50 + "\n")
        else:
            logger.error(f"Erro ao renderizar QR Code via qrencode: {stderr}")
            
    except FileNotFoundError:
        print("\n" + "═" * 80)
        print("💡 [DICA SRE] Utilitário 'qrencode' não localizado no host.")
        print("Para que o QR Code apareça AUTOMATICAMENTE na tela do terminal, instale-o rodando:")
        print("👉 sudo apt update && sudo apt install -y qrencode")
        print("═" * 80 + "\n")

async def provisionar_e_conectar():
    api_url_resolvida = resolver_url_api(EVOLUTION_API_URL)
    headers = {"apikey": EVOLUTION_API_KEY, "Content-Type": "application/json"}

    async with httpx.AsyncClient(timeout=20.0) as client:
        # 1. Verifica se a instância já existe (GET /instance/fetchInstances)
        logger.info("Auditando instâncias existentes na Evolution API...")
        try:
            instances_resp = await client.get(f"{api_url_resolvida}/instance/fetchInstances", headers=headers)
            instances_resp.raise_for_status()
            instances = instances_resp.json()
            
            # Varre para encontrar cfo_bot
            instancia_existe = False
            for inst in instances:
                if inst.get("name") == INSTANCE_NAME or inst.get("instanceName") == INSTANCE_NAME:
                    instancia_existe = True
                    break
        except Exception:
            logger.warning("Não foi possível listar as instâncias de forma preliminar. Tentando fluxo resiliente...")
            instancia_existe = False

        # 2. Se a instância não existir, cria-a via POST /instance/create
        if not instancia_existe:
            logger.info(f"Instância '{INSTANCE_NAME}' não localizada. Iniciando criação (POST)...")
            create_url = f"{api_url_resolvida}/instance/create"
            create_payload = {
                "instanceName": INSTANCE_NAME,
                "token": INSTANCE_TOKEN,
                "qrcode": True,
                "integration": "WHATSAPP-BAILEYS"
            }
            try:
                create_resp = await client.post(create_url, json=create_payload, headers=headers)
                create_resp.raise_for_status()
                logger.info(f"✅ Instância '{INSTANCE_NAME}' criada com sucesso!")
                
                # Atraso tático para dar tempo do Prisma processar as tabelas internas no Postgres
                await asyncio.sleep(2.0)
            except httpx.HTTPStatusError as e:
                logger.error(f"Falha crítica na criação da instância: {e.response.text}")
                return

        # 3. Configura/Sobrescreve o Webhook para a rota correta (POST /webhook/set/{instance})
        logger.info(f"Configurando Webhook apontando para: {WEBHOOK_URL}")
        webhook_url = f"{api_url_resolvida}/webhook/set/{INSTANCE_NAME}"
        webhook_payload = {
            "webhook": {
                "enabled": True, # Corrigido de true (sintaxe JSON) para True (sintaxe Python)
                "url": WEBHOOK_URL,
                "byEvents": False,
                "base64": False,
                "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"]
            }
        }
        try:
            webhook_resp = await client.post(webhook_url, json=webhook_payload, headers=headers)
            webhook_resp.raise_for_status()
            logger.info("✅ Webhook acoplado de forma íntegra!")
        except httpx.HTTPStatusError as e:
            logger.error(f"Falha ao acoplar Webhook na instância: {e.response.text}")

        # 4. Solicita a geração do QR Code (GET /instance/connect/{instance})
        logger.info(f"Solicitando geração de QR Code de conexão...")
        connect_url = f"{api_url_resolvida}/instance/connect/{INSTANCE_NAME}"
        try:
            connect_resp = await client.get(connect_url, headers=headers)
            connect_resp.raise_for_status()
            payload = connect_resp.json()

            # Extração do código textual e imagem base64
            raw_code = payload.get("code") or payload.get("qrcode", {}).get("code")
            base64_img = payload.get("base64") or payload.get("qrcode", {}).get("base64")

            if not base64_img:
                logger.warning("Aviso: QR Code não retornado. O dispositivo pode já estar conectado.")
                return

            # Limpeza do cabeçalho Base64
            if "," in base64_img:
                base64_data = base64_img.split(",")[1]
            else:
                base64_data = base64_img

            image_data = base64.b64decode(base64_data)

            # Gravação segura do PNG em disco em thread separada
            filename = "qrcode_direto.png"
            filepath = Path(filename)
            await asyncio.to_thread(filepath.write_bytes, image_data)
            logger.info(f"💾 Cópia física gravada com sucesso em: '{filename}'")

            # Renderização nativa no terminal SSH
            if raw_code:
                renderizar_qrcode_terminal(raw_code)
            else:
                logger.warning("Código de pareamento textual ausente no payload de retorno.")

        except httpx.HTTPStatusError as e:
            logger.error(f"Erro ao conectar e obter QR Code: {e.response.text}")
        except Exception:
            logger.exception("Falha imprevista na fase de conexão criptográfica.")

if __name__ == "__main__":
    try:
        asyncio.run(provisionar_e_conectar())
    except KeyboardInterrupt:
        logger.info("Operação cancelada pelo usuário.")

