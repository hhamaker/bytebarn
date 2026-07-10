# Crew

A local desktop app that runs AI coding agents against your own codebases.
Open a project, type a prompt or a `/goal`, and watch a crew of pixel-art
critters do the work. Built from the spec in `python-desktop-rebuild.md`.

Everything runs locally: no cloud backend, no accounts, no telemetry. The
only network traffic is to the LLM providers you configure.

## Quick start

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'

export ANTHROPIC_API_KEY=sk-...        # and/or OPENAI_API_KEY
.venv/bin/python -m crew.main /path/to/your/project
```

No-GUI engine harness:

```bash
.venv/bin/python -m crew.cli "explain this repo" --project /path/to/project
```

Tests (no network, no API keys needed):

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/pytest
```

## The signature flow

Type `/goal add a --verbose flag to the CLI and test it` in the prompt bar:

1. The **orchestrator** agent restates the goal and writes a todo plan.
2. It casts a crew from the available subagents (`general`, `explore`, plus
   any you drop into `.crew/agent/*.md`) and delegates tasks — in parallel
   when independent.
3. The **crew stage** appears: one pixel-art critter per subagent (species
   by stable hash of the agent name, tinted with its configured color),
   ropes to the crowned orchestrator, pulse dots while working, worried
   brows on retry, happy eyes when done, sleeping critters for todos not
   yet delegated. Click a critter to open its session; back button returns.
4. The orchestrator verifies results and reports a per-agent summary.

## Configuration

Two layers, project wins per key: `~/.crew/config.json` and
`<project>/.crew/config.json`. JSON with `//` comments and trailing commas
tolerated; in-app edits patch files key-by-key, preserving your comments.

```jsonc
{
  "provider": {
    "anthropic": { "api_key_env": "ANTHROPIC_API_KEY" },
    "lmstudio":  { "base_url": "http://localhost:1234/v1", "api": "openai" }
  },
  "model": "anthropic/claude-sonnet-4-5",
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

### Custom agents: drop in a file, the crew grows

`.crew/agent/tester.md` (hot-reloaded, no restart):

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
and picks accordingly. A file named after a built-in (`build`, `plan`,
`orchestrator`, `general`, `explore`) merges over it.

Custom commands: `.crew/command/foo.md` with a `$ARGUMENTS` template →
`/foo`. Built-ins: `/goal`, `/compact`, `/new`, `/model`, `/agents`.

## Permissions

Per tool: `allow` / `ask` / `deny`, or pattern lists (bash patterns match
the command, edit/write match paths). Deny → allow → default. Per-agent
overrides in agent frontmatter. Session toggle in the status bar:
Safe / Ask / Full-auto. "Allow always" in the permission dialog appends the
pattern to project config.

## Architecture

```
crew/engine/   asyncio, zero Qt — session store (SQLite WAL), provider
               adapters (anthropic, openai-compatible), tool suite, agent
               registry, runner loop, compaction, event bus
crew/app/      PySide6 widgets — pure projection of engine events + DB
assets/        agent/tool prompts (prompt engineering lives here)
```

Engine and UI meet only through the async event stream (`crew/engine/events.py`)
and the `Engine` facade — enforced by a test that imports the engine with Qt
poisoned.

## Known gaps (v1)

- Theme/font settings are stored but light/dark palette switching is not applied yet.
- `@file` completion inserts the path; image attachment parts are stored but
  not previewed in the transcript.
- Packaging (PyInstaller/Briefcase) not wired; run from the venv.
