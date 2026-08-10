$ErrorActionPreference = "Stop"

function Show-UrlStatus([string]$label, [string]$url) {
    try {
        $response = Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 3
        Write-Host "$label`t$($response.StatusCode)`t$url"
    } catch {
        Write-Host "$label`tDOWN`t$url"
    }
}

Show-UrlStatus "frontend" "http://localhost:3000"
Show-UrlStatus "backend" "http://localhost:8000/api/health"
Show-UrlStatus "app" "http://localhost:8080"

podman ps --format "table {{.Names}}`t{{.Status}}`t{{.Ports}}"