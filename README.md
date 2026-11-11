# ac-ops

The `platform-ops` agent-compose project: agents, deployed on agent-compose itself, that watch the agent-compose platform they run on. The aim is to catch **platform** problems early and give agent-compose development concrete, evidence-backed direction.

## Agents

| Agent | Role | Triggers | Skills | Writes to |
| --- | --- | --- | --- | --- |
| `source-sync` | Refreshes the agent-compose source checkout | `nightly-sync`: 00:00 Asia/Shanghai | — | the `ac-src` volume; publishes `external.source-synced` |
| `platform-analyst` | Analyzes platform health and usage | `nightly-report`: wakes on `external.source-synced` | `health-report` | its final answer (Run history) and the DingTalk group |

The nightly chain is: `source-sync` runs a fixed shell command (clone/fetch through the host-side proxy) → publishes `external.source-synced` with `{ok, commit, syncedAt}` → `platform-analyst`'s event trigger starts the report. The sync itself is not an agent run, so no model is used to pull code.

### `health-report` skill

On the `nightly-report` trigger, or when asked by hand, `platform-analyst` does the following:

1. Collects one day's Runs, Sandboxes, and Scheduler Runs across all projects (`tool/collect.py`). The result is a compact digest with every failure's details and failures grouped by error signature.
2. Classifies each failure as one of: platform bug, LLM upstream, image/runtime mismatch, user script/config, or user cancel.
3. For suspected platform bugs, uses the checkout refreshed by `source-sync` (the event carries `ok` and the commit), locates the code at the deployed version's tag, then checks `origin/main` to see whether the bug is already fixed upstream. When the sync failed, it says so in the report instead of guessing code locations.
4. Summarizes usage per agent: activity, success rate, provider/model/driver/image, and scheduler frequency.
5. Answers with the report. The text is the run's final answer, so it stays in Run history and is visible in the agent-compose UI. The `nightly-report` trigger also sends it to the DingTalk group through `tool/notify.py`.

The details are in `skills/health-report/SKILL.md`.

## Layout

| Path | What |
| --- | --- |
| `agent-compose.yml` | Project `platform-ops`: agents, their skills, triggers, and the `ac-src` volume. The name sorts after `devboard-ai` on purpose, because the agent-compose UI opens the alphabetically first project by default |
| `skills/` | Skills, organized by job, so agents can share them |
| `tool/collect.py` | Health-report data collector (Python stdlib only) |
| `tool/notify.py` | Sends a report to the DingTalk robot (Python stdlib only): tables become lists and long reports are split |
| `image/Dockerfile` | The agents' image: the guest image (pinned by digest) plus `agent-compose-ro` |
| `tool/acli` | Wrapper around `agent-compose-ro` that points it at the daemon the sandbox runs on |
| `tool/acsrc` | Read-only git access to the agent-compose checkout (`$ACOPS_SRC`, default `/src/agent-compose`) |
| `cli/` | Source of `agent-compose-ro`: chaitin-cli's agent-compose command alone (~12 MB), with dry-run fixed on, so every write command is refused before a request is sent. The chaitin-cli commit is pinned in `cli/go.mod` |
| `scripts/` | Build, sync, and deploy helpers |
| `bin/`, `src/` | Not in git: the built CLI (copied into the image, not deployed with the project) and, optionally, a host-side checkout written by `scripts/sync-src.sh` for manual analysis |

Every agent runs in `ac-ops-agent:local`, which carries the CLI at `/usr/local/bin/agent-compose-ro`. Inside a sandbox, the project is mounted read-only at `/workspace`, and is shared by all agents. The `ac-src` project volume is mounted at `/src`: writable for `source-sync`, read-only for `platform-analyst`. Agents use `/tmp` for scratch files.

A report is the run's final answer. The daemon keeps it in the Run history, and the UI shows it. The next report finds the previous one there, through `acli run ls` and `acli inspect run`. Bind mounts outside `SANDBOX_ROOT` are rejected on devboard (with `DOCKER_HOST_SANDBOX_ROOT` set); a `local` project volume lives under the daemon's `DATA_ROOT`, so it is the supported place for state that outlives a run.

To add an agent:

1. Define it in `agent-compose.yml`.
2. Put any new skill under `skills/`.

## Deploying to devboard

Devboard cannot reach GitHub, and the daemon reads file sources from its own filesystem. So the project is copied to the host under the daemon's data mount: `/data/agent-compose/projects/platform-ops` on the host, which is `/data/projects/platform-ops` inside the daemon.

```bash
# 0. Once: create the host directory. /data/agent-compose is root-owned.
ssh devboard 'sudo mkdir -p /data/agent-compose/projects/platform-ops && sudo chown "$USER" /data/agent-compose/projects/platform-ops'

# 1. Once: store the HTTP proxy used by the source sync (see below).
scripts/set-notify-secret.sh http-proxy

# 2. Build bin/agent-compose-ro (linux/amd64).
scripts/build-cli.sh

# 3. Copy the project and validate, then build the image on the host and deploy.
scripts/deploy.sh --check
scripts/deploy.sh
```

