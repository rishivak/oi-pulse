$ErrorActionPreference = "Stop"

& (Join-Path $PSScriptRoot "podman-down.ps1")

$volumes = @(
    "oi_pulse_postgres_data",
    "oi_pulse_redis_data"
)

foreach ($name in $volumes) {
    podman volume inspect $name *> $null
    if ($LASTEXITCODE -eq 0) {
        podman volume rm -f $name | Out-Host
    }
}

podman network inspect "oi-pulse-dev" *> $null
if ($LASTEXITCODE -eq 0) {
    podman network rm "oi-pulse-dev" | Out-Host
}

Write-Host "Removed Podman local stack volumes and network."