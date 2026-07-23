"""Preview panel: render HTML artifacts, local files, and dev-server URLs.

A side pane holding a QWebEngineView. The web view is created lazily on
first show so headless tests (and users who never open it) pay nothing.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QUrl, Signal
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


class PreviewPanel(QWidget):
    closed = Signal()

    def __init__(self):
        super().__init__()
        self._view = None
        self._fallback: QLabel | None = None

        bar = QHBoxLayout()
        bar.setContentsMargins(0, 0, 0, 0)
        bar.setSpacing(4)
        back = QPushButton("←")
        back.setFlat(True)
        back.setFixedWidth(28)
        back.clicked.connect(lambda: self._view and self._view.back())
        reload_btn = QPushButton("⟳")
        reload_btn.setFlat(True)
        reload_btn.setFixedWidth(28)
        reload_btn.clicked.connect(lambda: self._view and self._view.reload())
        self.address = QLineEdit()
        self.address.setPlaceholderText("URL or file path — e.g. http://localhost:5173")
        self.address.returnPressed.connect(self._go)
        close = QPushButton("✕")
        close.setFlat(True)
        close.setFixedWidth(28)
        close.clicked.connect(self._close)
        bar.addWidget(back)
        bar.addWidget(reload_btn)
        bar.addWidget(self.address, 1)
        bar.addWidget(close)

        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(8, 4, 8, 8)
        self._layout.setSpacing(4)
        self._layout.addLayout(bar)

    # -- lazy web view -------------------------------------------------------

    def _ensure_view(self) -> bool:
        if self._view is not None:
            return True
        try:
            from PySide6.QtWebEngineWidgets import QWebEngineView

            self._view = QWebEngineView()
            self._view.urlChanged.connect(
                lambda url: self.address.setText(url.toString()))
            self._layout.addWidget(self._view, 1)
            return True
        except Exception as exc:  # WebEngine missing on this install
            if self._fallback is None:
                self._fallback = QLabel(
                    f"Preview needs QtWebEngine, which failed to load:\n{exc}")
                self._fallback.setWordWrap(True)
                self._layout.addWidget(self._fallback, 1)
            return False

    # -- public API ----------------------------------------------------------

    def show_url(self, url: str) -> None:
        if not self._ensure_view():
            return
        if "://" not in url and not url.startswith("about:"):
            path = Path(url).expanduser()
            if path.exists():
                self._view.load(QUrl.fromLocalFile(str(path.resolve())))
                return
            url = f"http://{url}"
        self._view.load(QUrl(url))

    def show_file(self, path: str | Path) -> None:
        self.show_url(str(path))

    def show_html(self, html: str, title: str = "artifact") -> None:
        """Render an HTML string (e.g. a fenced code block from the chat)."""
        if not self._ensure_view():
            return
        self._view.setHtml(html, QUrl("about:artifact"))
        self.address.setText(f"about:{title}")

    def _go(self) -> None:
        text = self.address.text().strip()
        if text:
            self.show_url(text)

    def _close(self) -> None:
        self.hide()
        self.closed.emit()
