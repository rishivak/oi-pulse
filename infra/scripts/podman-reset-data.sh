#!/usr/bin/env bash

set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

"$script_dir/podman-down.sh"

volumes=(
  "oi_pulse_postgres_data"
  "oi_pulse_redis_data"
)

for name in "${volumes[@]}"; do
  if podman volume inspect "$name" >/dev/null 2>&1; then
    podman volume rm -f "$name"
  fi
done

if podman network inspect "oi-pulse-dev" >/dev/null 2>&1; then
  podman network rm "oi-pulse-dev"
fi

echo "Removed Podman local stack volumes and network."