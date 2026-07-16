# Claude-Desktop-style session handling

Date: 2026-07-16
Status: approved (autonomous /goal run)

## Assessment: how Claude Code desktop / Claude Desktop handle sessions

- **Flat, time-bucketed recents.** The sidebar is a single reverse-chronological
  list of sessions grouped under date headers (Today, Yesterday, This week,
  This month, Older) — not a project tree. The project/directory is a subtitle
  on each row, not a container the user must expand.
- **Instant new session.** "New chat"/"New session" never opens a modal; the
  working context is inherited (current project / last used directory) and can
  be changed afterwards from the session view.
- **No empty-chat litter.** Repeatedly starting a new chat does not stack empty
  untitled rows in the sidebar.
- **Internal sessions hidden.** Subagent runs never appear as top-level
  sidebar entries; they are reached from the conversation itself.
- **Projects are secondary.** Projects exist as their own small section for
  organization; chats are not forced into per-project subtrees.

## Crew today (gap)

- Sidebar is a tree: project → folders → sessions → subagent children
  (`crew/app/session_list.py`).
- Every "New session" opens a modal `QFileDialog` directory picker
  (`main_window._prompt_new_session`).
- `Engine.new_session` always inserts a row, so repeated ⌘N stacks empty
  "(untitled)" sessions.
- Subagent sessions render nested in the sidebar even though they are also
  reachable via transcript task cards and crew-stage sprites.

## Design

### 1. Sidebar (`session_list.py`)

Keep the widget's public API (signals + `populate(projects,
sessions_by_project, running, current, agent_colors, folders_by_project)`)
so `main_window` churn stays small. Internally render:

```
[+ New session]
[search]
▸ Projects            ← section shown only when >1 project exists
    📁 Alpha           (folders nested beneath, as today)
    📁 Beta
Today                  ← non-selectable bucket headers
    ● fix login bug · crew        (running, bold)
    add dark mode · webapp · 2h
Yesterday
    …
This week / This month / Older
```

- Sessions from **all** projects are flattened, sorted by `updated_at`
  descending, and bucketed by local-midnight boundaries: Today, Yesterday,
  This week (7 days), This month (30 days), Older.
- Session row: title, `· <dir name>` subtitle, `· <relative time>`; running
  sessions keep the green dot + bold treatment. Tooltip gains the project
  name and full directory.
- **Subagent children are not rendered.** Navigation to them stays via
  transcript task cards and crew-stage sprites (already wired to
  `_open_child`).
- Project rows keep their context menu (new session here, rename, add
  folder, delete) and remain drag-drop targets for moving sessions.
- Search filter adapts: bucket/section headers hide when no child matches.

### 2. New-session flow (`main_window.py`)

- **⌘N / "+ New session" is instant** — no modal. Directory resolution:
  current session's directory → `last_project` config → engine project dir.
- New menu action **"New Session in Folder…" (⇧⌘N)** keeps the old
  explicit-picker behavior.
- Sidebar project context menu "New session in this project" uses the
  project's path directly (no modal).

### 3. Empty-session reuse (`facade.py`, `store.py`)

`Engine.new_session` first looks for an existing top-level, non-archived,
untitled session in the same project + directory with **zero messages**; if
found it returns that session instead of inserting a new row. New store
helper `message_count(session_id)` supports the check. This mirrors Claude
Desktop's lazy-chat behavior without threading a "draft" state through the
runner.

### 4. Unchanged

- Launch still restores the most recent session (Claude Code desktop also
  resumes context).
- Delete/close/rename/move-to-project flows, multi-select delete, project
  folders — all preserved.

## Testing

- Update `tests/app/test_ui_smoke.py` session-list tests for the new
  structure (buckets at top level, projects section, children hidden).
- New engine test: `new_session` twice in the same directory returns the
  same id until a message lands.
- Full suite: `QT_QPA_PLATFORM=offscreen .venv/bin/pytest`.
