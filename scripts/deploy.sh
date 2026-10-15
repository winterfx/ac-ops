#!/usr/bin/env bash
# Copy the project to the devboard host and deploy it with the daemon's own
# agent-compose CLI. The daemon reads file sources from its own filesystem, so
# the project has to live on the host, under the daemon's /data mount.
#
# Env: DEVBOARD_HOST (default devboard), REMOTE_DIR (host path, under
#      /data/agent-compose), DAEMON_CONTAINER (default agent-compose).
# Pass --check to validate without deploying.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
host="${DEVBOARD_HOST:-devboard}"
remote_dir="${REMOTE_DIR:-/data/agent-compose/projects/platform-ops}"
container="${DAEMON_CONTAINER:-agent-compose}"
# The daemon container mounts the host's /data/agent-compose at /data.
daemon_dir="/data/${remote_dir#/data/agent-compose/}"

[ -x "$root/bin/agent-compose-ro" ] || { echo "bin/agent-compose-ro is missing; run scripts/build-cli.sh" >&2; exit 1; }

ssh "$host" "mkdir -p '$remote_dir/src'"
# src/ lives only on the host (scripts/sync-src.sh); excluding it also keeps
# --delete from removing it.
rsync -a --delete --exclude .git --exclude /src/ "$root/" "$host:$remote_dir/"

ac="docker exec -w '$daemon_dir' '$container' agent-compose --host http://127.0.0.1:7410"
ssh "$host" "$ac config --quiet"
if [ "${1:-}" = "--check" ]; then
  echo "config ok; not deployed (--check)"
  exit 0
fi
ssh "$host" "$ac up"
