"""OS-level confinement for agent shell commands.

macOS: wrap with ``sandbox-exec`` and a seatbelt profile that allows writes
only under configured roots (project + temp dirs) and optionally denies
network. Other platforms degrade to a best-effort path check or no-op with
``backend`` reporting the mode.

Config (``config.model_extra`` / config.json)::

    "sandbox": {
      "enabled": true,          // master switch (default: true for Full-auto)
      "always": false,          // if true, sandboxed even in Ask mode
      "allow_network": false,
      "extra_writable": []      // extra absolute paths
    }
"""

from __future__ import annotations

import asyncio
import os
import platform
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass
class SandboxConfig:
    enabled: bool = True
    always: bool = False
    allow_network: bool = False
    extra_writable: list[str] | None = None

    @classmethod
    def from_config(cls, model_extra: dict | None) -> "SandboxConfig":
        raw = (model_extra or {}).get("sandbox") or {}
        if not isinstance(raw, dict):
            return cls()
        return cls(
            enabled=bool(raw.get("enabled", True)),
            always=bool(raw.get("always", False)),
            allow_network=bool(raw.get("allow_network", False)),
            extra_writable=list(raw.get("extra_writable") or []),
        )


def should_sandbox(session_mode: str, conf: SandboxConfig) -> bool:
    """Whether a bash call should run under OS sandbox given mode + config."""
    if not conf.enabled:
        return False
    if conf.always:
        return True
    from .permissions import FULL_AUTO
    return session_mode == FULL_AUTO


def backend_name() -> str:
    system = platform.system()
    if system == "Darwin" and Path("/usr/bin/sandbox-exec").is_file():
        return "macos-seatbelt"
    return "none"


def _writable_roots(project_dir: Path, conf: SandboxConfig) -> list[Path]:
    # Intentionally narrow: do NOT blanket-allow /var/folders (pytest and many
    # "outside project" paths live there). Only the project, the process temp
    # dir, classic /tmp, and the ByteBarn home are writable by default.
    roots = [
        project_dir.resolve(),
        Path(tempfile.gettempdir()).resolve(),
        Path("/tmp").resolve() if Path("/tmp").exists() else None,
        Path("/private/tmp").resolve() if Path("/private/tmp").exists() else None,
        Path.home().resolve() / ".bytebarn",
    ]
    for extra in conf.extra_writable or []:
        try:
            roots.append(Path(extra).expanduser().resolve())
        except OSError:
            pass
    # unique, existing-or-createable
    out: list[Path] = []
    seen: set[str] = set()
    for r in roots:
        if r is None:
            continue
        key = str(r)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out


def seatbelt_profile(project_dir: Path, conf: SandboxConfig) -> str:
    """Generate a macOS seatbelt (sandbox-exec) profile string."""
    roots = _writable_roots(project_dir, conf)
    write_rules = "\n".join(
        f'(allow file-write* (subpath "{r}"))' for r in roots
    )
    # allow creating files under project even if path is new
    net = "(allow network*)" if conf.allow_network else "(deny network*)"
    return f"""(version 1)
(deny default)
(allow process-exec)
(allow process-fork)
(allow signal)
(allow sysctl-read)
(allow mach-lookup)
(allow mach-register)
(allow ipc-posix-shm)
(allow ipc-posix-sem)
(allow system-socket)
(allow file-read*)
(allow file-write-data (literal "/dev/null"))
(allow file-write-data (literal "/dev/tty"))
(allow file-ioctl)
(allow file-write-setugid)
{write_rules}
{net}
"""


@dataclass
class SandboxRun:
    """How to invoke a shell command under the sandbox."""

    argv: list[str]           # full argv for create_subprocess_exec
    profile_path: Path | None  # temp file to delete after run
    backend: str


def prepare_sandboxed_command(
    command: str,
    cwd: Path,
    conf: SandboxConfig,
    profile_dir: Path | None = None,
) -> SandboxRun:
    """Build argv for a sandboxed shell. Falls back to plain /bin/sh -c."""
    backend = backend_name()
    if backend != "macos-seatbelt":
        return SandboxRun(
            argv=["/bin/sh", "-c", command],
            profile_path=None,
            backend="none",
        )
    profile = seatbelt_profile(cwd, conf)
    directory = profile_dir or Path(tempfile.gettempdir())
    directory.mkdir(parents=True, exist_ok=True)
    profile_path = directory / f"bytebarn-sb-{os.getpid()}-{id(command) & 0xffff:x}.sb"
    profile_path.write_text(profile)
    # sandbox-exec -f profile /bin/sh -c 'command'
    return SandboxRun(
        argv=["/usr/bin/sandbox-exec", "-f", str(profile_path), "/bin/sh", "-c", command],
        profile_path=profile_path,
        backend=backend,
    )


async def run_command(
    command: str,
    cwd: Path,
    *,
    conf: SandboxConfig | None = None,
    sandbox: bool = False,
    env: dict[str, str] | None = None,
    timeout: float = 120.0,
    abort: asyncio.Event | None = None,
) -> tuple[int, str, str]:
    """Run a shell command; optionally under OS sandbox.

    Returns (exit_code, output, backend_used).
    """
    conf = conf or SandboxConfig()
    profile_path = None
    try:
        if sandbox:
            prepared = prepare_sandboxed_command(command, cwd, conf)
            profile_path = prepared.profile_path
            argv = prepared.argv
            backend = prepared.backend
        else:
            argv = ["/bin/sh", "-c", command]
            backend = "none"

        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
            env=env or os.environ.copy(),
        )

        async def _wait() -> bytes:
            out, _ = await proc.communicate()
            return out

        wait_task = asyncio.ensure_future(_wait())
        abort_task = asyncio.ensure_future(abort.wait()) if abort else None
        pending = {wait_task} | ({abort_task} if abort_task else set())
        try:
            done, _ = await asyncio.wait(
                pending, timeout=timeout, return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            if abort_task:
                abort_task.cancel()

        if wait_task not in done:
            try:
                os.killpg(os.getpgid(proc.pid), 9)
            except (ProcessLookupError, PermissionError, OSError):
                pass
            output = (await wait_task).decode(errors="replace")
            reason = "aborted" if (abort_task and abort_task in done) else f"timed out after {timeout}s"
            return 1, f"{output}\n[command {reason}]", backend

        output = wait_task.result().decode(errors="replace")
        code = proc.returncode if proc.returncode is not None else 1
        return code, output, backend
    finally:
        if profile_path is not None:
            try:
                profile_path.unlink(missing_ok=True)
            except OSError:
                pass
