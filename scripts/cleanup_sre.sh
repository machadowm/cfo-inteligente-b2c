#!/usr/bin/env bash
# ==============================================================================
# CFO INTELIGENTE B2C - SCRIPT DE LIMPEZA E MANUTENÇÃO SRE (SRE-GRADE)
# Repositório: https://github.com/machadowm/cfo-inteligente-b2c
# Compatibilidade: Ubuntu Server 20.04/22.04/24.04/26.04 LTS (Host VM Hyper-V)
# ==============================================================================

# Tratamento estrito de erros e encerramento seguro
set -euo pipefail
IFS=$'\n\t'

# --- CORES DO TERMINAL (Padrão ANSI) ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m' # No Color

# --- CONFIGURAÇÕES DE DIRETÓRIO ---
PROJECT_DIR="$HOME/cfo-inteligente"
LOG_FILE="/tmp/cfo_cleanup_$(date +'%Y%m%d_%H%M%S').log"

# Redireciona stdout e stderr também para um arquivo de log
exec > >(tee -i "$LOG_FILE") 2>&1

# --- FUNÇÃO DE LOGGING ---
log_info() {
    echo -e "${CYAN}[INFO] $(date +'%Y-%m-%d %H:%M:%S') - $1${NC}"
}

log_success() {
    echo -e "${GREEN}[SUCESSO] $(date +'%Y-%m-%d %H:%M:%S') - $1${NC}"
}

log_warn() {
    echo -e "${YELLOW}[ALERTA] $(date +'%Y-%m-%d %H:%M:%S') - $1${NC}"
}

log_error() {
    echo -e "${RED}[ERRO] $(date +'%Y-%m-%d %H:%M:%S') - $1${NC}"
}

# --- CONTROLE DE SINAL (TRAP) ---
trap_cleanup() {
    echo ""
    log_warn "Interrupção pelo usuário detectada. Encerrando de forma segura."
    exit 130
}
trap trap_cleanup SIGINT SIGTERM

# --- VERIFICAÇÃO DE DEPENDÊNCIAS ---
verificar_dependencias() {
    log_info "Verificando dependências do sistema operacional..."
    for cmd in docker docker-compose; do
        if ! command -v "$cmd" &> /dev/null; then
            # Testa comando compose v2 alternativo (docker compose)
            if [ "$cmd" = "docker-compose" ] && docker compose version &> /dev/null; then
                continue
            fi
            log_error "Ferramenta essencial '$cmd' não localizada."
            log_warn "Certifique-se de que o Docker e o Docker Compose estão devidamente instalados no host."
            exit 1
        fi
    done
    log_success "Dependências validadas com sucesso."
}

# --- LIMPEZA SOFT (Não destrutiva / Segura para Produção) ---
limpeza_soft() {
    log_info "Iniciando Limpeza SOFT (Sem perda de dados de produção)..."

    # 1. Limpeza de Caches Python Locais
    log_info "Purgando caches locais do Python no Host..."
    find "$PROJECT_DIR" -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
    find "$PROJECT_DIR" -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
    find "$PROJECT_DIR" -type d -name ".ruff_cache" -exec rm -rf {} + 2>/dev/null || true
    find "$PROJECT_DIR" -type f -name "*.pyc" -delete 2>/dev/null || true
    log_success "Caches Python eliminados."

    # 2. Limpeza de Caches de Build e Imagens Órfãs (Dangling)
    log_info "Limpando imagens dangling e caches de build do Docker..."
    docker image prune -f
    docker builder prune -f
    log_success "Imagens e buffers temporários do Docker liberados."

    # 3. Truncagem Segura de Logs de Containers (Prevenção de estouro de disco)
    log_info "Truncando arquivos de log ativos dos containers..."
    # Limita e esvazia arquivos json-log sem quebrar os daemons ativos
    if [ "$EUID" -eq 0 ]; then
        log_info "Executando como root: truncando arquivos json-log físicos..."
        find /var/lib/docker/containers/ -name "*-json.log" -exec truncate -s 0 {} \; 2>/dev/null || true
    else
        log_warn "Executando como usuário comum. Tentando truncagem lógica..."
        # Truncagem lógica via container quando aplicável ou via sudo se permitido
        if sudo -n true 2>/dev/null; then
            sudo find /var/lib/docker/containers/ -name "*-json.log" -exec truncate -s 0 {} \; 2>/dev/null || true
            log_success "Logs ativos truncados via sudo."
        else
            log_warn "Sudo sem senha indisponível. Ignorando logs físicos de containers."
        fi
    fi

    log_success "Limpeza SOFT concluída com êxito."
}

