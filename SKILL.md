---
name: debug-codex-requests
description: Execute a fresh proxied Codex run, inspect the captured log, and summarize what Codex actually sent. Default to execute plus summarize, not tutorial. Use when debugging request payloads, injected instructions or system prompt, tool exposure, model or provider overrides, profile differences, context leakage, or throughput such as TTFT and tokens-per-second.
triggers:
  - debug-codex-requests
  - "debug codex requests"
  - "inspect codex request"
  - "capture codex api request"
  - "codex proxy log"
  - "codex request payload"
  - "codex system prompt"
  - "codex tool schema"
  - "debug codex outbound requests"
  - "отладка codex запросов"
  - "проверить запросы codex"
  - "поймать запросы codex"
  - "лог запроса codex"
  - "payload codex"
  - "system_prompt codex"
  - "схема инструментов codex"
  - "диагностика codex прокси"
---

# Debug Codex Requests

Use this skill when you need evidence from the real provider request, not a guessed explanation.

## Primary Objective

Default mode is `execute + summarize`.

Unless the user explicitly asked for instructions only:

1. Run the diagnostic workflow locally.
2. Finalize and inspect the proxy log or benchmark summary.
3. Return findings to the user.

A task is not done when you have only:

- started the proxy
- run `codex exec`
- printed commands for the user to run manually

A task is done only when all of these are true:

- a proxied Codex run or benchmark was attempted
- the resulting log or run summary was inspected with the bundled scripts
- the user received a concise answer with findings, actual model used, fallbacks, and remaining blockers

Never end in tutorial mode when local execution was possible.

## Standing Rules

- Treat the proxy log as the source of truth.
- Prefer `codex exec` over interactive `codex`.
- Prefer a fresh temp directory such as `/tmp/debug-codex-requests-<timestamp>`.
- Use bundled scripts and return findings. Do not stop at shell examples.
- Keep `--dump-context` opt-in. Use it only when the user explicitly wants prompt/context payload.
- If the user wants the system prompt imposed by the agent environment, use `--dump-context` and inspect `system_prompt`.
- Throughput is a separate benchmark workflow. Do not infer tokens-per-second from a normal run.
- The main throughput path is the multi-phase benchmark on the real budget resolved from `~/.codex/config.toml`.
- Use subagents only when the user explicitly asked for subagents or isolated worker execution. Otherwise run the same workflow locally.
- Even with redaction enabled, assume dumped context may still contain sensitive user content.

## Resolve Paths

Resolve bundled resources relative to this file and use absolute paths:

- `<proxy-script>`: `scripts/codex_proxy.py`
- `<inspect-script>`: `scripts/inspect_proxy_log.py`
- `<benchmark-script>`: `scripts/run_codex_benchmark.py`
- `<log-fields-ref>`: `references/log-fields.md`
- `<benchmark-ref>`: `references/throughput-benchmark.md`
- `<codex-config>`: `~/.codex/config.toml`

## Resolve The Flow

Choose exactly one flow:

- **Capture only**: default when the user asks to debug requests, payload shape, exposed tools, provider overrides, or model selection.
- **System prompt / context**: only when the user asks for `system_prompt`, `instructions`, or sanitized `input`.
- **Throughput**: only when the user explicitly asks for TTFT, speed, tokens-per-second, or near-context behavior.

If the request is underspecified, default to `capture only` and return a result.

## Resolve The Model Before Building The Command

Always inspect `<codex-config>` before building the run.

Model selection precedence:

1. A model name the user wrote in the prompt, exact or approximate.
2. A profile name the user wrote in the prompt, if no separate model name was given.
3. The default Codex behavior from `<codex-config>` when the user specified neither.

Approximate matching:

- lowercase the user text
- remove spaces, underscores, hyphens, and dots
- try exact configured model names
- try exact profile names
- try normalized exact matches
- try a strong alias or substring match such as `spark` -> `gpt-5.3-codex-spark`

Command construction rules:

- Use `-p <profile>` when the chosen model already matches that profile's configured model and you want that profile's surrounding settings.
- Use `-m <model>` when the user explicitly named a different model or when no profile cleanly represents the chosen model.
- If you fall back to `gpt-5.4`, set `model_reasoning_effort=medium` for that diagnostic run.

Fallback policy:

