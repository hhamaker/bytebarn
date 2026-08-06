"""Terminal Manager — real VT view for shells + plain log for backend tees."""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QKeyEvent,
    QPainter,
    QPaintEvent,
    QResizeEvent,
    QShowEvent,
    QTextCursor,
    QWheelEvent,
)
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .pty_session import PtySession, spawn_shell
from .vt import Cell, Color, VtScreen

# VS Code Dark+ style palette (default + 16 ANSI)
_PALETTE = {
    Color.DEFAULT: None,  # resolved against fg/bg defaults
    Color.BLACK: QColor("#1e1e1e"),
    Color.RED: QColor("#f44747"),
    Color.GREEN: QColor("#6a9955"),
    Color.YELLOW: QColor("#d7ba7d"),
    Color.BLUE: QColor("#569cd6"),
    Color.MAGENTA: QColor("#c586c0"),
    Color.CYAN: QColor("#4ec9b0"),
    Color.WHITE: QColor("#d4d4d4"),
    Color.BRIGHT_BLACK: QColor("#808080"),
    Color.BRIGHT_RED: QColor("#f44747"),
    Color.BRIGHT_GREEN: QColor("#b5cea8"),
    Color.BRIGHT_YELLOW: QColor("#dcdcaa"),
    Color.BRIGHT_BLUE: QColor("#9cdcfe"),
    Color.BRIGHT_MAGENTA: QColor("#c586c0"),
    Color.BRIGHT_CYAN: QColor("#4ec9b0"),
    Color.BRIGHT_WHITE: QColor("#ffffff"),
}
_DEFAULT_FG = QColor("#d4d4d4")
_DEFAULT_BG = QColor("#1e1e1e")
_CURSOR_COLOR = QColor("#aeafad")
_SELECTION_BG = QColor(85, 85, 255, 80)


