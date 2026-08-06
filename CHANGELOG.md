# Changelog

All notable changes to ByteBarn are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **Termius-style shell: a nav rail with Projects / Chat / Code / Terminal
  views.** A fixed icon rail (⌘1–⌘4) now anchors navigation: pick a project,
  then move between its chats, its goal runs (Code view, with a new
  "+ New code session" button that starts an orchestrator session directly),
  and a full-content Terminal view. Providers / agents / settings moved off
  the status bar onto the rail; leaving the Terminal view restores the chat
  layout exactly, including the bottom terminal pane state.

### Fixed

- PTY read loop crashed with a `NameError` the moment a read would block,
  killing interactive shells; it now awaits the readiness future it created.

- **Terminal Manager actually works as a terminal.** Local shells now use a
  real cell-grid VT emulator (colors, cursor addressing, clear, alt screen,
  scrollback) instead of dumping stripped ANSI into a text edit. PTY winsize
  tracks the widget, input covers arrows/Ctrl/function keys, and the reader
  uses non-blocking `add_reader` instead of executor thrash. Backend tees
  (Claude Code) stay on a plain log view.

## [0.3.5] — 2026-08-05

Package version alignment: `pyproject.toml` now reports **0.3.5** so editable
and wheel installs match the shipped feature set (tags had moved ahead of the
version field).

### Includes (since 0.3.2)

