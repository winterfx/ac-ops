# ac-ops

The `platform-ops` agent-compose project: agents, deployed on agent-compose itself, that watch the agent-compose platform they run on. The aim is to catch **platform** problems early and give agent-compose development concrete, evidence-backed direction.

## Agents

| Agent | Role | Triggers | Skills | Writes to |
| --- | --- | --- | --- | --- |
| `platform-analyst` | Analyzes platform health and usage | `nightly-report`: 00:00 Asia/Shanghai | `health-report` | its final answer (Run history) |

### `health-report` skill

On the `nightly-report` trigger, or when asked by hand, `platform-analyst` does the following:

1. Collects one day's Runs, Sandboxes, and Scheduler Runs across all projects (`tool/collect.py`). The result is a compact digest with every failure's details and failures grouped by error signature.
2. Classifies each failure as one of: platform bug, LLM upstream, image/runtime mismatch, user script/config, or user cancel.
3. For suspected platform bugs, locates the code at the deployed version's tag, then checks `origin/main` to see whether the bug is already fixed upstream.
4. Summarizes usage per agent: activity, success rate, provider/model/driver/image, and scheduler frequency.
5. Writes `health-report/<date>.md`. The same text is the run's final answer, so it is visible in the agent-compose UI.

The details are in `skills/health-report/SKILL.md`.

## Layout

| Path | What |
| --- | --- |
| `agent-compose.yml` | Project `platform-ops`: agents, their skills, and triggers. The name sorts after `devboard-ai` on purpose, because the agent-compose UI opens the alphabetically first project by default |
| `skills/` | Skills, organized by job, so agents can share them |
| `tool/collect.py` | Health-report data collector (Python stdlib only) |
| `image/Dockerfile` | The agents' image: the guest image (pinned by digest) plus `agent-compose-ro` |
| `tool/acli` | Wrapper around `agent-compose-ro` that points it at the daemon the sandbox runs on |
| `tool/acsrc` | Read-only git access to the agent-compose source |
| `cli/` | Source of `agent-compose-ro`: chaitin-cli's agent-compose command alone (~12 MB), with dry-run fixed on, so every write command is refused before a request is sent. The chaitin-cli commit is pinned in `cli/go.mod` |
| `scripts/` | Build, sync, and deploy helpers |
| `bin/`, `src/` | Not in git: the built CLI (copied into the image, not deployed with the project) and the synced agent-compose source |

Every agent runs in `ac-ops-agent:local`, which carries the CLI at `/usr/local/bin/agent-compose-ro`. Inside a sandbox, the project is mounted read-only at `/workspace`, and is shared by all agents. Agents mount nothing writable and use `/tmp` for scratch files.

A report is the run's final answer. The daemon keeps it in the Run history, and the UI shows it. The next report finds the previous one there, through `acli run ls` and `acli inspect run`. Writable mounts are not an option on devboard anyway: with `DOCKER_HOST_SANDBOX_ROOT` set, the daemon rejects every bind or `local` volume source outside `SANDBOX_ROOT`. The fix is chaitin/agent-compose#784; once a daemon with it is deployed, a `local` volume could keep report files as well.

To add an agent:

1. Define it in `agent-compose.yml`.
2. Put any new skill under `skills/`.

## Deploying to devboard

Devboard cannot reach GitHub, and the daemon reads file sources from its own filesystem. So the project is copied to the host under the daemon's data mount: `/data/agent-compose/projects/platform-ops` on the host, which is `/data/projects/platform-ops` inside the daemon.

```bash
# 0. Once: create the host directory. /data/agent-compose is root-owned.
ssh devboard 'sudo mkdir -p /data/agent-compose/projects/platform-ops && sudo chown "$USER" /data/agent-compose/projects/platform-ops'

# 1. Build bin/agent-compose-ro (linux/amd64).
scripts/build-cli.sh

# 2. Push the agent-compose source (main + tags) to the host.
scripts/sync-src.sh

# 3. Copy the project and validate, then build the image on the host and deploy.
scripts/deploy.sh --check
scripts/deploy.sh
```

`scripts/deploy.sh` builds the image with `scripts/build-image.sh`, which streams `image/Dockerfile` and `bin/agent-compose-ro` to the host's Docker and tags the result `ac-ops-agent:local`. The daemon uses that local image without pulling, so no registry is involved. To update only the CLI, run `scripts/build-cli.sh` and `scripts/build-image.sh`; new runs pick up the new image.

To move chaitin-cli forward, `cd cli && go get github.com/chaitin/chaitin-cli@<commit>`. Its `vYYMM.x.y` tags are not usable Go versions, so it is always pinned by commit. To move the guest image forward, change the digest in `image/Dockerfile`.

Run `scripts/sync-src.sh` again whenever the agents should see newer agent-compose commits. A GitLab mirror of agent-compose that devboard can reach would remove this step.

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

- **Read-only.** All queries go through `agent-compose-ro`. It is built with dry-run fixed on, so the following are refused before anything is sent: run, stop, rm, resume, exec, trigger, invoke, and auth login/logout. No flag turns this off.
- **Minimal mounts.** The project mount is read-only. The daemon's data directory and `data.db` are **not** mounted, because they hold LLM tokens and webhook secrets.
- **Untrusted content.** Other agents' prompts, outputs, and logs may carry text from external webhooks. The agents treat them as evidence only and never follow instructions in them.
- **Privacy.** Reports give aggregates and short IDs, and do not quote prompts or conversations. `collect.py` redacts common credential patterns before anything reaches the digest.
