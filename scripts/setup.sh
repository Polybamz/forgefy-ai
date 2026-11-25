#!/usr/bin/env bash
# First-time setup on the VPS. Safe to re-run: it never overwrites an existing
# password or .env value.
#
#   ./scripts/setup.sh ai.example.com
#
# 1. checks Docker (and the NVIDIA toolkit when there is a GPU)
# 2. creates .env with a random database password and your domain
# 3. sizes the model and its memory cap to this server's RAM / GPU
set -euo pipefail

cd "$(dirname "$0")/.."
DOMAIN_ARG="${1:-}"

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }
set_env() {  # set_env KEY VALUE — replace the line for KEY in .env
  sed -i "s|^$1=.*|$1=$2|" .env
}

# Files that only make sense on the PC they were made on (e.g. trusting an
# antivirus's HTTPS certificate) — copied here by mistake, they'd apply on the server.
if [ -f docker-compose.override.yml ] || [ -d local-ca ]; then
  warn "docker-compose.override.yml / local-ca/ are local-only fixes from another machine."
  warn "Remove them on this server:  rm -rf docker-compose.override.yml local-ca"
fi

# ── 1. Docker ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
  die "Docker is not installed. Install it with:
    curl -fsSL https://get.docker.com | sh
then re-run this script."
fi
docker compose version >/dev/null 2>&1 || die "The Docker Compose plugin is missing (docker compose version failed)."
say "Docker $(docker --version | awk '{print $3}' | tr -d ,) found"

# ── 2. Hardware ──────────────────────────────────────────────────────────────
ram_gb=$(awk '/MemTotal/ {printf "%d", $2/1024/1024 + 0.5}' /proc/meminfo)
cpus=$(nproc)
gpu_name=""
vram_gb=0
if command -v nvidia-smi >/dev/null 2>&1 && nvidia-smi >/dev/null 2>&1; then
  gpu_name=$(nvidia-smi --query-gpu=name --format=csv,noheader | head -n1)
  vram_gb=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -n1 | awk '{printf "%d", $1/1024}')
fi

if [ -n "$gpu_name" ]; then
  say "GPU: $gpu_name (${vram_gb} GB VRAM), RAM: ${ram_gb} GB, CPUs: $cpus"
  if ! docker info 2>/dev/null | grep -qi nvidia; then
    warn "NVIDIA Container Toolkit not detected — Docker cannot use the GPU yet. Install it:"
    warn "  https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html"
  fi
else
  say "No NVIDIA GPU — CPU only. RAM: ${ram_gb} GB, CPUs: $cpus"
fi

# The model runtime shares the server with the speech service (3 GB), Postgres,
# the API and the OS: leave them ~10 GB and give the runtime the rest.
mem_limit=$(( ram_gb > 14 ? ram_gb - 10 : 4 ))
# Speech gets half the cores, so transcribing a recording doesn't stall chat.
speech_cpus=$(( cpus / 2 > 2 ? cpus / 2 : 2 ))

# Pick a set of models from several families (MODELS.md) sized to that limit.
# Each role — general, fast, code, reasoning, vision — gets the best that
# fits; "others" are offered by name for apps that want them. The big coding
# and assistant models (qwen3-coder:30b, devstral:24b, gpt-oss:20b) are 14-19 GB
# each, so they only appear where two of them fit in memory at once.
embed="bge-m3"
loaded=2
if { [ -n "$gpu_name" ] && [ "$vram_gb" -ge 40 ]; } || [ "$mem_limit" -ge 40 ]; then
  general="qwen3:14b"; fast="qwen3:4b"; code="qwen3-coder:30b"; reasoning="deepseek-r1:14b"; vision="gemma3:12b"
  others="devstral:24b gpt-oss:20b phi4:14b mistral-nemo:12b"
elif { [ -n "$gpu_name" ] && [ "$vram_gb" -ge 20 ]; } || [ "$mem_limit" -ge 22 ]; then
  general="qwen3:14b"; fast="qwen3:4b"; code="qwen2.5-coder:14b"; reasoning="deepseek-r1:14b"; vision="gemma3:12b"
  others="phi4:14b mistral-nemo:12b"
elif { [ -n "$gpu_name" ] && [ "$vram_gb" -ge 10 ]; } || [ "$mem_limit" -ge 8 ]; then
  general="qwen3:8b"; fast="qwen3:4b"; code="qwen2.5-coder:7b"; reasoning="deepseek-r1:8b"; vision="gemma3:4b"
  others="mistral:7b llama3.1:8b phi4-mini:3.8b"
