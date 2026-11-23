#!/usr/bin/env bash
# End-to-end check through the public endpoint, the way the applications call it:
#   ./scripts/test.sh https://ai.example.com <api-key>
#
# Checks: health, that a request without a key is refused, that the key works
# and which application it belongs to, then runs one of that application's
# agents on a small sample and shows the answer and the speed.
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
app=$(printf '%s' "$health" | json 'd["app"]' '.app')
runtime=$(printf '%s' "$health" | json 'd["runtime"]' '.runtime')
models=$(printf '%s' "$health" | json '" ".join(d["models"])' '.models | join(" ")')
pass "key accepted — application: $app"
[ "$runtime" = "ok" ] || fail "model runtime: $runtime"
[ -n "$models" ] || fail "no models downloaded yet — run ./scripts/pull-models.sh on the server"
pass "models on the server: $models"

if [ "$app" = "forgefy" ]; then
  path="/api/v1/agents/forgefy/requirements"
  body='{"transcript": "Amina: Patients must be able to book an appointment online.\nPaul: And they need an SMS reminder the day before.\nAmina: Payments can come later, that is nice to have."}'
  show='d["result"]["summary"]'; show_jq='.result.summary'
else
  path="/api/v1/agents/vndor/product-description"
  body='{"name": "Handwoven raffia tote", "facts": {"material": "natural raffia", "size": "40 x 30 cm"}, "language": "en"}'
  show='d["result"]["short_description"]'; show_jq='.result.short_description'
fi

echo "..  running $path (the first call loads the model into memory — can take a minute)"
reply=$(curl -sS --max-time 900 -H "Authorization: Bearer $key" -H "Content-Type: application/json" \
  -w '\n%{http_code}' "$url$path" -d "$body")
status=$(printf '%s' "$reply" | tail -n1)
reply=$(printf '%s' "$reply" | sed '$d')
[ "$status" = "200" ] || fail "agent call returned HTTP $status: $reply"

pass "answer: $(printf '%s' "$reply" | json "$show" "$show_jq")"
pass "latency: $(printf '%s' "$reply" | json 'd["latency_ms"]' '.latency_ms') ms, speed: $(printf '%s' "$reply" | json 'd["usage"]["tokens_per_second"]' '.usage.tokens_per_second') tokens/s"
warnings=$(printf '%s' "$reply" | json '"; ".join(d["warnings"])' '.warnings | join("; ")')
[ -z "$warnings" ] || echo "!!  warnings: $warnings"
