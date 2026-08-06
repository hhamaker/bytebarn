# Saved hosts and the slide-out nav rail

**Date:** 2026-08-05
**Branch:** `ui-redesign`
**Goal:** store host/connection information in the app (Termius-style saved
SSH hosts you can open as terminals), and let the nav rail slide out to show
what its icons mean.

## Hosts

### Storage — `bytebarn/engine/hosts.py` (new, Qt-free)

- `Host` dataclass: `id`, `name`, `hostname`, `username`, `port` (default 22),
  `identity_file` (path, optional), `created_at`.
- **No passwords are stored** — connections authenticate via SSH keys or the
  agent, matching the project rule that secrets never live in config files.
- `HostStore(path)` — JSON list at `~/.bytebarn/hosts.json` (global, not
  per-project). `list() / add(...) / update(host) / remove(id)`; writes are
  atomic (tmp file + rename). Malformed file → empty list, never a crash.
- `ssh_argv(host) -> list[str]` builds the command: `ssh`, `-p <port>` when
  not 22, `-i <identity>` when set, then `user@hostname` (or bare hostname).
- `Engine` exposes `engine.hosts` (store rooted in the global dir).

### UI — Terminal Manager left column

The left column becomes two stacked sections: **Hosts** (saved connections,
with a `+ Host` button) above **Terminals** (the existing list). Host rows
show `⇄ name (user@hostname)`.

- Double-click / context-menu **Connect** spawns `ssh …` in a terminal pane —
  the same tiling flow as + Shell (never displaces; splits off a new tile).
  The terminal takes the host's name as its title.
- Context menu: Connect, Edit…, Delete.
- `bytebarn/app/host_dialog.py` (new): modal form — Name, Host, User, Port,
  Identity file (with Browse…). Saving adds/updates through the store.
- `pty_session.spawn_shell` gains `command: list[str] | None` — when given,
  it execs that argv instead of the login shell.

## Slide-out nav rail

- A `»` toggle at the bottom of the rail expands it (56 px → 172 px) with a
  width animation; expanded buttons show `glyph  label` left-aligned
  (Projects, Chat, Code, Terminal / Agents, Providers, Settings), and the
  toggle flips to `«`.
- State persists in global config under `ui.rail_expanded`; the window
  restores it on startup.
- `NavRail.set_expanded(bool)` + `expanded_toggled` signal; MainWindow owns
  persistence (via `patch_config_file`).

## Error handling

- Connect failures surface in the terminal pane itself (ssh's own stderr) —
  same path as a failed shell spawn.
- Deleting a host never touches its open terminals.
- Unreadable hosts.json → empty host list; the next save rewrites it.

## Testing

- `tests/engine/test_hosts.py` — CRUD + persistence roundtrip, atomicity
  (file parses after every op), `ssh_argv` variants (port/identity defaults),
  malformed-file fallback, no password field exists.
- `tests/app/test_hosts_ui.py` — panel lists store hosts; connect spawns the
  ssh argv (spawn monkeypatched); dialog round-trips fields to a Host.
- `tests/app/test_nav_rail.py` — expand shows labels and widens, collapse
  restores, signal fires; config restore path.
