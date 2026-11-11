#!/usr/bin/env bash
# Store a host-side secret on the devboard host, outside git.
#
# The value is read from stdin, never from an argument, so it does not land in
# shell history. It is written to <project>/secrets/<name> on the host, which
# scripts/deploy.sh never syncs or deletes and the agent sees read-only at
# /workspace/secrets/<name>.
#
# Usage:
#   scripts/set-notify-secret.sh                  # prompts for the webhook URL
#   scripts/set-notify-secret.sh dingtalk-secret  # a bot signing secret (加签)
#   scripts/set-notify-secret.sh http-proxy       # proxy used by source-sync
#   scripts/set-notify-secret.sh < /path/to/file  # read the value from a file
#
# Env: DEVBOARD_HOST (default devboard), REMOTE_DIR (default
#      /data/agent-compose/projects/platform-ops).
set -euo pipefail

host="${DEVBOARD_HOST:-devboard}"
remote_dir="${REMOTE_DIR:-/data/agent-compose/projects/platform-ops}"
name="${1:-dingtalk-webhook}"

case "$name" in
  *[!A-Za-z0-9._-]*|"") echo "invalid secret name: $name" >&2; exit 1 ;;
esac

if [ -t 0 ]; then
  case "$name" in
    dingtalk-webhook) printf 'Paste the DingTalk webhook URL, then Enter (input is hidden):\n' >&2 ;;
    http-proxy)       printf 'Paste the HTTP proxy URL, then Enter (input is hidden):\n' >&2 ;;
    *)                printf 'Paste the value for %s, then Enter (input is hidden):\n' "$name" >&2 ;;
  esac
  if ! IFS= read -rs value; then
    echo >&2
    echo "no input" >&2
    exit 1
  fi
  echo >&2
else
  value="$(cat)"
fi

if [ -z "$value" ]; then
  echo "empty value; nothing written" >&2
  exit 1
fi

if [ "$name" = "dingtalk-webhook" ]; then
  case "$value" in
    https://oapi.dingtalk.com/robot/send\?*) ;;
    *) echo "warning: this is not an oapi.dingtalk.com robot URL" >&2 ;;
  esac
fi

# The sandbox runs as root and reads the project mount read-only; 0600 on the
# host still lets root read it, and keeps it private from other host users.
printf '%s' "$value" |
  ssh "$host" "umask 077; mkdir -p '$remote_dir/secrets'; chmod 700 '$remote_dir/secrets'; cat > '$remote_dir/secrets/$name'; chmod 600 '$remote_dir/secrets/$name'"

echo "wrote $remote_dir/secrets/$name on $host"
