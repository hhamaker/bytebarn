# Host password auth and agent access to saved connections

**Date:** 2026-08-05
**Branch:** `ui-redesign`
**Goal:** let a saved host authenticate with a username/password instead of an
SSH key, and let agents run commands on saved hosts — behind the existing
permission system.

## Password auth for hosts

### Where the password lives

`hosts.json` stays secret-free. It gains one field, `auth_type`
(`"key"` | `"password"`, default `"key"`). The password itself goes in the
existing `AuthStore` (`~/.bytebarn/auth.json`, chmod 0600 — the same file
provider API keys use) under the key `host:<host id>`:

```json
{"type": "password", "password": "…"}
```

`HostStore` never sees the password. Two small helpers on `Engine`:
`set_host_password(host_id, password)` and `host_password(host_id)`.
Deleting a host deletes its record.

### How the password is delivered

No `sshpass` dependency, and the password is never placed on a command line
(where `ps` would expose it). Both paths spawn `ssh` on a PTY and answer its
prompt:

- **Terminal connect** (`app/terminal_panel.py`): after spawning, a one-shot
  watcher scans the first output for a `password:` / `passphrase` prompt and
  writes the password once, then detaches. Key-auth hosts keep today's path.
- **Agent tool** (`engine/remote.py`): same idea in the engine, without Qt.

`ssh_argv` gains flags that make automation predictable:
`-o BatchMode=yes` for key auth (fail instead of hanging on a prompt), and
for password auth `-o NumberOfPasswordPrompts=1 -o PreferredAuthentications=
password,keyboard-interactive`. Host-key checking is left at the user's ssh
defaults — ByteBarn does not weaken it.

### UI

`HostDialog` gains an **Auth** combo (SSH key / Password). Choosing SSH key
shows the key-file row; choosing Password shows a masked password field
(`QLineEdit.Password`). Saving writes the password through the engine helper,
never into `hosts.json`. An existing password shows as a placeholder
("saved — leave blank to keep") so editing other fields does not clear it.

## Agent access: the `ssh` tool

`engine/remote.py` — `run_remote(host, command, password=None, timeout=…)`
runs one non-interactive command over ssh on a PTY and returns
`(exit_code, output)`. Password prompts are answered from the PTY; nothing
sensitive is logged.

`engine/tools/ssh.py` — `SshTool`:

- Params: `host` (saved host name or id), `command`, `timeout`
  (default 120 s, max 600 s).
- Resolves the host through `ctx.hosts`; unknown host → error result listing
  the saved host names, so the agent can retry with a valid one.
- `permission_arg` is `"<host name>: <command>"`, so config rules can allow
  or deny per host and per command, e.g.
  `"permission": {"ssh": {"default": "ask", "allow": ["staging: systemctl status*"]}}`.
- Registered in `ALL_TOOLS` and in `WRITE_TOOLS` (serialized like bash).

### Permission behaviour

- Default is **ask** — every remote command prompts unless the user allows it.
- **Safe** mode: denied (added to `_DEFAULTS`, which Safe hard-denies).
- **Plan** mode: denied — it is a mutation-capable tool and Plan's fence is
  hard, so no read-only carve-out.
- **Full-auto**: allowed, like every other tool in that mode.
- Only agents whose `tools` map includes `ssh` (or agents with no map, which
  get everything) can see it; read-only agents such as `explore` and `chat`
  do not, because their maps are explicit.

The permission dialog already shows the tool name and its argument, so the
user sees exactly which host and command are being requested.

## Error handling

- Unknown host id/name → error result naming the known hosts.
- Password auth with no stored password → error telling the user to set one
  in the host dialog.
- ssh not installed / connection refused → ssh's own stderr, plus the exit
  code, come back as the tool result (marked `is_error`).
- Timeout kills the process group and reports what was captured.

## Testing

- `tests/engine/test_hosts.py` — `auth_type` round-trips; `ssh_argv` carries
  the right `-o` flags per auth type; no password field on `Host`.
- `tests/engine/test_remote.py` — `run_remote` against a local stand-in
  command (an `ssh` shim script on PATH) covering success, non-zero exit,
  and password-prompt answering; timeout path.
- `tests/engine/test_ssh_tool.py` — unknown host error; `permission_arg`
  format; policy resolution: ask by default, deny in Safe and Plan, allow in
  Full-auto and via an allow pattern.
- `tests/app/test_hosts_ui.py` — dialog auth switching shows/hides the right
  rows; saving a password stores it in AuthStore (not hosts.json) and the
  file has 0600 perms.
