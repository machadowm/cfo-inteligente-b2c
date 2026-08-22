#!/usr/bin/env python3
# ==============================================================================
# Script de Conexão WhatsApp - Geração e Exibição de QR Code Automático
# Arquitetura Assíncrona Não-Bloqueante (Zero Event-Loop Blocking)
# Versão Defensiva com Tradução Dinâmica de DNS e Renderização UTF-8 no Terminal
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

# Configurações originais lidas do .env ou fallbacks
EVOLUTION_API_URL = os.getenv("EVOLUTION_API_URL", "http://localhost:8080")
EVOLUTION_API_KEY = os.getenv("EVOLUTION_API_KEY", "evolution_secret_key")
INSTANCE_NAME = os.getenv("EVOLUTION_INSTANCE_NAME", "cfo_bot")

def resolver_url_api(url_original: str) -> str:
    """
    Analisa se o hostname interno do Docker 'cfo_evolution' é resolvível.
    Caso não seja (execução no Host da VM), chaveia dinamicamente para loopback (127.0.0.1).
    """
    if "cfo_evolution" in url_original:
        try:
            # Tenta resolver o nome do container
            socket.gethostbyname("cfo_evolution")
        except socket.gaierror:
            # Se falhar gaierror, estamos no Host. Traduz para localhost exposto na porta 8080
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
        # Tenta executar o qrencode enviando a string do token criptográfico para o stdin
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

async def baixar_qrcode():
    """
    Comunica com a Evolution API e descarrega o QR Code sem bloquear o Event Loop.
    """
    api_url_resolvida = resolver_url_api(EVOLUTION_API_URL)
    url = f"{api_url_resolvida}/instance/connect/{INSTANCE_NAME}"
    headers = {"apikey": EVOLUTION_API_KEY}

    logger.info(f"A solicitar geração de QR Code para a instância: '{INSTANCE_NAME}'...")

    try:
        # Contexto assíncrono para a requisição HTTP com timeout estrito
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()

            payload = response.json()

            # Extração tática do código de pareamento puro e da imagem Base64
            raw_code = payload.get("code") or payload.get("qrcode", {}).get("code")
            base64_img = payload.get("base64") or payload.get("qrcode", {}).get("base64")

            # Valida se a API retornou dados válidos
            if not base64_img:
                logger.warning("QR Code não retornado. A instância pode já estar conectada ou em handshake.")
                return

            # Extrai e limpa a string base64 do prefixo se existir
            if "," in base64_img:
                base64_data = base64_img.split(",")[1]
            else:
                base64_data = base64_img

            image_data = base64.b64decode(base64_data)

            filename = "qrcode_direto.png"
            filepath = Path(filename)

            # RESOLUÇÃO ASYNC230: Delega o Disk I/O síncrono para uma Thread separada.
            await asyncio.to_thread(filepath.write_bytes, image_data)
            logger.info(f"💾 Imagem do QR Code gravada com sucesso em: '{filename}'")

            # EXIBIÇÃO AUTOMÁTICA DO QR CODE NO TERMINAL CLI
            if raw_code:
                renderizar_qrcode_terminal(raw_code)
            else:
                logger.warning("Código textual de emparelhamento não localizado na resposta.")

    except httpx.HTTPStatusError as e:
        logger.error(f"[ERRO HTTP] Falha na comunicação com a API (Status: {e.response.status_code}): {e.response.text}")
    except Exception:
        logger.exception("[FALHA CRÍTICA] Erro interno imprevisto na geração do QR Code.")

if __name__ == "__main__":
    try:
        # Ponto de entrada moderno do asyncio
        asyncio.run(baixar_qrcode())
    except KeyboardInterrupt:
        logger.info("Operação cancelada pelo utilizador.")

