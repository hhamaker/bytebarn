from PySide6.QtWidgets import QDialog, QLabel, QVBoxLayout


class FirstRunWizard(QDialog):
    """Stub first-run wizard dialog."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Welcome to Crew")
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("First-run wizard placeholder"))
        self.resize(400, 300)

        self._persist_skip = False  # persist skip flag stub
