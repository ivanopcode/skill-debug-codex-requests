# Throughput Benchmark

Use this reference when the user wants TTFT or tokens-per-second rather than a general diagnostic capture.

## Goal

Measure model latency and generation speed cleanly enough that the result reflects provider behavior, not agent orchestration noise.

## Benchmark Rules

- The default skill path is multi-phase benchmarking on the real context budget resolved from the selected profile in `~/.codex/config.toml`.
- Resolve the practical budget from `model_auto_compact_token_limit` first and fall back to `model_context_window` only when needed.
- Keep the original one-shot mode only for quick single-request captures or when the user explicitly asks for a lighter run.
- Prefer multi-phase mode when the user wants:
  - cold `TTFT`
  - warmer raw generation speed
  - a near-context behavior probe
- Avoid tool calls, browsing, retries, or follow-up turns inside the measured phases.
- Prefer prompts that force the model to think and then emit a long natural-language answer.
- Treat any phase with more than one provider request as invalid for throughput reporting.

## Prompt Guidance

Good benchmark prompts:

- ask for a long structured explanation
- ask for a careful comparison across several items
- ask for a large but deterministic output such as a long outline or detailed walkthrough
- keep the measured prompt stable across the cold probe and warm repeated runs

Avoid prompts that:

- invite tool usage
- require browsing
- ask for extremely short answers
- depend on user-specific secrets or local files unless that is the benchmark target
- vary wildly between runs if the goal is to compare warm throughput

## Suggested Flow

1. Write the measured benchmark prompt into a temp file.
2. Start the proxy normally. Add `--dump-context` only if the user wants the full prompt captured too.
   If the real question is "what system prompt did the agent environment inject?", use `--dump-context` and later inspect `system_prompt` with `--show-system-prompt`.
3. Run `run_codex_benchmark.py` with the selected profile and let it resolve the real budget from config.
4. Use `single-phase` only if the user explicitly wants a quick one-shot check.
5. Inspect the proxy log together with the generated summary JSON.
6. Report:
   - cold `TTFT`
   - warm repeated-run `tokens_per_second_generation`
   - warm repeated-run `tokens_per_second_end_to_end`
   - near-context behavior score and observed context occupancy when enabled

The runner passes prompts over `stdin`, which matters for near-context probes because the generated prompt can exceed normal shell argument limits.

## Validity Checks

The one-shot benchmark is valid only when all of these are true:

- `codex exec` exited successfully
- the summary contains `usage.output_tokens`
- the proxy log contains exactly one request entry
- that request has a successful HTTP status
- `ttft_ms`, `generation_duration_ms`, and `end_to_end_duration_ms` are all present

The multi-phase workflow is valid only when all of these are true:

- the cold probe is valid
- at least one warm measured run is valid
- if near-context is enabled, the selected near-context attempt is valid

If any condition fails, report the affected run or phase as invalid and explain why instead of inventing a speed number.

## Near-Context Probe

The near-context phase is not just another throughput sample. It is a behavior probe.

- Resolve the practical token budget from `~/.codex/config.toml`, preferring `model_auto_compact_token_limit` and falling back to `model_context_window`.
- In the main skill path, do not override that budget manually unless you are doing a smoke test or a deliberate comparison run.
- Target a ratio such as `0.90` of that budget, while reserving some output tokens.
- Use synthetic long-context data with exact sentinel values so correctness can be scored automatically.
- Report both latency and correctness:
  - observed input-token ratio
  - `ttft_ms`
  - `tokens_per_second_generation`
  - matched sentinel fields / total sentinel fields
  - behavior score
