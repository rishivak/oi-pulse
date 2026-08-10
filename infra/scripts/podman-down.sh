#!/usr/bin/env bash

set -euo pipefail

containers=(
  "oi-pulse-nginx"
  "oi-pulse-frontend"
  "oi-pulse-worker"
  "oi-pulse-api"
  "oi-pulse-redis"
  "oi-pulse-postgres"
)

for name in "${containers[@]}"; do
  if podman container exists "$name" >/dev/null 2>&1; then
    podman rm -f "$name"
  fi
done

echo "Stopped Podman local stack containers."