- **Claude Code** as a first-class provider + per-agent / subagent model routing
- **Create agents in the UI** (+ Primary / + Subagent / Delete)
- **Terminal Manager** (Tools → Terminals / ⌘\`) — Claude Code streams + local shells
- Workspace tabs show full **Chats / Goals / Memory / Agents** labels (no Ch…)

## [0.3.4] — 2026-08-05

- Create primary/subagent agents from the Agents dialog (`.bytebarn/agent/*.md`).

## [0.3.3] — 2026-08-05

### Added

- **Claude Code as a first-class provider.** Listed in `KNOWN_PROVIDERS` (and
  the ⚡ providers manager) with curated CLI aliases (`default`, `sonnet`,
  `opus`, `haiku`). Always "connected" as a local runtime — Test connection
  checks that the `claude` binary is on PATH.
- **Per-agent / per-subagent Claude Code defaults.** Set an agent's model to
  `claude-code/…` in the agent editor (or `agent.<name>.model` in config).
  Engine routes that session through the Claude Code CLI even when the sticky
  global `"runtime"` is still `"native"`. Subagents inherit the parent's
  Claude Code model when they have no override of their own.
- Settings / agent-editor model pickers offer **Claude Code** alongside API
  providers.

### Notes

- Sticky global runtime (`"runtime": "claude-code"`) still works from the
  prompt-bar provider dropdown. Per-model routing is additive: a session with
  `claude-code/sonnet` uses CC; an explicit API model under sticky CC runtime
  uses the native Runner.

## [0.3.2] — 2026-08-05

### Fixed

- **Claude Code is obvious in the provider picker.** Listed **first** as
  **Claude Code** (not a trailing `claude-code` slug). The combo stores the
  stable id `claude-code` while showing the friendly label.

## [0.3.1] — 2026-08-05

Claude Code is selectable from the **provider** dropdown — no JSON edit required.

### Added

- **`claude-code` in the provider picker.** Always listed next to connected API
  providers. Choosing it sets `"runtime": "claude-code"` and seeds the model
  list with CLI aliases (`default`, `sonnet`, `opus`, `haiku`). Choosing any
  other provider restores `"runtime": "native"`.
- Status-bar chip **◈ Claude Code** while that runtime is active.
- `claude-code/default` omits the CLI `--model` flag so Claude Code picks its
  own default; other ids pass through as `--model <id>`.

### Notes

- Sticky Claude Code is stored as `runtime`, not `last_model`, so it does not
  poison new native sessions.
- Still requires a local `claude` binary and CLI auth / subscription.

## [0.3.0] — 2026-08-05

Optional **Claude Code runtime** plus layout fixes so splitters and snap tools
stop fighting the window.

### Added

- **Claude Code CLI runtime.** Set `"runtime": "claude-code"` in config to drive
  sessions through headless Claude Code (`claude -p --output-format stream-json`)
  instead of the native Runner+Provider loop. One process per run; stream-json
  events project into the same transcript parts and `RunFinished` contract the UI
  already uses. Follow-up turns resume via `--resume` and a small session-id map
  under `<project>/.bytebarn/claude_code_sessions.json`.

  Optional `claude_code` block: `command`, `permission_mode`, `allowed_tools`,
  `include_partial_messages`, `bare`, `model`, `extra_args`, `max_turns`.

  Default remains `"runtime": "native"`. Tests inject `FakeClaudeCodeRuntime` so
  CI never needs the `claude` binary.

### Fixed

- **Session-picker splitter snap.** `ModelPicker`, `ProjectWorkspace`, and the
  prompt-bar combos advertised large minimum widths (~380px sidebar / ~580px
  transcript), so dragging the session list handle immediately snapped back.
  Soft floors and wrap-friendly labels let the handle track 160px through a wide
  range without growing the window.
- **Crew-stage height restore** no longer applies an absurd saved `stage_height`
  in a way that can grow the main window past the splitter total.
- **Graceful shutdown.** Closing the window cancels UI tasks, awaits
  `engine.stop()`, and drains in-flight work before quitting (including
  subprocess groups for bash/sandbox and provider clients).
- Provider / MCP / hooks teardown paths that could hang or leave tasks running
  on quit.

### Notes

- Under `claude-code` mode, Claude Code owns its tool loop; ByteBarn’s native
  `run_subagent` path is unchanged. Subagent stream events with
  `parent_tool_use_id` are not projected yet.

## [0.2.0] — 2026-08-05

The headline is **isolated sessions**: run ByteBarn against a repository —
including ByteBarn's own — without the working tree changing underneath you
mid-run.

### Added

- **Isolated sessions.** Tick **Isolated** next to "+ New session" and the
  session gets its own `git worktree`, checked out from HEAD, on a
  `bytebarn/session-<id8>` branch. Its whole delegation tree writes there, so
  a `/goal` run never mutates your live checkout. The worktree and branch
  persist after the run — nothing is auto-applied or auto-removed — and you
  merge in git when you are ready.

  This works because `run_subagent` already derives a subagent's working
  directory from its parent session, so giving the parent a worktree nests the
  entire delegation tree inside it and makes that worktree the merge target
  instead of the live tree.

- **A pre-flight warning when the repository is dirty.** The worktree is checked
  out from HEAD, so uncommitted work will not be in it. Starting an isolated
  session lists what is missing and asks before continuing.

- **Worktree cleanup at delete time.** Deleting an isolated session offers to
  remove its worktree and branch, saying first what removal would cost — commits
  that exist only on that branch, *and* uncommitted files in the tree. Keep /
  Remove / Cancel, with Keep as the default so Enter never destroys work. One
  dialog for the whole selection, however many sessions are selected.

  "Merged" means reachable from any other ref, not a comparison against `main`,
  so the answer holds however the work landed — merge, rebase, squash, or
  cherry-pick.

- **Visible isolation.** Isolated sessions carry a `⑂` mark and their branch
  short-name in the sidebar and the project workspace, the branch and worktree
  path in the tooltip, and a status-bar line naming both when the session is
  created.

- `CHANGELOG.md` — this file.

### Changed

- `Engine.new_session` gained `isolated=`; `Engine.delete_session` gained
  `discard_worktree=`. Both default to the previous behaviour.
- `Engine._worktree_enabled` is now the public `Engine.worktree_enabled`, so the
  UI can distinguish "you turned worktrees off in config" from the other reasons
  isolation is unavailable instead of blaming your repository.
- A session whose worktree directory has been removed by hand now falls back to
  the project directory with one notice, rather than failing every tool call.
- Isolation is refused rather than half-delivered when a branch name collision
  would produce a detached worktree — there would be no branch to merge, so the
  session falls back to an ordinary one.
- **`mcp` is capped below 2.0.** mcp 2.0 removed `mcp.server.fastmcp` and renamed
  `streamablehttp_client` to `streamable_http_client`, which the MCP HTTP
  transport still calls. The cap keeps installs on the version the code targets;
  porting is tracked separately.

### Fixed

- A worktree path could leak into subsequently created sessions, so a plain
  session could write onto another session's branch while your live checkout
  stayed clean and `git status` showed nothing.
- Opening a *running* subagent and creating a session from there rooted it in
  that subagent's worktree — which is removed when the run finishes.
- The dirty-repo warning inspected the open project rather than the repository
  actually being branched from, so it could describe a repository that was never
  consulted.
- A project directory below the git root had its session silently relocated to
  the repository root, so edits landed at the wrong depth and project
  instructions stopped loading.
- Worktree removal could be handed a path the caller did not intend, including
  one that resolved to the process working directory or to a containing
  checkout. Removal now refuses anything that is not the session's own worktree.
- A failed store write after worktree creation left the worktree and branch
  orphaned; teardown now runs and the original error is what propagates.
- Cleanup failures during a delete are reported instead of being silent.
- The test suite made real network connections — every `Engine.start` warmed the
  model cache against ollama and lmstudio on localhost, and one dialog test
  called a live GitHub endpoint and spawned `npx`. Suites now run offline as
  documented, and are a few seconds faster for it.
- `ruff check .` passes; the lint job had been failing on `main`.

### Known limitations

- Deleting a *project* removes its sessions without the worktree prompt, so
  their worktrees are still orphaned.
- The uncommitted-file count collapses untracked directories to one entry and
  does not count gitignored paths.
- A cross-project session whose worktree directory is already gone cannot be
  traced back to its repository, so its branch is left in place.

## [0.1.0] — 2026-07-23

First release.