elif [ "$mem_limit" -ge 4 ]; then
  general="qwen3:4b"; fast="qwen3:1.7b"; code="qwen2.5-coder:3b"; reasoning="deepseek-r1:1.5b"; vision="gemma3:4b"
  others="phi4-mini:3.8b"; loaded=1
  warn "Little memory: only small models fit, one at a time — expect noticeably weaker answers."
else
  die "Under ~8 GB RAM is too small to run a useful model next to the other services."
fi
chat_models=$(printf '%s\n' "$general" "$fast" "$code" $others "$reasoning" "$vision" | awk '!seen[$0]++' | paste -sd, -)
all_models="$(echo "$chat_models" | tr , ' ') $embed"
aliases="general=$general,fast=$fast,code=$code,reasoning=$reasoning,vision=$vision,embed=$embed,claude-*=$general"
say "Suggested models: ${chat_models//,/, } + $embed (embeddings); memory limit ${mem_limit}g"

# ── 3. .env ──────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  set_env MODELS "\"$all_models\""
  set_env DEFAULT_MODEL "$general"
  set_env CHAT_MODELS "$chat_models"
  set_env EMBEDDING_MODELS "$embed"
  set_env MODEL_ALIASES "$aliases"
  set_env OLLAMA_MEMORY_LIMIT "${mem_limit}g"
  set_env OLLAMA_MAX_LOADED_MODELS "$loaded"
  set_env SPEECH_CPUS "$speech_cpus"
  say "Created .env with the suggested models, roles and memory limit"
else
  say ".env already exists — leaving its model and capacity settings as they are"
fi

if ! grep -qE '^POSTGRES_PASSWORD=.{24,}' .env; then
  password=$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  set_env POSTGRES_PASSWORD "$password"
  say "Generated a database password (in .env)"
fi
if ! grep -qE '^ADMIN_TOKEN=.{32,}' .env; then
  grep -q '^ADMIN_TOKEN=' .env || echo "ADMIN_TOKEN=" >> .env  # .env made before the console existed
  admin=$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  set_env ADMIN_TOKEN "$admin"
  say "Generated an admin console token (ADMIN_TOKEN in .env)"
fi
chmod 600 .env

if [ -n "$DOMAIN_ARG" ]; then
  set_env DOMAIN "$DOMAIN_ARG"
fi
domain=$(grep -E '^DOMAIN=' .env | cut -d= -f2-)
if [ "$domain" = "ai.example.com" ] || [ -z "$domain" ]; then
  warn "DOMAIN is still the placeholder. Re-run with your domain: ./scripts/setup.sh ai.yourdomain.com"
elif [ "$domain" != ":80" ]; then
  ip=$(curl -fsS4 --max-time 5 https://api.ipify.org 2>/dev/null || true)
  resolved=$( (getent ahostsv4 "$domain" 2>/dev/null || true) | awk 'NR==1 {print $1}')
  if [ -z "$ip" ]; then
    warn "Could not look up this server's public IP — check that $domain points at it yourself."
  elif [ "$resolved" != "$ip" ]; then
    warn "$domain resolves to '${resolved:-nothing}', but this server is $ip."
    warn "Point an A record for $domain at $ip, or Caddy cannot get a certificate."
  else
    say "$domain points at this server"
  fi
fi

# The API container runs as uid 10001 and writes benchmark reports here.
mkdir -p benchmarks backups
chown 10001 benchmarks 2>/dev/null || chmod a+w benchmarks

# ── 4. Next steps ────────────────────────────────────────────────────────────
compose="docker compose"
[ -n "$gpu_name" ] && compose="docker compose -f docker-compose.yml -f docker-compose.gpu.yml"
cat <<EOF

Next:
  1. Harden the server (firewall, SSH, automatic updates) if not done yet:
       sudo ./scripts/harden.sh
  2. Start:            $compose up -d --build
  3. Download models:  ./scripts/pull-models.sh     (several GB — takes a while)
  4. Open the admin console:  https://$domain/admin
     sign in with ADMIN_TOKEN (grep ADMIN_TOKEN .env), then create a key for
     each app under "API keys" — or from the command line:
       docker compose exec api python -m app.cli keys create --app forgefy --env production
  5. Test:             ./scripts/test.sh https://$domain <a key>
  6. Compare models:   docker compose exec api python -m app.bench
     then adjust which model fills each role (MODEL_ALIASES in .env)
EOF
