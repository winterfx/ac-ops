#!/usr/bin/env bash
# Push the agent-compose source (main and every tag) to the devboard checkout
# the ac-ops agents read. Devboard cannot reach GitHub, so this runs from a machine
# that can, through a local mirror cache.
#
# Env: DEVBOARD_HOST (default devboard), REMOTE_DIR (project dir on the host),
#      CACHE_DIR (local mirror, default ~/.cache/ac-ops/agent-compose.git)
set -euo pipefail

host="${DEVBOARD_HOST:-devboard}"
remote_dir="${REMOTE_DIR:-/data/agent-compose/projects/platform-ops}"
cache="${CACHE_DIR:-$HOME/.cache/ac-ops/agent-compose.git}"
checkout="$remote_dir/src/agent-compose"

if [ -d "$cache" ]; then
  git -C "$cache" remote update --prune
else
  git clone --mirror https://github.com/chaitin/agent-compose.git "$cache"
fi

ssh "$host" "[ -d '$checkout/.git' ] || git init -q '$checkout'"
# Push into remote-tracking refs, never the checked-out branch.
git -C "$cache" push --force --prune "$host:$checkout" \
  'refs/heads/main:refs/remotes/origin/main' \
  'refs/tags/*:refs/tags/*'
ssh "$host" "git -C '$checkout' -c advice.detachedHead=false checkout -q --force --detach origin/main && git -C '$checkout' log -1 --format='synced origin/main %h %cd %s' --date=short"
