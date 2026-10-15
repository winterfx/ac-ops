# ac-nightly-report

An agent, deployed on agent-compose itself, that writes a nightly report on how that agent-compose daemon is being used and where it is failing. The aim is to catch **platform** problems early and give agent-compose development concrete direction.

Every night at 00:00 (Asia/Shanghai) the `nightly-reporter` agent:

1. Collects the previous day's Runs, Sandboxes, and Scheduler Runs across all projects (`tool/collect.py`). It produces a compact digest with every failure's details and failures grouped by error signature.
2. Classifies each failure as one of: platform bug, LLM upstream, image/runtime mismatch, user script/config, or user cancel.
3. For suspected platform bugs, locates the code at the deployed version's tag. It then checks `origin/main` to see whether the bug is already fixed upstream.
4. Summarizes usage per agent: activity, success rate, provider/model/driver/image, and scheduler frequency.
5. Writes `reports/<date>.md`. The same text is the run's final answer, so it is visible in the agent-compose UI.

How it works is in `skills/ac-nightly-report/SKILL.md`.

## Layout

| Path | What |
| --- | --- |
| `agent-compose.yml` | Agent definition and the nightly cron trigger |
| `tool/collect.py` | Data collector (Python stdlib only) |
| `tool/acli` | Wrapper around `bin/agent-compose-ro`: points it at the daemon the sandbox runs on |
| `cli/` | Source of `bin/agent-compose-ro`: chaitin-cli's agent-compose command alone, with dry-run fixed on, so every write command is refused before a request is sent. Its chaitin-cli version is pinned in `cli/go.mod` |
| `tool/acsrc` | Read-only git access to the agent-compose source |
| `skills/ac-nightly-report/` | The workflow, failure taxonomy, report template, and rules the agent follows |
| `scripts/` | Build, sync, and deploy helpers |
| `bin/`, `src/`, `reports/` | Not in git: the built CLI, the synced source, and the output |

Inside the sandbox, the project is mounted read-only at `/workspace`. Only `reports/` is writable, at `/reports`.

## Deploying to devboard

Devboard cannot reach GitHub, and the daemon reads file sources from its own filesystem. So the project is copied to the host under the daemon's data mount: `/data/agent-compose/projects/ac-nightly-report` on the host, which is `/data/projects/ac-nightly-report` inside the daemon.

```bash
# 1. Build bin/agent-compose-ro (linux/amd64, ~12 MB).
scripts/build-cli.sh

# 2. Push the agent-compose source (main + tags) to the host.
scripts/sync-src.sh

# 3. Copy the project and validate, then deploy.
scripts/deploy.sh --check
scripts/deploy.sh
```

The host directory has to exist and be writable by the SSH user first. `/data/agent-compose` is root-owned:

```bash
ssh devboard 'sudo mkdir -p /data/agent-compose/projects/ac-nightly-report && sudo chown "$USER" /data/agent-compose/projects/ac-nightly-report'
```

Run `scripts/sync-src.sh` again whenever the report should see newer agent-compose commits. A GitLab mirror of agent-compose that devboard can reach would remove this step.

## Running it

- Run once by hand. Pass a date to report on a specific day.

  ```bash
  agent-compose run nightly-reporter --prompt "生成 2026-10-14 的 agent-compose 日报"
  ```

- Collect only, without the agent (for example, from a machine with a tunnel to the daemon):

  ```bash
  GOOS=darwin GOARCH=arm64 OUT=/tmp/agent-compose-ro scripts/build-cli.sh
  ACNR_CLI=/tmp/agent-compose-ro AGENT_COMPOSE_URL=http://127.0.0.1:17410 \
  ACNR_REPORTS=/tmp/acnr python3 tool/collect.py --date 2026-10-14
  ```

## Safety

- **Read-only.** All queries go through `bin/agent-compose-ro`. It is built with dry-run fixed on, so run, stop, rm, resume, exec, trigger, invoke, and auth login/logout are refused before anything is sent. No flag turns this off. The project mount is read-only. The daemon's data directory and `data.db` are **not** mounted, because they hold LLM tokens and webhook secrets.
- **Untrusted content.** Other agents' prompts, outputs, and logs may carry text from external webhooks. The agent treats them as evidence only and never follows instructions in them.
- **Privacy.** The report gives aggregates and short IDs. It does not quote prompts or conversations. `collect.py` redacts common credential patterns before anything reaches the digest.
