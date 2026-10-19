"""Вложения чата: карточки-превью (миниатюра / значок, имя, размер, ✕) и лента над полем ввода."""
import os
import tempfile
import time

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

import files_io

from .theme_manager import theme
from .widgets import IconButton, T, Tile, repolish

KIND_ICON = {"image": "image", "audio": "mic", "text": "file", "doc": "file"}


def save_clipboard_image(img: QImage) -> str:
    """Скриншот из буфера -> временный PNG (удаляется при очистке ленты)."""
    d = os.path.join(tempfile.gettempdir(), "ai_assistant_paste")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, time.strftime("screenshot_%H%M%S") + ".png")
    img.save(path, "PNG")
    return path


class AttachChip(QFrame):
    removed = Signal(str)

    def __init__(self, info: dict, temp=False, parent=None):
        super().__init__(parent)
        self.info, self.temp = info, temp
        self.setObjectName("attach")
        self.setProperty("bad", not info["ok"])
        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)
        thumb = None
        if info["ok"] and info["kind"] == "image":
            px = QPixmap(info["path"])
            if not px.isNull():
                thumb = QLabel()
                thumb.setPixmap(px.scaled(40, 40, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation).copy(0, 0, 40, 40))
                thumb.setFixedSize(40, 40)
        lay.addWidget(thumb or Tile(KIND_ICON.get(info["kind"], "file"), 40, 10))
        col = QVBoxLayout()
        col.setSpacing(0)
        name = QLabel(self._elide(info["name"]))
        name.setToolTip(info["path"])
        sub = QLabel(info["reason"] if not info["ok"] else files_io.human_size(info["size"]))
        sub.setObjectName("small" if info["ok"] else "err")
        col.addWidget(name)
        col.addWidget(sub)
        lay.addLayout(col)
        x = IconButton("x", "mut", size=14)
        x.setFixedSize(24, 24)
        x.setToolTip(T("Убрать"))
        x.clicked.connect(lambda: self.removed.emit(info["path"]))
        lay.addWidget(x, 0, Qt.AlignTop)

    @staticmethod
    def _elide(name, n=26):
        return name if len(name) <= n else name[:n - 9] + "…" + name[-8:]


class AttachTray(QWidget):
    """Лента карточек. paths() — только подходящие файлы."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._lay = QHBoxLayout(self)
        self._lay.setContentsMargins(0, 0, 0, 0)
        self._lay.setSpacing(8)
        self._lay.addStretch(1)
        self._chips = {}
        self.setVisible(False)

    def add(self, paths, temp=False):
        added = 0
        for p in paths:
            p = os.path.abspath(p)
            if p in self._chips or len(self._chips) >= files_io.MAX_ATTACH:
                continue
            chip = AttachChip(files_io.describe(p), temp)
            chip.removed.connect(self.remove)
            self._lay.insertWidget(self._lay.count() - 1, chip)
            self._chips[p] = chip
            added += 1
        self.setVisible(bool(self._chips))
        if added:
            self.changed.emit()
        return added

    def remove(self, path):
        chip = self._chips.pop(os.path.abspath(path), None)
        if chip:
            chip.deleteLater()
            if chip.temp:
                try:
                    os.remove(path)
                except OSError:
                    pass
        self.setVisible(bool(self._chips))
        self.changed.emit()

    def clear(self):
        for p in list(self._chips):
            self.remove(p)

    def paths(self):
        return [p for p, c in self._chips.items() if c.info["ok"]]

    def count(self):
        return len(self._chips)
