"""Async facade over the engine — the only surface the UI talks to (spec §3, §5.6)."""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path
from typing import Any

from .agents import AgentDef, AgentRegistry
from .commands import CommandRegistry
from .config import GLOBAL_DIR, Config, load_config, patch_config_file
from .skills import SkillRegistry
from .events import (
    EventBus,
    GoalQueueUpdated,
    PermissionAsked,
    QuestionAsked,
    QueueUpdated,
    SessionUpdated,
    TaskFinished,
    TaskStarted,
)
from .permissions import ASK_MODE, PermissionPolicy
from .providers.registry import ProviderRegistry
from .runner import RunHandle, Runner
from .store import Session, Store


class Engine:
    def __init__(
        self,
        project_dir: Path | str,
        db_path: Path | str | None = None,
        global_dir: Path | None = None,
    ):
        self.project_dir = Path(project_dir).resolve()
        self.global_dir = global_dir or GLOBAL_DIR
        self.config: Config = load_config(self.project_dir, self.global_dir)
        self.store = Store(db_path or (self.global_dir / "crew.db"))
        self.bus = EventBus()
        self.providers = ProviderRegistry(self.config, self.global_dir)
        self.agents = AgentRegistry(self.config, self.project_dir, self.global_dir)
        self.commands = CommandRegistry(self.project_dir, self.global_dir)
        self.skills = SkillRegistry(
            global_dir=self.global_dir, project_dir=self.project_dir)
        self.runner = Runner(self)
        self.session_mode = ASK_MODE
        self.project = None
        from .checkpoints import CheckpointStore
        from .mcp import MCPManager

        self.checkpoints = CheckpointStore(self.global_dir / "checkpoints")
        self.mcp = MCPManager()
        from .worktree import WorktreeManager
        self.worktrees = WorktreeManager(self.global_dir / "worktrees")

        self._runs: dict[str, RunHandle] = {}
        self._new_session_lock = asyncio.Lock()
        self._files_read: dict[str, set[str]] = {}
        self._pending: dict[str, asyncio.Future] = {}  # permission/question futures
        self._pending_permissions: set[str] = set()
        # last successful live model list per provider — pickers + fallback
        # prefer this over the static curated recipes in known.py
        self._live_models: dict[str, list[str]] = {}
        self._live_model_fetches: dict[str, asyncio.Task] = {}

    # -- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Open the store and register the project (call once before use)."""
        await self.store.open()
        self.project = await self.store.open_project(str(self.project_dir))
        await self.mcp.start(self.config)
        # warm the live model cache so pickers/fallback aren't stuck on curated
        try:
            asyncio.get_running_loop().create_task(self.refresh_all_models())
        except RuntimeError:
            pass

    async def stop(self) -> None:
        """Cancel all running sessions and close the store."""
        for handle in self._runs.values():
            if handle.task and not handle.task.done():
                handle.task.cancel()
        await self.mcp.stop()
        await self.store.close()

    def reload_config(self) -> None:
        """Re-read config layers and rebuild providers/agents/commands."""
        self.config = load_config(self.project_dir, self.global_dir)
        self.providers = ProviderRegistry(self.config, self.global_dir)
        self.agents = AgentRegistry(self.config, self.project_dir, self.global_dir)
        self.commands.reload()
        self.skills.reload()
        # auth/base URLs may have changed — drop stale live lists and re-warm
        self._live_models.clear()
        for task in self._live_model_fetches.values():
            if not task.done():
                task.cancel()
        self._live_model_fetches.clear()
        try:  # reconnect MCP servers with the fresh config
            loop = asyncio.get_running_loop()
            asyncio.ensure_future(self.mcp.restart(self.config))
            loop.create_task(self.refresh_all_models())
        except RuntimeError:
            pass  # no loop (sync test contexts): connect on next start()

    # -- sessions ------------------------------------------------------------

    def list_skills(self):
        """Return list of Skill(name, body, source) for both global and project."""
        return self.skills.list()

    async def new_session(
        self, agent: str = "", model: str = "", directory: str = "",
        project_id: str | None = None, isolated: bool = False,
    ) -> Session:
        """Create a session (optionally rooted in its own working directory).

        Reuses an existing empty untitled session in the same place instead of
        stacking "(untitled)" rows — mirrors Claude Desktop's lazy-chat feel.
        The lock keeps concurrent calls (e.g. startup auto-create racing a
        user's ⌘N) from both passing the reuse check before either inserts.

        ``isolated`` gives the session its own git worktree so its whole
        delegation tree writes there instead of the live checkout (spec:
        isolated sessions). Isolated sessions never reuse an existing row —
        each one needs its own worktree — and they are never reused *by* a
        plain request either: handing back an isolated row for a plain
        ``+ New session`` would silently drop the user into a worktree.
        """
        pid = project_id or self.project.id
        async with self._new_session_lock:
            if not isolated:
                for existing in await self.store.list_sessions(pid):
                    if (not existing.title and existing.directory == directory
                            and not existing.worktree_branch
                            and not await self.store.message_count(existing.id)):
                        return existing
            # per-project defaults fill in whatever the caller didn't pin
            project = await self.store.get_project(pid)
            if project and not agent:
                agent = project.default_agent
            if project and not model:
                model = project.default_model
            session = await self.store.create_session(
                pid, agent=agent or "build", model=model, directory=directory)
            if isolated:
                session = await self._isolate_session(session, directory)
        self.bus.emit(SessionUpdated(session_id=session.id))
        return session

    async def _isolate_session(self, session: Session, directory: str) -> Session:
        """Give a top-level session its own git worktree.

        Returns the session unchanged when isolation is unavailable — a non-git
        directory, a repo with no commits, ``worktree.enabled=false``, or a
        worktree git would only give us **detached**. The worktree is created
        untracked so no subagent cleanup path can remove it; the user merges
        the branch in git when the run is done.

        A detached worktree cannot deliver what isolation promises: there is no
        branch for the user to merge, and every later lookup keys off the
        branch — cleanup identifies the session's worktree by the ref it is
        checked out on, so a detached one is unrecognisable and would be
        cleaned up by path instead, which is wrong whenever the project sits
        below the git root. Better to hand back a plain session and say so
        (the UI already shows "Isolation unavailable") than an isolated one
        that is isolated in name only.
        """
        if not self.worktree_enabled():
            return session
        base = Path(directory) if directory else self.project_dir
        project_key = (self.project.id if self.project else "p")[:16]
        wt = await self.worktrees.create(
            session.id, base, project_key=project_key,
            track=False, branch=f"bytebarn/session-{session.id[:8]}",
        )
        if wt is None:
            return session
        if wt.detached:
            try:
                from .worktree import discard

                await discard(wt.git_root, wt.path, wt.branch)
            except Exception:
                # the session is going back un-isolated either way; a stray
                # checkout under the worktree store is a far smaller problem
                # than failing to create the session the user asked for
                pass
            return session
        session_dir = self._worktree_cwd(wt.path, wt.git_root, base)
        try:
            await self.store.update_session(
                session.id, directory=str(session_dir), worktree_branch=wt.branch)
        except Exception:
            # store write failed after the worktree/branch already exist on
            # disk (untracked, so nothing else will ever clean them up) —
            # best-effort teardown, then let the *original* error propagate
            # so the caller never silently gets back a non-isolated session.
            try:
                from .worktree import discard

                await discard(wt.git_root, wt.path, wt.branch)
            except Exception:
                # teardown is best-effort only: whatever went wrong here is
                # strictly less informative than the store failure that
                # triggered it, so swallow it rather than let it mask the
                # real cause.
                pass
            raise
        return await self.store.get_session(session.id) or session

    @staticmethod
    def _worktree_cwd(wt_path: Path, git_root: Path, base: Path) -> Path:
        """Where inside the worktree the session should be rooted.

        ``git worktree add`` always checks out the whole repository, so a
        project opened *below* the git root (``/repo/frontend``) would land the
        session at the repo-root checkout — an agent told to edit
        ``src/App.tsx`` would write ``<wt>/src/App.tsx``, and
        ``/repo/frontend/CLAUDE.md`` would stop loading as project
        instructions. Mirror the depth instead.

        The subdirectory is created when the checkout lacks it: ``base`` may be
        untracked (gitignored build output, a fresh dir never committed), in
        which case HEAD has no such path. An empty dir is still the right cwd —
        without it every tool call would fail on a missing cwd, which is
        exactly the failure this mapping exists to avoid.
        """
        try:
            rel = base.resolve().relative_to(git_root.resolve())
        except ValueError:
            return wt_path  # base is not inside the repo — nothing to mirror
        if not rel.parts:
            return wt_path
        sub = wt_path / rel
        try:
            sub.mkdir(parents=True, exist_ok=True)
        except OSError:
            return wt_path  # unwritable: repo root beats a nonexistent cwd
        return sub

    async def repo_dirty(self, directory: Path | str | None = None) -> list[str]:
        """Uncommitted paths in a checkout ([] when it is not a repo).

        The UI warns with this before starting an isolated session, because the
        worktree is checked out from HEAD and will not contain them. Callers
        must pass the *same* directory ``_isolate_session`` will branch from —
        a new session can be rooted in any registered project, and warning
        about a repo that is not the one being branched is worse than silence.
        Defaults to the engine's own project dir.
        """
        from .worktree import dirty_files

        return await dirty_files(Path(directory) if directory else self.project_dir)

    async def close_session(self, session_id: str) -> None:
        """Archive a session: abort any run, hide it from the session list."""
        await self.abort(session_id)
        await self.store.update_session(session_id, archived=1)
        self.bus.emit(SessionUpdated(session_id=session_id))

    async def session_worktree_info(self, session_id: str) -> dict | None:
        """What deleting this session's worktree would cost, or None.

        ``None`` means the session is not isolated and there is nothing to ask
        the user about. ``path`` is the worktree root (not necessarily the
        session's directory, which sits below it when the project was opened
        below the git root). ``unmerged`` is the number of commits that exist
        only on this branch. ``uncommitted`` is the number of files the
        worktree has changed but never committed — **the engine never commits**,
        so that is the normal end state of an isolated session, and
        ``worktree remove --force`` destroys them by design; a dialog that
        weighed only commits would say "nothing to lose" about a whole run's
        output. ``exists`` is False when the directory has already been removed
        by hand, in which case only the branch is left to clean up.

        Everything is measured from the session's own checkout, so a session
        living in another registered project is described from *its*
        repository rather than the open one — asking the wrong repo about a
        branch it has never heard of answers "fully merged" every time.
        """
        from .worktree import dirty_files, git_root, unmerged_count, worktree_roots

        session = await self.store.get_session(session_id)
        if session is None or not session.worktree_branch:
            return None
        directory = Path(session.directory) if session.directory else None
        roots = await worktree_roots(directory, session.worktree_branch) if directory else None
        if roots is not None:
            wt_root, repo = roots
        else:
            # the checkout is gone: nothing records which repo owned it, so
            # fall back to the open project — the branch is reported as
            # unknown-cost rather than not reported at all
            wt_root, repo = directory, await git_root(self.project_dir)
        unmerged = await unmerged_count(repo, session.worktree_branch) if repo else 0
        return {
            "branch": session.worktree_branch,
            "path": str(wt_root) if wt_root else "",
            "unmerged": unmerged,
            "uncommitted": len(await dirty_files(wt_root)) if roots else 0,
            "exists": bool(wt_root and wt_root.is_dir()),
        }

    async def delete_session(
        self, session_id: str, discard_worktree: bool = False
    ) -> str:
        """Permanently remove a session, its children, and their history.

        ``discard_worktree`` also removes an isolated session's worktree and
        branch. Opt-in, because that destroys any commits living only there.
        Cleanup failure never blocks the delete: a worktree left behind can be
        removed by hand, a session row cannot be brought back.

        Returns "" normally, or a reason naming what cleanup could not remove
        and where — the delete still happened, and the caller is expected to
        show it, because the alternative is a delete that looks complete while
        a full checkout of the repo stays on disk.
        """
        await self.abort(session_id)
        problem = ""
        if discard_worktree:
            problem = await self._discard_session_worktree(session_id)
        await self.store.delete_session(session_id)
        self._files_read.pop(session_id, None)
        self._runs.pop(session_id, None)
        self.bus.emit(SessionUpdated(session_id=session_id))
        return problem

    async def _discard_session_worktree(self, session_id: str) -> str:
        """Best-effort worktree teardown — never raises into the delete path.

        Returns "" or a reason naming the path, so the delete path can report
        it. Failures are *returned*, never raised: the session row must go
        even when the checkout will not.
        """
        from . import worktree as worktree_mod

        where = ""
        try:
            session = await self.store.get_session(session_id)
            if session is None or not session.worktree_branch or not session.directory:
                return ""
            where = session.directory
            roots = await worktree_mod.worktree_roots(
                Path(session.directory), session.worktree_branch)
            if roots is None:
                # directory already gone (or no longer a checkout): the branch
                # may still be registered, so prune + delete it from the open
                # project — the only repo left to ask
                repo = await worktree_mod.git_root(self.project_dir)
                if repo is None:
                    # no repo left to ask. Only a worktree still sitting on
                    # disk is worth reporting — when the directory is already
                    # gone there was nothing to remove, and saying otherwise
                    # is status-bar noise about a success.
                    return (f"could not remove worktree at {where}"
                            if Path(where).exists() else "")
                return await worktree_mod.discard(
                    repo, Path(session.directory), session.worktree_branch)
            wt_root, repo = roots
            return await worktree_mod.discard(
                repo, wt_root, session.worktree_branch)
        except Exception as exc:
            return f"could not remove worktree at {where or '(unknown path)'}: {exc}"

    async def list_models(self, provider_name: str, *, force: bool = True) -> list[str]:
        """Live model ids for a connected provider ([] if unreachable).

        Always hits the provider when ``force`` (the default) so every picker
        selection gets an up-to-date catalog. Successful fetches update
        ``_live_models``; failures leave the previous cache entry alone and
        return [].
        """
        from .providers.probe import fetch_models

        if not provider_name:
            return []
        if not force and provider_name in self._live_models:
            return list(self._live_models[provider_name])

        # coalesce concurrent fetches for the same provider
        existing = self._live_model_fetches.get(provider_name)
        if existing and not existing.done():
            return list(await existing)

        async def _fetch() -> list[str]:
            live = await fetch_models(
                provider_name, self.config, self.providers.auth)
            if live:
                self._live_models[provider_name] = list(live)
            return list(live)

        task = asyncio.create_task(_fetch())
        self._live_model_fetches[provider_name] = task
        try:
            return await task
        finally:
            if self._live_model_fetches.get(provider_name) is task:
                self._live_model_fetches.pop(provider_name, None)

    def cached_models(self, provider_name: str) -> list[str] | None:
        """Last successful live list for ``provider_name``, or None if never fetched."""
        cached = self._live_models.get(provider_name)
        return list(cached) if cached is not None else None

    async def refresh_all_models(self) -> None:
        """Prefetch live model lists for every currently connected provider."""
        from .providers.known import connected_providers

        providers = connected_providers(self.config, self.providers.auth)
        if not providers:
            return
        await asyncio.gather(
            *(self.list_models(name, force=True) for name in providers),
            return_exceptions=True,
        )

    def fallback_model(self, model: str, exclude: list[str]) -> str | None:
        """Comparable available model for automatic fallback (None = give up)."""
        from .providers.fallback import comparable_model

        return comparable_model(
            model, self.config, self.providers.auth, exclude,
            live=self._live_models,
        )

    def files_read(self, session_id: str) -> set[str]:
        """Paths the session has read (gates edit-before-read, spec §5.4)."""
        return self._files_read.setdefault(session_id, set())

    def is_running(self, session_id: str) -> bool:
        """True while the session has an active run task."""
        handle = self._runs.get(session_id)
        return bool(handle and handle.task and not handle.task.done())

    def queue_depth(self, session_id: str) -> int:
        handle = self._runs.get(session_id)
        return len(handle.queued) if handle else 0

    # -- prompts -------------------------------------------------------------

    async def submit_prompt(
        self, session_id: str, text: str, agent: str | None = None,
        model: str | None = None, files: list[str] | None = None,
    ) -> None:
        """Persist the user prompt (plus attachments) and start or queue a run."""
        session = await self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"no session {session_id}")

        # session-bound meta commands (no model turn)
        stripped = text.strip()
        if stripped.startswith("/"):
            cname, _, cargs = stripped[1:].partition(" ")
            if cname == "context":
                bd = await self.context_breakdown(session_id)
                um = await self.store.add_message(session_id, "user")
                await self.store.add_part(um.id, "text", {"text": "/context"})
                am = await self.store.add_message(session_id, "assistant")
                await self.store.add_part(am.id, "text", {"text": bd.format_text()})
                self.bus.emit(SessionUpdated(session_id=session_id))
                return
            if cname == "diff":
                report = await self.session_diff(session_id)
                um = await self.store.add_message(session_id, "user")
                await self.store.add_part(um.id, "text", {"text": "/diff"})
                am = await self.store.add_message(session_id, "assistant")
                await self.store.add_part(am.id, "text", {"text": report})
                self.bus.emit(SessionUpdated(session_id=session_id))
                return
            if cname == "rewind":
                mid = cargs.strip() or None
                result = await self.rewind(session_id, mid)
                note = (
                    f"Rewound to message {result.get('message_id', '?')}; "
                    f"restored {len(result.get('restored') or [])} file(s)."
                    if result.get("ok") else
                    f"Rewind failed: {result.get('error', 'unknown')}"
                )
                am = await self.store.add_message(session_id, "assistant")
                await self.store.add_part(am.id, "text", {"text": note})
                self.bus.emit(SessionUpdated(session_id=session_id))
                return
            if cname == "review":
                from .commands import REVIEW_TEMPLATE
                diff = await self.session_diff(session_id)
                focus = cargs.strip()
                text = REVIEW_TEMPLATE.replace(
                    "$ARGUMENTS",
                    (focus + "\n\n" if focus else "") + "Changes to review:\n\n" + diff,
                )
                # explore agent for this run only — do NOT persist session.agent
                rendered, route_agent, route_model = text, "explore", None
                run_agent_override = "explore"
            else:
                rendered, route_agent, route_model = self._apply_command(text)
                run_agent_override = None
        else:
            rendered, route_agent, route_model = self._apply_command(text)
            run_agent_override = None

        agent = route_agent or agent
        model = route_model or model
        updates: dict[str, Any] = {}
        # /review routes to explore for one run only; keep the session agent
        if agent and agent != session.agent and run_agent_override is None:
            updates["agent"] = agent
        if model and model != session.model:
            updates["model"] = model
        if updates:
            await self.store.update_session(session_id, **updates)
            session = await self.store.get_session(session_id)

        if self.is_running(session_id):
            self._runs[session_id].queued.append((rendered, list(files or ())))
            self.bus.emit(QueueUpdated(session_id=session_id, depth=len(self._runs[session_id].queued)))
            return

        message = await self.store.add_message(session_id, "user")
        await self.store.add_part(message.id, "text", {"text": rendered})
        for path in files or ():
            await self.store.add_part(message.id, "file", {"path": path})
        self.bus.emit(SessionUpdated(session_id=session_id))
        self._start_run(session, agent_override=run_agent_override)

        if not session.title and not session.parent_session_id:
            from .compaction import generate_title

            asyncio.ensure_future(self._swallow(generate_title(self, session, rendered)))

    async def regenerate(self, session_id: str) -> bool:
        """Drop everything after the last user message and run it again."""
        if self.is_running(session_id):
            return False
        messages = await self.store.list_messages(session_id)
        last_user = next((m for m in reversed(messages) if m.role == "user"), None)
        if last_user is None:
            return False
        # delete the assistant turn(s) after the prompt, keep the prompt itself
        idx = messages.index(last_user)
        if idx + 1 < len(messages):
            await self.store.delete_messages_from(session_id, messages[idx + 1].id)
        session = await self.store.get_session(session_id)
        self.bus.emit(SessionUpdated(session_id=session_id))
        self._start_run(session)
        return True

    async def edit_and_rerun(self, session_id: str, message_id: str, new_text: str) -> None:
        """Fork the session at a user message: truncate from it, resubmit."""
        if self.is_running(session_id):
            await self.abort(session_id)
        await self.store.delete_messages_from(session_id, message_id)
        self.bus.emit(SessionUpdated(session_id=session_id))
        await self.submit_prompt(session_id, new_text)

    async def export_markdown(self, session_id: str) -> str:
        """Whole transcript as portable markdown."""
        session = await self.store.get_session(session_id)
        if session is None:
            raise KeyError(f"no session {session_id}")
        lines = [f"# {session.title or 'Untitled session'}", ""]
        if session.model:
            lines += [f"*agent: {session.agent} · model: {session.model}*", ""]
        for message, parts in await self.store.session_parts(session_id):
            for part in parts:
                data = part.data
                if part.type == "text":
                    who = "**You:**" if message.role == "user" else "**Assistant:**"
                    lines += [who, "", data.get("text", ""), ""]
                elif part.type == "reasoning":
                    text = data.get("text", "").strip()
                    if text:
                        lines += ["<details><summary>thinking</summary>", "",
                                  text, "", "</details>", ""]
                elif part.type in ("tool", "task"):
                    tool = data.get("tool", "")
                    title = data.get("title", "") or ""
                    status = data.get("status", "")
                    lines += [f"`{tool}` {title} *({status})*", ""]
                    output = (data.get("output") or "").strip()
                    if output:
                        lines += ["```", output[:4000], "```", ""]
                elif part.type == "compaction":
                    lines += ["*context compacted — summary:*", "",
                              data.get("text", ""), ""]
                elif part.type == "file":
                    lines += [f"📎 `{data.get('path', '')}`", ""]
        return "\n".join(lines).rstrip() + "\n"

    def _apply_command(self, text: str) -> tuple[str, str | None, str | None]:
        """Expand a leading /command; returns (prompt, agent, model)."""
        if not text.startswith("/"):
            return text, None, None
        name, _, args = text[1:].partition(" ")
        command = self.commands.get(name)
        if command is None or command.action:
            return text, None, None
        # /skill <name> [args] — expand skill body into the user prompt
        if name == "skill":
            from .skills import format_skill_prompt

            skill_name, _, rest = args.strip().partition(" ")
            if not skill_name:
                available = ", ".join(s.name for s in self.skills.list()) or "(none)"
                return (
                    f"No skill name given. Available skills: {available}. "
                    "Usage: /skill <name> [request]",
                    None, None,
                )
            skill = self.skills.get(skill_name)
            if skill is None:
                available = ", ".join(s.name for s in self.skills.list()) or "(none)"
                return (
                    f"Unknown skill '{skill_name}'. Available: {available}.",
                    None, None,
                )
            return format_skill_prompt(skill, rest), command.agent, command.model
        return command.render(args.strip()), command.agent, command.model

    def _start_run(self, session: Session, agent_override: str | None = None) -> None:
        handle = self._runs.get(session.id) or RunHandle()
        handle.abort = asyncio.Event()
        handle.agent_override = agent_override
        self._runs[session.id] = handle
        handle.task = asyncio.ensure_future(self.runner.run(session, handle))

    async def on_run_finished(self, session_id: str) -> None:
        """Promote a queued prompt, if any; else advance the goal queue."""
        handle = self._runs.get(session_id)
        if not handle or not handle.queued:
            goal = await self.store.goal_for_session(session_id)
            if goal is not None:
                await self.store.update_goal(goal.id, status="done")
                self.bus.emit(GoalQueueUpdated(project_id=goal.project_id))
                await self._advance_goal_queue(goal.project_id)
            return
        queued = handle.queued.pop(0)
        text, files = queued if isinstance(queued, tuple) else (queued, [])
        self.bus.emit(QueueUpdated(session_id=session_id, depth=len(handle.queued)))
        session = await self.store.get_session(session_id)
        message = await self.store.add_message(session_id, "user")
        await self.store.add_part(message.id, "text", {"text": text})
        for path in files:
            await self.store.add_part(message.id, "file", {"path": path})
        self.bus.emit(SessionUpdated(session_id=session_id))
        self._start_run(session)

    async def abort(self, session_id: str) -> None:
        """Stop a session's run (and its children), dropping queued prompts."""
        handle = self._runs.get(session_id)
        if handle:
            handle.queued.clear()
            self.bus.emit(QueueUpdated(session_id=session_id, depth=0))
            handle.abort.set()
            if handle.task and not handle.task.done():
                handle.task.cancel()
        # abort children too
        for child in await self.store.child_sessions(session_id):
            await self.abort(child.id)

    # -- goal queue (walk-away workflows) --------------------------------------

    async def run_routines(self, poll_s: float = 30.0) -> None:
        """Scheduler loop: queue each due routine's prompt as a goal.

        Run as a background task for the app's lifetime; catching up after
        sleep queues at most one run per routine (next_run advances past now).
        """
        import time as _time

        while True:
            try:
                now = _time.time()
                for routine in await self.store.due_routines(now):
                    next_run = routine.next_run + routine.interval_s
                    if next_run <= now:  # missed cycles (machine asleep) — skip them
                        next_run = now + routine.interval_s
                    await self.store.update_routine(routine.id, next_run=next_run)
                    await self.queue_goal(routine.prompt, routine.project_id)
            except asyncio.CancelledError:
                raise
            except Exception:
                pass  # scheduler must survive transient store/provider errors
            await asyncio.sleep(poll_s)

    async def queue_goal(self, prompt: str, project_id: str | None = None):
        """Queue a goal; runs immediately if nothing is running, else waits."""
        pid = project_id or self.project.id
        goal = await self.store.add_goal(pid, prompt.strip())
        self.bus.emit(GoalQueueUpdated(project_id=pid))
        await self._advance_goal_queue(pid)
        return goal

    async def cancel_goal(self, goal_id: str) -> None:
        await self.store.update_goal(goal_id, status="cancelled")
        self.bus.emit(GoalQueueUpdated(project_id=self.project.id if self.project else ""))

    async def _advance_goal_queue(self, project_id: str) -> None:
        """Start the next pending goal unless one is already running."""
        if await self.store.running_goal(project_id) is not None:
            return
        goal = await self.store.next_pending_goal(project_id)
        if goal is None:
            return
        session = await self.new_session(project_id=project_id)
        await self.store.update_goal(goal.id, status="running", session_id=session.id)
        self.bus.emit(GoalQueueUpdated(project_id=project_id))
        await self.submit_prompt(session.id, f"/goal {goal.prompt}")

    # -- context window --------------------------------------------------------

    async def context_usage(self, session_id: str) -> tuple[int, int]:
        """(tokens used last turn, model context window) for the meter."""
        from .providers.catalog import model_info

        tokens, model_id = await self.store.last_usage(session_id)
        if not model_id:
            session = await self.store.get_session(session_id)
            model = (session.model if session else "") or self.config.model
            model_id = model.split("/", 1)[1] if "/" in model else model
        info = model_info(model_id, self.providers.extra_catalog)
        return tokens, info.context_window

    async def context_breakdown(self, session_id: str):
        """Structured estimate of what fills the context window."""
        from .context_usage import build_breakdown
        from .providers.catalog import model_info
        from .runner import build_system_prompt, history_to_messages, load_memory
        from .skills import catalog_section
        from .tools.registry import build_tools

        session = await self.store.get_session(session_id)
        if session is None:
            raise KeyError(session_id)
        agent = self.agents.get(session.agent)
        model = session.model or agent.model or self.config.model
        model_id = model.split("/", 1)[1] if "/" in model else model
        info = model_info(model_id, self.providers.extra_catalog)
        tokens, _ = await self.context_usage(session_id)

        cwd = Path(session.directory) if session.directory else self.project_dir
        proj_instructions, proj_assets = await self.project_knowledge(session.project_id)
        skills_catalog = catalog_section(self.skills.list())
        mem = load_memory(self.memory_dir(session.project_id))
        system = await build_system_prompt(
            agent, cwd, self.config.instructions,
            project_instructions=proj_instructions, assets=proj_assets,
            memory=mem, skills_catalog=skills_catalog,
        )
        history = await self.store.session_parts(session_id)
        hist_msgs = history_to_messages(history)
        history_text = ""
        for m in hist_msgs:
            history_text += json.dumps(m.content, default=str) + "\n"
        tools = build_tools(
            agent.tools, include_task=session.parent_session_id is None,
            skill_registry=self.skills,
        )
        tools_text = "\n".join(f"{t.name}: {t.description()}" for t in tools)
        instructions_text = ""
        for name in self.config.instructions:
            p = cwd / name
            if p.is_file():
                instructions_text += p.read_text() + "\n"
        instructions_text += proj_instructions or ""
        memory_text = "\n".join(t for _, t in mem)
        return build_breakdown(
            system=system,
            history_text=history_text,
            tools_text=tools_text,
            skills_text=skills_catalog,
            memory_text=memory_text,
            instructions_text=instructions_text,
            model=model,
            context_window=info.context_window,
            last_turn_tokens=tokens,
        )

    async def compact(self, session_id: str) -> None:
        """Summarize old history into a compaction part to free context."""
        from .compaction import compact_session

        session = await self.store.get_session(session_id)
        await compact_session(self, session)

    # -- review / diff / rewind -----------------------------------------------

    def last_run_diff(self, session_id: str) -> str:
        """Unified diff of files changed by the last agent run (write/edit)."""
        return self.checkpoints.last_run_diff(session_id)

    async def git_diff(self, directory: Path | str | None = None) -> str:
        """Uncommitted git changes for a directory (status + diff)."""
        import asyncio
        cwd = Path(directory) if directory else self.project_dir
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "status", "--short",
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            status, _ = await proc.communicate()
            proc2 = await asyncio.create_subprocess_exec(
                "git", "diff", "HEAD",
                cwd=str(cwd),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            diff, _ = await proc2.communicate()
        except OSError as exc:
            return f"(git unavailable: {exc})"
        parts = []
        st = status.decode(errors="replace").strip()
        df = diff.decode(errors="replace").strip()
        if st:
            parts.append("## git status\n```\n" + st + "\n```")
        if df:
            parts.append("## git diff\n```diff\n" + df + "\n```")
        return "\n\n".join(parts) if parts else "(working tree clean — no uncommitted changes)"

    async def session_diff(self, session_id: str) -> str:
        """Last-run agent diff plus current git diff for the session directory."""
        session = await self.store.get_session(session_id)
        cwd = Path(session.directory) if session and session.directory else self.project_dir
        last = self.last_run_diff(session_id)
        git = await self.git_diff(cwd)
        return f"# Last agent run\n\n{last}\n\n# Working tree\n\n{git}"

    @staticmethod
    def _is_meta_user_text(text: str) -> bool:
        """True for slash meta commands that are not real user work turns."""
        t = (text or "").strip()
        if not t.startswith("/"):
            return False
        name = t[1:].split(None, 1)[0].split("\n", 1)[0].lower()
        return name in ("diff", "context", "rewind")

    async def _user_text(self, message_id: str) -> str:
        parts = await self.store.list_parts(message_id)
        return "\n".join(
            p.data.get("text", "") for p in parts if p.type == "text"
        ).strip()

    async def rewind(
        self, session_id: str, message_id: str | None = None,
    ) -> dict[str, Any]:
        """Drop transcript after a user message and restore snapshotted files.

        If ``message_id`` is omitted, rewinds to the latest *real* user message
        (skips meta-only turns like ``/diff`` / ``/context`` / ``/rewind`` so
        a default rewind still undoes the last agent write).
        """
        if self.is_running(session_id):
            await self.abort(session_id)
        messages = await self.store.list_messages(session_id)
        if not messages:
            return {"ok": False, "error": "empty session", "restored": []}
        if message_id is None:
            users = [m for m in messages if m.role == "user"]
            if not users:
                return {"ok": False, "error": "no user message", "restored": []}
            target = None
            for m in reversed(users):
                text = await self._user_text(m.id)
                if not self._is_meta_user_text(text):
                    target = m
                    break
            if target is None:
                # only meta turns exist — fall back to oldest user message
                target = users[0]
        else:
            target = next((m for m in messages if m.id == message_id), None)
            if target is None:
                return {"ok": False, "error": "message not found", "restored": []}
        restored = self.checkpoints.restore_after(session_id, target.created_at)
        await self.store.delete_messages_after(session_id, target.id)
        self.bus.emit(SessionUpdated(session_id=session_id))
        return {
            "ok": True,
            "message_id": target.id,
            "restored": restored,
        }

    def hooks(self):
        """Live hook runner from current config."""
        from .hooks import HookRunner
        # support top-level hooks key via model_extra (config extra=allow)
        top = {}
        extra = self.config.model_extra or {}
        if "hooks" in extra:
            top = extra
        # also check attribute if pydantic put it somewhere
        hooks_attr = getattr(self.config, "hooks", None)
        if isinstance(hooks_attr, dict):
            return HookRunner(hooks_attr)
        return HookRunner.from_engine_config(extra, top)

    def install_mcp_recipe(
        self, recipe_id: str, values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """One-click install of a curated MCP server into global config.

        Returns the written config entry. Raises KeyError for unknown recipes.
        """
        from .mcp import KNOWN_MCP_SERVERS, config_entry

        spec = KNOWN_MCP_SERVERS.get(recipe_id)
        if spec is None:
            raise KeyError(f"unknown MCP recipe: {recipe_id}")
        entry = config_entry(spec, values or {})
        patch_config_file(self.global_dir / "config.json", {f"mcp.{spec.id}": entry})
        self.config = load_config(self.project_dir, self.global_dir)
        return entry

    async def install_mcp_recipe_async(
        self, recipe_id: str, values: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Install recipe and reconnect MCP manager."""
        entry = self.install_mcp_recipe(recipe_id, values)
        await self.mcp.restart(self.config)
        return entry

    # -- permissions & questions ----------------------------------------------

    def policy_for(self, agent: AgentDef) -> PermissionPolicy:
        """Effective permission policy: config + agent overrides + session mode.

        The mode is read live so switching to Full-auto mid-run applies to
        tool calls of runs that started earlier."""
        return PermissionPolicy(
            self.config.permission, agent.permission,
            session_mode=lambda: self.session_mode,
        )

    def set_session_mode(self, mode: str) -> None:
        """Change the session permission mode; Full-auto also releases every
        permission prompt currently waiting on the user."""
        from .permissions import FULL_AUTO

        self.session_mode = mode
        if mode == FULL_AUTO:
            for request_id in list(self._pending_permissions):
                self.answer_permission(request_id, "allow")

    async def ask_permission(
        self, session_id: str, tool: str, arg: str, input: dict, policy: PermissionPolicy
    ) -> str:
        """Emit permission.asked and await the user's verdict; "allow_always"
        persists the pattern to project config and collapses to "allow"."""
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        self._pending_permissions.add(request_id)
        self.bus.emit(PermissionAsked(
            session_id=session_id, request_id=request_id, tool=tool, arg=arg, input=input
        ))
        answer = await future
        if answer == "allow_always":
            pattern = arg + "*" if tool == "bash" else arg
            policy.with_added_allow(tool, pattern)
            path = self.project_dir / ".bytebarn" / "config.json"
            rule = policy.rules[tool]
            patch_config_file(path, {
                f"permission.{tool}": {
                    "default": rule.default, "allow": rule.allow, "deny": rule.deny,
                }
            })
            return "allow"
        return answer  # "allow" | "deny"

    def answer_permission(self, request_id: str, answer: str) -> None:
        """Resolve a pending permission.asked event ("allow"/"allow_always"/"deny")."""
        self._pending_permissions.discard(request_id)
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(answer)

    async def ask_question(self, session_id: str, question: str, options: list[str]) -> str:
        """Emit question.asked and await the user's chosen/typed answer."""
        request_id = uuid.uuid4().hex
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        self._pending[request_id] = future
        self.bus.emit(QuestionAsked(
            session_id=session_id, request_id=request_id, question=question, options=options
        ))
        return await future

    def answer_question(self, request_id: str, answer: str) -> None:
        """Resolve a pending question.asked event with the user's answer."""
        future = self._pending.pop(request_id, None)
        if future and not future.done():
            future.set_result(answer)

    # -- subagents (task tool) -------------------------------------------------

    def worktree_enabled(self) -> bool:
        """Whether `worktree.enabled` permits isolation (defaults to true).

        Public because the UI distinguishes "you turned this off" from the
        other reasons isolation can be unavailable.
        """
        conf = (self.config.model_extra or {}).get("worktree") or {}
        if isinstance(conf, dict):
            return bool(conf.get("enabled", True))
        return True

    async def run_subagent(
        self, parent: Session, agent: str, prompt: str, description: str, task_id: str | None
    ) -> str:
        """Run a task-tool delegation in a child session and return its final
        text (task.started/finished events drive the crew stage).

        New subagents in a git project get an isolated worktree when
        ``worktree.enabled`` is true (default). Changes are merged back into
        the parent working tree when the subagent finishes.
        """
        agent_def = self.agents.get(agent)  # raises KeyError for unknown agents
        parent_cwd = Path(parent.directory) if parent.directory else self.project_dir
        if task_id:
            child = await self.store.get_session(task_id)
            if child is None or child.parent_session_id != parent.id:
                raise ValueError(f"task_id {task_id} is not a child of this session")
        else:
            child = await self.store.create_session(
                self.project.id,
                agent=agent,
                model=agent_def.model or "",
                parent_session_id=parent.id,
                title=description[:80],
                directory=parent.directory or "",
            )

        # Isolate each run in a fresh worktree (git projects only) so parallel
        # writers cannot collide. Disabled via config worktree.enabled=false.
        if self.worktree_enabled() and self.worktrees.get(child.id) is None:
            project_key = (self.project.id if self.project else "p")[:16]
            wt = await self.worktrees.create(
                child.id, parent_cwd, project_key=project_key,
            )
            if wt is not None:
                await self.store.update_session(child.id, directory=str(wt.path))
                child = await self.store.get_session(child.id)

        self.bus.emit(TaskStarted(
            session_id=parent.id, subagent_session_id=child.id,
            agent=agent, description=description,
        ))

        message = await self.store.add_message(child.id, "user")
        await self.store.add_part(message.id, "text", {"text": prompt})

        handle = RunHandle()
        self._runs[child.id] = handle
        handle.task = asyncio.ensure_future(self.runner.run(child, handle))
        try:
            await handle.task
        finally:
            status = "error" if (handle.task.cancelled() or handle.task.exception()) else "done"
        # final assistant text = tool result
        final_text = await self._final_text(child.id)

        merge_note = ""
        if self.worktrees.get(child.id) is not None:
            result = await self.worktrees.apply_and_remove(child.id)
            # follow-ups should not keep a deleted worktree path
            await self.store.update_session(
                child.id, directory=parent.directory or "")
            if result is not None:
                merge_note = f"\n[{result.summary()}]"

        self.bus.emit(TaskFinished(
            session_id=parent.id, subagent_session_id=child.id, status=status,
        ))
        return (
            (final_text or "[subagent produced no output]")
            + f"\n\n[task_id: {child.id}]"
            + merge_note
        )

    async def _final_text(self, session_id: str) -> str:
        history = await self.store.session_parts(session_id)
        for message, parts in reversed(history):
            if message.role != "assistant":
                continue
            texts = [p.data.get("text", "") for p in parts if p.type == "text"]
            if any(t.strip() for t in texts):
                return "\n".join(t for t in texts if t.strip())
        return ""

    @staticmethod
    async def _swallow(coro) -> None:
        try:
            await coro
        except Exception:
            pass

    async def create_project(self, path: str, name: str | None = None):
        return await self.store.create_project(path, name)

    async def list_projects(self):
        return await self.store.list_projects()

    # -- project knowledge (Claude-style: instructions + assets) ---------------

    def _assets_dir(self, project_id: str) -> Path:
        return self.global_dir / "assets" / project_id

    def memory_dir(self, project_id: str) -> Path:
        """Root of the project's persistent OKF memory bundle (okf.md)."""
        return self.global_dir / "memory" / project_id

    async def add_project_asset(self, project_id: str, src: Path | str):
        """Copy a file into the project's knowledge base and register it."""
        import shutil

        src = Path(src)
        dest_dir = self._assets_dir(project_id)
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name
        stem, suffix, n = src.stem, src.suffix, 1
        while dest.exists():  # don't clobber a different file with the same name
            dest = dest_dir / f"{stem}-{n}{suffix}"
            n += 1
        shutil.copy2(src, dest)
        asset = await self.store.add_project_asset(project_id, dest, src.name)
        self.bus.emit(SessionUpdated(session_id=""))  # sidebar/dialog refresh
        return asset

    async def remove_project_asset(self, project_id: str, path: Path | str) -> None:
        """Remove an asset row and its copied file."""
        await self.store.remove_project_asset(project_id, path)
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            pass

    async def project_knowledge(self, project_id: str):
        """(instructions, assets) injected into every run in the project."""
        project = await self.store.get_project(project_id)
        assets = await self.store.list_project_assets(project_id)
        return (project.instructions if project else "", assets)
