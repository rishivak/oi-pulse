#!/usr/bin/env bash

set -euo pipefail

infra_only=0
full=0

for arg in "$@"; do
  case "$arg" in
    -InfraOnly|--infra-only)
      infra_only=1
      ;;
    -Full|--full)
      full=1
      ;;
    *)
      echo "Unknown argument: $arg" >&2
      exit 1
      ;;
  esac
done

network_name="oi-pulse-dev"
postgres_volume="oi_pulse_postgres_data"
redis_volume="oi_pulse_redis_data"
backend_image="localhost/oi-pulse-backend:dev"
frontend_image="localhost/oi-pulse-frontend:dev"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
nginx_conf="$root/infra/nginx/nginx.podman.conf"

ensure_network() {
  if ! podman network inspect "$network_name" >/dev/null 2>&1; then
    podman network create "$network_name"
  fi
}

ensure_volume() {
  local name="$1"
  if ! podman volume inspect "$name" >/dev/null 2>&1; then
    podman volume create "$name"
  fi
}

remove_container() {
  local name="$1"
  if podman container exists "$name" >/dev/null 2>&1; then
    podman rm -f "$name"
  fi
}

start_postgres() {
  remove_container "oi-pulse-postgres"
  podman run -d \
    --name oi-pulse-postgres \
    --network "$network_name" \
    -p 5432:5432 \
    -e POSTGRES_USER=oi_pulse \
    -e POSTGRES_PASSWORD=changeme \
    -e POSTGRES_DB=oi_pulse \
    -v "${postgres_volume}:/var/lib/postgresql/data" \
    docker.io/library/postgres:16-alpine
}

start_redis() {
  remove_container "oi-pulse-redis"
  podman run -d \
    --name oi-pulse-redis \
    --network "$network_name" \
    -p 6379:6379 \
    -v "${redis_volume}:/data" \
    docker.io/library/redis:7-alpine \
    redis-server --save 60 1 --loglevel warning
}

build_backend_image() {
  podman build -t "$backend_image" -f "$root/backend/Dockerfile" "$root/backend"
}

build_frontend_image() {
  podman build -t "$frontend_image" -f "$root/frontend/Dockerfile" "$root/frontend"
}

start_api() {
  remove_container "oi-pulse-api"
  podman run -d \
    --name oi-pulse-api \
    --network "$network_name" \
    -p 8000:8000 \
    --env-file "$root/.env" \
    -e DATABASE_URL=postgresql+asyncpg://oi_pulse:changeme@oi-pulse-postgres:5432/oi_pulse \
    -e REDIS_URL=redis://oi-pulse-redis:6379/0 \
    "$backend_image" \
    python run_api.py
}

start_worker() {
  remove_container "oi-pulse-worker"
  podman run -d \
    --name oi-pulse-worker \
    --network "$network_name" \
    --env-file "$root/.env" \
    -e DATABASE_URL=postgresql+asyncpg://oi_pulse:changeme@oi-pulse-postgres:5432/oi_pulse \
    -e REDIS_URL=redis://oi-pulse-redis:6379/0 \
    "$backend_image" \
    python run_worker.py
}

start_frontend() {
  remove_container "oi-pulse-frontend"
  podman run -d \
    --name oi-pulse-frontend \
    --network "$network_name" \
    -p 3000:3000 \
    "$frontend_image"
}

start_nginx() {
  remove_container "oi-pulse-nginx"
  podman run -d \
    --name oi-pulse-nginx \
    --network "$network_name" \
    -p 8080:8080 \
    -v "$nginx_conf:/etc/nginx/conf.d/default.conf:ro" \
    docker.io/library/nginx:1.27-alpine
}

ensure_network
ensure_volume "$postgres_volume"
ensure_volume "$redis_volume"

start_postgres
start_redis

if [[ "$infra_only" -eq 1 && "$full" -ne 1 ]]; then
  echo "Started Postgres and Redis via native Podman commands."
  exit 0
fi

build_backend_image
build_frontend_image
start_api
start_worker
start_frontend
start_nginx

echo "Started full Podman local stack on http://localhost:8080"