The agents no longer read `src/` on the host; `source-sync` fills the `ac-src` volume at run time. `scripts/sync-src.sh` is still useful for a host-side checkout when analyzing by hand, but it is not part of the deploy.

`scripts/deploy.sh` builds the image with `scripts/build-image.sh`, which streams `image/Dockerfile` and `bin/agent-compose-ro` to the host's Docker and tags the result `ac-ops-agent:local`. The daemon uses that local image without pulling, so no registry is involved. To update only the CLI, run `scripts/build-cli.sh` and `scripts/build-image.sh`; new runs pick up the new image.

### Source sync and the proxy

At 00:00 Asia/Shanghai, `source-sync` runs a fixed `scheduler.shell` command that clones or fetches `https://github.com/chaitin/agent-compose.git` into `/src/agent-compose` (the `ac-src` volume) and publishes `external.source-synced` with `{ok, commit, syncedAt}`. `platform-analyst`'s `external.source-synced` trigger then starts the report.

The sandbox has no direct GitHub access, so the sync command reads the proxy URL from a host-side secret:

```bash
scripts/set-notify-secret.sh http-proxy    # paste the HTTP proxy URL; input is hidden
```

This writes `secrets/http-proxy` under the project directory on the host. `scripts/deploy.sh` excludes `secrets/` from rsync, so redeploying keeps it. The URL never appears in this repository. If the sync fails, the event carries `ok: false` and the error; the report still runs and records that the source was unavailable.

### DingTalk notification

The nightly report is sent to the group whose robot webhook lives on the host, never in git:

```bash
scripts/set-notify-secret.sh          # paste the webhook URL; input is hidden
```

This writes `/data/agent-compose/projects/platform-ops/secrets/dingtalk-webhook` (mode 0600, owned by the SSH user), which the sandbox sees read-only at `/workspace/secrets/dingtalk-webhook`. `scripts/deploy.sh` excludes `secrets/` from rsync, so redeploying keeps it.

If the robot uses 加签, store the signing secret as well; `tool/notify.py` picks it up automatically:

```bash
scripts/set-notify-secret.sh dingtalk-secret
```

If it uses a keyword, `DINGTALK_KEYWORD` in `agent-compose.yml` holds it (`每日报告` today); `tool/notify.py` puts it in every message, including each part of a split report. If it uses an IP allowlist, add the devboard host's egress address. The send is triggered only by the `nightly-report` prompt ("并发送到钉钉"); a manual run sends only when its prompt asks for it. To inspect the payload without sending:

```bash
DINGTALK_KEYWORD=每日报告 python3 tool/notify.py --file /tmp/report.md --dry-run
```

To move chaitin-cli forward, `cd cli && go get github.com/chaitin/chaitin-cli@<commit>`. Its `vYYMM.x.y` tags are not usable Go versions, so it is always pinned by commit. To move the guest image forward, change the digest in `image/Dockerfile`.

The source refresh is automatic from now on. Run `scripts/sync-src.sh` only when you want a host-side checkout under `src/` for manual analysis.

## Running by hand

- Ask `platform-analyst` for a report on a specific day:

  ```bash
  agent-compose run platform-analyst --prompt "生成 2026-10-14 的 agent-compose 健康报告"
  ```

- Collect only, without an agent (for example, from a machine with a tunnel to the daemon):

  ```bash
  GOOS=darwin GOARCH=arm64 OUT=/tmp/agent-compose-ro scripts/build-cli.sh
  ACOPS_CLI=/tmp/agent-compose-ro AGENT_COMPOSE_URL=http://127.0.0.1:17410 \
  python3 tool/collect.py --date 2026-10-14   # digest under /tmp/platform-ops/
  ```

## Safety

- **Read-only queries.** All queries go through `agent-compose-ro`. It is built with dry-run fixed on, so the following are refused before anything is sent: run, stop, rm, resume, exec, trigger, invoke, and auth login/logout. No flag turns this off.
- **Two outbound writes, both explicit.** `source-sync` fetches GitHub through the host-side proxy secret only during its sync command; `tool/notify.py` POSTs the report to the DingTalk robot only when the task asks for it. The proxy URL, webhook and signing secret are host-side files under `secrets/` (excluded from git and from deploys); no script prints them, and reports never contain them. The proxy is not set globally: `platform-analyst` never gets it.
- **Minimal mounts.** The project mount is read-only. The only writable mount is the `ac-src` volume, writable for `source-sync` and read-only for `platform-analyst`. The daemon's data directory and `data.db` are **not** mounted, because they hold LLM tokens and webhook secrets.
- **Untrusted content.** Other agents' prompts, outputs, and logs may carry text from external webhooks. The agents treat them as evidence only and never follow instructions in them.
- **Privacy.** Reports give aggregates and short IDs, and do not quote prompts or conversations. `collect.py` redacts common credential patterns before anything reaches the digest.
