# Log Fields

Use this reference when the proxy log exists but the user needs help interpreting what the numbers mean.

## Core Fields

- `path`: The provider endpoint that Codex called. This is usually something like `/v1/responses`.
- `model`: The model name sent in the request body.
- `reasoning`: Provider-specific reasoning configuration from the outgoing request, if present.
- `stream`: Whether Codex asked for a streaming response.
- `total_bytes`: Raw request size in bytes before forwarding.
- `instructions_chars`: Character count of the top-level `instructions` string. Large values usually mean heavy system or skill context.
- `tools_chars`: Character count of the serialized `tools` array. This reflects exposed tool schema, not tool usage.
- `input_chars`: Character count of the serialized `input` array. This mainly reflects the user prompt and attached items.
- `num_tools`: Number of tool definitions exposed to the model.
- `num_input_items`: Number of entries in the `input` array.
- `tool_types`: Count by tool type.
- `tool_names`: Function tool names exposed in the request.
- `request_id`: Sequential id assigned by the local proxy.
- `request_started_at`: Local timestamp when the proxy finished reading the request body.
- `first_response_byte_at`: Local timestamp when the first upstream response chunk was read.
- `response_completed_at`: Local timestamp when the upstream response finished streaming.
- `ttft_ms`: Time to first token/byte measured by the proxy.
- `generation_duration_ms`: Time from first response byte until the response finished streaming.
- `end_to_end_duration_ms`: Time from completed request read until completed response stream.
- `response_bytes`: Total response bytes forwarded by the proxy.
- `http_status`: Final upstream HTTP status observed by the proxy.
- `instructions`: Sanitized top-level `instructions` string. Present only when the proxy ran with `--dump-context`.
- `system_prompt`: Explicit alias for the sanitized top-level prompt that Codex sent in `instructions`. In practice this is the clearest field for “the system prompt from the agent environment”.
- `input`: Sanitized `input` payload. Present only when the proxy ran with `--dump-context`.
- `context_redacted`: `true` when the proxy ran the captured context through the built-in masking pass.
- `redaction_summary`: Counts of replacements performed by the masking pass.

## Benchmark Summary Fields

- `summary_version`: Summary schema version.
- `status`: Runner status, usually `completed` or `failed`.
- `valid_benchmark`: Whether the run satisfied the benchmark rules. In multi-phase mode this mirrors `workflow_valid`.
- `invalid_reason`: Why the one-shot benchmark was rejected when `valid_benchmark` is `false`.
- `usage`: `codex exec --json` token usage block for the one-shot run or for each phase.
- `request_count`: Number of provider requests captured in the proxy log slice for the one-shot run or phase.
- `tokens_per_second_generation`: `output_tokens / generation_duration_seconds`.
- `tokens_per_second_end_to_end`: `output_tokens / end_to_end_duration_seconds`.
- `events_log_path`: Raw JSONL event stream path for a one-shot run or an individual phase.
- `proxy_log_path`: Proxy JSON log path used to compute the summary.

### Multi-Phase Summary Fields

- The primary skill benchmark path now produces a multi-phase summary by default.
- `workflow_mode`: `multi_phase` when the runner executed the expanded workflow.
- `workflow_valid`: `true` when the cold probe, warm runs, and optional near-context phase all satisfied their validity rules.
- `workflow_exit_code`: First non-zero `codex exec` exit code across the phases, else `0`.
- `context_info`: Resolved model-budget metadata from `~/.codex/config.toml`, including:
  - `effective_model`
  - `context_window_tokens`
  - `model_auto_compact_token_limit`
  - `context_budget_tokens`
  - `context_budget_source`
- `cold_probe`: Summary of the first measured run. Use it primarily for cold `ttft_ms`.
- `warm_runs`: List of repeated measured-run summaries after the cold probe.
- `warm_aggregate`: Aggregate stats across valid warm runs, including median and mean generation speed.
- `baseline_overhead_tokens_estimate`: Rough estimate of Codex/system overhead tokens before the user prompt.
- `reported_metrics`: High-signal values already prepared for user-facing reporting:
  - `cold_ttft_ms`
  - `warm_ttft_ms_median`
  - `raw_generation_tokens_per_second_median`
  - `raw_generation_tokens_per_second_mean`
  - `raw_generation_tokens_per_second_min`
  - `raw_generation_tokens_per_second_max`
  - `end_to_end_tokens_per_second_median`
- `near_context`: Optional near-context behavior probe summary.

### Near-Context Fields

- `near_context.target_ratio`: Requested occupancy ratio against the resolved context budget.
- `near_context.selected_attempt`: The valid attempt closest to the target occupancy ratio.
- `observed_budget_ratio`: `input_tokens / context_budget_tokens`.
- `observed_window_ratio`: `input_tokens / context_window_tokens` when the full window is known.
- `behavior.score`: Fraction of sentinel fields recovered correctly from the long synthetic archive.
- `behavior.matched_fields` and `behavior.total_fields`: Raw numerator and denominator behind the score.
- `behavior.mismatched_fields`: Which sentinel values were missing or wrong.

## Interpretation Hints

- Large `instructions_chars` with small `input_chars` usually means the request bulk came from system prompts, skills, or accumulated conversation context.
- Large `tools_chars` or `num_tools` usually means the diagnostic question is really about tool injection, not the user prompt itself.
- `tool_names` shows the available function tools, not the tools the model actually called.
- `instructions` and `input` are omitted unless the proxy was started with `--dump-context`.
- `system_prompt` and `instructions` carry the same sanitized text in new logs. `system_prompt` exists to make agent-environment prompt inspection explicit.
- `context_redacted` means masking was attempted, not that the log is guaranteed to be safe to share broadly.
- `ttft_ms` and throughput metrics are local measurements from the proxy and benchmark runner, not values reported by the upstream model provider.
- In multi-phase mode, use `cold_probe.ttft_ms` for cold latency and `warm_aggregate.tokens_per_second_generation_median` for the cleanest raw-speed number.
- `baseline_overhead_tokens_estimate` is heuristic. It is useful for shaping near-context probes, not for strict accounting.
- `observed_budget_ratio` near `1.0` means the probe nearly filled the practical Codex budget, not necessarily the provider's absolute hard limit.
- If two runs differ mostly in `input_chars`, the user prompt or attached artifacts changed.
- If two runs differ mostly in `instructions_chars`, a profile, skill, or earlier context changed.
- If the proxy recorded a request and then an error entry, the capture still succeeded. The upstream failure only affects completion, not request visibility.
- Repeated near-identical request entries usually mean Codex retried after a transport or upstream failure.
- If no request entry exists at all, the failure happened before Codex reached the configured provider.

## Comparison Strategy

When comparing two runs, look at deltas in this order:

1. `path`, `model`, `reasoning`, `stream`
2. `total_bytes`
3. `instructions_chars`, `tools_chars`, `input_chars`
4. `num_tools`, `tool_types`, `tool_names`
5. error entries