1. Start with the user-requested model if one was provided.
2. If that model is unavailable, unloaded, or the provider returns `model not found`, retry with `gpt-5.3-codex-spark`.
3. If Spark is unavailable, retry with `gpt-5.4` and `medium` reasoning.
4. Report `requested_model`, `resolved_model`, `actual_model`, and `fallback_reason` in the final summary.

Rules:

- `model not found` is not a blocker if a fallback succeeds.
- Do not stop to ask the user which fallback to use.
- If the current proxied provider path cannot serve a fallback model, switch to another already-available local OpenAI-compatible provider path if your environment exposes one.
- Ask about model choice only if every fallback path failed.

## Proxy Command

Normal capture:

```bash
python3 <proxy-script> -p <proxy-port> -t <target-port> -o <log-file>
```

Context capture:

```bash
python3 <proxy-script> -p <proxy-port> -t <target-port> -o <log-file> --dump-context
```

`--dump-context` stores:

- `instructions`
- `system_prompt`
- `input`
- `context_redacted`
- `redaction_summary`

## Canonical Execution Order

1. Inspect `<codex-config>`.
2. Resolve `flow`, `requested_model`, `resolved_model`, and whether `--dump-context` is needed.
3. Start the proxy.
4. Run a fresh `codex exec` through the proxy.
5. Confirm the proxy log contains the request attempt.
6. Stop the proxy cleanly so the JSON array is finalized.
7. Inspect the log or benchmark summary with the bundled scripts.
8. Return a result summary to the user.

Do not skip steps 6 or 7.

## Capture Only

Default prompt:

```text
Reply with exactly PONG and do not call any tools.
```

Without a profile:

```bash
codex exec --skip-git-repo-check --ephemeral --json \
  -C <workdir> \
  -m <model> \
  -c model_provider=ollama-proxy \
  -c 'model_providers.ollama-proxy.name="Proxy"' \
  -c 'model_providers.ollama-proxy.base_url="http://127.0.0.1:<proxy-port>/v1"' \
  '<prompt>'
```

With a profile:

```bash
codex exec --skip-git-repo-check --ephemeral --json \
  -C <workdir> \
  -p <profile> \
  -c 'profiles.<profile>.model_provider="ollama-proxy"' \
  -c 'model_providers.ollama-proxy.name="Proxy"' \
  -c 'model_providers.ollama-proxy.base_url="http://127.0.0.1:<proxy-port>/v1"' \
  '<prompt>'
```

Defaults:

- `<workdir>`: the user's requested repo, else the current working directory
- `<profile>`: the named profile from `<codex-config>` when the user provided one and it matches the chosen model
- `<model>`: the chosen model after applying user intent and fallback rules
- `<prompt>`: a short one-shot prompt unless the user is testing a specific request shape

If the selected path points at Ollama and successful completion matters, verify the model exists in `ollama list` before the first run. If it does not, continue with the fallback chain instead of asking the user.

## System Prompt And Context

Use this path when the user wants the environment-imposed prompt or the actual sanitized payload.

Workflow:

1. Start the proxy with `--dump-context`.
2. Run a fresh `codex exec` through the proxy.
3. Stop the proxy cleanly.
4. Inspect the log with one of these commands:

Show only the environment/system prompt:

```bash
python3 <inspect-script> --show-system-prompt <log-file>
```

Show the full sanitized capture:

```bash
python3 <inspect-script> --show-context <log-file>
```

Interpretation:

- `system_prompt` is the sanitized top-level prompt that Codex actually sent in `instructions`.
- Use `--show-system-prompt` when the user only cares about agent-environment instructions.
- Use `--show-context` when the user also wants the sanitized `input`.
- Report whether `--dump-context` was used, whether `system_prompt` was captured, and whether redaction changed anything.
- Do not claim `system_prompt` or sanitized `input` were captured unless this run used `--dump-context`.

## Throughput Benchmark

Use this path only when the user explicitly wants speed, TTFT, or tokens-per-second.

Default benchmark path:

```bash
python3 <benchmark-script> \
  --workdir <workdir> \
  --profile <profile> \
  --proxy-base-url "http://127.0.0.1:<proxy-port>/v1" \
  --proxy-log <log-file> \
  --events-log <events-log-file> \
  --summary-out <summary-file> \
  --prompt-file <prompt-file>
```

What this means:

