#!/usr/bin/env python3
"""Codex diagnostic proxy for OpenAI-compatible providers.

Logs request metadata to JSON and forwards the original body upstream.
"""

from __future__ import annotations

import argparse
import datetime as dt
from itertools import count
import json
import re
import signal
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
import urllib.error
import urllib.request

ARGS: argparse.Namespace | None = None
LOG_PATH: Path | None = None
FIRST_ENTRY = True
LOG_CLOSED = False
REQUEST_IDS = count(1)
REDACTED = "[REDACTED]"
INLINE_REDACTIONS = (
    (
        re.compile(r"(?i)\b(Bearer)(\s+)([^\s,\"']+)"),
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        "inline_string_matches",
    ),
    (
        re.compile(r"(?i)\b(sk-[A-Za-z0-9_-]+)\b"),
        lambda _match: REDACTED,
        "inline_string_matches",
    ),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|password|authorization|cookie)(\s*[:=]\s*)([^\s,\"']+)"
        ),
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        "inline_string_matches",
    ),
)


def append_entry(entry: dict[str, Any]) -> None:
    global FIRST_ENTRY

    if LOG_PATH is None:
        raise RuntimeError("LOG_PATH is not initialized")

    with LOG_PATH.open("a", encoding="utf-8") as handle:
        if not FIRST_ENTRY:
            handle.write(",\n")
        handle.write(json.dumps(entry, indent=2, sort_keys=True))
        handle.flush()

    FIRST_ENTRY = False


def finalize_log() -> None:
    global LOG_CLOSED

    if LOG_PATH is None or LOG_CLOSED:
        return

    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write("\n]\n")

    LOG_CLOSED = True


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="milliseconds")


def elapsed_ms(start_monotonic: float, end_monotonic: float | None) -> int | None:
    if end_monotonic is None:
        return None
    return int(round((end_monotonic - start_monotonic) * 1000))


def summarize_tools(tools: list[dict[str, Any]]) -> tuple[dict[str, int], list[str]]:
    tool_types: dict[str, int] = {}
    tool_names: list[str] = []

    for tool in tools:
        tool_type = tool.get("type", "?")
        tool_types[tool_type] = tool_types.get(tool_type, 0) + 1
        if tool_type == "function":
            tool_names.append(tool.get("name", "?"))

    return tool_types, tool_names


def normalized_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]", "", key.lower())


def is_sensitive_key(key: str) -> bool:
    normalized = normalized_key(key)
    if not normalized:
        return False

    exact_matches = {
        "apikey",
        "authorization",
        "cookie",
        "password",
        "secret",
        "token",
    }
    if normalized in exact_matches:
        return True

    substrings = ("token", "secret", "password", "authorization", "cookie", "apikey")
    return any(part in normalized for part in substrings)


def increment_redaction(summary: dict[str, int], key: str, amount: int = 1) -> None:
    summary[key] = summary.get(key, 0) + amount


def redact_string(value: str, summary: dict[str, int]) -> str:
    redacted = value
    for pattern, replacer, summary_key in INLINE_REDACTIONS:
        redacted, count = pattern.subn(replacer, redacted)
        if count:
            increment_redaction(summary, summary_key, count)
    return redacted


