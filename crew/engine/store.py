"""SQLite persistence (aiosqlite, WAL). Schema per spec §5.1."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project (
    id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    last_opened_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS project_folder (
    project_id TEXT NOT NULL REFERENCES project(id),
    path TEXT NOT NULL,
    PRIMARY KEY (project_id, path)
);
CREATE TABLE IF NOT EXISTS project_asset (
    project_id TEXT NOT NULL REFERENCES project(id),
    path TEXT NOT NULL,
    name TEXT NOT NULL,
    added_at REAL NOT NULL,
    PRIMARY KEY (project_id, path)
);
CREATE TABLE IF NOT EXISTS session (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    parent_session_id TEXT REFERENCES session(id),
    title TEXT NOT NULL DEFAULT '',
    agent TEXT NOT NULL DEFAULT 'build',
    model TEXT NOT NULL DEFAULT '',
    directory TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    archived INTEGER NOT NULL DEFAULT 0,
    permission_mode TEXT
);
CREATE TABLE IF NOT EXISTS message (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES session(id),
    role TEXT NOT NULL CHECK (role IN ('user','assistant')),
    created_at REAL NOT NULL,
    model TEXT,
    provider TEXT,
    tokens_in INTEGER NOT NULL DEFAULT 0,
    tokens_out INTEGER NOT NULL DEFAULT 0,
    cost REAL NOT NULL DEFAULT 0,
    error TEXT
);
CREATE TABLE IF NOT EXISTS part (
    id TEXT PRIMARY KEY,
    message_id TEXT NOT NULL REFERENCES message(id),
    idx INTEGER NOT NULL,
    type TEXT NOT NULL,
    json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS goal_queue (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    prompt TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','running','done','error','cancelled')),
    session_id TEXT,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS todo (
    session_id TEXT NOT NULL REFERENCES session(id),
    idx INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','in_progress','completed')),
    PRIMARY KEY (session_id, idx)
);
CREATE TABLE IF NOT EXISTS routine (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project(id),
    prompt TEXT NOT NULL,
    interval_s INTEGER NOT NULL,
    next_run REAL NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_session_project ON session(project_id);
CREATE INDEX IF NOT EXISTS idx_project_folder ON project_folder(project_id);
CREATE INDEX IF NOT EXISTS idx_session_parent ON session(parent_session_id);
CREATE INDEX IF NOT EXISTS idx_message_session ON message(session_id);
CREATE INDEX IF NOT EXISTS idx_part_message ON part(message_id);
"""


def _id() -> str:
    return uuid.uuid4().hex


@dataclass
class Project:
    id: str
    path: str
    name: str
    last_opened_at: float
    instructions: str = ""
    default_agent: str = ""
    default_model: str = ""


@dataclass
class ProjectAsset:
    project_id: str
    path: str    # copy under <global>/assets/<project_id>/
    name: str    # original file name
    added_at: float


@dataclass
class Session:
    id: str
    project_id: str
    parent_session_id: str | None
    title: str
    agent: str
    model: str
    created_at: float
    updated_at: float
    archived: bool
    directory: str = ""   # per-session working dir ('' = project default)
    permission_mode: str | None = None


@dataclass
class Message:
    id: str
    session_id: str
    role: str
    created_at: float
    model: str | None = None
    provider: str | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost: float = 0.0
    error: str | None = None


@dataclass
class Part:
    id: str
    message_id: str
    idx: int
    type: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class Todo:
    content: str
    status: str = "pending"


@dataclass
class Routine:
    id: str
    project_id: str
    prompt: str
    interval_s: int
    next_run: float
    enabled: bool
    created_at: float


@dataclass
class GoalItem:
    id: str
    project_id: str
    prompt: str
    status: str          # pending | running | done | error | cancelled
    session_id: str | None
    created_at: float


