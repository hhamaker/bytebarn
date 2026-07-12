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
    archived INTEGER NOT NULL DEFAULT 0
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
CREATE TABLE IF NOT EXISTS todo (
    session_id TEXT NOT NULL REFERENCES session(id),
    idx INTEGER NOT NULL,
    content TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','in_progress','completed')),
    PRIMARY KEY (session_id, idx)
);
CREATE INDEX IF NOT EXISTS idx_session_project ON session(project_id);
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
            return Project(row["id"], row["path"], row["name"], now)
        pid = _id()
        name = name or Path(path).name
        await self.db.execute(
            "INSERT INTO project (id, path, name, last_opened_at) VALUES (?,?,?,?)",
            (pid, path, name, now),
        )
        await self.db.commit()
        return Project(pid, path, name, now)

    # -- session ------------------------------------------------------------

    async def create_session(
        self,
        project_id: str,
        agent: str = "build",
        model: str = "",
        parent_session_id: str | None = None,
        title: str = "",
        directory: str = "",
    ) -> Session:
        now = time.time()
        sid = _id()
        await self.db.execute(
            "INSERT INTO session (id, project_id, parent_session_id, title, agent, model,"
            " directory, created_at, updated_at, archived) VALUES (?,?,?,?,?,?,?,?,?,0)",
            (sid, project_id, parent_session_id, title, agent, model, directory, now, now),
        )
        await self.db.commit()
        return Session(sid, project_id, parent_session_id, title, agent, model,
                       now, now, False, directory)

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

    async def session_parts(self, session_id: str) -> list[tuple[Message, list[Part]]]:
        """All messages with their parts in one JOIN — this is the runner's
        per-step hot path; per-message queries made long sessions O(n²)."""
        rows = await self._fetchall(
            "SELECT m.id AS mid, m.session_id, m.role, m.created_at, m.model,"
            " m.provider, m.tokens_in, m.tokens_out, m.cost, m.error,"
            " p.id AS pid, p.idx, p.type AS ptype, p.json AS pjson"
            " FROM message m LEFT JOIN part p ON p.message_id = m.id"
            " WHERE m.session_id=?"
            " ORDER BY m.created_at, m.id, p.idx",
            (session_id,),
        )
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
        return out

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
    def _session(r: aiosqlite.Row) -> Session:
        directory = r["directory"] if "directory" in r.keys() else ""
        return Session(r["id"], r["project_id"], r["parent_session_id"], r["title"], r["agent"],
                       r["model"], r["created_at"], r["updated_at"], bool(r["archived"]),
                       directory)

    async def _fetchone(self, q: str, args: tuple = ()) -> aiosqlite.Row | None:
        cur = await self.db.execute(q, args)
        return await cur.fetchone()

    async def _fetchall(self, q: str, args: tuple = ()) -> list[aiosqlite.Row]:
        cur = await self.db.execute(q, args)
        return list(await cur.fetchall())
