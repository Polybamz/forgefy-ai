# Same check as test.sh, from a Windows PC (Windows PowerShell 5.1 or 7):
#   .\scripts\test.ps1 -Url https://ai.example.com -Key <api-key>
param(
    [Parameter(Mandatory = $true)][string]$Url,
    [Parameter(Mandatory = $true)][string]$Key
)
$ErrorActionPreference = "Stop"
$Url = $Url.TrimEnd("/")
$auth = @{ Authorization = "Bearer $Key" }

function Pass($msg) { Write-Host "ok   $msg" -ForegroundColor Green }
function Fail($msg) { Write-Host "FAIL $msg" -ForegroundColor Red; exit 1 }

try { Invoke-RestMethod "$Url/healthz" -TimeoutSec 10 | Out-Null } catch { Fail "$Url/healthz is unreachable: $($_.Exception.Message)" }
Pass "reachable: $Url"

try {
    Invoke-WebRequest "$Url/api/v1/health" -TimeoutSec 10 -UseBasicParsing | Out-Null
    Fail "a request without a key was accepted"
} catch {
    $status = $_.Exception.Response.StatusCode.value__
    if ($status -ne 401) { Fail "a request without a key got HTTP $status, expected 401" }
}
Pass "requests without a key are refused"

try { $health = Invoke-RestMethod "$Url/api/v1/health" -Headers $auth -TimeoutSec 15 } catch { Fail "the API key was rejected: $($_.Exception.Message)" }
Pass "key accepted - application: $($health.app)"
if ($health.runtime -ne "ok") { Fail "model runtime: $($health.runtime)" }
if (@($health.models).Count -eq 0) { Fail "no models downloaded yet - run ./scripts/pull-models.sh on the server" }
Pass "models on the server: $(@($health.models) -join ', ')"

if ($health.app -eq "forgefy") {
    $path = "/api/v1/agents/forgefy/requirements"
    $body = @{ transcript = "Amina: Patients must be able to book an appointment online.`nPaul: And they need an SMS reminder the day before.`nAmina: Payments can come later, that is nice to have." }
} else {
    $path = "/api/v1/agents/vndor/product-description"
    $body = @{ name = "Handwoven raffia tote"; facts = @{ material = "natural raffia"; size = "40 x 30 cm" }; language = "en" }
}

Write-Host "..   running $path (the first call loads the model into memory - can take a minute)"
try {
    $r = Invoke-RestMethod "$Url$path" -Method Post -Headers $auth -ContentType "application/json; charset=utf-8" `
        -Body ([Text.Encoding]::UTF8.GetBytes(($body | ConvertTo-Json -Depth 5))) -TimeoutSec 900
} catch { Fail "agent call failed: $($_.Exception.Message) $($_.ErrorDetails.Message)" }

if ($health.app -eq "forgefy") { Pass "answer: $($r.result.summary)" } else { Pass "answer: $($r.result.short_description)" }
Pass "latency: $($r.latency_ms) ms, speed: $($r.usage.tokens_per_second) tokens/s"
if (@($r.warnings).Count -gt 0) { Write-Host "!!   warnings: $(@($r.warnings) -join '; ')" -ForegroundColor Yellow }
