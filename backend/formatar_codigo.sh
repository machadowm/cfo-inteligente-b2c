#!/usr/bin/env bash
# ==============================================================================
# Pipeline Local de Qualidade de Código (Linter & Formatter via Ruff)
# Nível: Arquitetura SRE / Zero-Fail
# ==============================================================================

# Aborta imediatamente em caso de erro, variável não declarada ou falha num pipe
set -euo pipefail

# --- Cores e Formatação ---
CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

VENV_DIR=".venv" # Padrão moderno (oculto) em vez de "venv"

echo -e "${CYAN}🚀 Iniciando Pipeline de Qualidade de Código (Ruff)...${NC}"

# 1. Auditoria de Dependências
if ! command -v python3 &> /dev/null; then
    echo -e "${RED}❌ Erro Crítico: 'python3' não encontrado no sistema.${NC}"
    exit 1
fi

# 2. Gestão Inteligente do Virtual Environment
if [[ -n "${VIRTUAL_ENV:-}" ]]; then
    echo -e "${GREEN}✅ Virtual Environment já ativo detetado: ${VIRTUAL_ENV}${NC}"
else
    if [[ ! -d "$VENV_DIR" ]]; then
        echo -e "${YELLOW}🔄 Criando novo Virtual Environment em '$VENV_DIR'...${NC}"
        python3 -m venv "$VENV_DIR"
    fi
    echo -e "${CYAN}🔌 Ativando contexto virtual...${NC}"
    # shellcheck disable=SC1090
    source "$VENV_DIR/bin/activate"
fi

# 3. Injeção de Dependências (Idempotente)
echo -e "${CYAN}📦 Garantindo dependências do motor Ruff...${NC}"
# Atualiza o pip silenciosamente (boas práticas de segurança)
python3 -m pip install --upgrade pip -q
# Instala/Atualiza o Ruff apenas se necessário, sem poluir o ecrã
python3 -m pip install --upgrade ruff -q

# 4. Execução das Camadas de Código
echo -e "\n${YELLOW}🔎 [1/2] Linter: Analisando e corrigindo violações de regras...${NC}"
# Verifica e aplica correções automáticas seguras
ruff check --fix .

echo -e "\n${YELLOW}🎨 [2/2] Formatter: Padronizando sintaxe...${NC}"
# O seu script original executava o --diff e logo a seguir aplicava. 
# Num fluxo profissional local, mostramos o que mudou e aplicamos atomicamente.
ruff format .

echo -e "\n${GREEN}🎯 Pipeline concluído com sucesso. O seu código está impecável.${NC}"

# NOTA ARQUITETURAL: 
# Omitimos propositadamente o comando 'deactivate'. 
# Como este script corre numa "subshell", o ambiente virtual é automaticamente 
# destruído no fim da execução, não afetando o terminal do utilizador.
