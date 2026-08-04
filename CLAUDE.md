# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ByteBarn — a local PySide6 desktop app that runs AI coding agents (an orchestrator
delegating to subagents, rendered as pixel-art farm animals) against a user's
codebase. `python-desktop-rebuild.md` is the original spec; code comments cite
it by section (`spec §5.2` etc.) — check it when intent is unclear.

## Commands

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# run the app (needs a project dir argument or it prompts with a file dialog)
.venv/bin/python -m bytebarn.main /path/to/project

# no-GUI engine harness
.venv/bin/python -m bytebarn.cli "prompt here" --project /path/to/project

# tests — no network, no API keys; offscreen Qt is required
QT_QPA_PLATFORM=offscreen .venv/bin/pytest
QT_QPA_PLATFORM=offscreen .venv/bin/pytest tests/engine/test_store.py -k cascade
```

pytest runs with `asyncio_mode = "auto"` — write async test functions
directly, no `@pytest.mark.asyncio` needed. No linter is configured.

## Architecture

Two layers that meet only through the async event stream and the `Engine`
facade:

- **`bytebarn/engine/`** — asyncio, **zero Qt**. Enforced by
  `tests/engine/test_no_qt_in_engine.py`, which imports the engine with Qt
  poisoned; importing PySide6 anywhere under `bytebarn/engine/` breaks the suite.
- **`bytebarn/app/`** — PySide6 widgets, a pure projection of engine events plus
  reads from the store. Main loop is qasync (`bytebarn/main.py`), so dialogs can
  `asyncio.ensure_future` freely.

Flow: UI calls `Engine` (facade.py) → `Runner` (runner.py) streams provider
events, executes tools, persists to `Store` (SQLite WAL via aiosqlite) → emits
dataclass events on `EventBus` (events.py) → `MainWindow._event_loop`
projects them into widgets. When touching the boundary, add/extend an event —
never import app code from the engine.

### Providers

Model strings are always `"provider/model-id"`. Resolution
(`providers/registry.py`): explicit `config.provider` entry, else the recipe
in `providers/known.py` (`KNOWN_PROVIDERS` — the single source of truth for
supported services, curated model lists, auth kind). API keys resolve config
key/env first, then `~/.bytebarn/auth.json` (AuthStore — secrets never go in
config files). OAuth records route specially: `xai` → loopback flow
(`xai_oauth.py`), `github-copilot` → device-code flow
(`github_copilot_oauth.py`). Wire protocols are just two: `anthropic` or
`openai` (openai_compat covers every other service via `base_url`).
`catalog.py` holds per-model cost/limits — add entries when adding models so
cost tracking works.

### MCP

`engine/mcp.py` connects Model Context Protocol servers declared under the
`mcp` config key (`command`+`args` = stdio, `url` = streamable HTTP). Each
server tool is exposed to agents as `mcp__<server>__<tool>`, appended to
`build_tools` output in the runner; permission default is ask (denied in
Safe mode). Each connection lives in its own task — anyio cancel scopes
must enter/exit in the same task. Tests spawn a real FastMCP stdio server
(`tests/engine/test_mcp.py`).

### Config

Two JSON-with-comments layers, project wins per key: `~/.bytebarn/config.json`
(overridable via `BYTEBARN_HOME` env — tests rely on this) and
`<project>/.bytebarn/config.json`. Programmatic writes must go through
`patch_config_file(path, {"dotted.key": value})` (config.py) — it patches
spans in place preserving user comments/formatting; `DELETE` sentinel removes
a key. Never rewrite config files wholesale.

### Agents

`agents.py` builtins (prompts live in `bytebarn/assets/prompts/agent_*.txt`) ←
overridden by `agent/*.md` files (global then project `.bytebarn/agent/`) ←
`config.agent.<name>` overrides on top. Hot-reloaded by a watchfiles task in
main_window. The GUI agent editor writes config overrides for builtins but
`.md` files for custom agents.

### Crew stage / sprites

`sprites.look_for(name)` maps agent names to a stable (species, accent) —
known types have fixed looks, custom names hash. `crew_stage.StageState` is
deliberately Qt-free-logic so it unit-tests headless; keep new stage state
there, not in the widget.

## Testing conventions

- Engine tests use `FakeProvider` (`providers/fake.py`) and `tmp_path` for
  store/global dirs; `test_goal_e2e.py` shows the full harness pattern.
- UI tests instantiate real widgets offscreen (`tests/app/test_ui_smoke.py`);
  a module-scoped `qapp` fixture provides the QApplication.
- HTTP flows are tested with `httpx.MockTransport` (see
  `test_copilot_device_flow`).

## Glossary

- **Engine** — asyncio, zero-Qt core that orchestrates runs via the `Runner`.
- **Store** — SQLite (WAL + aiosqlite) persistence layer for runs, messages, etc.
- **EventBus** — dataclass event stream emitted by the engine and consumed by the UI.
- **Provider** — model backend abstraction (`anthropic`/`openai` wire protocols).
- **Agent** — named prompt persona; can be builtin, `.md` file, or config override.
- **Stage** — headless `StageState` that manages on-screen sprite placement/logic.
- **Sprite** — pixel-art critter chosen by `sprites.look_for(name)` for an agent.
- **Config** — two-layer JSON-with-comments (`~/.bytebarn` then project `.bytebarn`).

## Glossary

- **crew / critters** — the pixel-art sprites on the crew stage; one per active subagent, species+accessory derived from the agent name (`sprites.look_for`).
- **orchestrator** — built-in agent that plans a goal and delegates via the task tool; shown as "goal" in the UI agent picker (the crowned critter).
- **crew stage** — the animated panel projecting `task.*` / `todo.updated` events (`crew_stage.StageState`).
- **part** — one unit of transcript content (text, reasoning, tool call, task, compaction, file); messages are ordered lists of parts.
- **compaction** — summarizing old history into a synthetic part when context nears the model's window (`compaction.py`).
- **provider / model string** — always `"provider/model-id"`; resolution order in `providers/registry.py`.
- **known provider** — an entry in `providers/known.py` with connection recipe + curated models; drives the ⚡ providers GUI.
- **auth record** — per-provider credential in `~/.bytebarn/auth.json`: `{"type": "api"|"oauth", ...}`.
- **model fallback** — automatic switch to a comparable connected model after repeated failures (`providers/fallback.py`).
- **permission mode** — session-wide Safe / Plan / Ask / Full-auto toggle layered under per-tool config rules (`permissions.py`). Plan mode explores and designs with hard write blocks (`/plan`).
- **subagent session** — child session created by the task tool; `parent_session_id` set, shown nested in the sidebar.
- **worktree isolation** — new subagents in a git repo get a private `git worktree` (`engine/worktree.py`); on finish, non-conflicting file changes are copied back into the parent tree. Disable with `"worktree": {"enabled": false}`.
- **isolated session** — a top-level session that owns a `git worktree` checked
  out from HEAD (`Engine.new_session(isolated=True)`). Its whole delegation tree
  writes there, so the live checkout never changes mid-run — this is how you run
  ByteBarn on ByteBarn. Branch `bytebarn/session-<id8>`, recorded in
  `session.worktree_branch`; never auto-applied or auto-removed, merge it in git.
- **sandbox** — macOS seatbelt confinement for bash under Full-auto (or `sandbox.always`); writable roots = project + temp + `~/.bytebarn` (`engine/sandbox.py`).
- **hooks** — config `hooks.pre_tool` / `post_tool` rules can deny tool calls or run side commands (`engine/hooks.py`).
- **rewind** — `/rewind` drops transcript after a user message and restores write/edit files from run checkpoints.
