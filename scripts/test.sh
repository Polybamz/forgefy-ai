#!/usr/bin/env bash
# End-to-end check through the public endpoint, the way your apps call it:
#   ./scripts/test.sh https://ai.example.com <api-key>
#
# Checks: health, that a request without a key is refused, that the key works,
# that the models are downloaded, then asks the model a question in OpenAI
# format and in Ollama format and shows the answers and the time taken.
set -euo pipefail

[ "$#" -ge 2 ] || { echo "usage: $0 <url> <api-key>" >&2; exit 2; }
url="${1%/}"; key="$2"

pass() { printf '\033[1;32mok\033[0m  %s\n' "$*"; }
fail() { printf '\033[1;31mFAIL\033[0m %s\n' "$*" >&2; exit 1; }

# JSON parsing: python3 (on most VPS images) or jq.
if python3 -c 'pass' >/dev/null 2>&1; then
  json() { python3 -c "import json,sys; d=json.load(sys.stdin); print(eval(sys.argv[1], {'d': d}))" "$1"; }
elif command -v jq >/dev/null 2>&1; then
  json() { jq -r "$2"; }
else
  fail "needs python3 or jq to read the server's replies: sudo apt install -y jq"
fi

curl -fsS --max-time 10 "$url/healthz" >/dev/null || fail "$url/healthz is unreachable (DNS, firewall, or containers down?)"
pass "reachable: $url"

code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$url/api/v1/health")
[ "$code" = "401" ] || fail "a request without a key got HTTP $code, expected 401"
pass "requests without a key are refused"

health=$(curl -fsS --max-time 15 -H "Authorization: Bearer $key" "$url/api/v1/health") || fail "the API key was rejected"
pass "key accepted — app: $(printf '%s' "$health" | json 'd["app"]' '.app')"
runtime=$(printf '%s' "$health" | json 'd["runtime"]' '.runtime')
[ "$runtime" = "ok" ] || fail "model runtime: $runtime"
missing=$(printf '%s' "$health" | json '" ".join(d["models_missing"])' '.models_missing | join(" ")')
[ -z "$missing" ] || fail "models not downloaded yet: $missing — run ./scripts/pull-models.sh on the server"
pass "models on offer: $(printf '%s' "$health" | json '" ".join(d["models_offered"])' '.models_offered | join(" ")')"

ask() {  # ask <path> <body> → prints the HTTP body, fails on non-200
  local reply status
  reply=$(curl -sS --max-time 900 -H "Authorization: Bearer $key" -H "Content-Type: application/json" \
    -w '\n%{http_code}' "$url$1" -d "$2")
  status=$(printf '%s' "$reply" | tail -n1)
  [ "$status" = "200" ] || fail "$1 returned HTTP $status: $(printf '%s' "$reply" | sed '$d')"
  printf '%s' "$reply" | sed '$d'
}

echo "..  asking in OpenAI format (the first call loads the model into memory — can take a minute)"
start=$(date +%s)
reply=$(ask /v1/chat/completions '{"model": "default", "messages": [{"role": "user", "content": "In one sentence: what is a to-do list app?"}]}')
pass "OpenAI format ($(( $(date +%s) - start ))s): $(printf '%s' "$reply" | json 'd["choices"][0]["message"]["content"].strip()[-160:]' '.choices[0].message.content | .[-160:]')"

start=$(date +%s)
reply=$(ask /api/chat '{"model": "default", "stream": false, "messages": [{"role": "user", "content": "Say hello in French."}]}')
pass "Ollama format ($(( $(date +%s) - start ))s): $(printf '%s' "$reply" | json 'd["message"]["content"].strip()[-160:]' '.message.content | .[-160:]')"
