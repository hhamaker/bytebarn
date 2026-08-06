# UI Redesign — Termius-inspired shell with Chat / Code / Terminal views

**Date:** 2026-08-05
**Branch:** `ui-redesign`
**Goal:** Reinvision the ByteBarn UI around a simple flow — pick a project, then
move between its chats, code sessions, and terminals from one place — taking
inspiration from Termius (rail + contextual sidebar + content) and Claude
Desktop (explicit Chat / Code mode switching). Every existing feature keeps
working; this is a navigation and visual restructure, not an engine change.

## Current state (what we're reshaping)

`MainWindow` is a two-pane splitter: a stacked sidebar (page 0 `SessionList`
"all projects" tree, page 1 `ProjectWorkspace` with Chats / Goals / Memory /
Agents tabs) and a content column (header, transcript + crew stage, collapsible
bottom terminal pane, todo strip, prompt bar). Global tools (providers, agents,
settings, theme toggle, permission mode) crowd the status bar. Navigation
concepts overlap: sessions appear in both sidebar pages, the terminal hides in
a bottom pane behind a menu item, and "goal" sessions (orchestrator code runs)
are buried in a workspace tab.

## Design

### Three-column shell

```
┌────┬───────────────┬──────────────────────────────┐
│ N  │   Sidebar     │   Content                    │
│ a  │  (contextual  │  chat view: header,          │
│ v  │   per rail    │  transcript+crew, todo,      │
│ R  │   selection)  │  prompt bar                  │
│ a  │               │  terminal view: full         │
│ i  │               │  terminal manager            │
│ l  │               │                              │
└────┴───────────────┴──────────────────────────────┘
```

**NavRail** (new widget `bytebarn/app/nav_rail.py`, fixed ~56 px):

- Top: **Projects** (home) — all-projects view.
- Project-scoped group (enabled once a project is active): **Chat**,
  **Code**, **Terminal**.
- Bottom: **Agents**, **Providers**, **Settings** — open the existing dialogs
  (moved off the status bar).
- Active view shown with an accent indicator. Tooltips everywhere.
- Shortcuts: `Cmd/Ctrl+1..4` for Projects / Chat / Code / Terminal.

**Sidebar** (existing widgets, re-scoped):

- *Projects* view → existing `SessionList` (search, drag, context menus — all
  preserved). Selecting/opening a project moves you into its Chat view.
- *Chat* view → `ProjectWorkspace` with the **Chats** tab selected: session
  list + New chat.
- *Code* view → `ProjectWorkspace` with the **Goals** tab selected: goal
  queue, past runs, routines — plus a **New code session** action that starts
  a session with the orchestrator ("goal") agent directly.
- *Terminal* view → sidebar keeps the workspace; the terminal manager already
  owns its own list of terminals on its left edge.
- Memory and Agents remain workspace tabs, reachable in Chat/Code views.

**Content**:

- Chat/Code views show the existing content column unchanged (header,
  transcript, crew stage, todo strip, prompt bar, optional preview, optional
  bottom terminal pane).
- Terminal view promotes the existing `TerminalPanel` to fill the content
  area (header/todo/prompt hidden), with a **New terminal** action spawning a
  local shell in the session's working directory. Leaving the view restores
  the previous layout, including a still-open bottom pane if it was open.

### Session taxonomy

- **Chat session** — `session.agent != "goal"`.
- **Code session** — `session.agent == "goal"` (orchestrator). "New code
  session" creates one directly, alongside the existing goal queue.
- **Terminal** — entries in the terminal manager (backend tees + user shells).

### Status bar cleanup

Keep: project dir, git, runtime pill, update, context meter, review, cost,
permission-mode combo, theme toggle. Remove: providers / agents / settings
buttons (now on the rail).

### Visual refresh

`theme.py` keeps its token/QSS architecture and all three themes. Changes:

- New tokens for the rail (`rail_bg`, slightly darker than sidebar) so the
  three columns read as distinct depths (Termius-style: rail darkest, sidebar
  mid, content lightest).
- QSS for `#navRail` buttons: icon-only, rounded, accent-tinted when active.
- Minor polish pass: consistent radii, hairlines between columns.

### What does not change

Engine layer, event flow, transcript, crew stage, prompt bar, dialogs,
permission system, worktrees, MCP, providers — untouched. `MainWindow` keeps
all its slots; only layout composition and a new `_set_view(view: str)`
navigation method change.

## Error handling

- Chat/Code/Terminal rail items disabled until a project is active; clicking
  Projects always works.
- Terminal spawn failures surface in the terminal list as they do today.
- View state is not persisted beyond what already persists (splitter sizes,
  stage height); reopening the app lands in the last session's Chat view.

## Testing

- New `tests/app/test_nav_rail.py`: rail construction, view switching signals,
  enabled/disabled states, shortcut wiring.
- Extend `tests/app/test_ui_smoke.py`-style tests: `_set_view` transitions
  show/hide the right widgets; terminal view restores prior layout.
- Existing suite (394 tests) stays green; UI tests touching the old status-bar
  buttons or sidebar indices get updated.
- Offscreen GUI verification with screenshots via the `verify` skill.
