$ErrorActionPreference = "Stop"

$containers = @(
	"oi-pulse-nginx",
	"oi-pulse-frontend",
	"oi-pulse-worker",
	"oi-pulse-api",
	"oi-pulse-redis",
	"oi-pulse-postgres"
)

foreach ($name in $containers) {
	podman container exists $name *> $null
	if ($LASTEXITCODE -eq 0) {
		podman rm -f $name | Out-Host
	}
}

Write-Host "Stopped Podman local stack containers."