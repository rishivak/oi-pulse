$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..")

function Test-Http([string]$url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 3
        return $response.StatusCode -ge 200 -and $response.StatusCode -lt 500
    } catch {
        return $false
    }
}

function Wait-ForHttp([string]$url, [int]$attempts = 30) {
    for ($i = 0; $i -lt $attempts; $i++) {
        if (Test-Http $url) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

Push-Location $root

& ".\infra\scripts\podman-up.ps1" -Full

if (-not (Wait-ForHttp "http://localhost:8000/api/health" 30)) {
    throw "Backend did not become healthy on http://localhost:8000/api/health"
}

if (-not (Wait-ForHttp "http://localhost:8080" 30)) {
    throw "Nginx entrypoint did not become ready on http://localhost:8080"
}

Write-Host "Local stack is ready:"
Write-Host "  Frontend: http://localhost:3000"
Write-Host "  Backend:  http://localhost:8000/api/health"
Write-Host "  App URL:  http://localhost:8080"

Pop-Location