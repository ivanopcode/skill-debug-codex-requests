#!/usr/bin/env python3
"""Inspect JSON logs produced by codex_proxy.py."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect a Codex proxy log")
    parser.add_argument("log_file", help="Path to a JSON log emitted by codex_proxy.py")
    parser.add_argument(
        "--repair",
        action="store_true",
        help="Append a closing bracket in memory if the log was not finalized",
    )
    parser.add_argument(
        "--details",
        action="store_true",
        help="Always print per-request details",
    )
    parser.add_argument(
        "--show-context",
        action="store_true",
        help="Print sanitized instructions and input when present in the log",
    )
    parser.add_argument(
        "--show-system-prompt",
        action="store_true",
        help="Print only the sanitized environment/system prompt when present in the log",
    )
    parser.add_argument(
        "--run-summary",
        type=str,
        default=None,
        help="Optional benchmark summary JSON to print alongside the proxy log",
    )
    return parser.parse_args()


def load_entries(path: Path, repair: bool) -> list[dict[str, Any]]:
    raw = path.read_text(encoding="utf-8")
    if not raw.strip():
        raise ValueError(f"log file is empty: {path}")

    cleaned = raw.rstrip()
    if not cleaned.endswith("]"):
        if not repair:
            raise ValueError(
                "log is not finalized; stop the proxy first or rerun with --repair"
            )
        cleaned = f"{cleaned}\n]\n"

    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON log: {exc}") from exc

    if not isinstance(payload, list):
        raise ValueError("proxy log must contain a JSON array")

    entries: list[dict[str, Any]] = []
    for item in payload:
        if isinstance(item, dict):
            entries.append(item)

    return entries


def load_run_summary(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid run summary JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("run summary must be a JSON object")
    return payload


def format_counter(counter: Counter[str]) -> str:
    if not counter:
        return "-"
    return ", ".join(f"{key}:{counter[key]}" for key in sorted(counter))


def format_reasoning(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def request_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if "total_bytes" in entry]


def error_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if "error" in entry and "total_bytes" not in entry]


def request_error_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [entry for entry in entries if "total_bytes" in entry and "error" in entry]


def request_signature(entry: dict[str, Any]) -> tuple[Any, ...]:
    tool_types = tuple(sorted((entry.get("tool_types") or {}).items()))
    tool_names = tuple(sorted(set(entry.get("tool_names") or [])))
    reasoning = json.dumps(entry.get("reasoning"), ensure_ascii=False, sort_keys=True)
    return (
        entry.get("path"),
        entry.get("model"),
        entry.get("stream"),
        reasoning,
        entry.get("total_bytes"),
        entry.get("instructions_chars"),
        entry.get("tools_chars"),
        entry.get("input_chars"),
        entry.get("num_tools"),
        entry.get("num_input_items"),
        tool_types,
        tool_names,
    )


def signature_summary(entry: dict[str, Any]) -> str:
    return (
        f"path={entry.get('path', '?')} model={entry.get('model', '?')} "
        f"stream={entry.get('stream', '?')} bytes={entry.get('total_bytes', '?')} "
        f"tools={entry.get('num_tools', '?')} input_items={entry.get('num_input_items', '?')}"
    )


def format_json_block(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def format_metric(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def print_phase_summary_line(label: str, phase: dict[str, Any], indent: str = "  ") -> None:
    print(
        f"{indent}{label}: status={phase.get('status', '-')} "
        f"valid_phase={phase.get('valid_phase', False)} "
        f"input_tokens={phase.get('input_tokens', '-')} "
        f"output_tokens={phase.get('output_tokens', '-')} "
        f"ttft_ms={phase.get('ttft_ms', '-')} "
        "tokens_per_second_generation="
        f"{format_metric(phase.get('tokens_per_second_generation'))} "
        f"invalid_reason={phase.get('invalid_reason') or '-'}"
    )


def print_multi_phase_summary(summary: dict[str, Any], detailed: bool) -> None:
    context_info = summary.get("context_info") or {}
    warm_aggregate = summary.get("warm_aggregate") or {}
    reported_metrics = summary.get("reported_metrics") or {}
    cold_probe = summary.get("cold_probe") or {}
    near_context = summary.get("near_context")

    print("Workflow summary:")
    print(f"  status={summary.get('status', '-')}")
    print(f"  workflow_mode={summary.get('workflow_mode', '-')}")
    print(f"  workflow_valid={summary.get('workflow_valid', False)}")
    print(f"  workflow_exit_code={summary.get('workflow_exit_code', '-')}")
    print(f"  profile={summary.get('profile') or '-'}")
    print(f"  model={summary.get('model') or '-'}")
    print(f"  proxy_log_path={summary.get('proxy_log_path', '-')}")
    print(
        f"  context_budget_tokens={context_info.get('context_budget_tokens', '-')}"
    )
    print(
        f"  context_budget_source={context_info.get('context_budget_source') or '-'}"
    )
    print(
        f"  context_window_tokens={context_info.get('context_window_tokens', '-')}"
    )
    print(
        "  model_auto_compact_token_limit="
        f"{context_info.get('model_auto_compact_token_limit', '-')}"
    )
    print(
        f"  cold_ttft_ms={format_metric(reported_metrics.get('cold_ttft_ms'))}"
    )
    print(
        f"  warm_run_count={warm_aggregate.get('run_count', '-')}"
    )
    print(
        f"  valid_warm_runs={warm_aggregate.get('valid_run_count', '-')}"
    )
    print(
        "  warm_ttft_ms_median="
        f"{format_metric(reported_metrics.get('warm_ttft_ms_median'))}"
    )
    print(
        "  raw_generation_tokens_per_second_median="
        f"{format_metric(reported_metrics.get('raw_generation_tokens_per_second_median'))}"
    )
    print(
        "  raw_generation_tokens_per_second_mean="
        f"{format_metric(reported_metrics.get('raw_generation_tokens_per_second_mean'))}"
    )
    print(
        "  raw_generation_tokens_per_second_min="
        f"{format_metric(reported_metrics.get('raw_generation_tokens_per_second_min'))}"
    )
    print(
        "  raw_generation_tokens_per_second_max="
        f"{format_metric(reported_metrics.get('raw_generation_tokens_per_second_max'))}"
    )
    print(
        "  end_to_end_tokens_per_second_median="
        f"{format_metric(reported_metrics.get('end_to_end_tokens_per_second_median'))}"
    )

    if near_context is None:
        print("  near_context=disabled")
    elif near_context.get("skipped"):
        print("  near_context=skipped")
        print(f"  near_context_skipped_reason={near_context.get('skipped_reason') or '-'}")
    else:
        selected = near_context.get("selected_attempt") or {}
        behavior = selected.get("behavior") if isinstance(selected.get("behavior"), dict) else {}
        print("  near_context=enabled")
        print(f"  near_context_attempt_count={near_context.get('attempt_count', '-')}")
        print(
            f"  near_context_target_ratio={format_metric(near_context.get('target_ratio'))}"
        )
        print(
            "  near_context_input_tokens="
            f"{format_metric(reported_metrics.get('near_context_input_tokens'))}"
        )
        print(
            "  near_context_budget_ratio="
            f"{format_metric(reported_metrics.get('near_context_budget_ratio'))}"
        )
        print(
            "  near_context_window_ratio="
            f"{format_metric(reported_metrics.get('near_context_window_ratio'))}"
        )
        print(
            "  near_context_ttft_ms="
            f"{format_metric(reported_metrics.get('near_context_ttft_ms'))}"
        )
        print(
            "  near_context_generation_tokens_per_second="
            f"{format_metric(reported_metrics.get('near_context_generation_tokens_per_second'))}"
        )
        print(
            "  near_context_behavior_score="
            f"{format_metric(reported_metrics.get('near_context_behavior_score'))}"
        )
        print(
            "  near_context_behavior_fields="
            f"{behavior.get('matched_fields', '-')}/{behavior.get('total_fields', '-')}"
        )
        mismatches = behavior.get("mismatched_fields") or []
        if mismatches:
            mismatch_names = ", ".join(
                mismatch.get("field", "?")
                for mismatch in mismatches[:5]
                if isinstance(mismatch, dict)
            )
            print(f"  near_context_mismatched_fields={mismatch_names}")

    if detailed:
        print("  phases:")
        print_phase_summary_line("cold_probe", cold_probe, indent="    ")
        for phase in summary.get("warm_runs") or []:
            if isinstance(phase, dict):
                print_phase_summary_line(phase.get("phase_name", "warm_run"), phase, indent="    ")
        if isinstance(near_context, dict):
            for attempt in near_context.get("attempts") or []:
                if isinstance(attempt, dict):
                    print_phase_summary_line(
                        attempt.get("phase_name", "near_context_attempt"),
                        attempt,
                        indent="    ",
                    )


def print_run_summary(summary: dict[str, Any], detailed: bool = False) -> None:
    if summary.get("workflow_mode") == "multi_phase" or summary.get("summary_version") == 2:
        print_multi_phase_summary(summary, detailed)
        return
    usage = summary.get("usage") or {}
    print("Benchmark summary:")
    print(f"  status={summary.get('status', '-')}")
    print(f"  valid_benchmark={summary.get('valid_benchmark', False)}")
    print(f"  invalid_reason={summary.get('invalid_reason') or '-'}")
    print(f"  profile={summary.get('profile') or '-'}")
    print(f"  model={summary.get('model') or '-'}")
    print(f"  request_count={summary.get('request_count', '-')}")
    print(f"  output_tokens={usage.get('output_tokens', '-')}")
    print(f"  input_tokens={usage.get('input_tokens', '-')}")
    print(f"  cached_input_tokens={usage.get('cached_input_tokens', '-')}")
    print(f"  ttft_ms={summary.get('ttft_ms', '-')}")
    print(f"  generation_duration_ms={summary.get('generation_duration_ms', '-')}")
    print(f"  end_to_end_duration_ms={summary.get('end_to_end_duration_ms', '-')}")
    print(
        "  tokens_per_second_generation="
        f"{format_metric(summary.get('tokens_per_second_generation'))}"
    )
    print(
        "  tokens_per_second_end_to_end="
        f"{format_metric(summary.get('tokens_per_second_end_to_end'))}"
    )
    print(f"  proxy_log_path={summary.get('proxy_log_path', '-')}")
    print(f"  events_log_path={summary.get('events_log_path', '-')}")


def system_prompt_text(entry: dict[str, Any]) -> str | None:
    if "system_prompt" in entry:
        return str(entry.get("system_prompt", ""))
    if "instructions" in entry:
        return str(entry.get("instructions", ""))
    return None


def print_system_prompt(entry: dict[str, Any]) -> None:
    prompt = system_prompt_text(entry)
    if prompt is None:
        return
    print("  system_prompt:")
    for line in prompt.splitlines() or [""]:
        print(f"    {line}")


def print_request(
    index: int,
    entry: dict[str, Any],
    show_context: bool,
    show_system_prompt: bool,
) -> None:
    tool_types = Counter(entry.get("tool_types") or {})
    tool_names = sorted(set(entry.get("tool_names") or []))

    print(
        f"#{index} {entry.get('ts', '?')} path={entry.get('path', '?')} "
        f"model={entry.get('model', '?')} stream={entry.get('stream', '?')}"
    )
    print(
        "  "
        f"bytes={entry.get('total_bytes', '?')} "
        f"instructions_chars={entry.get('instructions_chars', '?')} "
        f"tools_chars={entry.get('tools_chars', '?')} "
        f"input_chars={entry.get('input_chars', '?')}"
    )
    print(
        "  "
        f"num_tools={entry.get('num_tools', '?')} "
        f"num_input_items={entry.get('num_input_items', '?')} "
        f"reasoning={format_reasoning(entry.get('reasoning'))}"
    )
    timing_bits = [
        f"http_status={entry.get('http_status', '-')}",
        f"response_bytes={entry.get('response_bytes', '-')}",
        f"ttft_ms={entry.get('ttft_ms', '-')}",
        f"generation_duration_ms={entry.get('generation_duration_ms', '-')}",
        f"end_to_end_duration_ms={entry.get('end_to_end_duration_ms', '-')}",
    ]
    if any(entry.get(key) is not None for key in ("http_status", "ttft_ms", "end_to_end_duration_ms")):
        print(f"  {' '.join(timing_bits)}")
    print(f"  tool_types={format_counter(tool_types)}")
    print(f"  tool_names={', '.join(tool_names) if tool_names else '-'}")
    if "error" in entry:
        print(f"  error={entry.get('error')}")
    if show_context or show_system_prompt:
        if "context_redacted" in entry:
            print(f"  context_redacted={entry.get('context_redacted')}")
        if "redaction_summary" in entry:
            print(f"  redaction_summary={format_json_block(entry.get('redaction_summary'))}")
        print_system_prompt(entry)
    if show_context:
        if "input" in entry:
            print("  input:")
            for line in format_json_block(entry.get("input")).splitlines():
                print(f"    {line}")


def main() -> int:
    args = parse_args()
    log_path = Path(args.log_file).expanduser().resolve()
    if not log_path.exists():
        print(f"log file not found: {log_path}", file=sys.stderr)
        return 1

    run_summary: dict[str, Any] | None = None
    if args.run_summary is not None:
        summary_path = Path(args.run_summary).expanduser().resolve()
        if not summary_path.exists():
            print(f"run summary not found: {summary_path}", file=sys.stderr)
            return 1
        try:
            run_summary = load_run_summary(summary_path)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 1

    try:
        entries = load_entries(log_path, args.repair)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    requests = request_entries(entries)
    errors = error_entries(entries)
    request_errors = request_error_entries(entries)

    total_bytes = sum(int(entry.get("total_bytes", 0) or 0) for entry in requests)
    overall_tool_types: Counter[str] = Counter()
    overall_tool_names: Counter[str] = Counter()
    signature_counts: Counter[tuple[Any, ...]] = Counter()
    signature_examples: dict[tuple[Any, ...], dict[str, Any]] = {}
    for entry in requests:
        overall_tool_types.update(entry.get("tool_types") or {})
        overall_tool_names.update(entry.get("tool_names") or [])
        signature = request_signature(entry)
        signature_counts[signature] += 1
        signature_examples.setdefault(signature, entry)

    print(f"File: {log_path}")
    print(f"Requests: {len(requests)}")
    print(f"Errors: {len(errors) + len(request_errors)}")
    print(f"Total bytes: {total_bytes}")
    print(f"Unique request shapes: {len(signature_counts)}")
    print(f"Overall tool types: {format_counter(overall_tool_types)}")
    print(
        "Overall function tools: "
        f"{', '.join(sorted(overall_tool_names)) if overall_tool_names else '-'}"
    )

    if signature_counts:
        print("Top request shapes:")
        for signature, count in signature_counts.most_common(3):
            print(f"- {count}x {signature_summary(signature_examples[signature])}")

    if run_summary is not None:
        print()
        print_run_summary(run_summary, detailed=args.details)

    if requests and (args.details or len(requests) <= 3):
        for index, entry in enumerate(requests, start=1):
            print()
            print_request(index, entry, args.show_context, args.show_system_prompt)

    combined_errors = list(request_errors) + list(errors)
    if combined_errors:
        print()
        print("Errors:")
        for index, entry in enumerate(combined_errors, start=1):
            print(
                f"{index}. ts={entry.get('ts', '?')} path={entry.get('path', '?')} "
                f"status={entry.get('http_status', entry.get('status', '-'))} "
                f"error={entry.get('error', '?')}"
            )

    return 0


if __name__ == "__main__":
    sys.exit(main())
