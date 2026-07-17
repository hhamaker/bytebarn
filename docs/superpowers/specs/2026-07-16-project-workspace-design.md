# Project workspace: deliberate per-project interface

Date: 2026-07-16
Status: approved (autonomous /goal run)

## Requirements (from /goal)

1. Good way of managing per-project memory.
2. Two ways of viewing projects: **All projects** (multi, like today) and the
   **standard single-project** view.
3. Deliberate interface: clicking a project opens a whole project view where
   its chats, goals, memory and agents live.
4. Agent configs per session **and** per project.

## Design

### View modes (left panel = QStackedWidget)

- **Project workspace (standard, index 1)** — default on launch, scoped to
  the working-directory project. Header: `← All projects · <name>`, then a
  QTabWidget:
  - **Chats** — the project's sessions, newest first, running indicator,
    `+ New chat`, context menu (rename/delete). Click loads the transcript
    (right side unchanged).
  - **Goals** — orchestrator sessions with todo progress (`3/5 done`);
    click opens the run.
  - **Memory** — OKF bundle manager: file list, markdown editor, New /
    Save / Delete (log.md read-only view included). Direct file ops on
    `Engine.memory_dir(project_id)`.
  - **Agents** — per-project defaults: default agent combo + default model
    picker (stored on the project row), plus "Edit agents…" opening the
    existing AgentEditor. Per-session config stays in the prompt bar
    (agent/model pickers already persist per session).
  - Footer: "Project settings…" (existing ProjectDialog: name,
    instructions, knowledge assets).
- **All projects (index 0)** — today's sidebar (time-bucketed recents +
  Projects section). Single-click a project row → enter its workspace.
  "Project settings…" stays in the context menu.

### Store

`project` gains `default_agent` and `default_model` TEXT columns
(migration). `Engine.new_session` resolves: explicit arg → project default
→ existing fallback. Sessions keep their own agent/model (per-session
config, already persisted on the session row).

### Out of scope

Per-project *per-agent* prompt overrides (already possible via
`<project>/.crew/agent/*.md` for directory projects).

## Testing

Store default roundtrip; new_session applies project defaults; workspace
builds with all four tabs; memory tab save/delete round-trips files;
goals tab lists orchestrator sessions; view switching signals.
