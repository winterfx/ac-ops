#!/usr/bin/env bash
# Build the platform-ops agent image (image/Dockerfile) on the devboard host.
#
# The build context (the Dockerfile and bin/agent-compose-ro) is streamed over
# ssh, so nothing extra is left on the host. The image stays in the host's
# Docker: the daemon's default pull policy uses a local image as is, so it does
# not need a registry.
#
# Env: DEVBOARD_HOST (default devboard), IMAGE (default ac-ops-agent:local;
#      must match agent-compose.yml).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
host="${DEVBOARD_HOST:-devboard}"
image="${IMAGE:-ac-ops-agent:local}"

[ -x "$root/bin/agent-compose-ro" ] || { echo "bin/agent-compose-ro is missing; run scripts/build-cli.sh" >&2; exit 1; }
grep -q "image: $image\$" "$root/agent-compose.yml" || { echo "agent-compose.yml does not use $image" >&2; exit 1; }
cli_version="$(cd "$root/cli" && go list -m -f '{{.Version}}' github.com/chaitin/chaitin-cli)"

# The Dockerfile goes at the root of the context: the host's legacy builder
# rejects -f together with a context on stdin.
COPYFILE_DISABLE=1 tar --format=ustar -cf - -C "$root/image" Dockerfile -C "$root" bin/agent-compose-ro |
  ssh "$host" "docker build -q --build-arg CLI_VERSION='$cli_version' -t '$image' -"
echo "built $image on $host (chaitin-cli $cli_version)"
