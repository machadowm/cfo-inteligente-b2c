#!/usr/bin/env bash
# ==============================================================================
# Script de Backup Automatizado do Cofre Contábil (PostgreSQL 15)
# ==============================================================================

set -euo pipefail

BACKUP_DIR="$HOME/cfo-inteligente/backups"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
CONTAINER_NAME="cfo_postgres"
DB_NAME="cfo_inteligente"
DB_USER="cfo_admin"
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"

echo "💾 [1/3] Iniciando backup do banco de dados '$DB_NAME'..."
docker exec -t "$CONTAINER_NAME" pg_dump -U "$DB_USER" -d "$DB_NAME" --format=c > "$BACKUP_DIR/cfo_backup_$TIMESTAMP.dump"

echo "🧹 [2/3] Removendo backups com mais de $RETENTION_DAYS dias..."
find "$BACKUP_DIR" -type f -name "cfo_backup_*.dump" -mtime +$RETENTION_DAYS -delete

echo "✅ [3/3] Backup concluído com sucesso em: $BACKUP_DIR/cfo_backup_$TIMESTAMP.dump"