def _mono_font(point_size: int = 12) -> QFont:
    font = QFont("Menlo")
    if not font.exactMatch():
        font = QFont("ui-monospace")
    font.setStyleHint(QFont.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(point_size)
    return font


def _resolve(color_idx: int, *, is_fg: bool) -> QColor:
    if color_idx == Color.DEFAULT or color_idx not in _PALETTE:
        return _DEFAULT_FG if is_fg else _DEFAULT_BG
    c = _PALETTE[color_idx]
    return c if c is not None else (_DEFAULT_FG if is_fg else _DEFAULT_BG)


class TerminalView(QWidget):
    """Painted cell-grid terminal backed by VtScreen + optional PtySession."""

    def __init__(self, interactive: bool = False, parent=None):
        super().__init__(parent)
        self._interactive = interactive
        self._pty: PtySession | None = None
        self._screen = VtScreen(cols=80, rows=24)
        self._scroll_offset = 0  # lines into scrollback (0 = live)
        self._font = _mono_font(12)
        self._fm = QFontMetrics(self._font)
        self._cell_w = max(1, self._fm.horizontalAdvance("M"))
        self._cell_h = max(1, self._fm.height())
        self._baseline = self._fm.ascent()
        self._cursor_on = True
        self._blink = QTimer(self)
        self._blink.setInterval(530)
        self._blink.timeout.connect(self._toggle_cursor)
        self._blink.start()
        self._repaint_pending = False
        self._coalesce = QTimer(self)
        self._coalesce.setSingleShot(True)
        self._coalesce.setInterval(16)  # ~60 fps
        self._coalesce.timeout.connect(self._do_repaint)

        self.setFocusPolicy(Qt.StrongFocus)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(QSize(120, 60))
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WA_OpaquePaintEvent, True)
        self.setCursor(Qt.IBeamCursor)

    # -- public --------------------------------------------------------------

    def set_pty(self, session: PtySession | None) -> None:
        self._pty = session
        self._interactive = session is not None
        if session is not None:
            # Prefer the widget geometry once it has a real size; otherwise keep
            # the winsize the PTY was spawned with (avoid 1×1 clobber).
            cols, rows = self._cols_rows_for_size(self.size())
            if self.width() >= self._cell_w * 4 and self.height() >= self._cell_h * 2:
                self._screen.resize(rows, cols)
                session.resize(rows, cols)
            else:
                self._screen.resize(session.rows, session.cols)

    def feed(self, text: str) -> None:
        if not text:
            return
        # Live follow: stick to bottom unless user scrolled up.
        follow = self._scroll_offset == 0
        self._screen.feed(text)
        if follow:
            self._scroll_offset = 0
        self._schedule_repaint()

    def append_text(self, text: str) -> None:
        """Compatibility alias used by the panel for both modes."""
        self.feed(text)

    def set_text(self, text: str) -> None:
        self._screen.clear()
        self._screen.feed(text or "")
        self._scroll_offset = 0
        self._schedule_repaint()

    def clear(self) -> None:
        self._screen.clear()
        self._scroll_offset = 0
        self.update()

    def toPlainText(self) -> str:
        lines = []
        for row in self._screen.scrollback:
            lines.append("".join(c.char for c in row).rstrip())
        for row in self._screen.buffer:
            lines.append("".join(c.char for c in row).rstrip())
        return "\n".join(lines)

    # -- sizing --------------------------------------------------------------

    def _cols_rows_for_size(self, size: QSize) -> tuple[int, int]:
        cols = max(1, size.width() // self._cell_w)
        rows = max(1, size.height() // self._cell_h)
        return cols, rows

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_size(event.size())

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # First show often lands after a 0×0 layout pass — sync winsize now.
        self._sync_size(self.size())

    def _sync_size(self, size: QSize) -> None:
        # Ignore transient 0×0 / tiny layouts while the splitter is collapsing.
        if size.width() < self._cell_w * 4 or size.height() < self._cell_h * 2:
            return
        cols, rows = self._cols_rows_for_size(size)
        if cols != self._screen.cols or rows != self._screen.rows:
            self._screen.resize(rows, cols)
            if self._pty is not None:
                self._pty.resize(rows, cols)
            self._scroll_offset = min(
                self._scroll_offset, len(self._screen.scrollback))
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(self._cell_w * 80, self._cell_h * 24)

    # -- paint ---------------------------------------------------------------

    def _toggle_cursor(self) -> None:
        self._cursor_on = not self._cursor_on
        if self._interactive and self._scroll_offset == 0:
            self.update()

    def _schedule_repaint(self) -> None:
        if not self._coalesce.isActive():
            self._coalesce.start()

    def _do_repaint(self) -> None:
        self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        p = QPainter(self)
        p.fillRect(self.rect(), _DEFAULT_BG)
        p.setFont(self._font)
        lines = self._screen.display_lines(scrollback_offset=self._scroll_offset)
        cw, ch = self._cell_w, self._cell_h
        show_cursor = (
            self._interactive
            and self._screen.cursor_visible
            and self._cursor_on
            and self._scroll_offset == 0
            and self.hasFocus()
        )
        cx, cy = self._screen.cursor_x, self._screen.cursor_y

        for y, row in enumerate(lines):
            x = 0
            # run-length paint by (fg,bg,bold,underline,inverse)
            while x < len(row):
                cell = row[x]
                run_end = x + 1
                while run_end < len(row) and self._same_style(row[run_end], cell):
                    run_end += 1
                text = "".join(row[i].char for i in range(x, run_end))
                fg = _resolve(cell.fg, is_fg=True)
                bg = _resolve(cell.bg, is_fg=False)
                if cell.inverse:
                    fg, bg = bg, fg
                if cell.bold:
                    font = QFont(self._font)
                    font.setBold(True)
                    p.setFont(font)
                else:
                    p.setFont(self._font)
                rect = QRect(x * cw, y * ch, (run_end - x) * cw, ch)
                if bg != _DEFAULT_BG:
                    p.fillRect(rect, bg)
                p.setPen(fg)
                # Draw per-cell so fixed pitch is exact even with bold.
                for i, ch_s in enumerate(text):
                    if ch_s != " ":
                        p.drawText(
                            QPoint((x + i) * cw, y * ch + self._baseline), ch_s)
                if cell.underline:
                    p.drawLine(
                        x * cw, y * ch + ch - 1,
                        run_end * cw, y * ch + ch - 1,
                    )
                x = run_end

            # pad rest of row
            if len(row) < self._screen.cols:
                pass

        if show_cursor and 0 <= cy < len(lines) and 0 <= cx < self._screen.cols:
            crect = QRect(cx * cw, cy * ch, cw, ch)
            p.fillRect(crect, _CURSOR_COLOR)
            # redraw glyph under cursor inverted
            if cy < len(lines) and cx < len(lines[cy]):
                cell = lines[cy][cx]
                p.setPen(_DEFAULT_BG)
                if cell.char and cell.char != " ":
                    p.drawText(QPoint(cx * cw, cy * ch + self._baseline), cell.char)

        p.end()

    @staticmethod
    def _same_style(a: Cell, b: Cell) -> bool:
        return (
            a.fg == b.fg and a.bg == b.bg and a.bold == b.bold
            and a.underline == b.underline and a.inverse == b.inverse
            and a.italic == b.italic
        )

    # -- input ---------------------------------------------------------------

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta().y()
        if delta == 0:
            return
        steps = max(1, abs(delta) // 40)
        if delta > 0:
            max_off = len(self._screen.scrollback)
            self._scroll_offset = min(max_off, self._scroll_offset + steps)
        else:
            self._scroll_offset = max(0, self._scroll_offset - steps)
        self.update()
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._interactive or self._pty is None:
            super().keyPressEvent(event)
            return
        # Any key returns to live view
        if self._scroll_offset:
            self._scroll_offset = 0
            self.update()

        key = event.key()
        mods = event.modifiers()
        ctrl = bool(mods & Qt.ControlModifier)
        meta = bool(mods & Qt.MetaModifier)
        alt = bool(mods & Qt.AltModifier)

        # Copy (⌘C / Ctrl+Shift+C) when not sending interrupt
        if key == Qt.Key_C and (meta or (ctrl and mods & Qt.ShiftModifier)):
            self._copy_all()
            return
        if key == Qt.Key_V and (meta or (ctrl and mods & Qt.ShiftModifier)):
            self._paste()
            return

        seq = self._key_to_seq(key, mods, event.text(), ctrl=ctrl, alt=alt)
        if seq is not None:
            self._pty.write(seq)
            return
        super().keyPressEvent(event)

    def _key_to_seq(
        self, key: int, mods, text: str, *, ctrl: bool, alt: bool,
    ) -> str | None:
        # Ctrl+letter → C0
        if ctrl and not (mods & Qt.ShiftModifier) and not alt:
            if Qt.Key_A <= key <= Qt.Key_Z:
                return chr(key - Qt.Key_A + 1)
            if key == Qt.Key_Space:
                return "\x00"
            if key in (Qt.Key_Backslash,):
                return "\x1c"
            if key == Qt.Key_BracketLeft:
                return "\x1b"
            if key == Qt.Key_BracketRight:
                return "\x1d"
            if key == Qt.Key_Minus:
                return "\x1f"

        if key in (Qt.Key_Return, Qt.Key_Enter):
            return "\r"
        if key == Qt.Key_Backspace:
            return "\x7f"
        if key == Qt.Key_Tab:
            if mods & Qt.ShiftModifier:
                return "\x1b[Z"
            return "\t"
        if key == Qt.Key_Escape:
            return "\x1b"
        if key == Qt.Key_Delete:
            return "\x1b[3~"
        if key == Qt.Key_Home:
            return "\x1b[H"
        if key == Qt.Key_End:
            return "\x1b[F"
        if key == Qt.Key_PageUp:
            return "\x1b[5~"
        if key == Qt.Key_PageDown:
            return "\x1b[6~"
        if key == Qt.Key_Insert:
            return "\x1b[2~"
        if key == Qt.Key_Up:
            return "\x1bOA" if False else "\x1b[A"
        if key == Qt.Key_Down:
            return "\x1b[B"
        if key == Qt.Key_Right:
            return "\x1b[C"
        if key == Qt.Key_Left:
            return "\x1b[D"
        if Qt.Key_F1 <= key <= Qt.Key_F12:
            fmaps = {
                Qt.Key_F1: "\x1bOP", Qt.Key_F2: "\x1bOQ",
                Qt.Key_F3: "\x1bOR", Qt.Key_F4: "\x1bOS",
                Qt.Key_F5: "\x1b[15~", Qt.Key_F6: "\x1b[17~",
                Qt.Key_F7: "\x1b[18~", Qt.Key_F8: "\x1b[19~",
                Qt.Key_F9: "\x1b[20~", Qt.Key_F10: "\x1b[21~",
                Qt.Key_F11: "\x1b[23~", Qt.Key_F12: "\x1b[24~",
            }
            return fmaps.get(key)

        if text:
            if alt and len(text) == 1:
                return "\x1b" + text
            return text
        return None

    def _copy_all(self) -> None:
        from PySide6.QtWidgets import QApplication

        QApplication.clipboard().setText(self.toPlainText())

    def _paste(self) -> None:
        from PySide6.QtWidgets import QApplication

        if self._pty is None:
            return
        text = QApplication.clipboard().text()
        if text:
            # bracketed paste if programs expect it — send raw; most shells ok
            self._pty.write(text.replace("\n", "\r"))


class LogTerminalView(QPlainTextEdit):
    """Read-only plain log for backend tees (Claude Code stream-json etc.)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.setMaximumBlockCount(20_000)
        self.setFont(_mono_font(12))
        self.setStyleSheet(
            "QPlainTextEdit { background: #1e1e1e; color: #d4d4d4; border: none; }")
        self.setPlaceholderText("No output yet…")

    def append_text(self, text: str) -> None:
        if not text:
            return
        at_end = (
            self.verticalScrollBar().value()
            >= self.verticalScrollBar().maximum() - 4
        )
        self.moveCursor(QTextCursor.End)
        self.insertPlainText(text)
        if at_end:
            self.moveCursor(QTextCursor.End)

    def set_text(self, text: str) -> None:
        self.setPlainText(text or "")
        self.moveCursor(QTextCursor.End)

    def feed(self, text: str) -> None:
        self.append_text(text)


# Type alias for either view
AnyTermView = TerminalView | LogTerminalView


class TerminalPanel(QWidget):
    """Bottom/side manager: list of terminals + active view."""

    closed = Signal()

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._views: dict[str, AnyTermView] = {}
        self._ptys: dict[str, PtySession] = {}
        self._kinds: dict[str, str] = {}
        self._active_id: str | None = None

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        title = QLabel("<b>Terminals</b>")
        self.new_btn = QPushButton("+ Shell")
        self.new_btn.setFlat(True)
        self.new_btn.setToolTip("Open a new local shell in the project directory")
        self.new_btn.clicked.connect(self._new_shell)
        self.kill_btn = QPushButton("Kill")
        self.kill_btn.setFlat(True)
        self.kill_btn.setToolTip(
            "Kill the selected local shell (or dismiss a finished backend)")
        self.kill_btn.clicked.connect(self._kill_selected)
        self.clear_btn = QPushButton("Clear")
        self.clear_btn.setFlat(True)
        self.clear_btn.clicked.connect(self._clear_view)
        close = QPushButton("✕")
        close.setFlat(True)
        close.setFixedWidth(28)
        close.clicked.connect(self._close)
        bar.addWidget(title)
        bar.addStretch(1)
        bar.addWidget(self.new_btn)
        bar.addWidget(self.kill_btn)
        bar.addWidget(self.clear_btn)
        bar.addWidget(close)

        self.list = QListWidget()
        self.list.setMaximumWidth(220)
        self.list.currentItemChanged.connect(self._on_select)
        self.stack_host = QWidget()
        self.stack_layout = QVBoxLayout(self.stack_host)
        self.stack_layout.setContentsMargins(0, 0, 0, 0)
        self._empty = QLabel(
            "No terminals yet.\n\n"
            "• Claude Code runs appear here automatically.\n"
            "• Click + Shell for a local interactive terminal.")
        self._empty.setWordWrap(True)
        self._empty.setAlignment(Qt.AlignCenter)
        self.stack_layout.addWidget(self._empty)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.list)
        split.addWidget(self.stack_host)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([180, 600])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 8)
        layout.setSpacing(4)
        layout.addLayout(bar)
        layout.addWidget(split, 1)
        self.setMinimumHeight(140)

    # -- public API ----------------------------------------------------------

    def set_engine(self, engine) -> None:
        self.engine = engine
        self.refresh_from_hub()

    def refresh_from_hub(self) -> None:
        if self.engine is None:
            return
        for info in self.engine.terminals.list():
            self._ensure_backend_item(info.id, info.title, info.kind, info.status)
            view = self._views.get(info.id)
            if view is not None:
                snap = self.engine.terminals.snapshot(info.id)
                if snap and not view.toPlainText():
                    view.set_text(snap)

    def handle_event(self, event) -> None:
        name = getattr(event, "name", "")
        if name == "terminal.opened":
            self._ensure_backend_item(
                event.terminal_id, event.title or event.terminal_id,
                event.kind, "running")
            if event.kind != "user" and self.engine is not None:
                view = self._views.get(event.terminal_id)
                if view is not None:
                    view.set_text(self.engine.terminals.snapshot(event.terminal_id))
            self._select_id(event.terminal_id)
        elif name == "terminal.chunk":
            view = self._views.get(event.terminal_id)
            if view is not None:
                view.append_text(event.text)
            else:
                self._ensure_backend_item(
                    event.terminal_id, event.terminal_id, "claude-code", "running")
                view = self._views.get(event.terminal_id)
                if view is not None:
                    if self.engine is not None:
                        view.set_text(self.engine.terminals.snapshot(event.terminal_id))
                    else:
                        view.append_text(event.text)
        elif name == "terminal.closed":
            self._mark_status(event.terminal_id, "exited", event.exit_code)

    async def shutdown(self) -> None:
        for tid, pty in list(self._ptys.items()):
            await pty.close()
        self._ptys.clear()

    # -- internals -----------------------------------------------------------

    def _ensure_backend_item(
        self, tid: str, title: str, kind: str, status: str,
    ) -> None:
        self._kinds[tid] = kind
        if tid not in self._views:
            # Backend tees (claude-code / bash) → plain log.
            # Interactive user shells get TerminalView (set in _spawn_shell).
            if kind == "user":
                view: AnyTermView = TerminalView(interactive=True)
            else:
                view = LogTerminalView()
            view.hide()
            self.stack_layout.addWidget(view)
            self._views[tid] = view
            if self.engine is not None and kind != "user":
                view.set_text(self.engine.terminals.snapshot(tid))
        item = self._find_item(tid)
        label = self._format_label(title, kind, status)
        if item is None:
            item = QListWidgetItem(label)
            item.setData(Qt.UserRole, tid)
            item.setData(Qt.UserRole + 1, kind)
            self.list.addItem(item)
        else:
            item.setText(label)
        self._empty.hide()

    def _format_label(self, title: str, kind: str, status: str) -> str:
        icon = {"claude-code": "◈", "user": "❯", "bash": "$"}.get(kind, "•")
        suffix = "" if status == "running" else " (done)"
        return f"{icon} {title}{suffix}"

    def _find_item(self, tid: str) -> QListWidgetItem | None:
        for i in range(self.list.count()):
            item = self.list.item(i)
            if item and item.data(Qt.UserRole) == tid:
                return item
        return None

    def _select_id(self, tid: str) -> None:
        item = self._find_item(tid)
        if item is not None:
            self.list.setCurrentItem(item)

    def _on_select(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        tid = item.data(Qt.UserRole)
        self._show_view(tid)

    def _show_view(self, tid: str) -> None:
        self._active_id = tid
        for vid, view in self._views.items():
            view.setVisible(vid == tid)
        view = self._views.get(tid)
        if view is not None:
            view.setFocus()

    def _new_shell(self) -> None:
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        loop.create_task(self._spawn_shell())

    async def _spawn_shell(self) -> None:
        cwd = Path.home()
        if self.engine is not None:
            cwd = Path(self.engine.project_dir)
        tid = f"user:{uuid.uuid4().hex[:8]}"

        # Estimate size from stack host so the shell starts with a real winsize
        host = self.stack_host
        fm = QFontMetrics(_mono_font(12))
        cw = max(1, fm.horizontalAdvance("M"))
        ch = max(1, fm.height())
        cols = max(40, (host.width() or 640) // cw)
        rows = max(10, (host.height() or 240) // ch)

        try:
            session = await spawn_shell(
                terminal_id=tid, cwd=cwd, rows=rows, cols=cols)
        except Exception as exc:
            view: AnyTermView = LogTerminalView()
            view.set_text(f"Failed to open shell:\n{exc}\n")
            view.hide()
            self.stack_layout.addWidget(view)
            self._views[tid] = view
            self._ensure_backend_item(tid, "Shell (failed)", "user", "exited")
            self._select_id(tid)
            return

        self._ptys[tid] = session
        view_t = TerminalView(interactive=True)
        # Seed screen to the spawn winsize before the widget is laid out so the
        # first paint isn't a 80×24 lie and set_pty won't shrink the PTY.
        view_t._screen.resize(rows, cols)
        view_t.set_pty(session)
        view_t.hide()
        self.stack_layout.addWidget(view_t)
        self._views[tid] = view_t
        self._ensure_backend_item(tid, session.title, "user", "running")
        self._empty.hide()

        def on_data(text: str, _tid=tid) -> None:
            v = self._views.get(_tid)
            if v is not None:
                v.append_text(text)

        def on_exit(code, _tid=tid) -> None:
            self._mark_status(_tid, "exited", code)

        await session.start_reader(on_data, on_exit)
        self._select_id(tid)
        # After the splitter shows the view, push real geometry once more.
        if view_t.width() >= view_t._cell_w * 4 and view_t.height() >= view_t._cell_h * 2:
            c2, r2 = view_t._cols_rows_for_size(view_t.size())
            view_t._screen.resize(r2, c2)
            session.resize(r2, c2)

    def _mark_status(self, tid: str, status: str, exit_code=None) -> None:
        item = self._find_item(tid)
        if item is None:
            return
        kind = item.data(Qt.UserRole + 1) or ""
        title = item.text()
        raw = title
        for prefix in ("◈ ", "❯ ", "$ ", "• "):
            if raw.startswith(prefix):
                raw = raw[len(prefix):]
                break
        raw = raw.replace(" (done)", "")
        # strip prior exit code tag
        if " [" in raw and raw.endswith("]"):
            raw = raw.rsplit(" [", 1)[0]
        if exit_code not in (None, -1) and status == "exited":
            raw = f"{raw} [{exit_code}]"
        item.setText(self._format_label(raw, kind, status))
        if tid in self._ptys and status == "exited":
            self._ptys[tid].status = "exited"

    def _kill_selected(self) -> None:
        item = self.list.currentItem()
        if item is None:
            return
        tid = item.data(Qt.UserRole)
        kind = item.data(Qt.UserRole + 1)
        if kind == "user" and tid in self._ptys:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._close_pty(tid))
            return
        if kind == "claude-code" and self.engine is not None:
            info = self.engine.terminals.get(tid)
            sid = info.session_id if info else ""
            if sid and info and info.status == "running":
                import asyncio

                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError:
                    return
                loop.create_task(self.engine.abort(sid))
                return
        self._remove_item(tid)

    async def _close_pty(self, tid: str) -> None:
        pty = self._ptys.pop(tid, None)
        if pty is not None:
            await pty.close()
        self._mark_status(tid, "exited", pty.exit_code if pty else None)

    def _remove_item(self, tid: str) -> None:
        item = self._find_item(tid)
        if item is not None:
            row = self.list.row(item)
            self.list.takeItem(row)
        view = self._views.pop(tid, None)
        if view is not None:
            view.setParent(None)
            view.deleteLater()
        self._kinds.pop(tid, None)
        if self.engine is not None:
            try:
                self.engine.terminals.remove(tid)
            except Exception:
                pass
        if not self._views:
            self._empty.show()

    def _clear_view(self) -> None:
        if self._active_id and self._active_id in self._views:
            self._views[self._active_id].clear()

    def _close(self) -> None:
        self.setVisible(False)
        self.closed.emit()
