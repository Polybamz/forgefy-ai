#!/usr/bin/env bash
# Restore the database from a backup made by backup.sh:
#   ./scripts/restore.sh backups/ai-20261123-033000.sql.gz
#
# Replaces the current API keys and usage log with the backup's. On a new
# server: restore .env first (same POSTGRES_PASSWORD), start the stack, then
# run this. Practise it once before you need it.
set -euo pipefail

cd "$(dirname "$0")/.."
file="${1:-}"
[ -f "$file" ] || { echo "usage: $0 backups/ai-YYYYmmdd-HHMMSS.sql.gz" >&2; exit 2; }

read -r -p "Replace the current database with $file? Type 'restore' to continue: " answer
[ "$answer" = "restore" ] || { echo "Cancelled."; exit 1; }

docker compose stop api
gunzip -c "$file" | docker compose exec -T postgres psql -q -v ON_ERROR_STOP=1 -U ai -d ai
docker compose start api
echo "Restored from $file. Check: docker compose exec api python -m app.cli keys list"