# --- LIMPEZA HARD (Destrutiva / Tábula Rasa para Dev) ---
limpeza_hard() {
    log_warn "ATENÇÃO: A limpeza HARD irá destruir a stack atual, volumes de banco e redes!"
    log_warn "Isso resultará na PERDA TOTAL de faturamento, transações e pareamento do WhatsApp."
    
    echo -ne "${RED}${BOLD}Tem certeza absoluta de que deseja prosseguir com a Tábula Rasa? (sim/Não): ${NC}"
    read -r confirmacao
    
    if [[ ! "$confirmacao" =~ ^[S|s][I|i][M|m]$ ]]; then
        log_info "Operação cancelada pelo SRE."
        return 0
    fi

    log_info "Executando Tábula Rasa profunda..."

    # 1. Derrubar a stack inteira destruindo os volumes gerenciados nomeados
    log_info "Derrubando contêineres e expurgando volumes nomeados do Docker..."
    docker compose down -v --remove-orphans || docker-compose down -v --remove-orphans || true
    log_success "Contêineres e volumes nomeados derrubados."

    # 2. Expurgar fisicamente as pastas de montagem local de dados (Bind mounts)
    log_info "Purgando diretórios físicos de dados persistentes no Host..."
    for dir in "postgres_data" "redis_data" "minio_data"; do
        if [ -d "$PROJECT_DIR/$dir" ]; then
            log_info "Removendo pasta física: $PROJECT_DIR/$dir"
            sudo rm -rf "$PROJECT_DIR/$dir"
        fi
    done
    log_success "Pastas locais de bancos e caches físicos obliteradas."

    # 3. Remover redes órfãs e cache residual do Docker
    log_info "Expurgando rede do cluster e limpando sistema docker..."
    docker network prune -f
    docker system prune -f --volumes
    log_success "Redes e caches de volumes purgados do sistema Docker."

    log_success "Limpeza HARD concluída. O ambiente está 100% limpo (Tábula Rasa)."
}

# --- EXIBIÇÃO DO MENU ---
exibir_menu() {
    clear
    echo -e "${CYAN}${BOLD}"
    echo "=========================================================================="
    echo "        CFO INTELIGENTE B2C - PAINEL OPERACIONAL DE LIMPEZA SRE"
    echo "=========================================================================="
    echo -e "${NC}"
    echo -e "Selecione o nível de intervenção técnica a ser aplicada na VM:"
    echo ""
    echo -e "  ${BOLD}[1] SOFT CLEAN${NC} - Seguro para Produção"
    echo -e "      (Purga caches Python, limpa imagens órfãs, trunca logs ativos do Docker)"
    echo ""
    echo -e "  ${BOLD}[2] HARD CLEAN${NC} - Destrutivo / Tábula Rasa (Ambiente de Dev)"
    echo -e "      (Derruba stack, apaga volumes, deleta postgres_data, redis_data, minio_data)"
    echo ""
    echo -e "  ${BOLD}[3] ESCAPE / SAIR${NC}"
    echo ""
    echo -ne "Opção desejada (1-3): "
}

# --- PROGRAMA PRINCIPAL ---
main() {
    # Garante que o script está sendo rodado a partir da pasta correta
    if [ ! -f "$PROJECT_DIR/docker-compose.yml" ]; then
        log_error "Arquivo docker-compose.yml não localizado em $PROJECT_DIR."
        log_warn "Certifique-se de clonar ou mover o projeto para o caminho padrão: $PROJECT_DIR"
        exit 1
    fi

    cd "$PROJECT_DIR"
    verificar_dependencias

    # Processamento de flags CLI rápidas
    if [ $# -gt 0 ]; then
        case "$1" in
            --soft|-s)
                limpeza_soft
                exit 0
                ;;
            --hard|-h)
                limpeza_hard
                exit 0
                ;;
            *)
                echo "Uso: ./cleanup_sre.sh [--soft | --hard]"
                exit 1
                ;;
        esac
    fi

    # Execução interativa padrão
    while true; do
        exibir_menu
        read -r opcao
        case "$opcao" in
            1)
                limpeza_soft
                echo ""
                read -n 1 -s -r -p "Pressione qualquer tecla para continuar..."
                ;;
            2)
                limpeza_hard
                echo ""
                read -n 1 -s -r -p "Pressione qualquer tecla para continuar..."
                ;;
            3)
                log_info "Saindo do painel de manutenção. Até breve!"
                exit 0
                ;;
            *)
                log_warn "Opção inválida. Escolha entre 1, 2 ou 3."
                sleep 2
                ;;
        esac
    done
}

# Inicia execução
main "$@"

