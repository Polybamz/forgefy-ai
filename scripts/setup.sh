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

# The model runtime shares the server with Postgres, the API and the OS:
# leave them ~8 GB and give the runtime the rest.
mem_limit=$(( ram_gb > 12 ? ram_gb - 8 : 4 ))

# Suggest a model (README "Choosing a model") that fits in that limit.
if [ -n "$gpu_name" ] && [ "$vram_gb" -ge 20 ]; then
  model="qwen3:30b"
elif [ -n "$gpu_name" ] && [ "$vram_gb" -ge 10 ]; then
  model="qwen3:14b"
elif [ "$mem_limit" -ge 22 ]; then
  model="qwen3:30b"
elif [ "$mem_limit" -ge 8 ]; then
  model="qwen3:8b"
elif [ "$mem_limit" -ge 4 ]; then
  model="qwen3:4b"
  warn "Little memory: qwen3:4b fits, but its blueprints and descriptions will be noticeably weaker."
else
  die "Under ~8 GB RAM is too small to run a useful model next to the other services."
fi
say "Suggested: DEFAULT_MODEL=$model, OLLAMA_MEMORY_LIMIT=${mem_limit}g"

# ── 3. .env ──────────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  cp .env.example .env
  set_env MODELS "\"$model\""
  set_env DEFAULT_MODEL "$model"
  set_env OLLAMA_MEMORY_LIMIT "${mem_limit}g"
  say "Created .env with the suggested model and memory limit"
else
  say ".env already exists — leaving its model and capacity settings as they are"
fi

if ! grep -qE '^POSTGRES_PASSWORD=.{24,}' .env; then
  password=$(openssl rand -hex 24 2>/dev/null || head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')
  set_env POSTGRES_PASSWORD "$password"
  say "Generated a database password (in .env)"
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
  3. Download models:  ./scripts/pull-models.sh
  4. Create a key for each application:
       docker compose exec api python -m app.cli keys create --app forgefy --env production
       docker compose exec api python -m app.cli keys create --app vndor --env production
  5. Test:             ./scripts/test.sh https://$domain <a key>
  6. Benchmark:        docker compose exec api python -m app.bench
EOF
