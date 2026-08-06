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
from .term_themes import DEFAULT_THEME, TermTheme, get_theme
from .vt import Cell, VtScreen


def _rgba(spec: str) -> QColor:
    """#RRGGBB or #RRGGBBAA → QColor (QColor's own parser reads AA first)."""
    if len(spec) == 9:  # #RRGGBBAA
        c = QColor(spec[:7])
        c.setAlpha(int(spec[7:9], 16))
        return c
    return QColor(spec)


def _mono_font(point_size: int = 12) -> QFont:
    font = QFont("Menlo")
    if not font.exactMatch():
        font = QFont("ui-monospace")
    font.setStyleHint(QFont.Monospace)
    font.setFixedPitch(True)
    font.setPointSize(point_size)
    return font


class TerminalView(QWidget):
    """Painted cell-grid terminal backed by VtScreen + optional PtySession."""

    def __init__(self, interactive: bool = False, parent=None):
        super().__init__(parent)
        self._interactive = interactive
        self._pty: PtySession | None = None
        self._screen = VtScreen(cols=80, rows=24)
        self._theme_name = DEFAULT_THEME
        self._apply_theme_colors(get_theme(DEFAULT_THEME))
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

    def set_theme(self, theme: TermTheme) -> None:
        self._theme_name = theme.name
        self._apply_theme_colors(theme)
        self.update()

    def theme_name(self) -> str:
        return self._theme_name

    def _apply_theme_colors(self, theme: TermTheme) -> None:
        self._fg = QColor(theme.fg)
        self._bg = QColor(theme.bg)
        self._cursor_color = QColor(theme.cursor)
        self._selection_bg = _rgba(theme.selection)
        self._palette = [QColor(c) for c in theme.ansi]

    def _resolve(self, color_idx: int, *, is_fg: bool) -> QColor:
        if 0 <= color_idx < len(self._palette):
            return self._palette[color_idx]
        return self._fg if is_fg else self._bg

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
        p.fillRect(self.rect(), self._bg)
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
                fg = self._resolve(cell.fg, is_fg=True)
                bg = self._resolve(cell.bg, is_fg=False)
                if cell.inverse:
                    fg, bg = bg, fg
                if cell.bold:
                    font = QFont(self._font)
                    font.setBold(True)
                    p.setFont(font)
                else:
                    p.setFont(self._font)
                rect = QRect(x * cw, y * ch, (run_end - x) * cw, ch)
                if bg != self._bg:
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
            p.fillRect(crect, self._cursor_color)
            # redraw glyph under cursor inverted
            if cy < len(lines) and cx < len(lines[cy]):
                cell = lines[cy][cx]
                p.setPen(self._bg)
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
        self._theme_name = DEFAULT_THEME
        self.set_theme(get_theme(DEFAULT_THEME))
        self.setPlaceholderText("No output yet…")

    def set_theme(self, theme: TermTheme) -> None:
        self._theme_name = theme.name
        self.setStyleSheet(
            "QPlainTextEdit { background: %s; color: %s; border: none; }"
            % (theme.bg, theme.fg))

    def theme_name(self) -> str:
        return self._theme_name

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


class _TermList(QListWidget):
    """Terminal list that drags entries as terminal ids (drop on a pane)."""

    delete_requested = Signal()

    def keyPressEvent(self, event) -> None:
        if event.key() in (Qt.Key_Delete, Qt.Key_Backspace):
            self.delete_requested.emit()
            return
        super().keyPressEvent(event)

    def startDrag(self, supported_actions) -> None:
        item = self.currentItem()
        tid = item.data(Qt.UserRole) if item else None
        if not tid:
            return
        from PySide6.QtCore import QMimeData
        from PySide6.QtGui import QDrag

        from .pane_area import TERMINAL_MIME

        drag = QDrag(self)
        mime = QMimeData()
        mime.setData(TERMINAL_MIME, str(tid).encode())
        drag.setMimeData(mime)
        drag.exec(Qt.CopyAction)


