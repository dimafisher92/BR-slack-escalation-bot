#!/usr/bin/env bash
set -euo pipefail

DB_PATH="${DB_PATH:-/var/lib/escalation-guard/guard.db}"
BACKUP_DIR="${BACKUP_DIR:-/var/backups/escalation-guard}"
STAMP="$(date +%Y-%m-%d)"

mkdir -p "$BACKUP_DIR"
sqlite3 "$DB_PATH" ".backup '$BACKUP_DIR/guard-$STAMP.db'"
find "$BACKUP_DIR" -name 'guard-*.db' -mtime +14 -delete