def sanitize_value(value: Any, summary: dict[str, int], key: str | None = None) -> Any:
    if key is not None and is_sensitive_key(key):
        increment_redaction(summary, "sensitive_key_values")
        return REDACTED

    if isinstance(value, dict):
        return {
            item_key: sanitize_value(item_value, summary, key=item_key)
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [sanitize_value(item, summary) for item in value]
    if isinstance(value, str):
        return redact_string(value, summary)
    return value


class ProxyHandler(BaseHTTPRequestHandler):
    server_version = "CodexProxy/1.0"
    protocol_version = "HTTP/1.1"

    def do_POST(self) -> None:
        assert ARGS is not None

        request_id = next(REQUEST_IDS)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        request_started_at = now_iso()
        request_started_monotonic = time.monotonic()

        try:
            request_json = json.loads(raw)
        except json.JSONDecodeError as exc:
            append_entry(
                {
                    "error": f"invalid_json: {exc}",
                    "path": self.path,
                    "request_id": request_id,
                    "request_started_at": request_started_at,
                    "raw_bytes": length,
                    "ts": request_started_at,
                }
            )
            body = json.dumps({"error": f"Invalid JSON: {exc}"}).encode("utf-8")
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            return

        tools = request_json.get("tools") or []
        input_items = request_json.get("input") or []
        tool_types, tool_names = summarize_tools(tools)
        entry = {
            "input_chars": len(json.dumps(input_items)),
            "instructions_chars": len(request_json.get("instructions", "")),
            "model": request_json.get("model"),
            "num_input_items": len(input_items),
            "num_tools": len(tools),
            "path": self.path,
            "request_id": request_id,
            "request_started_at": request_started_at,
            "reasoning": request_json.get("reasoning"),
            "response_bytes": 0,
            "response_completed_at": None,
            "first_response_byte_at": None,
            "stream": request_json.get("stream"),
            "tool_names": tool_names,
            "tool_types": tool_types,
            "tools_chars": len(json.dumps(tools)),
            "total_bytes": length,
            "ts": request_started_at,
            "ttft_ms": None,
            "generation_duration_ms": None,
            "end_to_end_duration_ms": None,
            "http_status": None,
        }
        if ARGS.dump_context:
            redaction_summary: dict[str, int] = {}
            sanitized_instructions = sanitize_value(
                request_json.get("instructions", ""), redaction_summary
            )
            entry["instructions"] = sanitized_instructions
            entry["system_prompt"] = sanitized_instructions
            entry["input"] = sanitize_value(input_items, redaction_summary)
            entry["context_redacted"] = True
            entry["redaction_summary"] = {
                "inline_string_matches": redaction_summary.get("inline_string_matches", 0),
                "sensitive_key_values": redaction_summary.get("sensitive_key_values", 0),
            }

        upstream = urllib.request.Request(
            f"http://127.0.0.1:{ARGS.target}{self.path}",
            data=raw,
            headers={"Content-Type": self.headers.get("Content-Type", "application/json")},
            method="POST",
        )

        try:
            with urllib.request.urlopen(upstream, timeout=ARGS.timeout) as response:
                entry["http_status"] = response.status
                self.send_response(response.status)
                for key, value in response.getheaders():
                    lower_key = key.lower()
                    if lower_key in {"transfer-encoding", "connection"}:
                        continue
                    self.send_header(key, value)
                self.end_headers()

                first_response_byte_monotonic: float | None = None
                response_completed_monotonic: float | None = None
                while True:
                    chunk = response.read(4096)
                    if not chunk:
                        response_completed_monotonic = time.monotonic()
                        entry["response_completed_at"] = now_iso()
                        break
                    if first_response_byte_monotonic is None:
                        first_response_byte_monotonic = time.monotonic()
                        entry["first_response_byte_at"] = now_iso()
                    self.wfile.write(chunk)
                    self.wfile.flush()
                    entry["response_bytes"] = int(entry["response_bytes"]) + len(chunk)

                if first_response_byte_monotonic is not None:
                    entry["ttft_ms"] = elapsed_ms(
                        request_started_monotonic, first_response_byte_monotonic
                    )
                    if response_completed_monotonic is not None:
                        entry["generation_duration_ms"] = elapsed_ms(
                            first_response_byte_monotonic, response_completed_monotonic
                        )
                if response_completed_monotonic is not None:
                    entry["end_to_end_duration_ms"] = elapsed_ms(
                        request_started_monotonic, response_completed_monotonic
                    )
        except urllib.error.HTTPError as exc:
            error_body = exc.read()
            entry["error"] = f"upstream_http_error: {exc.code} {exc.reason}"
            entry["http_status"] = exc.code
            entry["response_completed_at"] = now_iso()
            error_completed_monotonic = time.monotonic()
            entry["end_to_end_duration_ms"] = elapsed_ms(
                request_started_monotonic, error_completed_monotonic
            )
            if error_body:
                entry["response_bytes"] = len(error_body)
                entry["first_response_byte_at"] = entry["response_completed_at"]
                entry["ttft_ms"] = entry["end_to_end_duration_ms"]
                entry["generation_duration_ms"] = 0
            self.send_response(exc.code)
            for key, value in exc.headers.items():
                lower_key = key.lower()
                if lower_key in {"transfer-encoding", "connection"}:
                    continue
                self.send_header(key, value)
            self.end_headers()
            if error_body:
                self.wfile.write(error_body)
                self.wfile.flush()
        except Exception as exc:  # pragma: no cover - exercised in manual diagnostics
            entry["error"] = str(exc)
            entry["http_status"] = 502
            entry["response_completed_at"] = now_iso()
            entry["end_to_end_duration_ms"] = elapsed_ms(
                request_started_monotonic, time.monotonic()
            )
            body = json.dumps({"error": str(exc)}).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
        append_entry(entry)

    def log_message(self, *_: object) -> None:
        pass


def handle_signal(signum: int, _frame: object) -> None:
    finalize_log()
    raise SystemExit(128 + signum)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Codex diagnostic proxy")
    parser.add_argument(
        "-p",
        "--port",
        type=int,
        default=11435,
        help="proxy listen port (default: 11435)",
    )
    parser.add_argument(
        "-t",
        "--target",
        type=int,
        default=11434,
        help="upstream target port (default: 11434)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=str,
        default=None,
        help="log file path (default: /tmp/codex-proxy-HHMMSS.json)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="request timeout seconds (default: 300)",
    )
    parser.add_argument(
        "--dump-context",
        action="store_true",
        help="log sanitized instructions and input in addition to summary metrics",
    )
    return parser.parse_args()


def main() -> int:
    global ARGS
    global LOG_PATH

    ARGS = parse_args()
    log_name = ARGS.output or (
        f"/tmp/codex-proxy-{dt.datetime.now().strftime('%H%M%S')}.json"
    )
    LOG_PATH = Path(log_name).expanduser().resolve()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.write_text("[\n", encoding="utf-8")

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    server = ThreadingHTTPServer(("127.0.0.1", ARGS.port), ProxyHandler)

    print(f"Proxy: 127.0.0.1:{ARGS.port} -> 127.0.0.1:{ARGS.target}", flush=True)
    print(f"Log:   {LOG_PATH}", flush=True)
    print(f"READY {LOG_PATH}", flush=True)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        finalize_log()
        print(f"FINALIZED {LOG_PATH}", flush=True)

    return 0


if __name__ == "__main__":
    sys.exit(main())