class TerminalPanel(QWidget):
    """Bottom/side manager: list of terminals + active view."""

    closed = Signal()

    def __init__(self, engine=None, parent=None):
        super().__init__(parent)
        self.engine = engine
        self._views: dict[str, AnyTermView] = {}
        self._ptys: dict[str, PtySession] = {}
        self._kinds: dict[str, str] = {}
        self._base_titles: dict[str, str] = {}   # hub / pty titles
        self._custom_titles: dict[str, str] = {}  # user renames win
        self._statuses: dict[str, str] = {}
        self._exit_codes: dict[str, int | None] = {}
        self._theme_names: dict[str, str] = {}
        self._active_id: str | None = None

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        title = QLabel("<b>Terminals</b>")
        self.new_btn = QPushButton("+ Shell")
        self.new_btn.setFlat(True)
        self.new_btn.setToolTip("Open a new local shell in the project directory")
        self.new_btn.clicked.connect(self._new_shell)
        self.split_h_btn = QPushButton("Split →")
        self.split_h_btn.setFlat(True)
        self.split_h_btn.setToolTip(
            "Split the active pane side-by-side and open a new shell there")
        self.split_h_btn.clicked.connect(lambda: self._split(Qt.Horizontal))
        self.split_v_btn = QPushButton("Split ↓")
        self.split_v_btn.setFlat(True)
        self.split_v_btn.setToolTip(
            "Split the active pane top/bottom and open a new shell there")
        self.split_v_btn.clicked.connect(lambda: self._split(Qt.Vertical))
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
        bar.addWidget(self.split_h_btn)
        bar.addWidget(self.split_v_btn)
        bar.addWidget(self.kill_btn)
        bar.addWidget(self.clear_btn)
        bar.addWidget(close)

        self.list = _TermList()
        self.list.setDragEnabled(True)
        self.list.setMaximumWidth(220)
        self.list.delete_requested.connect(self._close_selected)
        # Mount on click/activate, NOT on currentItemChanged: selection fires
        # on mouse *press*, so it would mount mid-drag and turn every
        # drag-to-split into a "move" that closes the source pane.
        self.list.itemClicked.connect(self._on_select)
        self.list.itemActivated.connect(self._on_select)
        self.list.itemDoubleClicked.connect(
            lambda item: self._rename_terminal(item.data(Qt.UserRole)))
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._list_menu)

        from .pane_area import PaneArea

        self.pane_area = PaneArea()
        self._wire_pane(self.pane_area.panes()[0])
        # hidden parking lot for views not mounted in any pane
        self._park = QWidget()
        self._park.hide()
        self._park_layout = QVBoxLayout(self._park)
        self._park_layout.setContentsMargins(0, 0, 0, 0)

        split = QSplitter(Qt.Horizontal)
        split.addWidget(self.list)
        split.addWidget(self.pane_area)
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
        self._base_titles[tid] = title
        self._statuses[tid] = status
        if tid not in self._views:
            # Backend tees (claude-code / bash) → plain log.
            # Interactive user shells get TerminalView (set in _spawn_shell).
            if kind == "user":
                view: AnyTermView = TerminalView(interactive=True)
            else:
                view = LogTerminalView()
            self._adopt_view(tid, view)
            if self.engine is not None and kind != "user":
                view.set_text(self.engine.terminals.snapshot(tid))
        item = self._find_item(tid)
        if item is None:
            item = QListWidgetItem(self._label(tid))
            item.setData(Qt.UserRole, tid)
            item.setData(Qt.UserRole + 1, kind)
            self.list.addItem(item)
        else:
            item.setText(self._label(tid))

    def _adopt_view(self, tid: str, view: AnyTermView) -> None:
        """Register a new view, parked (hidden) until a pane mounts it."""
        self._views[tid] = view
        self._park_layout.addWidget(view)
        view.hide()
        view.set_theme(get_theme(self._default_theme_name()))
        self._theme_names[tid] = view.theme_name()

    def _display_title(self, tid: str) -> str:
        return self._custom_titles.get(tid) or self._base_titles.get(tid, tid)

    def _label(self, tid: str) -> str:
        icon = {"claude-code": "◈", "user": "❯", "bash": "$"}.get(
            self._kinds.get(tid, ""), "•")
        title = self._display_title(tid)
        code = self._exit_codes.get(tid)
        if code not in (None, -1) and self._statuses.get(tid) == "exited":
            title = f"{title} [{code}]"
        suffix = "" if self._statuses.get(tid, "running") == "running" else " (done)"
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
            self._show_view(tid)  # selection no longer mounts implicitly

    def _on_select(self, item: QListWidgetItem | None, _prev=None) -> None:
        if item is None:
            return
        tid = item.data(Qt.UserRole)
        self._show_view(tid)

    # -- panes ----------------------------------------------------------------

    def _wire_pane(self, pane) -> None:
        pane.close_requested.connect(lambda p=pane: self._pane_closed(p))
        pane.rename_requested.connect(
            lambda p=pane: p.terminal_id and self._rename_terminal(p.terminal_id))
        pane.theme_menu_requested.connect(
            lambda pos, p=pane: self._theme_menu(p, pos))
        pane.terminal_dropped.connect(
            lambda tid, zone, p=pane: self._terminal_dropped(p, tid, zone))

    def _show_view(self, tid: str) -> None:
        """Mount the terminal's view into the active pane."""
        self._active_id = tid
        view = self._views.get(tid)
        if view is None:
            return
        existing = self.pane_area.pane_for(tid)
        if existing is not None:  # already tiled — just focus its pane
            self.pane_area.set_active(existing)
            view.setFocus()
            return
        self._mount_in_pane(self.pane_area.active_pane(), tid)

    def _mount_in_pane(self, pane, tid: str) -> None:
        view = self._views.get(tid)
        if view is None:
            return
        source = self.pane_area.pane_for(tid)
        if source is not None and source is not pane:
            # move, not copy — and never leave an empty tile behind
            source.set_view(None)
            self.pane_area.close_pane(source)
        displaced = pane.set_view(view)
        if displaced is not None and displaced is not view:
            self._park_layout.addWidget(displaced)
            displaced.hide()
        pane.terminal_id = tid
        pane.set_title(self._display_title(tid))
        view.setFocus()

    def _terminal_dropped(self, pane, tid: str, zone: str) -> None:
        """List/header drag landed on a pane: center swaps, edges split.

        Deferred one event-loop turn: the drop arrives inside the source
        pane's blocking drag.exec, and a move can delete that very pane —
        never tear down widgets whose event frames are still on the stack."""
        from PySide6.QtCore import QTimer

        QTimer.singleShot(0, lambda: self._apply_drop(pane, tid, zone))

    def _apply_drop(self, pane, tid: str, zone: str) -> None:
        if tid not in self._views or pane not in self.pane_area.panes():
            return
        if zone == "center":
            if pane.terminal_id != tid:
                self._mount_in_pane(pane, tid)
            self.pane_area.set_active(pane)
        else:
            orientation = Qt.Horizontal if zone in ("left", "right") else Qt.Vertical
            new_pane = self.pane_area.split(
                pane, orientation, before=zone in ("left", "top"))
            self._wire_pane(new_pane)
            self._mount_in_pane(new_pane, tid)
        self._active_id = tid
        self._select_id(tid)

    def _pane_closed(self, pane) -> None:
        view = pane.set_view(None)
        if view is not None:
            self._park_layout.addWidget(view)
            view.hide()
        self.pane_area.close_pane(pane)

    def _split(self, orientation) -> None:
        pane = self.pane_area.active_pane()
        new_pane = self.pane_area.split(pane, orientation)
        self._wire_pane(new_pane)
        self._new_shell()  # spawns async; lands in the (new) active pane

    # -- rename / themes -------------------------------------------------------

    def _rename_terminal(self, tid: str) -> None:
        if not tid:
            return
        from PySide6.QtWidgets import QInputDialog

        current = self._display_title(tid)
        title, ok = QInputDialog.getText(
            self, "Rename terminal", "Name:", text=current)
        title = title.strip()
        if not ok or not title or title == current:
            return
        self._custom_titles[tid] = title
        if self.engine is not None:
            self.engine.terminals.rename(tid, title)
        item = self._find_item(tid)
        if item is not None:
            item.setText(self._label(tid))
        pane = self.pane_area.pane_for(tid)
        if pane is not None:
            pane.set_title(title)

    def _default_theme_name(self) -> str:
        if self.engine is not None:
            extra = self.engine.config.model_extra or {}
            section = extra.get("terminal") or {}
            if isinstance(section, dict) and section.get("theme"):
                return str(section["theme"])
        return DEFAULT_THEME

    def _set_terminal_theme(self, tid: str, name: str) -> None:
        view = self._views.get(tid)
        if view is None:
            return
        view.set_theme(get_theme(name))
        self._theme_names[tid] = name

    def _save_default_theme(self, name: str) -> None:
        if self.engine is None:
            return
        try:
            from ..engine.config import patch_config_file

            patch_config_file(self.engine.global_dir / "config.json",
                              {"terminal.theme": name})
            self.engine.reload_config()
        except Exception:
            pass

    def _theme_menu(self, pane, global_pos) -> None:
        tid = pane.terminal_id
        if not tid:
            return
        self._show_theme_menu(tid, global_pos)

    def _show_theme_menu(self, tid: str, global_pos) -> None:
        from PySide6.QtWidgets import QMenu

        from .term_themes import THEMES

        menu = QMenu(self)
        current = self._theme_names.get(tid, self._default_theme_name())
        for name in THEMES:
            action = menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(name == current)
            action.triggered.connect(
                lambda _=False, n=name, t=tid: self._set_terminal_theme(t, n))
        menu.addSeparator()
        default = menu.addAction("Set current as default")
        default.triggered.connect(
            lambda: self._save_default_theme(self._theme_names.get(tid, current)))
        menu.exec(global_pos)

    def _list_menu(self, pos) -> None:
        item = self.list.itemAt(pos)
        if item is None:
            return
        tid = item.data(Qt.UserRole)
        from PySide6.QtWidgets import QMenu

        menu = QMenu(self)
        rename = menu.addAction("Rename…")
        theme = menu.addAction("Theme…")
        kill = menu.addAction("Kill")
        close = menu.addAction("Close (remove from list)")
        chosen = menu.exec(self.list.viewport().mapToGlobal(pos))
        if chosen is rename:
            self._rename_terminal(tid)
        elif chosen is theme:
            self._show_theme_menu(tid, self.list.viewport().mapToGlobal(pos))
        elif chosen is kill:
            self.list.setCurrentItem(item)
            self._kill_selected()
        elif chosen is close:
            self.list.setCurrentItem(item)
            self._close_selected()

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

        # Estimate size from the pane area so the shell starts with a real winsize
        host = self.pane_area
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
            self._adopt_view(tid, view)
            view.set_text(f"Failed to open shell:\n{exc}\n")
            self._ensure_backend_item(tid, "Shell (failed)", "user", "exited")
            self._select_id(tid)
            return

        self._ptys[tid] = session
        view_t = TerminalView(interactive=True)
        # Seed screen to the spawn winsize before the widget is laid out so the
        # first paint isn't a 80×24 lie and set_pty won't shrink the PTY.
        view_t._screen.resize(rows, cols)
        view_t.set_pty(session)
        self._adopt_view(tid, view_t)
        self._ensure_backend_item(tid, session.title, "user", "running")

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
        self._statuses[tid] = status
        self._exit_codes[tid] = exit_code
        item = self._find_item(tid)
        if item is not None:
            item.setText(self._label(tid))
        if tid in self._ptys and status == "exited":
            self._ptys[tid].status = "exited"

    def _kill_selected(self) -> None:
        """Kill kills AND removes user shells; running backends only abort."""
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
            loop.create_task(self._close_terminal(tid))
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

    def _close_selected(self) -> None:
        """Delete key / context 'Close': drop the terminal from the list."""
        item = self.list.currentItem()
        if item is None:
            return
        tid = item.data(Qt.UserRole)
        if tid in self._ptys:
            import asyncio

            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                return
            loop.create_task(self._close_terminal(tid))
        else:
            self._remove_item(tid)

    async def _close_terminal(self, tid: str) -> None:
        """Kill the PTY (if any) and remove the terminal entirely."""
        pty = self._ptys.pop(tid, None)
        if pty is not None:
            await pty.close()
        self._remove_item(tid)

    def _remove_item(self, tid: str) -> None:
        item = self._find_item(tid)
        if item is not None:
            row = self.list.row(item)
            self.list.takeItem(row)
        pane = self.pane_area.pane_for(tid)
        if pane is not None:
            pane.set_view(None)
            self.pane_area.close_pane(pane)  # collapse the vacated tile
        view = self._views.pop(tid, None)
        if view is not None:
            view.setParent(None)
            view.deleteLater()
        for d in (self._kinds, self._base_titles, self._custom_titles,
                  self._statuses, self._exit_codes, self._theme_names):
            d.pop(tid, None)
        if self.engine is not None:
            try:
                self.engine.terminals.remove(tid)
            except Exception:
                pass

    def _clear_view(self) -> None:
        if self._active_id and self._active_id in self._views:
            self._views[self._active_id].clear()

    def _close(self) -> None:
        self.setVisible(False)
        self.closed.emit()
