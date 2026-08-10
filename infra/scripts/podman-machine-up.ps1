$ErrorActionPreference = "Stop"

$machines = podman machine list --format json | ConvertFrom-Json
$defaultMachine = $machines | Where-Object { $_.Default -eq $true } | Select-Object -First 1

if (-not $defaultMachine) {
    Write-Host "No default Podman machine found. Initializing podman-machine-default..."
    podman machine init | Out-Host
}

podman machine start | Out-Host
podman machine list | Out-Host