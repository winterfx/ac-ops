#!/usr/bin/env python3
"""Send a health report to the DingTalk custom-robot webhook.

The report is the run's final answer. This tool sends the same text to
DingTalk as one or more markdown messages, and never prints the webhook.

Webhook lookup, first hit wins:
  --webhook URL
  $DINGTALK_WEBHOOK
  the file named by $DINGTALK_WEBHOOK_FILE
  /workspace/secrets/dingtalk-webhook

If the bot uses 加签 (signature), put the secret in $DINGTALK_SECRET, or in the
file named by $DINGTALK_SECRET_FILE (default /workspace/secrets/dingtalk-secret).
A missing signing secret file just means "not signed".

If the bot uses a keyword, set $DINGTALK_KEYWORD (or --keyword); every message
gets the keyword in its title and text. Without it a keyword-protected bot
rejects the message.

Report input: --file PATH, or stdin.

DingTalk markdown does not render tables, so markdown tables are rewritten as
bullet lists. A report longer than --limit-bytes (UTF-8, default 12000) is sent
as several messages.

Examples:
  notify.py --file /tmp/platform-ops/health-report/2026-11-02.md \
            --title "agent-compose 健康报告 2026-11-02"
  notify.py --file report.md --dry-run      # print the payload, send nothing
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_WEBHOOK_FILE = "/workspace/secrets/dingtalk-webhook"
DEFAULT_SECRET_FILE = "/workspace/secrets/dingtalk-secret"
DEFAULT_LIMIT_BYTES = 12000  # DingTalk's markdown cap is 20000 bytes.
RATE_LIMIT_RETRIES = 3
HTTP_TIMEOUT = 20


class NotifyError(Exception):
    pass


def env_or_file(value_env, file_env, default_file, required):
    """Return an env var, else a file's contents. Never echo the value."""
    value = os.environ.get(value_env, "").strip()
    if value:
        return value
    path = os.environ.get(file_env, "").strip() or default_file
    try:
        with open(path, "r", encoding="utf-8") as handle:
            value = handle.read().strip()
    except FileNotFoundError:
        value = ""
    except OSError as exc:
        raise NotifyError(f"cannot read {path}: {exc}")
    if not value and required:
        raise NotifyError(
            f"no webhook: set ${value_env}, ${file_env}, or create {default_file}"
        )
    return value


def scrub(text):
    """Hide an access_token that a library may have copied into an error."""
    return re.sub(r"(access_token=)[^&\s\"']+", r"\1***", str(text))


def table_cells(line):
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def is_table_row(line):
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]


def is_table_separator(line):
    if not is_table_row(line):
        return False
    cells = [cell.replace(" ", "") for cell in table_cells(line)]
    return bool(cells) and all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells)


def render_tables(lines):
    """Rewrite markdown tables as bullet lists DingTalk can render."""
    out = []
    index = 0
    while index < len(lines):
        if (
            is_table_row(lines[index])
            and index + 1 < len(lines)
            and is_table_separator(lines[index + 1])
        ):
            header = table_cells(lines[index])
            index += 2
            while index < len(lines) and is_table_row(lines[index]):
                row = table_cells(lines[index])
                index += 1
                parts = []
                for column, cell in enumerate(row):
                    if not cell:
                        continue
                    key = header[column] if column < len(header) else ""
                    parts.append(f"{key}: {cell}" if key else cell)
                if parts:
                    out.append("- " + "；".join(parts))
            continue
        out.append(lines[index])
        index += 1
    return out


def split_long_line(line, limit):
    """Split one line that alone exceeds the byte limit."""
    if len(line.encode("utf-8")) <= limit:
        return [line]
    pieces, current, current_bytes = [], [], 0
    for char in line:
        size = len(char.encode("utf-8"))
        if current and current_bytes + size > limit:
            pieces.append("".join(current))
            current, current_bytes = [], 0
        current.append(char)
        current_bytes += size
    if current:
        pieces.append("".join(current))
    return pieces


def split_message(text, limit):
    """Split into chunks that each fit the byte limit, on line boundaries."""
    lines = []
    for line in text.splitlines():
        lines.extend(split_long_line(line, limit))
    chunks, current, current_bytes = [], [], 0
    for line in lines:
        size = len(line.encode("utf-8")) + 1
        if current and current_bytes + size > limit:
            chunks.append("\n".join(current).strip())
            current, current_bytes = [], 0
        current.append(line)
        current_bytes += size
    if current:
        chunks.append("\n".join(current).strip())
    return [chunk for chunk in chunks if chunk]


