#!/usr/bin/env bash
# ==============================================================================
# Script de Backup Automatizado - Projeto CFO Inteligente B2C
# Foco: Geração de Script SQL Limpo do Schema PUBLIC (13 Tabelas de Ouro)
# Nível: SRE / Confiabilidade Bank-Grade
# ==============================================================================

set -euo pipefail

# --- Configurações de Cores ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "$(date +"%Y-%m-%d %H:%M:%S") [$1] - $2"; }

# --- Configurações de Ambiente ---
PROJECT_DIR="$HOME/cfo-inteligente"
BACKUP_DIR="$PROJECT_DIR/backups"
ENV_FILE="$PROJECT_DIR/.env"

# Carrega variáveis do .env se existir
if [[ -f "$ENV_FILE" ]]; then
    set -a; source "$ENV_FILE"; set +a
fi

# Variáveis locais (Ajustadas conforme auditoria da máquina local)
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
CONTAINER_NAME="cfo_postgres"
DB_NAME="${POSTGRES_DB:-cfo_b2c}"
DB_USER="${POSTGRES_USER:-admin}"
RETENTION_DAYS=7

# Nome do arquivo de saída (O Script SQL que você solicitou)
BACKUP_SQL_FILE="$BACKUP_DIR/cfo_schema_public_$TIMESTAMP.sql"

mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

log "${GREEN}INFO${NC}" "Iniciando extração do Script SQL atual do banco public..."

# Verificação de segurança: Senha do banco
PG_PASS_ENV=""
if [[ -n "${POSTGRES_PASSWORD:-}" ]]; then
    PG_PASS_ENV="-e PGPASSWORD=$POSTGRES_PASSWORD"
fi

# --- Execução do pg_dump para Script SQL Limpo ---
# Explicação dos parâmetros:
# -n public: Extrai APENAS o schema public (suas 13 tabelas)
# --format=plain: Gera o script em texto puro (SQL)
# --no-owner: Remove comandos de mudança de dono (facilita leitura e portabilidade)
# --no-privileges: Remove GRANT/REVOKE específicos
if ! docker exec -i $PG_PASS_ENV "$CONTAINER_NAME" \
    pg_dump -U "$DB_USER" -d "$DB_NAME" \
    -n public \
    --format=plain \
    --no-owner \
    --no-privileges \
    --clean \
    --if-exists > "$BACKUP_SQL_FILE"; then
    
    log "${RED}ERROR${NC}" "Falha crítica ao gerar o script SQL do schema public."
    exit 1
fi

# Aplica permissão restrita ao arquivo gerado
chmod 600 "$BACKUP_SQL_FILE"

# --- Validação de Sucesso ---
if grep -q "PostgreSQL database dump complete" "$BACKUP_SQL_FILE"; then
    log "${GREEN}SUCCESS${NC}" "Script SQL gerado com sucesso!"
    log "${GREEN}INFO${NC}" "Localização: $BACKUP_SQL_FILE"
    log "${GREEN}INFO${NC}" "Tamanho: $(du -sh "$BACKUP_SQL_FILE" | cut -f1)"
else
    log "${RED}ERROR${NC}" "O arquivo gerado parece estar incompleto ou corrompido."
    exit 1
fi

# --- Rotação de Backups Antigos ---
find "$BACKUP_DIR" -type f -name "cfo_schema_public_*.sql" -mtime +$RETENTION_DAYS -delete
log "${YELLOW}WARN${NC}" "Rotação concluída: backups com mais de $RETENTION_DAYS dias foram removidos."
