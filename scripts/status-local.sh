#!/usr/bin/env bash

set -euo pipefail

show_url_status() {
  local label="$1"
  local url="$2"

  if status=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 "$url" 2>/dev/null); then
    echo -e "${label}\t${status}\t${url}"
  else
    echo -e "${label}\tDOWN\t${url}"
  fi
}

show_url_status "frontend" "http://localhost:3000"
show_url_status "backend" "http://localhost:8000/api/health"
show_url_status "app" "http://localhost:8080"

podman ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"