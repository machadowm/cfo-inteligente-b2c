#!/usr/bin/env bash
# ==============================================================================
# Script de Atualização Automatizada - CFO Inteligente B2C
# Compatível com Ubuntu Server 26.04 LTS (Bare-Metal / Hyper-V)
# ==============================================================================

set -euo pipefail

PROJECT_DIR="$HOME/cfo-inteligente"

echo "🔄 [1/4] Acessando diretório do projeto: $PROJECT_DIR"
cd "$PROJECT_DIR"

echo "📥 [2/4] Atualizando repositório via Git (Pull)..."
git pull origin main

echo "🏗️ [3/4] Reconstruindo e reiniciando os microsserviços Docker..."
docker compose down
docker compose up -d --build

echo "🩺 [4/4] Verificando saúde dos contêineres..."
docker compose ps

echo "✅ Atualização concluída com sucesso! Sistema operacional e blindado."
