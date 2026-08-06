"""PATH recovery for GUI launches (Finder/Dock inherit launchd's bare PATH)."""

import os

import pytest

from bytebarn.engine import shell_path


def test_detects_the_launchd_path():
    assert shell_path.looks_minimal("/usr/bin:/bin:/usr/sbin:/sbin")
    assert shell_path.looks_minimal("")
    assert not shell_path.looks_minimal("/opt/homebrew/bin:/usr/bin:/bin")


def test_hydrate_adds_missing_dirs_and_keeps_existing_order(monkeypatch, tmp_path):
    tool_dir = tmp_path / "brew" / "bin"
    tool_dir.mkdir(parents=True)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(shell_path, "login_shell_path",
                        lambda timeout=4.0: f"/usr/bin:{tool_dir}")

    merged = shell_path.hydrate_path()
    entries = merged.split(os.pathsep)
    assert entries[:2] == ["/usr/bin", "/bin"]     # nothing reordered
    assert str(tool_dir) in entries
    assert entries.count("/usr/bin") == 1          # no duplicates
    assert os.environ["PATH"] == merged


def test_hydrate_is_a_noop_when_launched_from_a_shell(monkeypatch):
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin:/bin")
    monkeypatch.setattr(shell_path, "login_shell_path",
                        lambda timeout=4.0: pytest.fail("should not be asked"))
    assert shell_path.hydrate_path() == "/opt/homebrew/bin:/usr/bin:/bin"


def test_hydrate_skips_directories_that_do_not_exist(monkeypatch, tmp_path):
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(shell_path, "login_shell_path",
                        lambda timeout=4.0: str(tmp_path / "nope"))
    assert str(tmp_path / "nope") not in shell_path.hydrate_path()


def test_resolve_command_finds_a_tool_only_on_the_login_path(monkeypatch, tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    tool = bin_dir / "pretend-cli"
    tool.write_text("#!/bin/sh\n")
    tool.chmod(0o755)

    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setattr(shell_path, "login_shell_path",
                        lambda timeout=4.0: str(bin_dir))
    assert shell_path.resolve_command("pretend-cli") == str(tool)


def test_claude_runtime_explains_a_missing_cli(monkeypatch):
    from bytebarn.engine.runtimes import claude_code

    monkeypatch.setattr(claude_code, "resolve_cli", claude_code.resolve_cli)
    monkeypatch.setattr(shell_path, "resolve_command", lambda cmd: None)
    with pytest.raises(FileNotFoundError) as excinfo:
        claude_code.resolve_cli("claude")
    message = str(excinfo.value)
    assert "not on PATH" in message
    assert "claude_code" in message and "command" in message


def test_claude_runtime_uses_an_explicit_path_as_given():
    from bytebarn.engine.runtimes.claude_code import resolve_cli

    assert resolve_cli("/custom/bin/claude") == "/custom/bin/claude"


def test_privacy_hint_explains_eperm_in_protected_folders():
    from bytebarn.engine.runtimes.claude_code import privacy_hint

    hint = privacy_hint("open /x: Operation not permitted",
                        "/Users/x/Documents/GitHub/proj")
    assert "Privacy & Security" in hint and "ByteBarn" in hint

    # a normal failure is not dressed up as a permissions problem
    assert privacy_hint("claude: command failed", "/Users/x/Documents/p") == ""
    # nor is a bare EPERM, which has many causes that Settings will not fix
    assert privacy_hint("An internal error occurred (EPERM)",
                        "/Users/x/Documents/p") == ""
    # nor a denial outside the protected folders
    assert privacy_hint("Operation not permitted", "/Users/x/code/proj") == ""
