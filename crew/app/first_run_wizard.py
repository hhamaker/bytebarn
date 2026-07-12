"""First-run welcome dialog: what Crew is, how to start, sample prompts.

Shown once (``onboarded: true`` is written to global config afterwards).
Deliberately one screen — a short how-to and sample prompts, not a slideshow.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from .onboarding_text import SAMPLE_PROMPTS
from .sprites import crew_banner


class FirstRunWizard(QDialog):
    """Emits ``prompt_picked`` when the user starts with a sample prompt."""

    prompt_picked = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Crew")
        self.setMinimumWidth(520)

        sprites = QLabel()
        sprites.setPixmap(crew_banner())
        sprites.setAlignment(Qt.AlignCenter)

        intro = QLabel(
            "<h2>Meet your crew</h2>"
            "<p>Crew runs AI coding agents against your project. Type a prompt,"
            " or pick the <b>goal</b> agent to have the whole crew plan and"
            " execute a bigger task together.</p>"
            "<p><b>Get started:</b></p>"
            "<ol>"
            "<li>Connect a model — <b>⚡ providers</b> in the status bar"
            " (paste a key or log in via web)</li>"
            "<li>Pick an agent + model under the prompt box</li>"
            "<li>Type a prompt and press Enter</li>"
            "</ol>"
            "<p><b>Try one of these first prompts:</b></p>"
        )
        intro.setWordWrap(True)
        intro.setTextFormat(Qt.RichText)

        layout = QVBoxLayout(self)
        layout.setSpacing(10)
        layout.addWidget(sprites)
        layout.addWidget(intro)
        for prompt in SAMPLE_PROMPTS:
            button = QPushButton(f"▸  {prompt}")
            button.setStyleSheet("text-align: left; padding: 6px 10px;")
            button.clicked.connect(lambda _=False, p=prompt: self._pick(p))
            layout.addWidget(button)

        footer = QHBoxLayout()
        footer.addStretch(1)
        skip = QPushButton("Skip — I\'ll explore myself")
        skip.clicked.connect(self.reject)
        footer.addWidget(skip)
        layout.addLayout(footer)

    def _pick(self, prompt: str) -> None:
        self.prompt_picked.emit(prompt)
        self.accept()
