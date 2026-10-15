#!/usr/bin/env python3
"""Collect a compact digest of agent-compose activity for one time window.

The digest is what the health report is written from, instead of paging through the
daemon itself: run, sandbox, and scheduler activity per agent, the details of
every failure, and failures grouped by a normalized error signature.

Everything goes through `acli` (read-only chaitin-cli). A step that fails is
recorded under `collection_errors` rather than aborting the digest, because a
slow or failing API is itself something the report should mention.

Usage:
  collect.py                      # previous calendar day, Asia/Shanghai
  collect.py --date 2026-10-14    # that day
  collect.py --since 6h           # the last 6 hours
"""

import argparse
import collections
import datetime as dt
import json
import os
import re
import statistics
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ACLI = os.environ.get("ACOPS_ACLI", os.path.join(HERE, "acli"))
LOCAL_TZ = dt.timezone(dt.timedelta(hours=8))  # Asia/Shanghai has no DST.

MAX_FAILED_RUN_DETAILS = 60
MAX_SANDBOX_HISTORIES = 150
TEXT_TAIL = 800
STACK_LINES = 15


class CLIError(Exception):
    pass


def acli(*args, timeout=300):
    started = time.monotonic()
    try:
        proc = subprocess.run([ACLI, "--json", *args], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise CLIError(f"acli {' '.join(args)}: timed out after {timeout}s")
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        raise CLIError(f"acli {' '.join(args)}: exit {proc.returncode}: {proc.stderr.strip()[-400:] or proc.stdout.strip()[-400:]}")
    STATS["calls"] += 1
    STATS["seconds"] += elapsed
    STATS["slowest"] = max(STATS["slowest"], [round(elapsed, 1), " ".join(args)])
    return json.loads(proc.stdout) if proc.stdout.strip() else {}


STATS = {"calls": 0, "seconds": 0.0, "slowest": [0.0, ""]}
ERRORS = []


def attempt(label, fn, default=None):
    try:
        return fn()
    except (CLIError, ValueError, KeyError) as exc:
        ERRORS.append({"step": label, "error": str(exc)[:600]})
        return default


def parse_ts(value):
    """Parse the CLI's RFC3339 timestamps, which may carry nanoseconds."""
    if not value:
        return None
    value = value.replace("Z", "+00:00")
    value = re.sub(r"(\.\d{6})\d+", r"\1", value)
    return dt.datetime.fromisoformat(value)


def iso(moment):
    return moment.astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def window_from_args(args):
    now = dt.datetime.now(dt.timezone.utc)
    if args.since:
        match = re.fullmatch(r"(\d+)([hd])", args.since)
        if not match:
            raise SystemExit("--since must look like 6h or 2d")
        amount = int(match.group(1)) * (24 if match.group(2) == "d" else 1)
        return now - dt.timedelta(hours=amount), now, now.astimezone(LOCAL_TZ).strftime("%Y-%m-%d")
    if args.date:
        day = dt.date.fromisoformat(args.date)
    else:
        day = now.astimezone(LOCAL_TZ).date() - dt.timedelta(days=1)
    start = dt.datetime.combine(day, dt.time(), LOCAL_TZ)
    return start, start + dt.timedelta(days=1), day.isoformat()


SECRET_PATTERNS = [
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1<redacted>"),
    (re.compile(r"(?i)((?:api[_-]?key|token|secret|password|passwd|authorization)[\"']?\s*[:=]\s*[\"']?)[^\s\"',}]{6,}"), r"\1<redacted>"),
    (re.compile(r"\b(sk-[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|glpat-[A-Za-z0-9_-]{16,}|AKIA[0-9A-Z]{16})\b"), "<redacted>"),
    (re.compile(r"(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1<redacted>@"),
]


def redact(text):
    for pattern, replacement in SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def tail(text, limit=TEXT_TAIL):
    text = redact((text or "").strip())
    return text if len(text) <= limit else "…" + text[-limit:]


def first_lines(text, count=STACK_LINES, limit=1500):
    text = redact((text or "").strip())
    return "\n".join(text.splitlines()[:count])[:limit]


NOISE = [
    (re.compile(r"https?://\S+"), "<url>"),
    (re.compile(r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"), "<uuid>"),
    (re.compile(r"\b[0-9a-f]{12,}\b"), "<id>"),
    (re.compile(r"\d{4}-\d{2}-\d{2}[T ][\d:.]+Z?"), "<time>"),
    (re.compile(r"\b\d{1,2}:\d{2}:\d{2}(\.\d+)?\b"), "<time>"),
    (re.compile(r"/[\w./-]{3,}"), "<path>"),
    (re.compile(r"\d+"), "<n>"),
]
ERROR_HINT = re.compile(r"(?i)error|fail|denied|forbidden|refused|timeout|timed out|panic|exception|not found|unknown|mismatch|cannot|unable|invalid|killed|oom|exit")
# Line patterns in order of confidence that the line states the failure.
ERROR_RANKS = [
    re.compile(r"\b(ERROR|FATAL|PANIC)\b|\b\w*(?:[Ee]rror|Exception|panic|fatal)(?:\[[^\]]*\])?:|Traceback|✗|\bfailed to\b|\bfailed:"),
    # Wrappers report that something exited; the cause is usually above them.
    re.compile(r"exit(?:ed)? (?:with )?(?:code|status) [1-9]"),
    re.compile(r"\bWARN(?:ING)?\b"),
    ERROR_HINT,
]
# Informational log lines and JSON payloads mention "error" without being one
# (`"error":null`, `INFO request completed ... error=`).
NOT_AN_ERROR_LINE = re.compile(r'\b(INFO|DEBUG|TRACE)\b|^[\[{]|(?:"\w+"\s*:.*){3,}')
EMBEDDED_STAGE = re.compile(r'\{\s*"stage"\s*:\s*"([^"]{1,40})"')


def collapse_stage(line):
    """`failed: {"stage":"x",...}` -> `failed (stage=x)`: the JSON varies per run."""
    stage = EMBEDDED_STAGE.search(line)
    if not stage or not line[:line.index("{")].strip():
        return line
    return line[:line.index("{")].rstrip(" :") + f" (stage={stage.group(1)})"


def signature(text):
    """Reduce an error message to a stable key so repeats group together.

    Errors usually come last, so lines are searched from the end, most
    explicit error form first. Prose echoed from a prompt and log lines that
    merely mention "error" are passed over.
    """
    lines = [collapse_stage(line.strip()) for line in (text or "").splitlines() if line.strip()]
    candidates = [line for line in lines if not NOT_AN_ERROR_LINE.search(line)]
    pick = next((line for rank in ERROR_RANKS for line in reversed(candidates) if rank.search(line)), None)
    if pick is None:
        pick = candidates[-1] if candidates else "<no error line>"
    for pattern, replacement in NOISE:
        pick = pattern.sub(replacement, pick)
    return re.sub(r"\s+", " ", pick)[:200] or "<empty>"


def page_until(fetch, key, timestamp_field, start, limit=100, max_pages=200):
    """Page a newest-first listing until its items fall before start."""
    items, offset = [], 0
    for _ in range(max_pages):
        page = fetch(limit, offset)
        batch = page.get(key) or []
        items.extend(batch)
        oldest = parse_ts(batch[-1].get(timestamp_field)) if batch else None
        if not page.get("has_more") or not batch or (oldest and oldest < start):
            break
        offset = page.get("next_offset") or offset + len(batch)
    return items


def summarize_history(history):
    cells = history.get("cells") or []
    failed = []
    for cell in cells:
        if cell.get("running") or cell.get("success") or cell.get("exit_code", 0) == 0:
            continue
        source = next((line.strip() for line in (cell.get("source") or "").splitlines() if line.strip() and line.strip() != "set -eu"), "")
        output = cell.get("stderr") or ""
        if not ERROR_HINT.search(output):
            output = cell.get("output") or cell.get("stdout") or output
        failed.append({
            "created_at": cell.get("created_at"),
            "type": cell.get("type"),
            "agent": cell.get("agent") or None,
            "exit_code": cell.get("exit_code"),
            "first_line": redact(source)[:160],
            "tail": tail(output),
            "signature": signature(output),
        })
    events = [
        {"created_at": e.get("created_at"), "level": e.get("level"), "type": e.get("type"), "message": tail(e.get("message"), 300)}
        for e in (history.get("events") or [])
        if (e.get("level") or "").lower() in ("warn", "warning", "error")
    ]
    return {"cells": len(cells), "failed_cells": failed, "warning_events": events[-5:]}


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--date", help="local calendar day (Asia/Shanghai), default: yesterday")
    parser.add_argument("--since", help="relative window ending now, e.g. 6h or 2d")
    parser.add_argument("--out", help="digest path, default: $ACOPS_OUTPUTS/health-report/<date>.json")
    parser.add_argument("--exclude-project", action="append", default=[os.environ.get("ACOPS_SELF_PROJECT", "platform-ops")], help="project to leave out (repeatable)")
    args = parser.parse_args()

    start, end, label = window_from_args(args)
    out = args.out or os.path.join(os.environ.get("ACOPS_OUTPUTS", "/tmp/platform-ops"), "health-report", f"{label}.json")
    excluded = set(args.exclude_project)

    status = attempt("status", lambda: acli("status"), {})
    projects = [p for p in attempt("project ls", lambda: acli("project", "ls", "--all-pages"), {}).get("projects", []) if p.get("name") not in excluded]
    project_names = {p["id"]: p["name"] for p in projects}

    runs = attempt("run ls", lambda: acli("run", "ls", "--all-projects", "--started-from", iso(start), "--started-to", iso(end), "--all-pages"), {}).get("runs", [])
    runs = [r for r in runs if r.get("project_id") in project_names]

    agents, sandboxes, scheduler_runs = {}, [], []
    for project in projects:
        name = project["name"]
        for agent in attempt(f"agent ls ({name})", lambda: acli("-p", project["id"], "agent", "ls"), {}).get("agents", []):
            agents[(name, agent["name"])] = agent
        listed = attempt(f"ps ({name})", lambda: page_until(lambda limit, offset: acli("-p", project["id"], "ps", "--all", "--limit", str(limit), "--offset", str(offset)), "sandboxes", "updated_at", start), [])
        for sandbox in listed:
            created, updated = parse_ts(sandbox.get("created_at")), parse_ts(sandbox.get("updated_at"))
            if updated and updated >= start and (not created or created < end):
                sandbox["project"] = name
                sandboxes.append(sandbox)
        listed = attempt(f"scheduler runs ({name})", lambda: page_until(lambda limit, offset: acli("-p", project["id"], "scheduler", "runs", "--limit", str(limit), "--offset", str(offset)), "runs", "started_at", start), [])
        for run in listed:
            started = parse_ts(run.get("started_at"))
            if started and start <= started < end:
                run["project"] = name
                scheduler_runs.append(run)

    # Failed and canceled Runs: the summary error alone is usually just
    # "agent execution failed"; the cause is in error_stack.
    failed_runs = []
    for run in [r for r in runs if r.get("status") not in ("succeeded", "running", "pending")][:MAX_FAILED_RUN_DETAILS]:
        detail = attempt(f"inspect run {run['short_id']}", lambda: acli("-p", run["project_id"], "inspect", "run", run["id"]), {}) or {}
        cause = detail.get("error_stack") or detail.get("error") or run.get("error") or ""
        failed_runs.append({
            "project": project_names.get(run["project_id"]),
            "agent": run.get("agent_name"),
            "run": run.get("short_id"),
            "run_id": run.get("id"),
            "status": run.get("status"),
            "source": run.get("source"),
            "sandbox": run.get("sandbox_short_id"),
            "started_at": run.get("started_at"),
            "duration_ms": run.get("duration_ms"),
            "error": tail(detail.get("error") or run.get("error"), 300),
            "error_stack": first_lines(detail.get("error_stack")),
            "cleanup_error": tail(detail.get("cleanup_error"), 300) or None,
            "warnings": [tail(w, 200) for w in (detail.get("warnings") or [])][:5],
            "driver": detail.get("driver"),
            "image": detail.get("image_ref"),
            "signature": signature(cause),
        })

    # Sandbox histories: DevBoard tasks start Sandboxes without a Run, so a
    # failing step here is often the only record of what went wrong.
    sandbox_reports = []
    newest_first = sorted(sandboxes, key=lambda s: s.get("created_at") or "", reverse=True)
    for sandbox in newest_first[:MAX_SANDBOX_HISTORIES]:
        history = attempt(f"sandbox history {sandbox['sandbox_short_id']}", lambda: acli("-p", sandbox["project"], "sandbox", "history", sandbox["sandbox_id"], timeout=180), None)
        summary = summarize_history(history) if history else None
        if summary and (summary["failed_cells"] or summary["warning_events"]) or sandbox.get("status") == "failed":
            sandbox_reports.append({
                "project": sandbox["project"],
                "agent": sandbox.get("agent"),
                "sandbox": sandbox.get("sandbox_short_id"),
                "sandbox_id": sandbox.get("sandbox_id"),
                "status": sandbox.get("status"),
                "run": sandbox.get("run_short_id"),
                "driver": sandbox.get("driver"),
                "image": sandbox.get("image"),
                "created_at": sandbox.get("created_at"),
                "updated_at": sandbox.get("updated_at"),
                **(summary or {}),
            })
    long_running = [
        {"project": s["project"], "agent": s.get("agent"), "sandbox": s.get("sandbox_short_id"), "created_at": s.get("created_at"), "driver": s.get("driver")}
        for s in sandboxes
        if s.get("status") == "running" and parse_ts(s.get("created_at")) and parse_ts(s.get("created_at")) < end - dt.timedelta(hours=24)
    ]

    # Failures grouped by normalized message, across Runs, Sandbox steps,
    # and Scheduler Runs.
    groups = collections.OrderedDict()

    def add(sig, kind, agent, ref):
        group = groups.setdefault(sig, {"signature": sig, "count": 0, "kinds": set(), "agents": set(), "examples": []})
        group["count"] += 1
        group["kinds"].add(kind)
        group["agents"].add(agent or "?")
        if len(group["examples"]) < 3:
            group["examples"].append(ref)

    for run in failed_runs:
        if run["status"] == "failed":
            add(run["signature"], "run", run["agent"], f"run {run['run']}")
    for report in sandbox_reports:
        for cell in report.get("failed_cells", []):
            add(cell["signature"], "sandbox_step", report["agent"], f"sandbox {report['sandbox']}")
    failed_scheduler = [r for r in scheduler_runs if r.get("status") == "failed"]
    for run in failed_scheduler:
        add(signature(run.get("error") or run.get("result_json")), "scheduler_run", run.get("agent_name"), f"scheduler-run {run.get('run_short_id')}")
    signatures = sorted(
        ({**g, "kinds": sorted(g["kinds"]), "agents": sorted(g["agents"])} for g in groups.values()),
        key=lambda g: -g["count"],
    )

    # Usage per agent.
    usage = collections.defaultdict(lambda: {"runs": collections.Counter(), "sandboxes": 0, "sandboxes_with_failed_steps": 0, "scheduler_runs": collections.Counter(), "run_durations_ms": []})
    for run in runs:
        entry = usage[(project_names.get(run["project_id"]), run.get("agent_name"))]
        entry["runs"][run.get("status")] += 1
        if run.get("duration_ms"):
            entry["run_durations_ms"].append(run["duration_ms"])
    for sandbox in sandboxes:
        usage[(sandbox["project"], sandbox.get("agent"))]["sandboxes"] += 1
    for report in sandbox_reports:
        if report.get("failed_cells"):
            usage[(report["project"], report.get("agent"))]["sandboxes_with_failed_steps"] += 1
    for run in scheduler_runs:
        usage[(run["project"], run.get("agent_name"))]["scheduler_runs"][run.get("status")] += 1
    per_agent = []
    for (project, agent), entry in usage.items():
        config = agents.get((project, agent), {})
        durations = entry.pop("run_durations_ms")
        per_agent.append({
            "project": project, "agent": agent,
            "provider": config.get("provider"), "model": config.get("model") or "(daemon default)",
            "image": config.get("image"), "driver": config.get("driver"),
            "runs": dict(entry["runs"]), "median_run_seconds": round(statistics.median(durations) / 1000) if durations else None,
            "sandboxes": entry["sandboxes"], "sandboxes_with_failed_steps": entry["sandboxes_with_failed_steps"],
            "scheduler_runs": dict(entry["scheduler_runs"]),
        })
    per_agent.sort(key=lambda a: -(sum(a["runs"].values()) + a["sandboxes"] + sum(a["scheduler_runs"].values())))

    trigger_volume = collections.Counter((r.get("agent_name"), r.get("trigger_kind"), r.get("trigger_source")) for r in scheduler_runs)

    digest = {
        "window": {"label": label, "start": iso(start), "end": iso(end), "timezone": "Asia/Shanghai"},
        "generated_at": iso(dt.datetime.now(dt.timezone.utc)),
        "daemon": {"version": status.get("version"), "started_at": status.get("started_at"), "uptime_seconds": status.get("uptime_seconds")},
        "projects": [{"name": p["name"], "agents": p.get("agent_count"), "schedulers": p.get("scheduler_count"), "revision": p.get("current_revision")} for p in projects],
        "totals": {
            "runs": dict(collections.Counter(r.get("status") for r in runs)),
            "sandboxes_active": len(sandboxes),
            "sandboxes_by_status": dict(collections.Counter(s.get("status") for s in sandboxes)),
            "sandboxes_with_failed_steps": sum(1 for r in sandbox_reports if r.get("failed_cells")),
            "sandbox_histories_read": min(len(sandboxes), MAX_SANDBOX_HISTORIES),
            "scheduler_runs": dict(collections.Counter(r.get("status") for r in scheduler_runs)),
        },
        "error_signatures": signatures,
        "failed_runs": failed_runs,
        "failing_sandboxes": sandbox_reports,
        "failed_scheduler_runs": [
            {"project": r["project"], "agent": r.get("agent_name"), "scheduler_run": r.get("run_short_id"), "scheduler_run_id": r.get("run_id"), "trigger": r.get("trigger_id"), "started_at": r.get("started_at"), "error": tail(r.get("error"), 300), "result": tail(r.get("result_json"), 300)}
            for r in failed_scheduler[:40]
        ],
        "long_running_sandboxes": long_running,
        "usage_by_agent": per_agent,
        "scheduler_trigger_volume": [
            {"agent": agent, "kind": kind, "source": source, "runs": count}
            for (agent, kind, source), count in trigger_volume.most_common(10)
        ],
        "collection": {"acli_calls": STATS["calls"], "acli_seconds": round(STATS["seconds"], 1), "slowest_call": STATS["slowest"], "errors": ERRORS},
    }

    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as handle:
        json.dump(digest, handle, ensure_ascii=False, indent=1)
    totals = digest["totals"]
    print(f"digest: {out}")
    print(f"window: {digest['window']['start']} .. {digest['window']['end']} ({label})")
    print(f"runs: {totals['runs']}  sandboxes: {totals['sandboxes_active']} ({totals['sandboxes_with_failed_steps']} with failed steps)  scheduler runs: {totals['scheduler_runs']}")
    print(f"error signatures: {len(signatures)}  collection errors: {len(ERRORS)}  acli: {STATS['calls']} calls, {STATS['seconds']:.0f}s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
