"""Auto-updater: version compare, git-checkout flow, release check."""

import asyncio
import subprocess

import pytest

from crew import updater


def _git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True)


def test_parse_version_ordering():
    assert updater.parse_version("v1.2.3") == (1, 2, 3)
    assert updater.parse_version("0.10.0") > updater.parse_version("0.9.9")
    assert updater.parse_version("garbage") == (0,)


def test_repo_root_finds_this_checkout():
    root = updater.repo_root()
    assert root is not None and (root / "pyproject.toml").exists()


@pytest.fixture
def clone(tmp_path):
    """An 'upstream' repo and a clone of it (origin wired up like a user's)."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git(upstream, "init", "-b", "main")
    _git(upstream, "config", "user.email", "t@t")
    _git(upstream, "config", "user.name", "t")
    (upstream / "file.txt").write_text("v1\n")
    _git(upstream, "add", "-A")
    _git(upstream, "commit", "-m", "initial")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(upstream), str(clone)],
                   check=True, capture_output=True)
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    return upstream, clone


async def test_git_check_and_apply(clone):
    upstream, local = clone

    info = await updater.check_git_update(local)
    assert info.kind == "none"

    # upstream moves ahead
    (upstream / "file.txt").write_text("v2\n")
    _git(upstream, "commit", "-am", "feat: shiny new thing")

    info = await updater.check_git_update(local)
    assert info.kind == "git" and info.behind == 1
    assert any("shiny new thing" in c for c in info.commits)
    assert not info.dirty

    ok, detail = await updater.apply_git_update(local, run_pip=False)
    assert ok, detail
    assert (local / "file.txt").read_text() == "v2\n"
    info = await updater.check_git_update(local)
    assert info.kind == "none"


async def test_git_check_flags_dirty_tree(clone):
    upstream, local = clone
    (upstream / "file.txt").write_text("v2\n")
    _git(upstream, "commit", "-am", "update")
    (local / "scratch.txt").write_text("wip\n")

    info = await updater.check_git_update(local)
    assert info.kind == "git" and info.dirty


async def test_release_check_with_injected_fetch():
    async def newer(url):
        return {"tag_name": "v99.0.0", "html_url": "https://example/rel"}

    info = await updater.check_release_update(fetch=newer)
    assert info.kind == "release" and info.version == "v99.0.0"
    assert info.url == "https://example/rel"

    async def older(url):
        return {"tag_name": "v0.0.1"}

    info = await updater.check_release_update(fetch=older)
    assert info.kind == "none"

    async def boom(url):
        raise RuntimeError("api down")

    info = await updater.check_release_update(fetch=boom)
    assert info.kind == "error" and "api down" in info.message
