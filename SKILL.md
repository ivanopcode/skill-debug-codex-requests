---
name: debug-codex-requests
description: Capture and inspect outgoing Codex API requests by routing a fresh Codex CLI run through a local OpenAI-compatible proxy, then summarize the logged request metadata. Use when Codex needs to debug request size, injected instructions, exposed tools, model or provider overrides, profile differences, skill/context leakage, or model throughput such as TTFT and tokens-per-second; prefer the isolated subagent workflow for the proxy and the diagnostic Codex run.
---

# Debug Codex Requests

Use this skill when you need to inspect what Codex actually sent to the provider: model, reasoning, tool schema, `instructions`, `input`, `system_prompt`, request size, TTFT, or throughput.

## Standing Rules

- Treat the proxy log as the source of truth.
- Prefer `codex exec` over interactive `codex`.
- Prefer a fresh temp directory such as `/tmp/debug-codex-requests-<timestamp>`.
- Prefer a profile from `~/.codex/config.toml` over a raw `-m <model>` override.
- Keep `--dump-context` opt-in. Use it only when the user explicitly wants prompt/context payload.
- If the user wants the system prompt imposed by the agent environment, use `--dump-context` and inspect `system_prompt`.
- Throughput is a separate benchmark workflow. Do not infer tokens-per-second from a normal multi-step run.
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

## Resolve Profile First

Always inspect `<codex-config>` before building the diagnostic command.

Rules:

- If the user named a profile such as `oss` or `oss120`, pass `-p <profile>`.
- If the user wants the default Codex behavior, do not add `-p`.
- Prefer `-p <profile>` over `-m <model>` when both represent the same configured setup.
- If you use `-p <profile>`, attach the proxy through the profile itself:

```bash
-c 'profiles.<profile>.model_provider="ollama-proxy"'
-c 'model_providers.ollama-proxy.name="Proxy"'
-c 'model_providers.ollama-proxy.base_url="http://127.0.0.1:<proxy-port>/v1"'
```

- If you are not using a profile, use the top-level overrides:

```bash
-c model_provider=ollama-proxy
-c 'model_providers.ollama-proxy.name="Proxy"'
-c 'model_providers.ollama-proxy.base_url="http://127.0.0.1:<proxy-port>/v1"'
```

- If the selected profile points at Ollama and successful completion matters, verify the model exists in `ollama list`.

## Choose The Flow

Use one of these paths:

- **Capture only**: prove what request Codex sent.
- **System prompt / context**: capture sanitized `system_prompt`, `instructions`, and optionally `input`.
- **Throughput**: measure cold TTFT, warm generation speed, and near-context behavior.

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

## Capture Only

Default prompt:

```text
Reply with exactly PONG and do not call any tools.
```

Without a profile:

```bash
codex exec --skip-git-repo-check --ephemeral --json \
  -C <workdir> \
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
- `<profile>`: the named profile from `<codex-config>` when the user provided one
- `<prompt>`: a short one-shot prompt unless the user is testing a specific request shape

## System Prompt And Context

Use this path when the user wants the environment-imposed prompt or the actual sanitized payload.

Workflow:

1. Start the proxy with `--dump-context`.
2. Run a fresh `codex exec` through the proxy.
3. Inspect the log with one of these commands:

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

## Stop And Inspect

After the diagnostic or benchmark run:

- Stop the proxy cleanly so the JSON array is finalized.
- If the proxy was interrupted, inspect with `--repair`.

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

Raw JSON only when needed:

```bash
python3 -m json.tool <log-file>
```

## Output Contract

Answer the user's actual debugging question, not just the raw log.

Usually report:

- request path and model
- whether streaming was enabled
- total bytes
- `instructions_chars`, `tools_chars`, and `input_chars`
- `num_tools`, `num_input_items`, `tool_types`, and function `tool_names`
- any forwarding error or proxy exception

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

- no usable profile can be identified and the target behavior depends on it
- a destructive or ambiguous benchmark variant was requested
- the upstream model choice is unclear and completion success matters
- the user wants a specific port or provider shape that cannot be inferred

Otherwise run the workflow and return the result.

## Troubleshooting

- If no log entry appears, Codex probably failed before reaching the provider override.
- If the proxy logged a request and the run still failed, the capture still succeeded.
- Repeated near-identical requests usually mean retries.
- If the upstream rejects the model, inspect `<codex-config>` and the selected profile first.
- If the user asked for throughput and a phase produced more than one request, report that phase as invalid.
- If the context budget cannot be resolved from `<codex-config>`, use explicit overrides only as a fallback.
- If the user wants the current conversation context on purpose, use a local run or a forked worker instead of a fresh worker.

## References

- Read `<log-fields-ref>` for field meanings and interpretation hints.
- Read `<benchmark-ref>` for prompt guidance and benchmark validity rules.