def default_title(text, fallback):
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return fallback


def apply_keyword(title, text, keyword):
    """Make sure a keyword-protected robot accepts this message."""
    if not keyword:
        return title, text
    if keyword not in title:
        title = f"{keyword} · {title}"
    if keyword not in text:
        text = f"{keyword}\n\n{text}"
    return title, text


def signed_url(url, secret):
    if not secret:
        return url
    timestamp = str(int(time.time() * 1000))
    string_to_sign = f"{timestamp}\n{secret}"
    digest = hmac.new(
        secret.encode("utf-8"), string_to_sign.encode("utf-8"), hashlib.sha256
    ).digest()
    sign = urllib.parse.quote_plus(base64.b64encode(digest).decode("utf-8"))
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}timestamp={timestamp}&sign={sign}"


def post(url, payload):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raise NotifyError(f"HTTP {exc.code} from DingTalk: {scrub(exc.reason)}")
    except urllib.error.URLError as exc:
        raise NotifyError(f"cannot reach DingTalk: {scrub(exc.reason)}")
    try:
        answer = json.loads(raw)
    except ValueError:
        raise NotifyError(f"DingTalk returned non-JSON: {scrub(raw[:200])}")
    errcode = answer.get("errcode", -1)
    if errcode != 0:
        raise NotifyError(
            f"DingTalk errcode={errcode} errmsg={scrub(answer.get('errmsg', ''))}"
        )
    return answer


def is_rate_limited(exc):
    message = str(exc).lower()
    return "130101" in message or "too fast" in message or "频繁" in message


def send(url, payload):
    for attempt in range(RATE_LIMIT_RETRIES):
        try:
            return post(url, payload)
        except NotifyError as exc:
            retry = is_rate_limited(exc) and attempt + 1 < RATE_LIMIT_RETRIES
            if not retry:
                raise
            time.sleep(5)
    raise NotifyError("send failed")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--file", help="report path; default: stdin")
    parser.add_argument("--title", help="DingTalk title; default: first '# ' heading")
    parser.add_argument("--webhook", help="webhook URL; prefer the env var or file")
    parser.add_argument(
        "--keyword",
        help="robot keyword; default: $DINGTALK_KEYWORD. Added to the title and text",
    )
    parser.add_argument("--limit-bytes", type=int, default=DEFAULT_LIMIT_BYTES)
    parser.add_argument("--dry-run", action="store_true", help="print the payload, send nothing")
    args = parser.parse_args(argv)

    try:
        if args.file:
            with open(args.file, "r", encoding="utf-8") as handle:
                text = handle.read()
        else:
            text = sys.stdin.read()
        text = text.strip()
        if not text:
            raise NotifyError("empty report")

        title = args.title or default_title(text, "agent-compose 健康报告")
        body = "\n".join(render_tables(text.splitlines()))
        chunks = split_message(body, args.limit_bytes)
        keyword = (args.keyword or os.environ.get("DINGTALK_KEYWORD", "")).strip()

        def payload_for(index, chunk):
            chunk_title = title if len(chunks) == 1 else f"{title}（{index}/{len(chunks)}）"
            chunk_title, chunk = apply_keyword(chunk_title, chunk, keyword)
            return {
                "msgtype": "markdown",
                "markdown": {"title": chunk_title, "text": chunk},
            }

        if args.dry_run:
            for index, chunk in enumerate(chunks, 1):
                print(json.dumps(payload_for(index, chunk), ensure_ascii=False, indent=1))
            return 0

        webhook = args.webhook or env_or_file(
            "DINGTALK_WEBHOOK", "DINGTALK_WEBHOOK_FILE", DEFAULT_WEBHOOK_FILE, True
        )
        secret = env_or_file(
            "DINGTALK_SECRET", "DINGTALK_SECRET_FILE", DEFAULT_SECRET_FILE, False
        )
        url = signed_url(webhook, secret)

        for index, chunk in enumerate(chunks, 1):
            send(url, payload_for(index, chunk))
            if index < len(chunks):
                time.sleep(1)
        sent_title = payload_for(1, chunks[0])["markdown"]["title"]
        print(f"sent {len(chunks)} DingTalk message(s): {sent_title}")
        return 0
    except (NotifyError, OSError) as exc:
        print(f"notify: {scrub(exc)}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
