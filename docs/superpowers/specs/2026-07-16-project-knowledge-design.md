# Claude-style projects: instructions + knowledge assets

Date: 2026-07-16
Status: approved (autonomous /goal run)

## How Claude handles projects (target)

A project is a container with three things: **custom instructions**, a
**knowledge base of uploaded assets**, and the **chats that belong to it**.
Instructions and knowledge are injected into every conversation in the
project. There is no "folders" concept.

## Crew today (gap)

- `project` = name + path row; `project_folder` rows decorate the sidebar and
  are never consumed by the engine — no instructions, no knowledge, nothing
  reaches the model.
- No way to attach an asset to a project.

## Design

### Store (`store.py`)

- `project.instructions` TEXT column (migration, default '').
- New table `project_asset(project_id, path, name, added_at)`; path points at
  a copy under `<global_dir>/assets/<project_id>/`.
- Methods: `get_project`, `set_project_instructions`,
  `add_project_asset`, `remove_project_asset`, `list_project_assets`.
- `delete_project` cascades asset rows.

### Engine facade (`facade.py`)

- `add_project_asset(project_id, src)` — copies the file into
  `global_dir/assets/<project_id>/<name>` (deduping name collisions), inserts
  the row.
- `remove_project_asset(project_id, path)` — removes row + copied file.
- `project_knowledge(project_id)` → `(instructions, [ProjectAsset])` for the
  runner.

### Runner injection (`runner.py`)

`build_system_prompt` gains `project_instructions` and `assets`:

```
<project-instructions source="project">…user text…</project-instructions>
<project-knowledge file="notes.md">…inlined text ≤ 32 KB…</project-knowledge>
<project-knowledge file="big.bin" path="/…">[not inlined — read with tools]</project-knowledge>
```

Text files ≤ 32 KB inline; binary/oversized assets are listed by path so the
agent can read them with tools. Injected for every session in the project
(subagents included — they share `session.project_id`).

### UI

- **Project dialog** (`project_dialog.py`): name, instructions editor,
  assets list with Add/Remove, session count. Opened from the sidebar
  project row (double-click or context-menu "Project settings…").
- Sidebar: folder rows and "Add folder…" disappear; projects are flat rows
  under the Projects section. (Store folder methods stay — data preserved,
  UI no longer surfaces them.)

## Testing

- Store: instructions roundtrip, asset add/list/remove, delete cascade.
- Facade: asset file copied into global assets dir; removal deletes copy.
- Runner: `build_system_prompt` includes project instructions and inlines a
  small text asset, lists an oversized one by path.
- UI smoke: dialog constructs and round-trips instructions.
