#!/usr/bin/env python3
"""Run Codex throughput benchmarks, including multi-phase workflows."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.11+ should have tomllib
    tomllib = None


def now_iso() -> str:
    from datetime import datetime

    return datetime.now().isoformat(timespec="milliseconds")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Codex throughput benchmark")
    parser.add_argument("--workdir", required=True, help="Working directory for codex exec")
    prompt_group = parser.add_mutually_exclusive_group(required=True)
    prompt_group.add_argument("--prompt", help="Benchmark prompt text")
    prompt_group.add_argument("--prompt-file", help="Path to a file containing the benchmark prompt")
    parser.add_argument("--profile", help="Codex profile to use from ~/.codex/config.toml")
    parser.add_argument("--model", help="Optional model override")
    parser.add_argument(
        "--proxy-base-url",
        required=True,
        help="OpenAI-compatible base URL for the diagnostic proxy",
    )
    parser.add_argument(
        "--proxy-log",
        required=True,
        help="Path to the proxy JSON log that this benchmark run writes to",
    )
    parser.add_argument(
        "--events-log",
        required=True,
        help="Path to the raw codex --json event log to write",
    )
    parser.add_argument(
        "--summary-out",
        required=True,
        help="Path to the benchmark summary JSON to write",
    )
    parser.add_argument(
        "--provider-alias",
        default="ollama-proxy",
        help="Temporary provider alias injected into Codex config overrides",
    )
    parser.add_argument(
        "--provider-name",
        default="Proxy",
        help="Display name for the injected temporary provider",
    )
    parser.add_argument(
        "--extra-codex-arg",
        action="append",
        default=[],
        help="Additional codex CLI argument. Repeat for multiple arguments.",
    )
    parser.add_argument(
        "--proxy-log-wait-ms",
        type=int,
        default=5000,
        help="How long to wait for the proxy log to become readable after each codex run",
    )
    parser.add_argument(
        "--workflow-mode",
        choices=("single-phase", "multi-phase"),
        default="multi-phase",
        help="multi-phase is the default skill workflow; single-phase keeps the older one-shot benchmark",
    )
    parser.add_argument(
        "--measured-runs",
        type=int,
        default=3,
        help="In multi-phase mode, how many warm measured runs to execute after the cold probe",
    )
    parser.add_argument(
        "--codex-config",
        default="~/.codex/config.toml",
        help="Path to the Codex config used to resolve model context limits",
    )
    parser.add_argument(
        "--near-context",
        dest="near_context",
        action="store_true",
        help="Enable the near-context retrieval probe after the warm runs",
    )
    parser.add_argument(
        "--no-near-context",
        dest="near_context",
        action="store_false",
        help="Disable the near-context retrieval probe in multi-phase mode",
    )
    parser.add_argument(
        "--context-window-tokens",
        type=int,
        default=None,
        help="Optional explicit model context window token count",
    )
    parser.add_argument(
        "--context-budget-tokens",
        type=int,
        default=None,
        help="Optional explicit practical input budget token count for near-context targeting",
    )
    parser.add_argument(
        "--near-context-target-ratio",
        type=float,
        default=0.90,
        help="Target fraction of the resolved context budget to occupy with input tokens",
    )
    parser.add_argument(
        "--near-context-output-reserve-tokens",
        type=int,
        default=512,
        help="Approximate output token reserve to leave for the near-context probe",
    )
    parser.add_argument(
        "--near-context-max-attempts",
        type=int,
        default=2,
        help="Maximum number of prompt-size adjustment attempts for the near-context probe",
    )
    parser.add_argument(
        "--near-context-tolerance-ratio",
        type=float,
        default=0.03,
        help="Acceptable absolute gap between target and observed near-context occupancy ratios",
    )
    parser.set_defaults(near_context=None)
    args = parser.parse_args()
    if args.near_context is None:
        args.near_context = args.workflow_mode == "multi-phase"
    if args.measured_runs < 1:
        parser.error("--measured-runs must be at least 1")
    if not 0.1 <= args.near_context_target_ratio <= 0.99:
        parser.error("--near-context-target-ratio must be between 0.1 and 0.99")
    if args.near_context_max_attempts < 1:
        parser.error("--near-context-max-attempts must be at least 1")
    if not 0.0 <= args.near_context_tolerance_ratio <= 0.25:
        parser.error("--near-context-tolerance-ratio must be between 0 and 0.25")
    return args


def load_prompt(args: argparse.Namespace) -> str:
    if args.prompt is not None:
        return args.prompt
    assert args.prompt_file is not None
    return Path(args.prompt_file).expanduser().read_text(encoding="utf-8")


def build_command(args: argparse.Namespace) -> list[str]:
    command = [
        "codex",
        "exec",
        "--skip-git-repo-check",
        "--ephemeral",
        "--json",
        "-C",
        str(Path(args.workdir).expanduser().resolve()),
    ]
    if args.profile:
        command.extend(
            [
                "-p",
                args.profile,
                "-c",
                f'profiles.{args.profile}.model_provider="{args.provider_alias}"',
            ]
        )
    else:
        command.extend(["-c", f"model_provider={args.provider_alias}"])
    command.extend(
        [
            "-c",
            f'model_providers.{args.provider_alias}.name="{args.provider_name}"',
            "-c",
            f'model_providers.{args.provider_alias}.base_url="{args.proxy_base_url}"',
        ]
    )
    if args.model:
        command.extend(["-m", args.model])
    command.extend(args.extra_codex_arg)
    command.append("-")
    return command


def load_json_array_with_repair(path: Path) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    cleaned = raw.rstrip()
    if not cleaned:
        return []
    if not cleaned.endswith("]"):
        cleaned = f"{cleaned}\n]\n"
    payload = json.loads(cleaned)
    if not isinstance(payload, list):
        raise ValueError("proxy log must contain a JSON array")
    return [item for item in payload if isinstance(item, dict)]


def load_codex_config(path: Path) -> dict[str, Any]:
    if tomllib is None or not path.exists():
        return {}
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return payload if isinstance(payload, dict) else {}


def request_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if "total_bytes" in entry]


def legacy_error_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if "error" in entry and "total_bytes" not in entry]


def snapshot_entries(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        return load_json_array_with_repair(path)
    except Exception:
        return []


def wait_for_phase_entries(
    path: Path,
    before_count: int,
    timeout_ms: int,
) -> list[dict[str, Any]]:
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    last_entries: list[dict[str, Any]] = []
    while time.monotonic() < deadline:
        if path.exists():
            try:
                last_entries = load_json_array_with_repair(path)
            except Exception:
                time.sleep(0.1)
                continue
            if len(last_entries) > before_count:
                break
        time.sleep(0.1)
    return last_entries[before_count:]


def derive_invalid_reason(
    exit_code: int,
    usage: dict[str, Any] | None,
    requests: list[dict[str, Any]],
    legacy_errors: list[dict[str, Any]],
) -> str | None:
    if exit_code != 0:
        return f"nonzero_exit_code:{exit_code}"
    if usage is None:
        return "missing_turn_completed_usage"
    output_tokens = usage.get("output_tokens")
    if not isinstance(output_tokens, int) or output_tokens <= 0:
        return "missing_output_tokens"
    if legacy_errors:
        return f"legacy_error_entries:{len(legacy_errors)}"
    if not requests:
        return "no_proxy_request_captured"
    if len(requests) != 1:
        return f"request_count:{len(requests)}"
    request = requests[0]
    if request.get("error"):
        return f"request_error:{request.get('error')}"
    status = request.get("http_status")
    if not isinstance(status, int) or status < 200 or status >= 300:
        return f"http_status:{status}"
    if not isinstance(request.get("ttft_ms"), int):
        return "missing_ttft"
    generation_duration_ms = request.get("generation_duration_ms")
    if not isinstance(generation_duration_ms, int) or generation_duration_ms <= 0:
        return "missing_or_nonpositive_generation_duration"
    end_to_end_duration_ms = request.get("end_to_end_duration_ms")
    if not isinstance(end_to_end_duration_ms, int) or end_to_end_duration_ms <= 0:
        return "missing_or_nonpositive_end_to_end_duration"
    return None


def safe_tokens_per_second(tokens: int, duration_ms: int | None) -> float | None:
    if duration_ms is None or duration_ms <= 0:
        return None
    return round(tokens / (duration_ms / 1000.0), 3)


def median_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.median(values), 3)


def mean_or_none(values: list[float]) -> float | None:
    if not values:
        return None
    return round(statistics.mean(values), 3)


def min_or_none(values: list[float]) -> float | None:
    return round(min(values), 3) if values else None


def max_or_none(values: list[float]) -> float | None:
    return round(max(values), 3) if values else None


def estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def write_prompt(stdin_handle: Any, prompt: str) -> None:
    chunk_size = 65536
    payload = prompt if prompt.endswith("\n") else f"{prompt}\n"
    for index in range(0, len(payload), chunk_size):
        stdin_handle.write(payload[index : index + chunk_size])
    stdin_handle.close()


def execute_codex(command: list[str], prompt: str, events_log: Path) -> dict[str, Any]:
    process_started_at = now_iso()
    process_started_monotonic = time.monotonic()
    invalid_event_lines = 0
    event_count = 0
    usage: dict[str, Any] | None = None
    last_agent_message: str | None = None
    thread_id: str | None = None

    with events_log.open("w", encoding="utf-8") as events_handle:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        assert process.stdin is not None

        write_prompt(process.stdin, prompt)

        for line in process.stdout:
            events_handle.write(line)
            events_handle.flush()
            stripped = line.strip()
            if not stripped:
                continue
            try:
                payload = json.loads(stripped)
            except json.JSONDecodeError:
                invalid_event_lines += 1
                continue
            event_count += 1
            if payload.get("type") == "thread.started":
                thread_id = payload.get("thread_id")
            if payload.get("type") == "turn.completed":
                if isinstance(payload.get("usage"), dict):
                    usage = payload["usage"]
            item = payload.get("item")
            if isinstance(item, dict) and item.get("type") == "agent_message":
                last_agent_message = item.get("text")

        stderr_text = process.stderr.read()
        exit_code = process.wait()

    process_completed_at = now_iso()
    process_duration_ms = int(round((time.monotonic() - process_started_monotonic) * 1000))
    return {
        "exit_code": exit_code,
        "process_started_at": process_started_at,
        "process_completed_at": process_completed_at,
        "process_duration_ms": process_duration_ms,
        "event_count": event_count,
        "invalid_event_lines": invalid_event_lines,
        "usage": usage,
        "last_agent_message": last_agent_message,
        "thread_id": thread_id,
        "stderr_excerpt": stderr_text.strip()[:4000] if stderr_text.strip() else None,
    }


def derive_phase_events_log_path(base: Path, phase_name: str, preserve_original: bool) -> Path:
    if preserve_original:
        return base
    suffix = "".join(base.suffixes)
    stem = base.name[: -len(suffix)] if suffix else base.name
    filename = f"{stem}.{phase_name}{suffix or '.jsonl'}"
    return base.with_name(filename)


def build_phase_summary(
    *,
    phase_name: str,
    prompt: str,
    events_log: Path,
    run_result: dict[str, Any],
    phase_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    requests = request_entries(phase_entries)
    legacy_errors = legacy_error_entries(phase_entries)
    invalid_reason = derive_invalid_reason(
        int(run_result["exit_code"]),
        run_result.get("usage"),
        requests,
        legacy_errors,
    )
    valid_phase = invalid_reason is None
    request = requests[0] if len(requests) == 1 else {}
    usage = run_result.get("usage") or {}
    output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else None
    summary: dict[str, Any] = {
        "phase_name": phase_name,
        "status": "completed" if run_result["exit_code"] == 0 else "failed",
        "valid_phase": valid_phase,
        "invalid_reason": invalid_reason,
        "prompt_chars": len(prompt),
        "prompt_token_estimate": estimate_text_tokens(prompt),
        "events_log_path": str(events_log),
        "request_count": len(requests),
        "request_ids": [entry.get("request_id") for entry in requests],
        "thread_id": run_result.get("thread_id"),
        "exit_code": run_result["exit_code"],
        "usage": usage,
        "last_agent_message": run_result.get("last_agent_message"),
        "process_started_at": run_result["process_started_at"],
        "process_completed_at": run_result["process_completed_at"],
        "process_duration_ms": run_result["process_duration_ms"],
        "event_count": run_result["event_count"],
        "invalid_event_lines": run_result["invalid_event_lines"],
        "legacy_error_count": len(legacy_errors),
        "stderr_excerpt": run_result.get("stderr_excerpt"),
        "model": request.get("model"),
        "request_id": request.get("request_id"),
        "ttft_ms": request.get("ttft_ms"),
        "generation_duration_ms": request.get("generation_duration_ms"),
        "end_to_end_duration_ms": request.get("end_to_end_duration_ms"),
        "http_status": request.get("http_status"),
        "input_tokens": usage.get("input_tokens") if isinstance(usage, dict) else None,
        "output_tokens": output_tokens,
        "tokens_per_second_generation": None,
        "tokens_per_second_end_to_end": None,
    }

    if valid_phase and isinstance(output_tokens, int):
        summary["tokens_per_second_generation"] = safe_tokens_per_second(
            output_tokens, request.get("generation_duration_ms")
        )
        summary["tokens_per_second_end_to_end"] = safe_tokens_per_second(
            output_tokens, request.get("end_to_end_duration_ms")
        )

    return summary


def resolve_model_context(args: argparse.Namespace, config: dict[str, Any]) -> dict[str, Any]:
    profiles = config.get("profiles") if isinstance(config.get("profiles"), dict) else {}
    profile_config = profiles.get(args.profile) if args.profile else None
    if not isinstance(profile_config, dict):
        profile_config = {}

    configured_model = profile_config.get("model") if isinstance(profile_config.get("model"), str) else None
    effective_model = args.model or configured_model
    if effective_model is None and isinstance(config.get("model"), str):
        effective_model = config["model"]

    models = config.get("models") if isinstance(config.get("models"), dict) else {}
    model_config = models.get(effective_model) if effective_model else None
    if not isinstance(model_config, dict):
        model_config = {}

    configured_context_window = model_config.get("model_context_window")
    configured_budget = model_config.get("model_auto_compact_token_limit")

    if args.context_window_tokens is not None:
        context_window_tokens = args.context_window_tokens
        context_window_source = "arg.context_window_tokens"
    elif isinstance(configured_context_window, int):
        context_window_tokens = configured_context_window
        context_window_source = "config.model_context_window"
    else:
        context_window_tokens = None
        context_window_source = None

    if args.context_budget_tokens is not None:
        context_budget_tokens = args.context_budget_tokens
        context_budget_source = "arg.context_budget_tokens"
    elif isinstance(configured_budget, int):
        context_budget_tokens = configured_budget
        context_budget_source = "config.model_auto_compact_token_limit"
    elif context_window_tokens is not None:
        context_budget_tokens = context_window_tokens
        context_budget_source = context_window_source
    else:
        context_budget_tokens = None
        context_budget_source = None

    return {
        "config_path": str(Path(args.codex_config).expanduser().resolve()),
        "effective_model": effective_model,
        "profile_model": configured_model,
        "context_window_tokens": context_window_tokens,
        "context_window_source": context_window_source,
        "context_budget_tokens": context_budget_tokens,
        "context_budget_source": context_budget_source,
        "model_auto_compact_token_limit": configured_budget if isinstance(configured_budget, int) else None,
    }


def build_near_context_prompt(target_prompt_tokens: int) -> tuple[str, dict[str, Any]]:
    target_chars = max(2048, target_prompt_tokens * 4)
    header = (
        "You are running a long-context retrieval probe.\n"
        "Below is a synthetic archive of numbered records.\n"
        "Each record contains an exact KEY value.\n"
        "At the end, return valid JSON only.\n"
        "The JSON object must contain only the requested record ids as keys.\n"
        "Each value must be the exact KEY copied from the archive.\n"
        "Do not call any tools.\n\n"
        "ARCHIVE START\n"
    )
    footer_template = (
        "\nARCHIVE END\n"
        "Return valid JSON only with these keys: {keys}.\n"
        "Each value must be the exact KEY from the matching record.\n"
        "No markdown. No extra words.\n"
    )

    records: list[str] = []
    current_chars = len(header)
    index = 1
    while current_chars < target_chars:
        key = record_key(index)
        record = (
            f"RECORD {index:05d} | KEY {key} | LANE {(index % 23) + 1:02d} | "
            f"NOTE synthetic retrieval filler block {index:05d} with stable wording.\n"
        )
        records.append(record)
        current_chars += len(record)
        index += 1

    record_count = max(1, index - 1)
    probe_ids = sorted(
        {
            1,
            max(1, record_count // 4),
            max(1, record_count // 2),
            max(1, (record_count * 3) // 4),
            record_count,
        }
    )
    expected = {f"record_{probe_id:05d}": record_key(probe_id) for probe_id in probe_ids}
    footer = footer_template.format(keys=", ".join(expected))
    prompt = header + "".join(records) + footer
    metadata = {
        "record_count": record_count,
        "probe_ids": probe_ids,
        "expected_records": expected,
        "target_prompt_tokens_estimate": target_prompt_tokens,
        "prompt_chars": len(prompt),
    }
    return prompt, metadata


def record_key(index: int) -> str:
    checksum = (index * 2654435761) % 1_000_000_007
    return f"KEY-{index:05d}-{checksum:09d}"


def strip_code_fences(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if not lines:
        return stripped
    lines = lines[1:]
    if lines and lines[-1].strip().startswith("```"):
        lines = lines[:-1]
    return "\n".join(lines).strip()


def evaluate_near_context_behavior(
    last_agent_message: str | None,
    expected_records: dict[str, str],
) -> dict[str, Any]:
    response_text = last_agent_message or ""
    json_candidate = strip_code_fences(response_text)
    parsed_payload: dict[str, Any] | None = None
    try:
        decoded = json.loads(json_candidate)
        if isinstance(decoded, dict):
            parsed_payload = decoded
    except json.JSONDecodeError:
        parsed_payload = None

    matched_fields: list[str] = []
    mismatched_fields: list[dict[str, Any]] = []
    for field, expected_value in expected_records.items():
        actual_value = parsed_payload.get(field) if isinstance(parsed_payload, dict) else None
        if actual_value is None:
            if expected_value in response_text:
                matched_fields.append(field)
            else:
                mismatched_fields.append(
                    {"field": field, "expected": expected_value, "actual": None}
                )
            continue
        if actual_value == expected_value:
            matched_fields.append(field)
        else:
            mismatched_fields.append(
                {"field": field, "expected": expected_value, "actual": actual_value}
            )

    total_fields = len(expected_records)
    return {
        "response_format": "json" if parsed_payload is not None else "text",
        "matched_fields": len(matched_fields),
        "total_fields": total_fields,
        "score": round(len(matched_fields) / total_fields, 3) if total_fields else None,
        "matched_field_names": matched_fields,
        "mismatched_fields": mismatched_fields,
        "response_excerpt": response_text[:800],
    }


def summarize_warm_runs(phases: list[dict[str, Any]]) -> dict[str, Any]:
    valid_runs = [phase for phase in phases if phase.get("valid_phase")]
    generation_rates = [
        float(phase["tokens_per_second_generation"])
        for phase in valid_runs
        if isinstance(phase.get("tokens_per_second_generation"), (int, float))
    ]
    end_to_end_rates = [
        float(phase["tokens_per_second_end_to_end"])
        for phase in valid_runs
        if isinstance(phase.get("tokens_per_second_end_to_end"), (int, float))
    ]
    ttfts = [
        int(phase["ttft_ms"])
        for phase in valid_runs
        if isinstance(phase.get("ttft_ms"), int)
    ]
    input_tokens = [
        int(phase["input_tokens"])
        for phase in valid_runs
        if isinstance(phase.get("input_tokens"), int)
    ]
    return {
        "run_count": len(phases),
        "valid_run_count": len(valid_runs),
        "invalid_runs": [
            {"phase_name": phase.get("phase_name"), "invalid_reason": phase.get("invalid_reason")}
            for phase in phases
            if not phase.get("valid_phase")
        ],
        "warm_ttft_ms_median": median_or_none([float(value) for value in ttfts]),
        "tokens_per_second_generation_median": median_or_none(generation_rates),
        "tokens_per_second_generation_mean": mean_or_none(generation_rates),
        "tokens_per_second_generation_min": min_or_none(generation_rates),
        "tokens_per_second_generation_max": max_or_none(generation_rates),
        "tokens_per_second_end_to_end_median": median_or_none(end_to_end_rates),
        "tokens_per_second_end_to_end_mean": mean_or_none(end_to_end_rates),
        "input_tokens_median": median_or_none([float(value) for value in input_tokens]),
    }


def estimate_baseline_overhead_tokens(prompt: str, phases: list[dict[str, Any]]) -> int | None:
    estimates: list[int] = []
    prompt_token_estimate = estimate_text_tokens(prompt)
    for phase in phases:
        if not phase.get("valid_phase"):
            continue
        input_tokens = phase.get("input_tokens")
        if isinstance(input_tokens, int) and input_tokens >= prompt_token_estimate:
            estimates.append(input_tokens - prompt_token_estimate)
    if not estimates:
        return None
    return int(round(statistics.median(estimates)))


def build_reported_metrics(
    cold_probe: dict[str, Any],
    warm_aggregate: dict[str, Any],
    near_context_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    metrics = {
        "cold_ttft_ms": cold_probe.get("ttft_ms"),
        "warm_ttft_ms_median": warm_aggregate.get("warm_ttft_ms_median"),
        "raw_generation_tokens_per_second_median": warm_aggregate.get(
            "tokens_per_second_generation_median"
        ),
        "raw_generation_tokens_per_second_mean": warm_aggregate.get(
            "tokens_per_second_generation_mean"
        ),
        "raw_generation_tokens_per_second_min": warm_aggregate.get(
            "tokens_per_second_generation_min"
        ),
        "raw_generation_tokens_per_second_max": warm_aggregate.get(
            "tokens_per_second_generation_max"
        ),
        "end_to_end_tokens_per_second_median": warm_aggregate.get(
            "tokens_per_second_end_to_end_median"
        ),
    }
    if near_context_summary is not None:
        selected = near_context_summary.get("selected_attempt") or {}
        behavior = selected.get("behavior") if isinstance(selected.get("behavior"), dict) else {}
        metrics.update(
            {
                "near_context_input_tokens": selected.get("input_tokens"),
                "near_context_budget_ratio": selected.get("observed_budget_ratio"),
                "near_context_window_ratio": selected.get("observed_window_ratio"),
                "near_context_ttft_ms": selected.get("ttft_ms"),
                "near_context_generation_tokens_per_second": selected.get(
                    "tokens_per_second_generation"
                ),
                "near_context_behavior_score": behavior.get("score"),
                "near_context_matched_fields": behavior.get("matched_fields"),
                "near_context_total_fields": behavior.get("total_fields"),
            }
        )
    return metrics


def run_phase(
    *,
    args: argparse.Namespace,
    command: list[str],
    prompt: str,
    phase_name: str,
    proxy_log: Path,
    events_log_base: Path,
    preserve_original_events_path: bool = False,
) -> dict[str, Any]:
    events_log = derive_phase_events_log_path(events_log_base, phase_name, preserve_original_events_path)
    events_log.parent.mkdir(parents=True, exist_ok=True)
    before_entries = snapshot_entries(proxy_log)
    run_result = execute_codex(command, prompt, events_log)
    phase_entries = wait_for_phase_entries(proxy_log, len(before_entries), args.proxy_log_wait_ms)
    return build_phase_summary(
        phase_name=phase_name,
        prompt=prompt,
        events_log=events_log,
        run_result=run_result,
        phase_entries=phase_entries,
    )


def run_single_phase(
    args: argparse.Namespace,
    prompt: str,
    command: list[str],
    proxy_log: Path,
    events_log: Path,
) -> dict[str, Any]:
    phase = run_phase(
        args=args,
        command=command,
        prompt=prompt,
        phase_name="single_phase",
        proxy_log=proxy_log,
        events_log_base=events_log,
        preserve_original_events_path=True,
    )
    summary: dict[str, Any] = {
        "summary_version": 1,
        "status": phase["status"],
        "valid_benchmark": phase["valid_phase"],
        "invalid_reason": phase["invalid_reason"],
        "profile": args.profile,
        "model": phase.get("model") or args.model,
        "proxy_log_path": str(proxy_log),
        "events_log_path": phase["events_log_path"],
        "request_count": phase["request_count"],
        "thread_id": phase["thread_id"],
        "exit_code": phase["exit_code"],
        "usage": phase["usage"],
        "last_agent_message": phase["last_agent_message"],
        "process_started_at": phase["process_started_at"],
        "process_completed_at": phase["process_completed_at"],
        "process_duration_ms": phase["process_duration_ms"],
        "event_count": phase["event_count"],
        "invalid_event_lines": phase["invalid_event_lines"],
        "legacy_error_count": phase["legacy_error_count"],
        "stderr_excerpt": phase["stderr_excerpt"],
        "request_id": phase["request_id"],
        "ttft_ms": phase["ttft_ms"],
        "generation_duration_ms": phase["generation_duration_ms"],
        "end_to_end_duration_ms": phase["end_to_end_duration_ms"],
        "http_status": phase["http_status"],
        "tokens_per_second_generation": phase["tokens_per_second_generation"],
        "tokens_per_second_end_to_end": phase["tokens_per_second_end_to_end"],
    }
    return summary


def run_near_context(
    *,
    args: argparse.Namespace,
    command: list[str],
    proxy_log: Path,
    events_log_base: Path,
    context_info: dict[str, Any],
    baseline_overhead_tokens: int | None,
) -> dict[str, Any]:
    budget_tokens = context_info.get("context_budget_tokens")
    context_window_tokens = context_info.get("context_window_tokens")
    if not isinstance(budget_tokens, int) or budget_tokens <= 0:
        return {
            "enabled": True,
            "skipped": True,
            "skipped_reason": "missing_context_budget_tokens",
            "context_info": context_info,
            "attempts": [],
            "selected_attempt": None,
        }

    baseline_overhead = baseline_overhead_tokens or 0
    target_input_tokens = int(round(budget_tokens * args.near_context_target_ratio))
    desired_prompt_tokens = max(
        256,
        target_input_tokens - baseline_overhead - args.near_context_output_reserve_tokens,
    )

    attempts: list[dict[str, Any]] = []
    prompt_tokens_estimate = desired_prompt_tokens
    for attempt_number in range(1, args.near_context_max_attempts + 1):
        prompt, prompt_meta = build_near_context_prompt(prompt_tokens_estimate)
        phase = run_phase(
            args=args,
            command=command,
            prompt=prompt,
            phase_name=f"near_context_attempt_{attempt_number}",
            proxy_log=proxy_log,
            events_log_base=events_log_base,
        )
        phase["near_context_prompt"] = prompt_meta
        phase["target_input_tokens"] = target_input_tokens
        phase["target_ratio"] = args.near_context_target_ratio
        phase["context_budget_tokens"] = budget_tokens
        phase["context_window_tokens"] = context_window_tokens
        if isinstance(phase.get("input_tokens"), int):
            phase["observed_budget_ratio"] = round(phase["input_tokens"] / budget_tokens, 3)
            if isinstance(context_window_tokens, int) and context_window_tokens > 0:
                phase["observed_window_ratio"] = round(
                    phase["input_tokens"] / context_window_tokens, 3
                )
            else:
                phase["observed_window_ratio"] = None
        else:
            phase["observed_budget_ratio"] = None
            phase["observed_window_ratio"] = None
        phase["behavior"] = evaluate_near_context_behavior(
            phase.get("last_agent_message"),
            prompt_meta["expected_records"],
        )
        attempts.append(phase)

        observed_ratio = phase.get("observed_budget_ratio")
        if phase.get("valid_phase") and isinstance(observed_ratio, float):
            if abs(observed_ratio - args.near_context_target_ratio) <= args.near_context_tolerance_ratio:
                break
            scale = args.near_context_target_ratio / max(observed_ratio, 0.05)
            scale = min(max(scale, 0.5), 1.8)
            prompt_tokens_estimate = max(256, int(prompt_tokens_estimate * scale))
            continue
        break

    selected_attempt = None
    valid_attempts = [attempt for attempt in attempts if attempt.get("valid_phase")]
    if valid_attempts:
        selected_attempt = min(
            valid_attempts,
            key=lambda attempt: abs(
                float(attempt.get("observed_budget_ratio") or 0.0) - args.near_context_target_ratio
            ),
        )
    elif attempts:
        selected_attempt = attempts[-1]

    return {
        "enabled": True,
        "skipped": False,
        "context_info": context_info,
        "target_ratio": args.near_context_target_ratio,
        "output_reserve_tokens": args.near_context_output_reserve_tokens,
        "attempt_count": len(attempts),
        "attempts": attempts,
        "selected_attempt": selected_attempt,
    }


def run_multi_phase(
    args: argparse.Namespace,
    prompt: str,
    command: list[str],
    proxy_log: Path,
    events_log: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    context_info = resolve_model_context(args, config)
    cold_probe = run_phase(
        args=args,
        command=command,
        prompt=prompt,
        phase_name="cold_probe",
        proxy_log=proxy_log,
        events_log_base=events_log,
    )
    warm_runs = [
        run_phase(
            args=args,
            command=command,
            prompt=prompt,
            phase_name=f"measured_run_{index}",
            proxy_log=proxy_log,
            events_log_base=events_log,
        )
        for index in range(1, args.measured_runs + 1)
    ]
    warm_aggregate = summarize_warm_runs(warm_runs)
    baseline_overhead_tokens = estimate_baseline_overhead_tokens(prompt, [cold_probe] + warm_runs)

    near_context_summary = None
    if args.near_context:
        near_context_summary = run_near_context(
            args=args,
            command=command,
            proxy_log=proxy_log,
            events_log_base=events_log,
            context_info=context_info,
            baseline_overhead_tokens=baseline_overhead_tokens,
        )

    selected_model = None
    for phase in [cold_probe, *warm_runs]:
        if isinstance(phase.get("model"), str):
            selected_model = phase["model"]
            break
    if selected_model is None and near_context_summary is not None:
        selected_attempt = near_context_summary.get("selected_attempt")
        if isinstance(selected_attempt, dict) and isinstance(selected_attempt.get("model"), str):
            selected_model = selected_attempt["model"]
    if selected_model is None:
        selected_model = context_info.get("effective_model") or args.model

    phase_exit_codes = [int(cold_probe.get("exit_code") or 0)] + [
        int(phase.get("exit_code") or 0) for phase in warm_runs
    ]
    if near_context_summary is not None:
        phase_exit_codes.extend(
            int(attempt.get("exit_code") or 0)
            for attempt in near_context_summary.get("attempts") or []
            if isinstance(attempt, dict)
        )
    workflow_exit_code = next((code for code in phase_exit_codes if code != 0), 0)
    workflow_valid = bool(cold_probe.get("valid_phase")) and warm_aggregate.get("valid_run_count", 0) > 0
    if args.near_context:
        selected_attempt = near_context_summary.get("selected_attempt") if near_context_summary else None
        workflow_valid = workflow_valid and bool(
            selected_attempt and selected_attempt.get("valid_phase")
        )

    summary: dict[str, Any] = {
        "summary_version": 2,
        "workflow_mode": "multi_phase",
        "status": "completed" if workflow_exit_code == 0 else "failed",
        "workflow_valid": workflow_valid,
        "valid_benchmark": workflow_valid,
        "workflow_exit_code": workflow_exit_code,
        "profile": args.profile,
        "model": selected_model,
        "proxy_log_path": str(proxy_log),
        "context_info": context_info,
        "cold_probe": cold_probe,
        "warm_runs": warm_runs,
        "warm_aggregate": warm_aggregate,
        "baseline_overhead_tokens_estimate": baseline_overhead_tokens,
        "near_context": near_context_summary,
    }
    summary["reported_metrics"] = build_reported_metrics(
        cold_probe,
        warm_aggregate,
        near_context_summary,
    )
    return summary


def main() -> int:
    args = parse_args()
    prompt = load_prompt(args)
    events_log = Path(args.events_log).expanduser().resolve()
    summary_out = Path(args.summary_out).expanduser().resolve()
    proxy_log = Path(args.proxy_log).expanduser().resolve()
    events_log.parent.mkdir(parents=True, exist_ok=True)
    summary_out.parent.mkdir(parents=True, exist_ok=True)

    config_path = Path(args.codex_config).expanduser().resolve()
    config = load_codex_config(config_path)
    command = build_command(args)

    if args.workflow_mode == "multi-phase":
        summary = run_multi_phase(args, prompt, command, proxy_log, events_log, config)
        exit_code = int(summary.get("workflow_exit_code") or 0)
    else:
        summary = run_single_phase(args, prompt, command, proxy_log, events_log)
        exit_code = 0 if summary["status"] == "completed" else int(summary["exit_code"])

    summary_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
