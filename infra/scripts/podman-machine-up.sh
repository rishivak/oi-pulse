#!/usr/bin/env bash

set -euo pipefail

if ! podman machine list --format json | grep -Eq '"Default"\s*:\s*true'; then
  echo "No default Podman machine found. Initializing podman-machine-default..."
  podman machine init
fi

podman machine start
podman machine list