class Store:
    def __init__(self, db_path: Path | str):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(self.db_path)
        self._db.row_factory = aiosqlite.Row
        await self._db.execute("PRAGMA journal_mode=WAL")
        await self._db.execute("PRAGMA foreign_keys=ON")
        await self._db.executescript(_SCHEMA)
        # migration: sessions gained a per-session working directory
        cur = await self._db.execute("PRAGMA table_info(session)")
        cols = [r[1] for r in await cur.fetchall()]
        if "directory" not in cols:
            await self._db.execute(
                "ALTER TABLE session ADD COLUMN directory TEXT NOT NULL DEFAULT ''")
        if "permission_mode" not in cols:
            await self._db.execute(
                "ALTER TABLE session ADD COLUMN permission_mode TEXT")
        # migration: projects gained Claude-style custom instructions
        cur = await self._db.execute("PRAGMA table_info(project)")
        pcols = [r[1] for r in await cur.fetchall()]
        if "instructions" not in pcols:
            await self._db.execute(
                "ALTER TABLE project ADD COLUMN instructions TEXT NOT NULL DEFAULT ''")
        # migration: per-project agent/model defaults for new sessions
        if "default_agent" not in pcols:
            await self._db.execute(
                "ALTER TABLE project ADD COLUMN default_agent TEXT NOT NULL DEFAULT ''")
            await self._db.execute(
                "ALTER TABLE project ADD COLUMN default_model TEXT NOT NULL DEFAULT ''")
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    @property
    def db(self) -> aiosqlite.Connection:
        assert self._db is not None, "store not opened"
        return self._db

    # -- project ------------------------------------------------------------

    async def open_project(self, path: str, name: str | None = None) -> Project:
        now = time.time()
        row = await self._fetchone("SELECT * FROM project WHERE path=?", (path,))
        if row:
            await self.db.execute("UPDATE project SET last_opened_at=? WHERE id=?", (now, row["id"]))
            await self.db.commit()
            return self._row_to_project(row)
        pid = _id()
        name = name or Path(path).name
        await self.db.execute(
            "INSERT INTO project (id, path, name, last_opened_at) VALUES (?,?,?,?)",
            (pid, path, name, now),
        )
        await self.db.commit()
        return Project(pid, path, name, now)

    async def create_project(self, path: str, name: str | None = None) -> Project:
        now = time.time()
        pid = _id()
        name = name or Path(path).name
        await self._execute(
            "INSERT INTO project (id, path, name, last_opened_at) VALUES (?,?,?,?)",
            (pid, path, name, now),
        )
        return self._row_to_project((pid, path, name, now))

    async def list_projects(self) -> list[Project]:
        rows = await self._fetchall("SELECT * FROM project ORDER BY last_opened_at DESC")
        return [self._row_to_project(r) for r in rows]

    async def add_project(self, path: Path | str, name: str | None = None) -> Project:
        path_str = str(path)
        now = time.time()
        pid = _id()
        name = name or Path(path_str).name
        await self.db.execute(
            "INSERT INTO project (id, path, name, last_opened_at) VALUES (?,?,?,?)",
            (pid, path_str, name, now),
        )
        await self.db.commit()
        return Project(pid, path_str, name, now)

    async def get_project(self, project_id: str) -> Project | None:
        row = await self._fetchone("SELECT * FROM project WHERE id=?", (project_id,))
        return self._row_to_project(row) if row else None

    async def set_project_instructions(self, project_id: str, text: str) -> None:
        await self.db.execute(
            "UPDATE project SET instructions=? WHERE id=?", (text, project_id))
        await self.db.commit()

    async def set_project_defaults(
        self, project_id: str, agent: str = "", model: str = ""
    ) -> None:
        """Default agent/model applied to new sessions in the project."""
        await self.db.execute(
            "UPDATE project SET default_agent=?, default_model=? WHERE id=?",
            (agent, model, project_id))
        await self.db.commit()

    async def rename_project(self, project_id: str, new_name: str) -> None:
        now = time.time()
        await self.db.execute(
            "UPDATE project SET name=?, last_opened_at=? WHERE id=?",
            (new_name, now, project_id),
        )
        await self.db.commit()

    async def delete_project(self, project_id: str) -> None:
        # delete folders/assets first (FK not enforced on these tables)
        await self.db.execute("DELETE FROM project_folder WHERE project_id=?", (project_id,))
        await self.db.execute("DELETE FROM project_asset WHERE project_id=?", (project_id,))
        # delete all sessions under the project (cascades via FK on message/todo)
        sessions = await self._fetchall("SELECT id FROM session WHERE project_id=?", (project_id,))
        for (sid,) in sessions:
            await self.delete_session(sid)
        await self.db.execute("DELETE FROM project WHERE id=?", (project_id,))
        await self.db.commit()

    # -- project folders ----------------------------------------------------

    async def list_project_folders(self, project_id: str) -> list[str]:
        rows = await self._fetchall(
            "SELECT path FROM project_folder WHERE project_id=? ORDER BY path",
            (project_id,),
        )
        return [r["path"] for r in rows]

    async def add_project_folder(self, project_id: str, path: Path | str) -> None:
        await self.db.execute(
            "INSERT OR IGNORE INTO project_folder (project_id, path) VALUES (?,?)",
            (project_id, str(path)),
        )
        await self.db.commit()

    async def remove_project_folder(self, project_id: str, path: Path | str) -> None:
        await self.db.execute(
            "DELETE FROM project_folder WHERE project_id=? AND path=?",
            (project_id, str(path)),
        )
        await self.db.commit()

    # -- project assets (Claude-style knowledge) ------------------------------

    async def list_project_assets(self, project_id: str) -> list[ProjectAsset]:
        rows = await self._fetchall(
            "SELECT * FROM project_asset WHERE project_id=? ORDER BY name",
            (project_id,),
        )
        return [ProjectAsset(r["project_id"], r["path"], r["name"], r["added_at"])
                for r in rows]

    async def add_project_asset(
        self, project_id: str, path: Path | str, name: str
    ) -> ProjectAsset:
        now = time.time()
        await self.db.execute(
            "INSERT OR REPLACE INTO project_asset (project_id, path, name, added_at)"
            " VALUES (?,?,?,?)",
            (project_id, str(path), name, now),
        )
        await self.db.commit()
        return ProjectAsset(project_id, str(path), name, now)

    async def remove_project_asset(self, project_id: str, path: Path | str) -> None:
        await self.db.execute(
            "DELETE FROM project_asset WHERE project_id=? AND path=?",
            (project_id, str(path)),
        )
        await self.db.commit()

    # -- session ------------------------------------------------------------

    async def create_session(
        self,
        project_id: str,
        agent: str = "build",
        model: str = "",
        parent_session_id: str | None = None,
        title: str = "",
        directory: str = "",
        permission_mode: str | None = None,
    ) -> Session:
        now = time.time()
        sid = _id()
        await self.db.execute(
            "INSERT INTO session (id, project_id, parent_session_id, title, agent, model,"
            " directory, created_at, updated_at, archived, permission_mode) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (sid, project_id, parent_session_id, title, agent, model, directory, now, now, 0, permission_mode),
        )
        await self.db.commit()
        return Session(sid, project_id, parent_session_id, title, agent, model,
                       now, now, False, directory, permission_mode)

    async def get_session(self, session_id: str) -> Session | None:
        row = await self._fetchone("SELECT * FROM session WHERE id=?", (session_id,))
        return self._session(row) if row else None

    async def list_sessions(self, project_id: str, include_children: bool = False) -> list[Session]:
        q = "SELECT * FROM session WHERE project_id=? AND archived=0"
        if not include_children:
            q += " AND parent_session_id IS NULL"
        q += " ORDER BY updated_at DESC"
        rows = await self._fetchall(q, (project_id,))
        return [self._session(r) for r in rows]

    async def child_sessions(self, session_id: str) -> list[Session]:
        rows = await self._fetchall(
            "SELECT * FROM session WHERE parent_session_id=? ORDER BY created_at", (session_id,)
        )
        return [self._session(r) for r in rows]

    async def delete_session(self, session_id: str) -> None:
        """Hard-delete a session, its children, and all attached rows."""
        for child in await self.child_sessions(session_id):
            await self.delete_session(child.id)
        await self.db.execute("DELETE FROM todo WHERE session_id=?", (session_id,))
        await self.db.execute(
            "DELETE FROM part WHERE message_id IN (SELECT id FROM message WHERE session_id=?)",
            (session_id,),
        )
        await self.db.execute("DELETE FROM message WHERE session_id=?", (session_id,))
        await self.db.execute("DELETE FROM session WHERE id=?", (session_id,))
        await self.db.commit()

    async def update_session(self, session_id: str, **fields: Any) -> None:
        fields["updated_at"] = time.time()
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.db.execute(f"UPDATE session SET {cols} WHERE id=?", (*fields.values(), session_id))
        await self.db.commit()

    async def update_session_project(self, session_id: str, new_project_id: str) -> None:
        now = time.time()
        await self.db.execute(
            "UPDATE session SET project_id=?, updated_at=? WHERE id=?",
            (new_project_id, now, session_id),
        )
        await self.db.commit()

    async def set_session_mode(self, session_id: str, mode: str | None) -> None:
        now = time.time()
        await self.db.execute(
            "UPDATE session SET permission_mode=?, updated_at=? WHERE id=?",
            (mode, now, session_id),
        )
        await self.db.commit()

    # -- message / part -----------------------------------------------------

    async def add_message(self, session_id: str, role: str, **fields: Any) -> Message:
        mid = _id()
        now = time.time()
        msg = Message(mid, session_id, role, now, **fields)
        await self.db.execute(
            "INSERT INTO message (id, session_id, role, created_at, model, provider,"
            " tokens_in, tokens_out, cost, error) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (mid, session_id, role, now, msg.model, msg.provider, msg.tokens_in,
             msg.tokens_out, msg.cost, msg.error),
        )
        await self.db.execute("UPDATE session SET updated_at=? WHERE id=?", (now, session_id))
        await self.db.commit()
        return msg

    async def update_message(self, message_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.db.execute(f"UPDATE message SET {cols} WHERE id=?", (*fields.values(), message_id))
        await self.db.commit()

    async def message_count(self, session_id: str) -> int:
        row = await self._fetchone(
            "SELECT COUNT(*) AS n FROM message WHERE session_id=?", (session_id,)
        )
        return int(row["n"]) if row else 0

    async def list_messages(self, session_id: str) -> list[Message]:
        rows = await self._fetchall(
            "SELECT * FROM message WHERE session_id=? ORDER BY created_at, id", (session_id,)
        )
        return [
            Message(r["id"], r["session_id"], r["role"], r["created_at"], r["model"],
                    r["provider"], r["tokens_in"], r["tokens_out"], r["cost"], r["error"])
            for r in rows
        ]

    async def add_part(self, message_id: str, type: str, data: dict[str, Any], idx: int | None = None) -> Part:
        if idx is None:
            row = await self._fetchone(
                "SELECT COALESCE(MAX(idx), -1) + 1 AS nxt FROM part WHERE message_id=?", (message_id,)
            )
            idx = row["nxt"]
        pid = _id()
        await self.db.execute(
            "INSERT INTO part (id, message_id, idx, type, json) VALUES (?,?,?,?,?)",
            (pid, message_id, idx, type, json.dumps(data)),
        )
        await self.db.commit()
        return Part(pid, message_id, idx, type, data)

    async def update_part(self, part_id: str, data: dict[str, Any]) -> None:
        await self.db.execute("UPDATE part SET json=? WHERE id=?", (json.dumps(data), part_id))
        await self.db.commit()

    async def list_parts(self, message_id: str) -> list[Part]:
        rows = await self._fetchall(
            "SELECT * FROM part WHERE message_id=? ORDER BY idx", (message_id,)
        )
        return [Part(r["id"], r["message_id"], r["idx"], r["type"], json.loads(r["json"])) for r in rows]

    async def session_parts(
        self,
        session_id: str,
        limit: int | None = None,
        before: float | None = None,
    ) -> list[tuple[Message, list[Part]]]:
        """Return messages+parts for a session.

        When limit is given, returns the **most recent** messages first
        (newest → oldest).  If before is also given, only messages older
        than that timestamp are considered (used for pagination).
        """
        # the LIMIT must count messages, not joined message×part rows, so a
        # paged read selects the message window first and joins parts onto it
        message_src = "message"
        params: list = []
        where = "m.session_id=?"
        where_params: list = [session_id]
        if before is not None:
            where += " AND m.created_at < ?"
            where_params.append(before)
        if limit is not None:
            message_src = (
                "(SELECT * FROM message m WHERE " + where +
                " ORDER BY m.created_at DESC, m.id DESC LIMIT ?)"
            )
            params += [*where_params, limit]
            where = "1=1"
            where_params = []
        q = (
            "SELECT m.id AS mid, m.session_id, m.role, m.created_at, m.model,"
            " m.provider, m.tokens_in, m.tokens_out, m.cost, m.error,"
            " p.id AS pid, p.idx, p.type AS ptype, p.json AS pjson"
            f" FROM {message_src} m LEFT JOIN part p ON p.message_id = m.id"
            f" WHERE {where}"
        )
        params += where_params
        order = "DESC" if limit is not None else "ASC"
        q += f" ORDER BY m.created_at {order}, m.id {order}, p.idx"
        rows = await self._fetchall(q, tuple(params))
        out: list[tuple[Message, list[Part]]] = []
        current_id = None
        for r in rows:
            if r["mid"] != current_id:
                current_id = r["mid"]
                out.append((
                    Message(r["mid"], r["session_id"], r["role"], r["created_at"],
                            r["model"], r["provider"], r["tokens_in"], r["tokens_out"],
                            r["cost"], r["error"]),
                    [],
                ))
            if r["pid"] is not None:
                out[-1][1].append(
                    Part(r["pid"], r["mid"], r["idx"], r["ptype"], json.loads(r["pjson"])))
        if limit is not None:
            out.reverse()          # caller expects chronological order
        return out

    async def session_parts_page(
        self, session_id: str, page_size: int = 50
    ) -> list[tuple[Message, list[Part]]]:
        """Convenience wrapper: returns the most recent page_size messages."""
        return await self.session_parts(session_id, limit=page_size)

    async def delete_messages_from(self, session_id: str, message_id: str) -> None:
        """Delete a message and everything after it (edit-and-rerun fork).

        "After" follows the transcript order (created_at, id) used everywhere
        else, so ties on created_at cut consistently.
        """
        row = await self._fetchone(
            "SELECT created_at, id FROM message WHERE id=? AND session_id=?",
            (message_id, session_id),
        )
        if row is None:
            return
        doomed = (
            "SELECT id FROM message WHERE session_id=? AND"
            " (created_at > ? OR (created_at = ? AND id >= ?))"
        )
        args = (session_id, row["created_at"], row["created_at"], row["id"])
        await self.db.execute(f"DELETE FROM part WHERE message_id IN ({doomed})", args)
        await self.db.execute(f"DELETE FROM message WHERE id IN ({doomed})", args)
        await self.db.commit()

    async def search_sessions(
        self, query: str, project_id: str | None = None, limit: int = 30
    ) -> list[tuple[Session, str]]:
        """Top-level sessions whose title or transcript matches ``query``.

        Returns (session, snippet) pairs, newest first. The snippet is the
        matching text part excerpt ('' for title-only matches).
        """
        like = f"%{query}%"
        proj = " AND s.project_id=?" if project_id else ""
        proj_args: tuple = (project_id,) if project_id else ()
        # transcript matches: latest matching text/reasoning part per session
        rows = await self._fetchall(
            "SELECT s.*, p.json AS pjson, MAX(m.created_at) AS match_at"
            " FROM part p JOIN message m ON m.id = p.message_id"
            " JOIN session s ON s.id = m.session_id"
            " WHERE p.type IN ('text', 'reasoning') AND p.json LIKE ?"
            f" AND s.parent_session_id IS NULL AND s.archived = 0{proj}"
            " GROUP BY s.id ORDER BY match_at DESC LIMIT ?",
            (like, *proj_args, limit),
        )
        out: list[tuple[Session, str]] = []
        seen: set[str] = set()
        for r in rows:
            text = str(json.loads(r["pjson"]).get("text", ""))
            at = text.lower().find(query.lower())
            snippet = text[max(0, at - 40): at + len(query) + 60].strip() if at >= 0 else ""
            out.append((self._session(r), snippet))
            seen.add(r["id"])
        # title matches fill remaining slots
        rows = await self._fetchall(
            "SELECT s.* FROM session s WHERE s.title LIKE ?"
            f" AND s.parent_session_id IS NULL AND s.archived = 0{proj}"
            " ORDER BY s.updated_at DESC LIMIT ?",
            (like, *proj_args, limit),
        )
        for r in rows:
            if r["id"] not in seen and len(out) < limit:
                out.append((self._session(r), ""))
        return out

    # -- goal queue (walk-away workflows) ------------------------------------

    async def add_goal(self, project_id: str, prompt: str) -> GoalItem:
        gid = _id()
        now = time.time()
        await self.db.execute(
            "INSERT INTO goal_queue (id, project_id, prompt, status, session_id, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (gid, project_id, prompt, "pending", None, now),
        )
        await self.db.commit()
        return GoalItem(gid, project_id, prompt, "pending", None, now)

    async def list_goals(self, project_id: str) -> list[GoalItem]:
        rows = await self._fetchall(
            "SELECT * FROM goal_queue WHERE project_id=? ORDER BY created_at",
            (project_id,),
        )
        return [self._goal(r) for r in rows]

    async def update_goal(self, goal_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        await self.db.execute(
            f"UPDATE goal_queue SET {cols} WHERE id=?", (*fields.values(), goal_id))
        await self.db.commit()

    async def next_pending_goal(self, project_id: str) -> GoalItem | None:
        row = await self._fetchone(
            "SELECT * FROM goal_queue WHERE project_id=? AND status='pending'"
            " ORDER BY created_at LIMIT 1", (project_id,))
        return self._goal(row) if row else None

    async def running_goal(self, project_id: str) -> GoalItem | None:
        row = await self._fetchone(
            "SELECT * FROM goal_queue WHERE project_id=? AND status='running'"
            " ORDER BY created_at LIMIT 1", (project_id,))
        return self._goal(row) if row else None

    async def goal_for_session(self, session_id: str) -> GoalItem | None:
        row = await self._fetchone(
            "SELECT * FROM goal_queue WHERE session_id=? AND status='running'",
            (session_id,))
        return self._goal(row) if row else None

    @staticmethod
    def _goal(r: aiosqlite.Row) -> GoalItem:
        return GoalItem(r["id"], r["project_id"], r["prompt"], r["status"],
                        r["session_id"], r["created_at"])

    # -- usage ---------------------------------------------------------------

    # -- routines (recurring goals) ------------------------------------------

    async def add_routine(self, project_id: str, prompt: str, interval_s: int) -> Routine:
        rid = _id()
        now = time.time()
        routine = Routine(rid, project_id, prompt, interval_s, now + interval_s, True, now)
        await self._execute(
            "INSERT INTO routine (id, project_id, prompt, interval_s, next_run,"
            " enabled, created_at) VALUES (?,?,?,?,?,1,?)",
            (rid, project_id, prompt, interval_s, routine.next_run, now),
        )
        return routine

    async def list_routines(self, project_id: str | None = None) -> list[Routine]:
        where, args = ("WHERE project_id=?", (project_id,)) if project_id else ("", ())
        rows = await self._fetchall(
            f"SELECT * FROM routine {where} ORDER BY created_at", args)
        return [Routine(r["id"], r["project_id"], r["prompt"], r["interval_s"],
                        r["next_run"], bool(r["enabled"]), r["created_at"])
                for r in rows]

    async def due_routines(self, now: float) -> list[Routine]:
        rows = await self._fetchall(
            "SELECT * FROM routine WHERE enabled=1 AND next_run <= ?", (now,))
        return [Routine(r["id"], r["project_id"], r["prompt"], r["interval_s"],
                        r["next_run"], bool(r["enabled"]), r["created_at"])
                for r in rows]

    async def update_routine(self, routine_id: str, **fields: Any) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        await self._execute(
            f"UPDATE routine SET {cols} WHERE id=?", (*fields.values(), routine_id))

    async def delete_routine(self, routine_id: str) -> None:
        await self._execute("DELETE FROM routine WHERE id=?", (routine_id,))

    async def last_usage(self, session_id: str) -> tuple[int, str]:
        """(tokens_in, model_id) of the session's most recent metered turn."""
        row = await self._fetchone(
            "SELECT tokens_in, model FROM message WHERE session_id=? AND role='assistant'"
            " AND tokens_in > 0 ORDER BY created_at DESC, id DESC LIMIT 1",
            (session_id,),
        )
        return (int(row["tokens_in"]), row["model"] or "") if row else (0, "")

    # -- todo ---------------------------------------------------------------

    async def set_todos(self, session_id: str, todos: list[Todo]) -> None:
        await self.db.execute("DELETE FROM todo WHERE session_id=?", (session_id,))
        for i, t in enumerate(todos):
            await self.db.execute(
                "INSERT INTO todo (session_id, idx, content, status) VALUES (?,?,?,?)",
                (session_id, i, t.content, t.status),
            )
        await self.db.commit()

    async def get_todos(self, session_id: str) -> list[Todo]:
        rows = await self._fetchall(
            "SELECT * FROM todo WHERE session_id=? ORDER BY idx", (session_id,)
        )
        return [Todo(r["content"], r["status"]) for r in rows]

    # -- helpers ------------------------------------------------------------

    @staticmethod
    def _row_to_project(r: aiosqlite.Row | tuple) -> Project:
        if isinstance(r, tuple):
            return Project(*r)

        def col(name: str) -> str:
            return r[name] if name in r.keys() else ""

        return Project(r["id"], r["path"], r["name"], r["last_opened_at"],
                       col("instructions"), col("default_agent"), col("default_model"))

    @staticmethod
    def _session(r: aiosqlite.Row) -> Session:
        directory = r["directory"] if "directory" in r.keys() else ""
        pmode = r["permission_mode"] if "permission_mode" in r.keys() else None
        return Session(r["id"], r["project_id"], r["parent_session_id"], r["title"], r["agent"],
                       r["model"], r["created_at"], r["updated_at"], bool(r["archived"]),
                       directory, pmode)

    async def _execute(self, q: str, args: tuple = ()) -> None:
        await self.db.execute(q, args)
        await self.db.commit()

    async def _fetchone(self, q: str, args: tuple = ()) -> aiosqlite.Row | None:
        cur = await self.db.execute(q, args)
        return await cur.fetchone()

    async def _fetchall(self, q: str, args: tuple = ()) -> list[aiosqlite.Row]:
        cur = await self.db.execute(q, args)
        return list(await cur.fetchall())