- Multi-phase mode is the default.
- The near-context probe is enabled by default in multi-phase mode.
- The runner resolves the real budget from `<codex-config>`.
- Prefer the selected profile budget, such as `profiles.oss` resolving to `model_auto_compact_token_limit`.
- Use manual `--context-budget-tokens` or `--context-window-tokens` only for smoke tests or deliberate experiments.

Fallback modes:

Quick one-shot benchmark:

```bash
python3 <benchmark-script> \
  --workflow-mode single-phase \
  --workdir <workdir> \
  --profile <profile> \
  --proxy-base-url "http://127.0.0.1:<proxy-port>/v1" \
  --proxy-log <log-file> \
  --events-log <events-log-file> \
  --summary-out <summary-file> \
  --prompt-file <prompt-file>
```

Disable near-context but keep multi-phase:

```bash
python3 <benchmark-script> \
  --workdir <workdir> \
  --profile <profile> \
  --proxy-base-url "http://127.0.0.1:<proxy-port>/v1" \
  --proxy-log <log-file> \
  --events-log <events-log-file> \
  --summary-out <summary-file> \
  --prompt-file <prompt-file> \
  --no-near-context
```

Rules:

- Use the same measured prompt for the cold probe and warm runs.
- Report cold TTFT separately from warm generation speed.
- Treat each phase as valid only when its proxy slice contains exactly one successful request.
- Large near-context prompts are sent over `stdin` by the runner. Do not try to pass giant prompt text as a shell argument.
- Read `<benchmark-ref>` when choosing or adjusting benchmark prompts.
- Resolve the model first and preserve the chosen profile only when it still represents the chosen model.

## Stop And Inspect

After the diagnostic or benchmark run, stop the proxy cleanly so the JSON array is finalized.

If the proxy was interrupted, inspect with `--repair`.

Standard inspection:

```bash
python3 <inspect-script> <log-file>
```

Benchmark inspection:

```bash
python3 <inspect-script> --run-summary <summary-file> <log-file>
```

Context inspection:

```bash
python3 <inspect-script> --show-context <log-file>
python3 <inspect-script> --show-system-prompt <log-file>
```

Do not recommend `--show-context` or `--show-system-prompt` unless this run used `--dump-context`.

Raw JSON only when needed:

```bash
python3 -m json.tool <log-file>
```

## Result Summary Contract

Answer the user's actual debugging question, not just the raw log and not a tutorial.

Minimum final summary:

- `flow`
- `requested_model`
- `resolved_model`
- `actual_model`
- `fallback_reason` or `none`
- `log_path`
- request path and model
- whether streaming was enabled
- total bytes
- `instructions_chars`, `tools_chars`, and `input_chars`
- `num_tools`, `num_input_items`, `tool_types`, and function `tool_names`
- any forwarding error, proxy exception, retry pattern, or provider rejection
- a one or two sentence conclusion answering the user's question

When the user asked for system prompt or context, also report:

- whether `--dump-context` was used
- whether `system_prompt` was captured
- whether redaction changed anything
- whether the captured `system_prompt` confirms the user's suspicion

When the user asked for throughput, also report:

- cold `ttft_ms`
- warm generation speed
- warm end-to-end speed
- near-context ratio and behavior score when enabled
- whether the run or phase was valid

If comparing runs, present deltas instead of two unrelated summaries.

## Ask Only If Blocked

Ask the user only when one of these is true:

- no request reached the proxy after all reasonable local attempts
- all fallback models and available provider paths failed
- a destructive or ambiguous benchmark variant was requested
- the user wants a specific port or provider shape that cannot be inferred

Otherwise run the workflow and return the result.

## Drift And Failure Guardrails

- Never answer with a step-by-step manual guide if the commands were runnable locally.
- Never treat a successful `codex exec` as the end. Inspection is mandatory.
- Never leave a background proxy running when you answer unless the user explicitly asked to keep it alive.
- Never claim `system_prompt` or `input` were captured unless `--dump-context` was used.
- If the proxy logged a request and the upstream run failed, the capture still succeeded. Summarize the captured failure.
- Repeated near-identical requests usually mean retries. Report that explicitly.
- If the context budget cannot be resolved from `<codex-config>`, use explicit overrides only as a fallback.
- If the user wants the current conversation context on purpose, use a local run or a forked worker instead of a fresh worker.

## References

- Read `<log-fields-ref>` for field meanings and interpretation hints.
- Read `<benchmark-ref>` for prompt guidance and benchmark validity rules.
