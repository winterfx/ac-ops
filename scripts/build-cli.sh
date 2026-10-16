#!/usr/bin/env bash
# Build bin/agent-compose-ro: chaitin-cli's agent-compose command with only that
# product compiled in and dry-run fixed on, so it cannot send a write request.
#
# The chaitin-cli commit is pinned in cli/go.mod.
#
# GOOS/GOARCH default to linux/amd64 (the devboard sandbox); pass e.g.
# GOOS=darwin GOARCH=arm64 OUT=/tmp/agent-compose-ro for a local build.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
out="${OUT:-$root/bin/agent-compose-ro}"

mkdir -p "$(dirname "$out")"
(cd "$root/cli" && GOOS="${GOOS:-linux}" GOARCH="${GOARCH:-amd64}" CGO_ENABLED=0 \
  go build -trimpath -ldflags "-s -w" -o "$out" .)
echo "built $out ($(du -h "$out" | cut -f1)) from $(cd "$root/cli" && go list -m -f '{{.Path}}@{{.Version}}' github.com/chaitin/chaitin-cli)"
