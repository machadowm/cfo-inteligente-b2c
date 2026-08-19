#!/usr/bin/env bash
# ==============================================================================
# Script de Envio de Código (Push Automatizado) - CFO Inteligente B2C
# Nível: Tático / Automação de Desenvolvimento
# ==============================================================================

set -euo pipefail

PROJECT_DIR="$HOME/cfo-inteligente"

# Permite passar a mensagem de commit como argumento (ex: ./push_code.sh "Corrige bug no webhook")
# Se não passar nada, gera uma mensagem automática com timestamp.
COMMIT_MSG="${1:-"Auto-commit: Atualizações locais em $(date +'%Y-%m-%d %H:%M:%S')"}"

echo "🔄 [1/4] Acessando diretório do projeto: $PROJECT_DIR"
cd "$PROJECT_DIR"

echo "🔎 [2/4] Verificando e adicionando arquivos alterados..."
# Adiciona todas as modificações, criações e deleções
git add .

# Valida de forma segura se há realmente algo para commitar antes de prosseguir
if git diff-index --quiet HEAD --; then
    echo "✅ Nenhuma alteração local detectada. O repositório já está limpo."
    exit 0
fi

echo "📝 [3/4] Registrando commit..."
git commit -m "$COMMIT_MSG"

echo "🚀 [4/4] Enviando modificações para o GitHub (Push)..."
# Força o envio para a branch main
git push origin main

echo "✅ Código enviado com sucesso para a nuvem!"
