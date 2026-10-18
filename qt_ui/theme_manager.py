"""Текущая тема (тёмная/светлая) и сигнал смены — виджеты перекрашивают значки и стиль."""
from PySide6.QtCore import QObject, Signal

from .tokens import THEMES, stylesheet


class ThemeManager(QObject):
    changed = Signal(str)

    def __init__(self):
        super().__init__()
        self.name = "dark"

    @property
    def c(self) -> dict:
        return THEMES[self.name]

    def set(self, name: str, app=None):
        if name not in THEMES:
            name = "dark"
        self.name = name
        if app is not None:
            app.setStyleSheet(stylesheet(name))
        self.changed.emit(name)


theme = ThemeManager()
