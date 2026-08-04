# Isolated sessions — design

**Date:** 2026-08-04
**Status:** approved, not yet implemented

## Problem

Running ByteBarn against the ByteBarn repo means the agent edits the source of
the process that is running it. Python has already imported `bytebarn/**.py`,
so live source edits do not corrupt the running process, but three things do
bite:

1. The watchfiles hot-reloaders watch `<project>/.bytebarn/agent`, `skills`,
   `command`, and `config.json`. Agent edits to those hit the live process
   mid-run.
2. Quitting to test a change lands on possibly-broken code, leaving no working
   ByteBarn to fix ByteBarn with.
3. A `/goal` run fans out subagents that merge into the live working tree as
   each one finishes, so the tree mutates under you while you read it.

Goal: run `/goal` on the ByteBarn repo with the live working tree untouched
for the duration of the run.

## Scope

In scope: working-tree isolation. The isolated session shares the same
`crew.db` and the same global/project config as every other session, so it
still appears in the sidebar and streams live in the UI.

Out of scope: separate `BYTEBARN_HOME`, suspending the hot-reloaders,
auto-merge UI. Hot-reloaders need no special handling — they watch paths under
the live project dir, and an isolated session never writes there.

## Architecture

Top-level sessions can own a git worktree, the same way subagents already do
(`engine/worktree.py`, `WorktreeManager`).

The key existing behaviour is `Engine.run_subagent` (facade.py:805):

```python
parent_cwd = Path(parent.directory) if parent.directory else self.project_dir
```

Subagent worktrees are created from `parent_cwd` and applied back to it. So if
the orchestrator's own session has `directory` set to a worktree, the whole
delegation tree nests inside that worktree and merges back into it. The live
repo is never a merge target. `run_subagent` needs no changes.

Session worktrees are **never auto-applied and never auto-removed**. Getting
work out is a manual git operation on the branch (see "Merge-back" below).

`session.directory` is a persisted DB column (`store.py:40`), so isolation
survives an app restart even though `WorktreeManager._active` is in-memory.
Nothing needs to reconstruct manager state on startup: creation is the only
lifecycle event.

## Components

### `engine/worktree.py`

- `WorktreeManager.create(...)` gains two keyword params:
  - `track: bool = True` — when `False`, the worktree is returned but not
    stored in `self._active`. Session worktrees pass `track=False` so
    `apply_and_remove` and `remove` can never reach them.
  - `branch: str | None = None` — explicit branch name, defaulting to today's
    `f"bytebarn/{session_id[:12]}"`. Session worktrees pass
    `f"bytebarn/session-{session_id[:8]}"`, which reads distinctly in
    `git branch` and does not collide with subagent branches.
- New `async def dirty_files(root: Path) -> list[str]` — modified, staged, and
  untracked paths relative to the git root, for the pre-flight warning.

`_force_remove` already only deletes branches starting with `bytebarn/`, so the
session branch prefix stays consistent with that guard. Nothing calls it for
untracked worktrees regardless.

### `engine/facade.py`

- `Engine.new_session(..., isolated: bool = False)`. When `isolated` and
  `_worktree_enabled()`: after `store.create_session`, call
  `worktrees.create(session.id, self.project_dir, project_key, track=False,
  branch=...)`, then `store.update_session(session.id, directory=str(wt.path))`
  and re-read the session.
- New `async def repo_dirty(self) -> list[str]` — delegates to
  `worktree.dirty_files(self.project_dir)`, returns `[]` for non-git dirs.
- `run_subagent` unchanged. Session worktrees are untracked, so the existing
  `self.worktrees.get(child.id)` guards never see them.

### `bytebarn/app/`

- An "Isolated" toggle on the new-session control (`session_list.py` new
  button area), threaded through `main_window._prompt_new_session` →
  `_new_session(isolated=...)` → `engine.new_session(isolated=...)`.
- Pre-flight: when the toggle is on and `repo_dirty()` is non-empty, a dialog
  lists the uncommitted files and states that the worktree starts from HEAD, so
  those changes will not be present. Continue / Cancel.
- Sidebar badge on sessions whose `directory` differs from the project dir.
- The first part in an isolated session's transcript is a system note carrying
  the branch name and worktree path, so `git diff main..bytebarn/session-<id>`
  is copy-pasteable without hunting for it.

## Data flow

```
toggle Isolated → new_session(isolated=True)
  → git worktree add -b bytebarn/session-<sid8> \
        ~/.bytebarn/worktrees/<project_key>/<sid> HEAD
  → session.directory = worktree path        [persisted to store]
/goal → orchestrator runs with cwd = worktree
  → subagents get nested worktrees off the session worktree,
    apply back into the session worktree
run ends → worktree and branch remain on disk
  → user merges / cherry-picks / deletes in git
```

The live working tree is unchanged throughout, so the hot-reloaders never fire
and the running process never observes the run.

## Seeding

The worktree is a clean checkout of HEAD, matching how subagent worktrees
already work. Uncommitted changes in the live repo are **not** copied in; the
pre-flight dialog warns when any exist. This keeps the worktree's base state
reproducible from git — the agent's view of the code is always exactly one
commit.

## Merge-back

None, by design. The worktree and its `bytebarn/session-<sid8>` branch persist
after the run. The user merges, cherry-picks, or deletes with git. ByteBarn's
only obligation is to surface the branch name and worktree path.

Cleaning up stale session worktrees is a manual `git worktree remove` for now.

## Error handling

- **Non-git project, or a repo with no commits.** `WorktreeManager.create`
  already returns `None` for both. The session is created unisolated and the UI
  states that isolation was unavailable and why. Not a hard failure — a session
  is still produced.
- **`git worktree add` fails.** Same fallback path; the git error text is
  surfaced.
- **Worktree path missing on a later turn** (user deleted it by hand). Tool
  cwd resolution falls back to the project dir and emits one warning, rather
  than every tool call failing with a cwd error.

## Testing

Engine tests use `tmp_path` git repos; no network.

- **Load-bearing:** a subagent spawned from an isolated session writes a file;
  assert it lands under the session worktree and that the live repo tree is
  byte-identical to its pre-run state.
- An isolated session in a git project gets `directory != ""` and a worktree
  that exists on disk.
- `isolated=True` in a non-git `tmp_path` project is a silent no-op:
  `session.directory == ""`, session usable.
- The session worktree still exists after a child subagent's
  `apply_and_remove` — i.e. `track=False` holds.
- `repo_dirty` reports both modified and untracked files, and returns `[]` for
  a non-git dir.
- UI smoke test (offscreen): the toggle exists and threads `isolated` through
  to `engine.new_session`.
