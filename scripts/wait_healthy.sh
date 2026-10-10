#!/usr/bin/env bash
# Wait until the four Immich containers report healthy and /api/server/ping answers.
# Usage: scripts/wait_healthy.sh [IMMICH_URL] [TIMEOUT_SECONDS]
set -euo pipefail

URL="${1:-http://127.0.0.1:2283}"
TIMEOUT="${2:-300}"
CONTAINERS=(immich_server immich_machine_learning immich_postgres immich_redis)

deadline=$((SECONDS + TIMEOUT))
while :; do
  all_ok=true
  line=""
  for c in "${CONTAINERS[@]}"; do
    h="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$c" 2>/dev/null || echo missing)"
    line+="$c=$h  "
    [[ "$h" == healthy ]] || all_ok=false
  done
  echo "$line"
  if $all_ok; then
    if body="$(curl -fsS "$URL/api/server/ping")"; then
      echo "API ping: $body"
      echo "Immich is healthy at $URL"
      exit 0
    fi
  fi
  if (( SECONDS >= deadline )); then
    echo "error: not healthy after ${TIMEOUT}s. Try: make ps / make logs" >&2
    exit 1
  fi
  sleep 5
done
