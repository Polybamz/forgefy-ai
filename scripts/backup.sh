#!/usr/bin/env bash
# Back up the database (API keys and usage log) to backups/, keeping the last 14.
#   ./scripts/backup.sh
#
# Daily, from cron (crontab -e):
#   30 3 * * * cd /opt/forgefy-models && ./scripts/backup.sh >> backups/backup.log 2>&1
#
# A backup on the same server is lost with the server: copy backups/ — and .env,
# which holds the database password — somewhere else, e.g.
#   rclone copy backups/ remote:ai-platform-backups
# Models are not backed up: scripts/pull-models.sh downloads them again.
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p backups
file="backups/ai-$(date -u +%Y%m%d-%H%M%S).sql.gz"

docker compose exec -T postgres pg_dump -U ai -d ai --clean --if-exists | gzip > "$file.partial"
mv "$file.partial" "$file"
chmod 600 "$file"
echo "$(date -u '+%F %T') backup written: $file ($(du -h "$file" | cut -f1))"

# Keep the newest 14.
ls -1t backups/ai-*.sql.gz 2>/dev/null | tail -n +15 | xargs -r rm --
