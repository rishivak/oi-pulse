param(
    [switch]$InfraOnly,
    [switch]$Full
)

$ErrorActionPreference = "Stop"

$networkName = "oi-pulse-dev"
$postgresVolume = "oi_pulse_postgres_data"
$redisVolume = "oi_pulse_redis_data"
$backendImage = "localhost/oi-pulse-backend:dev"
$frontendImage = "localhost/oi-pulse-frontend:dev"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$nginxConf = Join-Path $root "infra\nginx\nginx.podman.conf"

function Ensure-PodmanMachine {
    $running = podman machine list --format json | ConvertFrom-Json | Where-Object { $_.Running -eq $true }
    if (-not $running) {
        Write-Host "Starting Podman machine..."
        podman machine start | Out-Host
    }
}

function Ensure-Network {
    podman network inspect $networkName *> $null
    if ($LASTEXITCODE -ne 0) {
        podman network create $networkName | Out-Host
    }
}

function Ensure-Volume([string]$name) {
    podman volume inspect $name *> $null
    if ($LASTEXITCODE -ne 0) {
        podman volume create $name | Out-Host
    }
}

function Remove-Container([string]$name) {
    podman container exists $name *> $null
    if ($LASTEXITCODE -eq 0) {
        podman rm -f $name | Out-Host
    }
}

function Start-Postgres {
    Remove-Container "oi-pulse-postgres"
    podman run -d `
        --name oi-pulse-postgres `
        --network $networkName `
        -p 5432:5432 `
        -e POSTGRES_USER=oi_pulse `
        -e POSTGRES_PASSWORD=changeme `
        -e POSTGRES_DB=oi_pulse `
        -v "${postgresVolume}:/var/lib/postgresql/data" `
        docker.io/library/postgres:16-alpine | Out-Host
}

function Start-Redis {
    Remove-Container "oi-pulse-redis"
    podman run -d `
        --name oi-pulse-redis `
        --network $networkName `
        -p 6379:6379 `
        -v "${redisVolume}:/data" `
        docker.io/library/redis:7-alpine `
        redis-server --save 60 1 --loglevel warning | Out-Host
}

function Build-BackendImage {
    podman build -t $backendImage -f (Join-Path $root "backend\Dockerfile") (Join-Path $root "backend") | Out-Host
}

function Build-FrontendImage {
    podman build -t $frontendImage -f (Join-Path $root "frontend\Dockerfile") (Join-Path $root "frontend") | Out-Host
}

function Start-Api {
    Remove-Container "oi-pulse-api"
    podman run -d `
        --name oi-pulse-api `
        --network $networkName `
        -p 8000:8000 `
        --env-file (Join-Path $root ".env") `
        -e DATABASE_URL=postgresql+asyncpg://oi_pulse:changeme@oi-pulse-postgres:5432/oi_pulse `
        -e REDIS_URL=redis://oi-pulse-redis:6379/0 `
        $backendImage `
        python run_api.py | Out-Host
}

function Start-Worker {
    Remove-Container "oi-pulse-worker"
    podman run -d `
        --name oi-pulse-worker `
        --network $networkName `
        --env-file (Join-Path $root ".env") `
        -e DATABASE_URL=postgresql+asyncpg://oi_pulse:changeme@oi-pulse-postgres:5432/oi_pulse `
        -e REDIS_URL=redis://oi-pulse-redis:6379/0 `
        $backendImage `
        python run_worker.py | Out-Host
}

function Start-Frontend {
    Remove-Container "oi-pulse-frontend"
    podman run -d `
        --name oi-pulse-frontend `
        --network $networkName `
        -p 3000:3000 `
        $frontendImage | Out-Host
}

function Start-Nginx {
    Remove-Container "oi-pulse-nginx"
    podman run -d `
        --name oi-pulse-nginx `
        --network $networkName `
        -p 8080:8080 `
        -v "${nginxConf}:/etc/nginx/conf.d/default.conf:ro" `
        docker.io/library/nginx:1.27-alpine | Out-Host
}

Ensure-PodmanMachine
Ensure-Network
Ensure-Volume $postgresVolume
Ensure-Volume $redisVolume

Start-Postgres
Start-Redis

if ($InfraOnly -and -not $Full) {
    Write-Host "Started Postgres and Redis via native Podman commands."
    exit 0
}

Build-BackendImage
Build-FrontendImage
Start-Api
Start-Worker
Start-Frontend
Start-Nginx

Write-Host "Started full Podman local stack on http://localhost:8080"