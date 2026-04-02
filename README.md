# skill-debug-codex-requests

This skill runs a fresh Codex request through a local OpenAI-compatible proxy, inspects the captured log, and summarizes what Codex actually sent to the provider.

## When To Use It

Use it when you need to debug:

- request size and payload shape
- injected `instructions` or `system_prompt`
- exposed tools and provider or model overrides
- profile differences from `~/.codex/config.toml`
- TTFT, warm generation speed, and near-context behavior

## Default Behavior

The skill is designed to execute the workflow end-to-end and return findings.

- It defaults to `capture only` when the request is underspecified.
- It inspects the resulting log before answering.
- It does not stop at printing commands unless the user explicitly asked for instructions only.
- Context dumping is opt-in because sanitized captures may still contain sensitive user content.

## Model Selection

The diagnostic run uses the model named by the user, with exact or approximate matching against configured models and profiles.

- If the requested model is unavailable or unloaded, the default fallback is `gpt-5.3-codex-spark`.
- If Spark is unavailable, the next fallback is `gpt-5.4` with `medium` reasoning.
- The final summary reports the requested model, resolved model, actual model, and any fallback reason.

## Main Flows

- Capture request metadata: run a fresh proxied `codex exec`, inspect the log, and summarize request shape, tool surface, overrides, and errors.
- Capture sanitized context: use `--dump-context` to inspect `system_prompt`, `instructions`, and sanitized `input`.
- Benchmark throughput: run the bundled multi-phase benchmark to measure cold TTFT, warm speed, and near-context behavior.

## Included Files

- `SKILL.md` with the operational contract and workflow
- `scripts/codex_proxy.py` for request capture and forwarding
- `scripts/inspect_proxy_log.py` for log inspection
- `scripts/run_codex_benchmark.py` for throughput benchmarking
- `references/` with field semantics and benchmark guidance

## Install

Install or update the managed copy with:

```bash
./setup.sh global --locale ru-en
```

This creates a managed runtime copy under `${XDG_DATA_HOME:-~/.local/share}/agents/skills/skill-debug-codex-requests`, updates localized metadata, and refreshes the symlinks in `~/.claude/skills/debug-codex-requests` and `~/.codex/skills/debug-codex-requests`.

## License

MIT
