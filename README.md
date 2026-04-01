# skill-debug-codex-requests

Open-source Codex skill for capturing and inspecting the exact provider requests that Codex sends through an OpenAI-compatible proxy.

It is useful when you need to debug:

- request size and payload shape
- injected `instructions` and `system_prompt`
- exposed tools and provider overrides
- profile differences from `~/.codex/config.toml`
- TTFT, warm generation speed, and near-context behavior

## What Is Included

- `SKILL.md` with the operational workflow
- `scripts/codex_proxy.py` for request capture and forwarding
- `scripts/inspect_proxy_log.py` for log inspection
- `scripts/run_codex_benchmark.py` for multi-phase throughput benchmarking
- `references/` with field semantics and benchmark guidance
- `agents/openai.yaml` for UI metadata

## Install

Clone or copy this repository into your Codex skills directory so the folder name stays `debug-codex-requests`.

One simple layout is:

```bash
mkdir -p ~/.codex/skills
git clone git@github.com:ivanopcode/skill-debug-codex-requests.git ~/.codex/skills/debug-codex-requests
```

## Main Flows

1. Capture a fresh Codex request through the bundled proxy.
2. Inspect sanitized `system_prompt`, `instructions`, `input`, tools, and request sizes.
3. Run the multi-phase benchmark for cold TTFT, warm raw speed, and near-context behavior.

## License

MIT

## Author

Ivan Oparin
