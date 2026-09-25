#!/usr/bin/env bash

set -euo pipefail

root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

test_http() {
  local url="$1"
  curl -fsS --max-time 3 "$url" >/dev/null 2>&1
}

wait_for_http() {
  local url="$1"
  local attempts="${2:-30}"

  for ((i=0; i<attempts; i+=1)); do
    if test_http "$url"; then
      return 0
    fi
    sleep 2
  done

  return 1
}

cd "$root"

./infra/scripts/podman-up.sh --full

if ! wait_for_http "http://localhost:8000/api/health" 30; then
  echo "Backend did not become healthy on http://localhost:8000/api/health" >&2
  exit 1
fi

if ! wait_for_http "http://localhost:8080" 30; then
  echo "Nginx entrypoint did not become ready on http://localhost:8080" >&2
  exit 1
fi

echo "Local stack is ready:"
echo "  Frontend: http://localhost:3000"
echo "  Backend:  http://localhost:8000/api/health"
echo "  App URL:  http://localhost:8080"