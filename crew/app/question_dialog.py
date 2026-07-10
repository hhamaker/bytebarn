"""Question dialog: options + free-text "other" (spec §7.1)."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QRadioButton,
    QVBoxLayout,
)


class QuestionDialog(QDialog):
    """Answer available via .answer after exec/accept."""

    def __init__(self, question: str, options: list[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Question")
        self.setMinimumWidth(440)
        self.answer = ""

        layout = QVBoxLayout(self)
        label = QLabel(question)
        label.setWordWrap(True)
        layout.addWidget(label)

        self._radios: list[QRadioButton] = []
        for option in options:
            radio = QRadioButton(option)
            self._radios.append(radio)
            layout.addWidget(radio)
        if self._radios:
            self._radios[0].setChecked(True)

        other_row = QHBoxLayout()
        self._other_radio = QRadioButton("Other:")
        if not self._radios:
            self._other_radio.setChecked(True)
        self._other_edit = QLineEdit()
        self._other_edit.textEdited.connect(lambda _: self._other_radio.setChecked(True))
        other_row.addWidget(self._other_radio)
        other_row.addWidget(self._other_edit)
        layout.addLayout(other_row)

        ok = QPushButton("Answer")
        ok.setDefault(True)
        ok.clicked.connect(self._finish)
        layout.addWidget(ok)

    def _finish(self) -> None:
        if self._other_radio.isChecked():
            self.answer = self._other_edit.text().strip()
        else:
            for radio in self._radios:
                if radio.isChecked():
                    self.answer = radio.text()
                    break
        if self.answer:
            self.accept()
