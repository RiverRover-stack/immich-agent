#!/usr/bin/env bash
# Download the pinned Immich release files into immich/ (git-ignored).
# We never modify Immich; we only run its official compose file.
#
# Usage: scripts/fetch_immich.sh            (version from IMMICH_VERSION in .env, default v3.3.1)
#        IMMICH_VERSION=v3.3.1 scripts/fetch_immich.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="$ROOT/immich"

# Read IMMICH_VERSION from our .env if not already set in the environment.
if [[ -z "${IMMICH_VERSION:-}" && -f "$ROOT/.env" ]]; then
  IMMICH_VERSION="$(grep -E '^IMMICH_VERSION=' "$ROOT/.env" | tail -1 | cut -d= -f2- | tr -d '"'\''[:space:]')"
fi
IMMICH_VERSION="${IMMICH_VERSION:-v3.3.1}"

# Never "latest": compose file and images must come from the same release.
if [[ ! "$IMMICH_VERSION" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "error: IMMICH_VERSION must be a pinned tag like v3.3.1 (got '$IMMICH_VERSION')" >&2
  exit 1
fi

BASE="https://github.com/immich-app/immich/releases/download/$IMMICH_VERSION"
mkdir -p "$DEST"

echo "Fetching Immich $IMMICH_VERSION into $DEST"
curl -fsSL "$BASE/docker-compose.yml" -o "$DEST/docker-compose.yml"
curl -fsSL "$BASE/example.env" -o "$DEST/example.env"
echo "$IMMICH_VERSION" > "$DEST/VERSION"

# Create immich/.env once from the release template; keep it on re-fetch.
if [[ -f "$DEST/.env" ]]; then
  echo "Keeping existing $DEST/.env"
else
  cp "$DEST/example.env" "$DEST/.env"
  # Pin the image tag and set a random alphanumeric DB password (Immich requires A-Za-z0-9).
  pw="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 24 || true)"
  sed -i.bak \
    -e "s|^IMMICH_VERSION=.*|IMMICH_VERSION=$IMMICH_VERSION|" \
    -e "s|^DB_PASSWORD=.*|DB_PASSWORD=$pw|" \
    "$DEST/.env"
  rm -f "$DEST/.env.bak"
  echo "Created $DEST/.env (IMMICH_VERSION=$IMMICH_VERSION, random DB_PASSWORD)"
fi

# Sanity check: the version line must be present after editing.
grep -q "^IMMICH_VERSION=$IMMICH_VERSION$" "$DEST/.env" \
  || { echo "error: IMMICH_VERSION not set in $DEST/.env; check example.env format" >&2; exit 1; }

echo "Done. Next: make up"
