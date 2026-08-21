#!/usr/bin/env bash
# ==============================================================================
# Script de Backup Automatizado do Cofre Contábil (PostgreSQL 15)
# Gera: Dump Binário de Recuperação (.dump) e SQL Plano Limpo (.sql)
# Nível: SRE / Confiabilidade Bank-Grade
# ==============================================================================

# Aborta o script caso qualquer comando falhe, variáveis vazias sejam chamadas
# ou falhas silenciosas ocorram em pipelines.
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

# Carrega segredos e variáveis do .env se existir, preservando o padrão estrito
if [[ -f "$ENV_FILE" ]]; then
    log "INFO" "Carregando segredos a partir do arquivo .env..."
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

# --- Variáveis de Configuração com Fallbacks Seguros ---
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
CONTAINER_NAME="${CONTAINER_NAME:-cfo_postgres}"
DB_NAME="${POSTGRES_DB:-cfo_inteligente}"
DB_USER="${POSTGRES_USER:-cfo_admin}"
RETENTION_DAYS=7

# Nomes dos arquivos de saída
BACKUP_BIN_FILE="$BACKUP_DIR/cfo_backup_$TIMESTAMP.dump"
BACKUP_SQL_FILE="$BACKUP_DIR/cfo_clean_$TIMESTAMP.sql"

# Garante a existência do diretório de backup com permissão restrita (700)
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log "INFO" "Iniciando verificação de pré-requisitos para a salvaguarda..."

# 1. Verificar se o Docker Daemon está em execução no host
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

# 3. Verificar se o banco de dados interno está saudável e pronto para conexões
if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" &> /dev/null; then
    log "WARN" "PostgreSQL no container '$CONTAINER_NAME' não está pronto. Aguardando 5 segundos..."
    sleep 5
    if ! docker exec "$CONTAINER_NAME" pg_isready -U "$DB_USER" -d "$DB_NAME" &> /dev/null; then
        log "ERROR" "PostgreSQL continua indisponível após timeout. Abortando."
        exit 1
    fi
fi

# Configura a senha de forma segura no escopo local da execução
PG_PASS_ENV=""
if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
    PG_PASS_ENV="-e PGPASSWORD=$POSTGRES_PASSWORD"
fi

# --- Execução do Backup Binário (.dump) ---
log "INFO" "💾 [1/4] Gerando dump binário compactado (.dump)..."
# shellcheck disable=SC2086
if ! docker exec -i $PG_PASS_ENV "$CONTAINER_NAME" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" --format=custom > "$BACKUP_BIN_FILE"; then
    log "ERROR" "Falha crítica na execução do comando pg_dump (formato binário)."
    rm -f "$BACKUP_BIN_FILE"
    exit 1
fi

# --- Execução do Backup em SQL Limpo (.sql) ---
log "INFO" "💾 [2/4] Gerando script SQL em texto limpo (.sql)..."
# shellcheck disable=SC2086
if ! docker exec -i $PG_PASS_ENV "$CONTAINER_NAME" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" --format=plain > "$BACKUP_SQL_FILE"; then
    log "ERROR" "Falha crítica na execução do comando pg_dump (formato SQL limpo)."
    rm -f "$BACKUP_BIN_FILE" "$BACKUP_SQL_FILE"
    exit 1
fi

# --- Validação de Integridade e Proteção de Acesso ---
log "INFO" "🔒 [3/4] Validando integridade física dos arquivos e configurando privilégios..."

# A. Aplica imediatamente o Princípio do Privilégio Mínimo (chmod 600)
chmod 600 "$BACKUP_BIN_FILE" "$BACKUP_SQL_FILE"
log "INFO" "Permissões de acesso aos arquivos definidas estritamente para 600 (Apenas proprietário)."

# B. Validação do arquivo binário (.dump) simulando leitura estrutural do cabeçalho
if ! docker exec -i "$CONTAINER_NAME" pg_restore -l < "$BACKUP_BIN_FILE" &> /dev/null; then
    log "ERROR" "O arquivo binário gerado falhou na validação de integridade estrutural (pg_restore)."
    rm -f "$BACKUP_BIN_FILE" "$BACKUP_SQL_FILE"
    exit 1
fi

# C. Validação do arquivo SQL (.sql) procurando tag de sucesso
if ! tail -n 10 "$BACKUP_SQL_FILE" | grep -q "PostgreSQL database dump complete"; then
    log "ERROR" "O arquivo SQL limpo gerado está incompleto ou corrompido (falha na assinatura final)."
    rm -f "$BACKUP_BIN_FILE" "$BACKUP_SQL_FILE"
    exit 1
fi

log "INFO" "Integridade física e estrutural de ambos os backups validada com sucesso absoluto."

# --- Limpeza de backups antigos (Rotação de Retenção) ---
log "INFO" "🧹 [4/4] Rotacionando backups antigos com mais de $RETENTION_DAYS dias..."

# Encontra e conta arquivos obsoletos correspondentes ao padrão do projeto
OLD_BACKUPS_COUNT=$(find "$BACKUP_DIR" -type f \( -name "cfo_backup_*.dump" -o -name "cfo_clean_*.sql" \) -mtime +$RETENTION_DAYS | wc -l)

if [[ "$OLD_BACKUPS_COUNT" -gt 0 ]]; then
    find "$BACKUP_DIR" -type f \( -name "cfo_backup_*.dump" -o -name "cfo_clean_*.sql" \) -mtime +$RETENTION_DAYS -delete
    log "INFO" "Removidos $OLD_BACKUPS_COUNT arquivos de backup obsoletos do armazenamento."
else
    log "INFO" "Nenhum arquivo de backup obsoleto encontrado para rotatividade."
fi

# --- Relatório Final de Conclusão ---
log "INFO" "✅ Processo de backup duplo concluído com sucesso!"
log "INFO" "Dump Binário de Restauração: $BACKUP_BIN_FILE ($(du -sh "$BACKUP_BIN_FILE" | cut -f1))"
log "INFO" "SQL Limpo de Auditoria: $BACKUP_SQL_FILE ($(du -sh "$BACKUP_SQL_FILE" | cut -f1))"

