#!/usr/bin/env bash
# Download the models listed in MODELS (.env), or the ones given as arguments:
#   ./scripts/pull-models.sh                 # everything in MODELS
#   ./scripts/pull-models.sh qwen3:14b       # one more model
# Re-running is cheap: models already downloaded are only checked for updates.
set -euo pipefail

cd "$(dirname "$0")/.."
[ -f .env ] || { echo "No .env — run ./scripts/setup.sh first" >&2; exit 1; }

if [ "$#" -gt 0 ]; then
  models="$*"
else
  models=$(grep -E '^MODELS=' .env | cut -d= -f2- | tr -d '"')
fi
[ -n "$models" ] || { echo "MODELS is empty in .env" >&2; exit 1; }

docker compose ps --status running --services | grep -qx ollama \
  || { echo "ollama is not running — start it with: docker compose up -d" >&2; exit 1; }

for model in $models; do
  echo "==> pulling $model"
  docker compose exec -T ollama ollama pull "$model"
done

echo
echo "==> models on this server:"
docker compose exec -T ollama ollama list
