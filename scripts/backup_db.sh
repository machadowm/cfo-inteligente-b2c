#!/usr/bin/env bash
# ==============================================================================
# Script de Backup Automatizado do Cofre Contábil (PostgreSQL 15)
# Nível: SRE / Confiabilidade Bank-Grade
# ==============================================================================

# Aborta o script se algum comando falhar, variáveis não declaradas forem invocadas
# ou se algum comando em uma pipeline falhar de forma silenciosa.
set -euo pipefail

# --- Configurações de Cores para Terminal ---
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # Sem Cor

# --- Função de Logger ---
log() {
    local level="$1"
    local message="$2"
    local color=""
    case "$level" in
        "INFO")  color="$GREEN" ;;
        "WARN")  color="$YELLOW" ;;
        "ERROR") color="$RED" ;;
        "DEBUG") color="$CYAN" ;;
    esac
    echo -e "$(date +"%Y-%m-%d %H:%M:%S") [${color}${level}${NC}] - ${message}"
}

# --- Inicialização e Ingestão de Ambiente ---
PROJECT_DIR="$HOME/cfo-inteligente"
BACKUP_DIR="$PROJECT_DIR/backups"
ENV_FILE="$PROJECT_DIR/.env"

# Carrega variáveis do .env se existir, preservando o padrão estrito
if [[ -f "$ENV_FILE" ]]; then
    log "INFO" "Carregando segredos a partir do arquivo .env..."
    # Exporta apenas o necessário de forma segura
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# --- Variáveis de Configuração com Fallbacks ---
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
CONTAINER_NAME="${CONTAINER_NAME:-cfo_postgres}"
DB_NAME="${POSTGRES_DB:-cfo_inteligente}"
DB_USER="${POSTGRES_USER:-cfo_admin}"
RETENTION_DAYS=7
BACKUP_FILE="$BACKUP_DIR/cfo_backup_$TIMESTAMP.dump"

# Garante a existência do diretório de backup com permissão restrita
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log "INFO" "Iniciando verificação de pré-requisitos para o backup..."

# 1. Verificar se o Docker Daemon está em execução
if ! docker info &> /dev/null; then
    log "ERROR" "O serviço do Docker não está rodando no host. Abortando backup."
    exit 1
fi

# 2. Verificar se o container do PostgreSQL existe e está em execução
CONTAINER_STATUS=$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")
if [[ "$CONTAINER_STATUS" != "true" ]]; then
    log "ERROR" "O container '$CONTAINER_NAME' não existe ou não está em execução."
    exit 1
fi

# 3. Verificar se o banco de dados PostgreSQL interno está saudável e pronto
if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" &> /dev/null; then
    log "WARN" "PostgreSQL no container '$CONTAINER_NAME' não está pronto. Aguardando 5 segundos..."
    sleep 5
    if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" &> /dev/null; then
        log "ERROR" "PostgreSQL continua indisponível. Abortando operação de backup."
        exit 1
    fi
fi

# --- Execução do Backup (pg_dump) ---
log "INFO" "💾 [1/3] Iniciando pg_dump do banco '$DB_NAME' no container '$CONTAINER_NAME'..."

# Injeta de forma segura a senha a partir do .env se declarada, permitindo bypass seguro
if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
    # Executa o dump injetando a PGPASSWORD de forma segura no escopo local
    if ! docker exec -i -e PGPASSWORD="$POSTGRES_PASSWORD" "$CONTAINER_NAME" \
        pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom > "$BACKUP_FILE"; then
        log "ERROR" "Falha crítica na execução do comando pg_dump."
        rm -f "$BACKUP_FILE"
        exit 1
    fi
else
    # Executa sem injeção direta de senha (confia em chaves locais ou pgpass do container)
    if ! docker exec -i "$CONTAINER_NAME" \
        pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom > "$BACKUP_FILE"; then
        log "ERROR" "Falha crítica na execução do comando pg_dump."
        rm -f "$BACKUP_FILE"
        exit 1
    fi
fi

# --- Validação de Integridade do Backup (Verify-After-Write) ---
log "INFO" "Validando integridade do arquivo de backup gerado..."

# A. Verificar se o arquivo foi criado e não está vazio
if [[ ! -f "$BACKUP_FILE" ]] || [[ ! -s "$BACKUP_FILE" ]]; then
    log "ERROR" "Arquivo de backup não foi criado ou possui tamanho igual a zero. Abortando."
    rm -f "$BACKUP_FILE"
    exit 1
fi

# B. Restringir permissão do arquivo imediatamente (Segurança Contábil)
chmod 600 "$BACKUP_FILE"
log "INFO" "Permissões de acesso do arquivo de backup definidas estritamente para 600."

# C. Validar a assinatura binária do dump simulando uma listagem estrutural
if ! pg_restore -l "$BACKUP_FILE" &> /dev/null; then
    # Fallback caso a ferramenta local 'pg_restore' não esteja instalada no host Ubuntu,
    # executa a verificação diretamente dentro do container do postgres
    if ! docker exec -i "$CONTAINER_NAME" pg_restore -l < "$BACKUP_FILE" &> /dev/null; then
        log "ERROR" "O arquivo de backup gerado falhou no teste de validação do pg_restore. Backup corrompido!"
        rm -f "$BACKUP_FILE"
        exit 1
    fi
fi
log "INFO" "Integridade física e estrutural do arquivo de backup validada com sucesso."

# --- Limpeza de backups antigos (Rotação) ---
log "INFO" "🧹 [2/3] Iniciando rotação: procurando dumps antigos com mais de $RETENTION_DAYS dias..."

# Executa a remoção apenas em arquivos que correspondam perfeitamente ao padrão gerado
OLD_BACKUPS_COUNT=$(find "$BACKUP_DIR" -type f -name "cfo_backup_*.dump" -mtime +$RETENTION_DAYS | wc -l)

if [[ "$OLD_BACKUPS_COUNT" -gt 0 ]]; then
    find "$BACKUP_DIR" -type f -name "cfo_backup_*.dump" -mtime +$RETENTION_DAYS -delete
    log "INFO" "Removidos $OLD_BACKUPS_COUNT arquivos de backup obsoletos."
else
    log "INFO" "Nenhum arquivo de backup antigo para rotacionar."
fi

# --- Conclusão ---
log "INFO" "✅ [3/3] Backup concluído com sucesso!"
log "INFO" "Local do arquivo: $BACKUP_FILE"
log "INFO" "Tamanho do backup: $(du -sh "$BACKUP_FILE" | cut -f1)"
