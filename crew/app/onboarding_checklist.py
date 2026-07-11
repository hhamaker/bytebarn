from PySide6.QtWidgets import QListWidget, QListWidgetItem


class OnboardingChecklist(QListWidget):
    """5-item checklist panel for first-run onboarding."""

    ITEMS = [
        "Create first session",
        "Connect a provider",
        "Pick an agent",
        "Run a prompt",
        "Review transcript",
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        for item in self.ITEMS:
            self.addItem(QListWidgetItem("☐ " + item))
        self.setFixedHeight(120)

    def start_tutorial(self):
        """Interactive tutorial hook (stub)."""
        pass
