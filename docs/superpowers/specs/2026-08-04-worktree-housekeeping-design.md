# Worktree housekeeping — design

**Date:** 2026-08-04
**Status:** approved, not yet implemented

## Problem

Isolated sessions own a `git worktree` and a `bytebarn/session-<id8>` branch,
deliberately never auto-removed so the user can merge the work in git
(`docs/superpowers/specs/2026-08-04-isolated-sessions-design.md`).

Nothing ever removes them. `Engine.delete_session` drops the session row, its
children, and their history, but leaves the worktree — a full checkout of the
repo — and the branch on disk. After the delete, the branch name exists only in
`git worktree list`; the app offers no listing and no removal. The toggle that
creates these is sticky, so once someone starts working this way the checkouts
accumulate steadily.

Deleting a worktree can destroy unmerged agent output, so removal cannot be
automatic.

## Scope

In scope: a prompt when an isolated session is **deleted**, offering to remove
its worktree and branch, with enough information to decide.

Out of scope, with reasons:

- **A worktree manager panel.** It would also reclaim worktrees orphaned by
  deletes that already happened, but it is a new surface to build and maintain,
  and fixing the leak at its source stops the problem growing. Revisit if a
  backlog proves annoying in practice.
- **Archive.** `close_session` only sets `archived=1`; the session stays usable
  and can be reopened and run. Pulling its worktree would strand it. Disk from
  archived sessions is reclaimed when they are eventually deleted.
- **Disk size in the dialog.** The decision is "will I lose work", not "how many
  megabytes". Size means a full tree walk per worktree and changes nobody's
  answer.

## Deciding what "merged" means

Not a comparison against `main`. The question that matters is *would deleting
this branch lose commits*, which is reachability from any other ref.

The obvious spelling is **wrong**, and was in an earlier draft of this spec:

```
# WRONG — returns 0 for every isolated session
git rev-list --count refs/heads/<branch> --not --exclude=refs/heads/<branch> --all
```

`--all` means "all refs in refs/, **along with HEAD**", and `--exclude` filters
refs by glob without touching HEAD. An isolated session's branch is checked out
in its worktree, so a HEAD somewhere still points at it and every commit looks
reachable. Verified against a real repo: with the branch checked out in a linked
worktree and two commits on it, this returns `0` — it would report "nothing to
lose" in exactly the case the dialog exists for.

Enumerate the other refs explicitly instead:

```
git for-each-ref --format='%(refname)'          # then drop refs/heads/<branch>
git rev-list --count refs/heads/<branch> --not <every other ref>
```

No glob semantics and no HEAD surprises. When the branch is the repository's
only ref the `--not` list is empty, which git accepts and which correctly counts
every commit on the branch.

This is right regardless of what the default branch is called and regardless of
whether the work landed by merge, rebase, squash, or cherry-pick — all of which
leave the original commits unreachable only if nothing else references them.

A count of zero means removal is lossless. A non-zero count is exactly the
number of commits that would become unreachable.

## Components

### `engine/worktree.py`

- `async def unmerged_count(git_root: Path, branch: str) -> int` — the count
  above. Returns 0 when git fails or the branch does not exist: the caller is
  deciding whether to warn, and a warning we cannot substantiate is noise.
- `async def discard(git_root: Path, path: Path, branch: str) -> None` — public
  counterpart to `_force_remove`, which removes the worktree (falling back to
  `shutil.rmtree` + `worktree prune`) and deletes the branch when it starts with
  `bytebarn/`. `Engine._isolate_session` currently calls `_force_remove`
  directly, a private-access minor flagged in the isolated-sessions review;
  it moves to `discard`, retiring that finding.

### `engine/facade.py`

- `async def session_worktree_info(session_id: str) -> dict | None` — `None` for
  a session with no `worktree_branch`. Otherwise `{"branch", "path",
  "unmerged", "exists"}`, where `exists` reports whether the directory is still
  there.
- `delete_session(session_id: str, discard_worktree: bool = False)` — the flag is
  opt-in so every existing caller keeps today's behaviour. When true and the
  session has a worktree, discard it before removing the row.

### `app/main_window.py`

`_delete_session` and the multi-select `_delete_sessions` collect
`session_worktree_info` for each session in the selection, drop the `None`s, and
if any remain show **one** dialog regardless of count:

> Delete 1 session?
> `bytebarn/session-abc12345` has 3 commits not on any other branch.
> [Remove worktrees] [Keep worktrees] [Cancel]

For a multi-session selection the dialog lists one line per isolated session
with its own count. One choice applies to the whole selection: a per-session
prompt on a twelve-session delete is a worse failure than over-keeping. A
selection containing no isolated sessions keeps today's confirmation exactly.

The dialog opens through the existing `_ask_yes_no` pattern — via
`QTimer.singleShot(0, ...)` resolving a future — because `_delete_session` runs
as an asyncio task and a modal opened directly inside one makes qasync warn
about task re-entry. Three buttons rather than two, so this needs a sibling
helper rather than reuse.

**Deleting an isolated session therefore shows two dialogs in sequence.** The
existing "permanently delete?" confirmation lives in
`SessionList._confirm_delete`, which runs synchronously from the context-menu
handler and has no engine access, so it cannot learn anything about worktrees.
Merging the two would mean either pushing engine access into a dumb widget or
moving the delete confirmation into `MainWindow` — a larger change than this
feature justifies. They are also genuinely different questions: whether to
destroy the conversation, and whether to destroy the code. The second dialog
appears only for isolated sessions, so ordinary deletes are unchanged.

## Error handling

**Cleanup never blocks the delete.** Worktree already gone, git missing,
`worktree remove` failing — the session is deleted regardless, and the status
bar names what could not be removed, including the path, so the user can finish
by hand. Losing a session row because a `git worktree remove` failed would be
the worse outcome, and the worktree is recoverable by hand while the row is not.

`unmerged_count` failing is treated as zero (see above). `session_worktree_info`
on a session whose worktree directory has been deleted by hand returns
`exists: False`; the dialog then offers to clean up the branch alone.

## Testing

Engine tests use `tmp_path` git repos, following `tests/engine/test_worktree.py`.

- `unmerged_count` returns 0 for a branch whose commits are all reachable from
  another ref, and the exact count for divergent commits.
- `unmerged_count` returns 0 rather than raising for a branch that does not
  exist.
- `discard` removes both the worktree directory and the branch.
- `delete_session(discard_worktree=True)` removes the worktree; with `False`,
  and by default, the worktree survives.
- A delete whose worktree removal fails still removes the session row.

UI tests instantiate real widgets offscreen:

- The dialog appears only when the selection contains an isolated session.
- Remove / Keep / Cancel each produce the right outcome — including that Cancel
  deletes nothing at all.
