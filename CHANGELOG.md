# Changelog

All notable changes to ByteBarn are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
