"""Tiling pane container for the Terminal Manager.

``PaneArea`` manages ``TermPane`` leaves in arbitrarily nested ``QSplitter``s
(tmux-style). Panes are dumb frames — a slim header plus one body slot — and
never own terminal state; ``TerminalPanel`` decides which terminal view is
mounted where. See
docs/superpowers/specs/2026-08-05-terminal-splits-themes-design.md.
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QMimeData, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QDrag, QPainter
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

TERMINAL_MIME = "application/x-bytebarn-terminal"

# drop zones: edge quarters split, the middle swaps the pane's terminal
ZONES = ("left", "right", "top", "bottom", "center")


def zone_for(pos: QPoint, width: int, height: int) -> str:
    """Drop zone for a cursor position inside a (width × height) pane."""
    if width <= 0 or height <= 0:
        return "center"
    fx = pos.x() / width
    fy = pos.y() / height
    # wide edge bands (30%) — splitting is the common intent, swapping the
    # rarity, so the center target stays small
    if fx < 0.3:
        return "left"
    if fx > 0.7:
        return "right"
    if fy < 0.3:
        return "top"
    if fy > 0.7:
        return "bottom"
    return "center"


def zone_rect(zone: str, width: int, height: int) -> QRect:
    """Overlay rectangle previewing where the terminal would land."""
    if zone == "left":
        return QRect(0, 0, width // 2, height)
    if zone == "right":
        return QRect(width - width // 2, 0, width // 2, height)
    if zone == "top":
        return QRect(0, 0, width, height // 2)
    if zone == "bottom":
        return QRect(0, height - height // 2, width, height // 2)
    return QRect(0, 0, width, height)


class _DropOverlay(QWidget):
    """Translucent accent highlight for the active drop zone."""

    def __init__(self, parent: QWidget):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.zone = "center"
        self.hide()

    def paintEvent(self, event) -> None:
        from . import theme

        painter = QPainter(self)
        color = QColor(theme.tokens()["accent"])
        color.setAlpha(60)
        rect = zone_rect(self.zone, self.width(), self.height())
        painter.fillRect(rect, color)
        color.setAlpha(180)
        painter.setPen(color)
        painter.drawRect(rect.adjusted(0, 0, -1, -1))


class TermPane(QFrame):
    """One tile: header (title · 🎨 · ✕) over a single view slot."""

    activated = Signal()
    close_requested = Signal()
    rename_requested = Signal()
    theme_menu_requested = Signal(QPoint)  # global position for the menu
    terminal_dropped = Signal(str, str)    # terminal id, zone

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("termPane")
        self.setAcceptDrops(True)
        self.terminal_id: str | None = None
        self._view: QWidget | None = None
        self._overlay = _DropOverlay(self)
        self._drag_start: QPoint | None = None

        self._title = QLabel("no terminal")
        self._title.setObjectName("paneTitle")
        theme_btn = QPushButton("🎨")
        theme_btn.setFlat(True)
        theme_btn.setFixedWidth(26)
        theme_btn.setToolTip("Terminal theme")
        theme_btn.clicked.connect(lambda: self.theme_menu_requested.emit(
            theme_btn.mapToGlobal(QPoint(0, theme_btn.height()))))
        close_btn = QPushButton("✕")
        close_btn.setFlat(True)
        close_btn.setFixedWidth(26)
        close_btn.setToolTip("Close this pane (the terminal keeps running)")
        close_btn.clicked.connect(self.close_requested.emit)

        header = QWidget()
        header.setObjectName("paneHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(8, 1, 2, 1)
        header_layout.setSpacing(2)
        header_layout.addWidget(self._title, 1)
        header_layout.addWidget(theme_btn)
        header_layout.addWidget(close_btn)
        self._header = header

        self._placeholder = QLabel(
            "No terminal in this pane.\nPick one from the list, or + Shell.")
        self._placeholder.setAlignment(Qt.AlignCenter)
        self._placeholder.setWordWrap(True)

        self._body = QVBoxLayout()
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.addWidget(self._placeholder)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)
        layout.addWidget(header)
        layout.addLayout(self._body, 1)

    # -- view slot ------------------------------------------------------------

    def set_view(self, view: QWidget | None) -> QWidget | None:
        """Mount ``view`` (or clear with None); returns the displaced view."""
        old, self._view = self._view, view
        if old is not None:
            old.removeEventFilter(self)
            self._body.removeWidget(old)
        if view is not None:
            self._placeholder.hide()
            self._body.addWidget(view, 1)
            view.installEventFilter(self)
            # the pane owns drag & drop — a QPlainTextEdit child would
            # otherwise swallow drops before the pane ever sees them
            view.setAcceptDrops(False)
            viewport = getattr(view, "viewport", None)
            if callable(viewport) and viewport() is not None:
                viewport().setAcceptDrops(False)
            view.show()
        else:
            self.terminal_id = None
            self._title.setText("no terminal")
            self._placeholder.show()
        return old

    def view(self) -> QWidget | None:
        return self._view

    def set_title(self, title: str) -> None:
        self._title.setText(title)

    def set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        style = self.style()
        style.unpolish(self)
        style.polish(self)

    # -- activation & header drag ---------------------------------------------

    def mousePressEvent(self, event) -> None:
        self.activated.emit()
        if self._header.underMouse() and self.terminal_id:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        from PySide6.QtWidgets import QApplication

        if (self._drag_start is not None and self.terminal_id
                and (event.position().toPoint() - self._drag_start).manhattanLength()
                >= QApplication.startDragDistance()):
            self._drag_start = None
            drag = QDrag(self)
            mime = QMimeData()
            mime.setData(TERMINAL_MIME, self.terminal_id.encode())
            drag.setMimeData(mime)
            drag.exec(Qt.MoveAction)
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        self._drag_start = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:
        if self.childAt(event.position().toPoint()) is self._title \
                or self._header.underMouse():
            self.rename_requested.emit()
        super().mouseDoubleClickEvent(event)

    def eventFilter(self, obj, event) -> bool:
        if obj is self._view and event.type() in (QEvent.FocusIn, QEvent.MouseButtonPress):
            self.activated.emit()
        return False

    # -- drops ----------------------------------------------------------------

    def dragEnterEvent(self, event) -> None:
        if event.mimeData().hasFormat(TERMINAL_MIME):
            event.acceptProposedAction()
            self._overlay.setGeometry(self.rect())
            self._overlay.zone = zone_for(
                event.position().toPoint(), self.width(), self.height())
            self._overlay.raise_()
            self._overlay.show()
            self._overlay.update()

    def dragMoveEvent(self, event) -> None:
        if event.mimeData().hasFormat(TERMINAL_MIME):
            event.acceptProposedAction()
            self._overlay.zone = zone_for(
                event.position().toPoint(), self.width(), self.height())
            self._overlay.update()

    def dragLeaveEvent(self, event) -> None:
        self._overlay.hide()

    def dropEvent(self, event) -> None:
        self._overlay.hide()
        if not event.mimeData().hasFormat(TERMINAL_MIME):
            return
        tid = bytes(event.mimeData().data(TERMINAL_MIME)).decode()
        zone = zone_for(event.position().toPoint(), self.width(), self.height())
        event.acceptProposedAction()
        self.terminal_dropped.emit(tid, zone)


class PaneArea(QWidget):
    """Nested-splitter tiling surface; always holds at least one pane."""

    pane_activated = Signal(object)  # TermPane

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._panes: list[TermPane] = []
        self._active: TermPane | None = None
        first = self._make_pane()
        self._layout.addWidget(first)
        self.set_active(first)

    # -- queries --------------------------------------------------------------

    def panes(self) -> list[TermPane]:
        return list(self._panes)

    def active_pane(self) -> TermPane:
        if self._active is None or self._active not in self._panes:
            self.set_active(self._panes[0])
        return self._active

    def pane_for(self, terminal_id: str) -> TermPane | None:
        for pane in self._panes:
            if pane.terminal_id == terminal_id:
                return pane
        return None

    # -- structure ------------------------------------------------------------

    def split(self, pane: TermPane, orientation: Qt.Orientation,
              before: bool = False) -> TermPane:
        """Split ``pane`` in two; the new (empty) pane becomes active.
        ``before`` puts the new pane left/above instead of right/below.

        Reparenting uses only addWidget/insertWidget —
        ``QSplitter.replaceWidget`` hands ownership of the returned widget to
        Python, and discarding that wrapper deletes the C++ widget."""
        new_pane = self._make_pane()
        parent = pane.parentWidget()
        if isinstance(parent, QSplitter) and parent.orientation() == orientation:
            index = parent.indexOf(pane) + (0 if before else 1)
            parent.insertWidget(index, new_pane)
            self._equalize(parent)
        else:
            splitter = QSplitter(orientation)
            splitter.setChildrenCollapsible(False)
            splitter.setHandleWidth(4)
            if isinstance(parent, QSplitter):
                index = parent.indexOf(pane)
                sizes = parent.sizes()
                splitter.addWidget(pane)          # reparents pane out
                parent.insertWidget(index, splitter)
                parent.setSizes(sizes)
            else:  # pane sits directly in the area layout
                self._layout.removeWidget(pane)
                splitter.addWidget(pane)
                self._layout.addWidget(splitter)
            if before:
                splitter.insertWidget(0, new_pane)
            else:
                splitter.addWidget(new_pane)
            self._equalize(splitter)
        self.set_active(new_pane)
        return new_pane

    def close_pane(self, pane: TermPane) -> None:
        """Remove a pane; the last one is only cleared, never removed."""
        if len(self._panes) <= 1:
            pane.set_view(None)
            return
        parent = pane.parentWidget()
        self._panes.remove(pane)
        pane.setParent(None)
        pane.deleteLater()
        # unwrap a splitter reduced to a single child
        if isinstance(parent, QSplitter) and parent.count() == 1:
            child = parent.widget(0)
            grand = parent.parentWidget()
            if isinstance(grand, QSplitter):
                index = grand.indexOf(parent)
                sizes = grand.sizes()
                grand.insertWidget(index, child)  # reparents child out
                parent.setParent(None)
                grand.setSizes(sizes)
            else:
                self._layout.removeWidget(parent)
                self._layout.addWidget(child)
                parent.setParent(None)
            parent.deleteLater()
        if self._active not in self._panes:
            self.set_active(self._panes[0])

    def set_active(self, pane: TermPane) -> None:
        self._active = pane
        for p in self._panes:
            p.set_active(p is pane)

    # -- internals ------------------------------------------------------------

    def _make_pane(self) -> TermPane:
        pane = TermPane()
        pane.activated.connect(lambda p=pane: self._on_activated(p))
        self._panes.append(pane)
        return pane

    def _on_activated(self, pane: TermPane) -> None:
        if pane is not self._active:
            self.set_active(pane)
        self.pane_activated.emit(pane)

    @staticmethod
    def _equalize(splitter: QSplitter) -> None:
        count = splitter.count()
        total = sum(splitter.sizes()) or count * 200
        splitter.setSizes([total // count] * count)
