"""run_remote + the ssh tool (spec: 2026-08-05-host-passwords-and-agent-ssh)."""

import os
import stat

import pytest

from bytebarn.engine.hosts import KEY_AUTH, PASSWORD_AUTH, Host
from bytebarn.engine.remote import run_remote


def _script(tmp_path, name: str, body: str) -> str:
    path = tmp_path / name
    path.write_text("#!/bin/sh\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


def _host(auth=KEY_AUTH) -> Host:
    return Host(id="h1", name="box", hostname="example.com", auth_type=auth)


async def test_runs_and_captures_output(tmp_path):
    fake = _script(tmp_path, "fake_ssh", 'echo "hello from remote"\n')
    code, output = await run_remote(_host(), "whoami", argv=[fake])
    assert code == 0
    assert "hello from remote" in output


async def test_non_zero_exit_is_reported(tmp_path):
    fake = _script(tmp_path, "fail_ssh", 'echo "boom" >&2\nexit 7\n')
    code, output = await run_remote(_host(), "whoami", argv=[fake])
    assert code == 7
    assert "boom" in output


async def test_password_prompt_is_answered(tmp_path):
    # stands in for ssh: prompts, reads the answer, echoes what it got
    fake = _script(tmp_path, "pw_ssh", (
        'printf "user@example.com\'s password: "\n'
        "read secret\n"
        'echo "\\ngot:[$secret]"\n'
    ))
    code, output = await run_remote(
        _host(PASSWORD_AUTH), "uptime", password="hunter2", argv=[fake])
    assert code == 0
    assert "got:[hunter2]" in output


async def test_no_password_supplied_leaves_prompt_unanswered(tmp_path):
    fake = _script(tmp_path, "pw2_ssh", (
        'printf "password: "\nread secret\necho "\\ngot:[$secret]"\n'))
    code, output = await run_remote(
        _host(PASSWORD_AUTH), "uptime", password=None, argv=[fake], timeout=2)
    # nothing typed: the stand-in reads EOF/blank, we never leak a secret
    assert "hunter2" not in output
    assert code in (0, 124)


async def test_prompt_split_across_reads_is_still_answered(tmp_path):
    """PTY reads split anywhere — half a prompt must not defeat the matcher."""
    fake = _script(tmp_path, "split_ssh", (
        'printf "hunter@10.0.0.1\'s passwo"\n'
        "sleep 0.2\n"
        'printf "rd: "\n'
        "read secret\n"
        'echo "\\ngot:[$secret]"\n'
    ))
    code, output = await run_remote(
        _host(PASSWORD_AUTH), "uptime", password="hunter2", argv=[fake])
    assert code == 0
    assert "got:[hunter2]" in output


async def test_permission_denied_explains_password_hosts(tmp_path):
    fake = _script(tmp_path, "denied_ssh", (
        'echo "hunter@10.0.0.1: Permission denied (publickey,password)." >&2\n'
        "exit 255\n"))
    _code, output = await run_remote(
        _host(PASSWORD_AUTH), "uptime", password="wrong", argv=[fake])
    assert "rejected the saved password" in output
    assert "PasswordAuthentication yes" in output


async def test_permission_denied_explains_key_hosts(tmp_path):
    fake = _script(tmp_path, "denied2_ssh", (
        'echo "Permission denied (publickey)." >&2\nexit 255\n'))
    _code, output = await run_remote(_host(KEY_AUTH), "uptime", argv=[fake])
    assert "switch this host to Password" in output


async def test_unknown_host_key_gets_actionable_hint(tmp_path):
    fake = _script(tmp_path, "hostkey_ssh", (
        'echo "Host key verification failed." >&2\nexit 255\n'))
    code, output = await run_remote(_host(), "uptime", argv=[fake])
    assert code == 255
    assert "Terminal view" in output and "Trust new host key" in output


async def test_changed_host_key_warns_without_offering_a_fix(tmp_path):
    fake = _script(tmp_path, "changed_ssh", (
        'echo "@@@ REMOTE HOST IDENTIFICATION HAS CHANGED! @@@" >&2\nexit 255\n'))
    _code, output = await run_remote(_host(), "uptime", argv=[fake])
    assert "intercepted" in output
    assert "will not do it for you" in output


def test_accept_new_key_is_opt_in():
    from bytebarn.engine.hosts import ssh_argv

    default_argv = " ".join(ssh_argv(_host(), "uptime", batch=True))
    assert "StrictHostKeyChecking" not in default_argv

    trusting = Host(id="h2", name="box", hostname="example.com",
                    accept_new_key=True)
    argv = " ".join(ssh_argv(trusting, "uptime", batch=True))
    assert "StrictHostKeyChecking=accept-new" in argv
    # never the blanket "no" — a changed key must still fail
    assert "StrictHostKeyChecking=no" not in argv


def test_accept_new_key_round_trips(tmp_path):
    from bytebarn.engine.hosts import HostStore

    store = HostStore(tmp_path / "hosts.json")
    host = store.add(name="box", hostname="example.com", accept_new_key=True)
    assert HostStore(tmp_path / "hosts.json").get(host.id).accept_new_key is True


async def test_timeout_kills_and_reports(tmp_path):
    fake = _script(tmp_path, "slow_ssh", "sleep 30\n")
    code, output = await run_remote(
        _host(), "sleep 30", argv=[fake], timeout=0.4)
    assert code == 124
    assert "timed out" in output


async def test_password_never_appears_in_argv():
    from bytebarn.engine.hosts import ssh_argv

    host = _host(PASSWORD_AUTH)
    argv = ssh_argv(host, "uptime")
    assert "hunter2" not in " ".join(argv)
    assert "NumberOfPasswordPrompts=1" in " ".join(argv)
    # agent (batch) runs fail fast; interactive connects can still prompt for
    # an encrypted key's passphrase
    assert "BatchMode=yes" in " ".join(ssh_argv(_host(KEY_AUTH), "up", batch=True))
    assert "BatchMode" not in " ".join(ssh_argv(_host(KEY_AUTH)))


# -- the tool ---------------------------------------------------------------


class _Ctx:
    def __init__(self, hosts=None, password=None):
        self.hosts = hosts
        self.host_password = (lambda _id: password) if password is not None else None


class _Store:
    def __init__(self, hosts):
        self._hosts = hosts

    def list(self):
        return list(self._hosts)

    def by_name(self, name):
        return next((h for h in self._hosts
                     if h.id == name or h.name.lower() == name.lower()), None)


async def test_tool_unknown_host_lists_known():
    from bytebarn.engine.tools.ssh import SshParams, SshTool

    tool = SshTool()
    result = await tool.execute(
        SshParams(host="nope", command="uptime"),
        _Ctx(hosts=_Store([_host()])))
    assert result.is_error
    assert "box" in result.output


async def test_tool_password_host_without_password_errors():
    from bytebarn.engine.tools.ssh import SshParams, SshTool

    tool = SshTool()
    result = await tool.execute(
        SshParams(host="box", command="uptime"),
        _Ctx(hosts=_Store([_host(PASSWORD_AUTH)])))
    assert result.is_error
    assert "no password is saved" in result.output


def test_tool_permission_arg_is_host_and_command():
    from bytebarn.engine.tools.ssh import SshParams, SshTool

    arg = SshTool().permission_arg(
        SshParams(host="staging", command="systemctl status nginx"))
    assert arg == "staging: systemctl status nginx"


@pytest.mark.parametrize("mode,expected", [
    ("ask", "ask"), ("safe", "deny"), ("plan", "deny"), ("full", "allow"),
])
def test_policy_gates_ssh(mode, expected):
    from bytebarn.engine.permissions import PermissionPolicy

    policy = PermissionPolicy(session_mode=mode)
    assert policy.resolve("ssh", "box: rm -rf /") == expected


def test_policy_allow_pattern_per_host():
    from bytebarn.engine.permissions import PermissionPolicy

    policy = PermissionPolicy(
        {"ssh": {"default": "ask", "allow": ["staging: systemctl status*"]}})
    assert policy.resolve("ssh", "staging: systemctl status nginx") == "allow"
    assert policy.resolve("ssh", "prod: systemctl status nginx") == "ask"


def test_ssh_tool_is_registered_and_serialized():
    from bytebarn.engine.tools.registry import ALL_TOOLS, WRITE_TOOLS

    assert "ssh" in ALL_TOOLS
    assert "ssh" in WRITE_TOOLS


def test_engine_stores_password_out_of_hosts_file(tmp_path):
    from bytebarn.engine.facade import Engine

    gdir = tmp_path / "g"
    gdir.mkdir()
    engine = Engine(tmp_path, db_path=tmp_path / "db.sqlite", global_dir=gdir)
    host = engine.hosts.add(name="box", hostname="example.com",
                            auth_type=PASSWORD_AUTH)
    engine.set_host_password(host.id, "hunter2")

    assert engine.host_password(host.id) == "hunter2"
    assert "hunter2" not in (gdir / "hosts.json").read_text()
    auth_file = gdir / "auth.json"
    assert "hunter2" in auth_file.read_text()
    assert stat.S_IMODE(os.stat(auth_file).st_mode) == 0o600

    engine.forget_host(host.id)
    assert engine.host_password(host.id) is None
    assert engine.hosts.get(host.id) is None
