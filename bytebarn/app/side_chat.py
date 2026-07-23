"""Side chat: a quick throwaway question without touching the main thread.

Opens a small overlay with its own hidden session (parented to the current
one, so it never appears in the sidebar). Ask, read, close — the main
conversation's context stays clean.
"""

from __future__ import annotations

import asyncio

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QLabel, QPlainTextEdit, QVBoxLayout

from .transcript import Transcript


class SideChatDialog(QDialog):
    def __init__(self, engine, model: str, parent_session_id: str | None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._model = model
        self._parent_session_id = parent_session_id
        self._session_id: str | None = None
        self.setWindowTitle("Side chat")
        self.resize(520, 480)

        layout = QVBoxLayout(self)
        hint = QLabel("Quick question on the side — this chat is throwaway and"
                      " never joins the main conversation's context.")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        self.transcript = Transcript()
        layout.addWidget(self.transcript, 1)
        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("Ask — Enter to send")
        self.editor.setMaximumHeight(64)
        self.editor.installEventFilter(self)
        layout.addWidget(self.editor)

    def eventFilter(self, obj, event) -> bool:
        from PySide6.QtCore import QEvent

        if obj is self.editor and event.type() == QEvent.KeyPress:
            if event.key() in (Qt.Key_Return, Qt.Key_Enter) \
                    and not event.modifiers() & Qt.ShiftModifier:
                self._send()
                return True
        return super().eventFilter(obj, event)

    def _send(self) -> None:
        text = self.editor.toPlainText().strip()
        if not text:
            return
        self.editor.clear()
        self.transcript.add_user_text(f"side-{id(text)}", text)
        self.transcript.show_thinking("chat")
        asyncio.ensure_future(self._run(text))

    async def _run(self, text: str) -> None:
        if self._session_id is None:
            session = await self.engine.store.create_session(
                self.engine.project.id, agent="chat", model=self._model,
                parent_session_id=self._parent_session_id, title="side chat")
            self._session_id = session.id
        sid = self._session_id
        await self.engine.submit_prompt(sid, text, model=self._model or None)
        for _ in range(3000):  # ≤5 min
            await asyncio.sleep(0.1)
            if not self.engine.is_running(sid):
                break
        history = await self.engine.store.session_parts(sid)
        self.transcript.dismiss_thinking()
        self.transcript.load_history(history)
