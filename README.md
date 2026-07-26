# ByteBarn 🛖

[![CI](https://github.com/hhamaker/bytebarn/actions/workflows/ci.yml/badge.svg)](https://github.com/hhamaker/bytebarn/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/bytebarn)](https://pypi.org/project/bytebarn/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

A local desktop harness for AI coding agents. Open a project, type a prompt
or a `/goal`, and a barn crew of pixel-art farm animals gets to work on your
codebase.

![The barn crew, ready for a goal](docs/media/welcome-dark.png)

The app itself is fully local — no cloud backend, no accounts, no
telemetry. Connect it to any of 17 LLM providers (Anthropic, OpenAI, Groq,
Bedrock, OpenRouter, Z.ai, and more), or skip the cloud entirely and run models
on your own machine via Ollama or LM Studio.

![A session: chat, tool calls, live diffing](docs/media/session-dark.png)

## What's in the barn

- **Chat + agents** — markdown/code transcript, streaming, collapsible tool
  and thinking cards, edit-and-re-run any prompt, regenerate, per-message
  copy, in-conversation search (⌘F), full-text search across all chats,
  transcript export, image paste with real vision support, PDF previews.
- **Goals** — hand the orchestrator a goal; it plans todos, casts subagents,
  and the crew stage animates them working in parallel. Queue goals and walk
  away; routines re-run a prompt on a schedule.
- **Harness tools** — web search + research agent, MCP servers (12 one-click
  recipes + custom), skills, per-project persistent memory, preview panel
  for HTML/dev servers, inline file editor, side chat (⌘;) for throwaway
  questions.
- **Control** — Safe / Ask / Full-auto permission modes with per-tool glob
  rules, run review with per-file revert, cost tracking, automatic model
  fallback, extended-thinking control per agent.

## Quick start

One line (macOS / Linux):

```bash
curl -fsSL https://raw.githubusercontent.com/hhamaker/bytebarn/main/scripts/install.sh | sh
bytebarn
```

The script finds Python 3.12+, installs into an isolated venv at
`~/.bytebarn/venv`, and puts `bytebarn` on your PATH. Re-run it to upgrade;
it's [40 lines, read it here](scripts/install.sh).

Prefer your own tooling? `pip install bytebarn` does the same thing. macOS
users can also grab `ByteBarn.app` from the
[latest release](https://github.com/hhamaker/bytebarn/releases) — drag to
Applications, open, done.

On first launch, connect a provider (**⚡ providers** in the status bar —
paste an API key or log in via web) and start typing. Ollama and LM Studio
work with no key at all.

<details>
<summary>Running from source / contributing</summary>

```bash
git clone https://github.com/hhamaker/bytebarn && cd bytebarn
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/bytebarn                          # or: -m bytebarn.main /path/to/project

# no-GUI engine harness
.venv/bin/python -m bytebarn.cli "explain this repo" --project /path/to/project

# tests — no network, no API keys needed
QT_QPA_PLATFORM=offscreen .venv/bin/pytest
```

</details>

## Requirements

- **Python 3.12+** (the install script checks and tells you how to get it).
- **~600 MB disk** — Qt (PySide6 + WebEngine) is the bulk of it.
- **macOS 12+** — the platform ByteBarn is built and tested on. The
  prebuilt `ByteBarn.app` is Apple Silicon only; Intel Macs should install
  via pip. Linux and Windows are **experimental**: the test suite passes on
  Linux in CI, but the GUI gets no regular testing there (Linux needs the
  usual Qt runtime libs — on minimal Debian/Ubuntu:
  `apt install libegl1 libgl1 libxkbcommon-x11-0 libxcb-cursor0`).
- **A model to talk to**: an API key for any supported provider, or a local
  server (Ollama / LM Studio). Agents drive everything through tool calls,
  so local models must support function calling — Qwen, Llama 3.1+, and
  similar work well; tiny chat-only models will disappoint.

## The signature flow

Type `/goal add a --verbose flag to the CLI and test it` in the prompt bar:

1. The **orchestrator** agent restates the goal and writes a todo plan.
2. It casts a crew from the available subagents (`general`, `explore`, plus
   any you drop into `.bytebarn/agent/*.md`) and delegates tasks — in parallel
   when independent.
3. The **crew stage** appears: one pixel-art farm animal per subagent,
   roped to the crowned cow (the orchestrator), with a live headline
   (working/done/failed/queued counts + elapsed time + the todo in progress)
   and a per-animal status line (live tool activity, ✓ done, ✗ failed
   badges). Known agent types get signature looks (explore is a sheep,
   testers are pigs in goggles, reviewers wear glasses, planners wear
   hats…); custom agents get a stable species + accessory from their name.
   Prefer woodland critters, anime characters, all dogs, or all cats? Swap
   the whole crew's style in Settings. Click any animal to open its
   session.
4. The orchestrator verifies results and reports a per-agent summary.

## Configuration

Two layers, project wins per key: `~/.bytebarn/config.json` and
`<project>/.bytebarn/config.json`. JSON with `//` comments and trailing commas
tolerated; in-app edits patch files key-by-key, preserving your comments.

```jsonc
{
  "provider": {
    "anthropic": { "api_key_env": "ANTHROPIC_API_KEY" },
    "lmstudio":  { "base_url": "http://localhost:1234/v1", "api": "openai" }
  },
  "model": "anthropic/claude-sonnet-5",
  "small_model": "anthropic/claude-haiku-4-5",   // titles, summaries, compaction
  "agent": { "build": { "temperature": 0.2 } },   // agent editor writes here
  "permission": {
    "bash": { "default": "ask", "allow": ["git status*"], "deny": ["rm -rf*"] },
    "edit": "allow"
  },
  "instructions": ["AGENTS.md", "CLAUDE.md"]
}
```

Any OpenAI-compatible endpoint works (LM Studio, Ollama, OpenRouter, Groq)
via `base_url` + `"api": "openai"`.

### Provider connections (⚡ providers in the status bar)

Pick a provider, paste a key or **🌐 log in via web**, hit *Test connection*:

| Provider | Auth | Notes |
|---|---|---|
| Anthropic (Claude) | API key / `ANTHROPIC_API_KEY` | pay-as-you-go key from console.anthropic.com |
| OpenAI | API key / `OPENAI_API_KEY` | |
| xAI (Grok) | API key or web login | browser code confirmation |
| Groq | API key / `GROQ_API_KEY` | |
| OpenRouter | API key / `OPENROUTER_API_KEY` | one key, many models |
| Google (Gemini) | API key / `GEMINI_API_KEY` | |
| Mistral | API key / `MISTRAL_API_KEY` | |
| DeepSeek | API key / `DEEPSEEK_API_KEY` | |
| Z.ai (GLM) | API key / `ZAI_API_KEY` | Zhipu GLM models; glm-4.7-flash is free |
| Together AI | API key / `TOGETHER_API_KEY` | |
| Cerebras | API key / `CEREBRAS_API_KEY` | |
| AWS Bedrock | Access Key ID + Secret Access Key (or AWS chain) | region field / `AWS_REGION`; boto3 optional for live lists |
| Cloudflare Workers AI | API key / `CLOUDFLARE_API_KEY` | needs `CLOUDFLARE_ACCOUNT_ID` |
| Cloudflare AI Gateway | API key / `CLOUDFLARE_API_KEY` | needs account + `CLOUDFLARE_GATEWAY_ID` |
| Ollama | none | local, `http://localhost:11434/v1` |
| LM Studio | none | local, `http://localhost:1234/v1` |
| GitHub Copilot | web login | GitHub device code — needs a Copilot subscription |

Web login flavors: xAI and GitHub Copilot show a short code to confirm in
the browser (auto-copied to your clipboard; the dialog closes itself on
approval), and their tokens refresh automatically. Anthropic is API-key
only — Claude Pro/Max subscription logins are locked to Anthropic's own
Claude Code and won't drive a third-party app, so use a pay-as-you-go key
from console.anthropic.com. Keys go to `~/.bytebarn/auth.json` (0600) —
never project config. Model pickers only list models from connected
providers.

### Model fallback

If a model keeps failing mid-run (outage, quota), ByteBarn retries once, then
switches to a comparable connected model — closest cost tier, different
provider preferred — announces the switch in the transcript, and keeps
going. Tune or disable it:

```jsonc
"model_fallback": { "enabled": true, "after": 2 }
```

### Custom agents: drop in a file, the crew grows

`.bytebarn/agent/tester.md` (hot-reloaded, no restart):

```markdown
---
description: Writes and runs tests for a described change; reports pass/fail
mode: subagent
model: lmstudio/qwen3-coder-30b
temperature: 0.1
color: "#61afef"
tools: { read: true, bash: true, write: true, edit: true }
---
You are the TESTER. ...
```

The orchestrator sees every visible subagent's description in its task tool
and picks accordingly. A file named after a built-in (`build`, `plan`, `orchestrator`,
`general`, `explore`, `chat`, `research`) merges over it.

Prefer a GUI? **🐾 agents** in the status bar opens the agent editor with a
live sprite preview per agent. Sessions are managed from the sidebar:
right-click one to **close** (archive) or **delete** it, subagents and all.

Custom commands: `.bytebarn/command/foo.md` with a `$ARGUMENTS` template →
`/foo`. Built-ins: `/init` (analyze the repo and write AGENTS.md — loaded
into every agent's system prompt), `/goal`, `/compact`, `/new`, `/model`,
`/agents`.

## Permissions

Per tool: `allow` / `ask` / `deny`, or pattern lists (bash patterns match
the command, edit/write match paths). Deny → allow → default. Per-agent
overrides in agent frontmatter. Session toggle in the status bar:
Safe / Ask / Full-auto. "Allow always" in the permission dialog appends the
pattern to project config.

## Architecture

```
bytebarn/engine/   asyncio, zero Qt — session store (SQLite WAL), provider
               adapters (anthropic, openai-compatible), tool suite, agent
               registry, runner loop, compaction, event bus
bytebarn/app/      PySide6 widgets — pure projection of engine events + DB
bytebarn/assets/  agent/tool prompts (prompt engineering lives here)
```

Engine and UI meet only through the async event stream (`bytebarn/engine/events.py`)
and the `Engine` facade — enforced by a test that imports the engine with Qt
poisoned.

## Packaging (macOS)

```bash
./scripts/build_macos_app.sh --install   # builds dist/ByteBarn.app, copies to /Applications
```

Uses PyInstaller; the app icon is rendered from the in-app sprite art.
Launching from the Dock opens straight into your last-used folder — each
session picks its own working directory from there.

## Platform support

macOS-first: developed, tested, and packaged for macOS. Linux and Windows
are experimental — pure Python + Qt, and CI runs the full suite on Linux,
but the GUI sees no regular use there yet. If you run it on either, an
issue report (good or bad) genuinely helps.

## License

MIT — see [LICENSE](LICENSE).
