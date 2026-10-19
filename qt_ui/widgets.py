"""Базовые виджеты новой дизайн-системы: карточки, кнопки со значками, переключатель, пузыри чата."""
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPainterPath, QPixmap
from PySide6.QtWidgets import (QButtonGroup, QFrame, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QTextEdit,
                               QVBoxLayout, QWidget)

import i18n

from . import icons
from .theme_manager import theme

T = i18n.t


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


class IconButton(QPushButton):
    """Кнопка со значком; цвет значка берётся из темы (role: mut / text / acc / white)."""

    def __init__(self, name, role="mut", checked_role=None, size=18, text="", object_name="iconbtn", parent=None):
        super().__init__(T(text), parent)
        self._name, self._role, self._crole, self._size = name, role, checked_role, size
        self.setObjectName(object_name)
        self.setCursor(Qt.PointingHandCursor)
        self.setIconSize(QSize(size, size))
        theme.changed.connect(lambda *_: self.refresh())
        self.refresh()

    def _color(self, role):
        return "#ffffff" if role == "white" else theme.c[role]

    def refresh(self):
        self.setIcon(icons.icon(self._name, self._color(self._role), self._size,
                                self._color(self._crole) if self._crole else None))


class NavButton(IconButton):
    def __init__(self, name, text, parent=None):
        super().__init__(name, "mut", "text", 18, text, "nav", parent)
        self.setCheckable(True)
        self.setAutoExclusive(True)
        self.setMinimumHeight(42)
        self.setText("  " + T(text))


class Card(QFrame):
    def __init__(self, parent=None, flat=False):
        super().__init__(parent)
        self.setObjectName("cardflat" if flat else "card")


class Segmented(QFrame):
    changed = Signal(str)

    def __init__(self, options, value, parent=None):
        super().__init__(parent)
        self.setObjectName("segbox")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(3, 3, 3, 3)
        lay.setSpacing(2)
        self._group = QButtonGroup(self)
        self._group.setExclusive(True)
        self._btns = {}
        for key, label in options:
            b = QPushButton(T(label))
            b.setObjectName("seg")
            b.setCheckable(True)
            b.setCursor(Qt.PointingHandCursor)
            b.setChecked(key == value)
            b.clicked.connect(lambda _=False, k=key: self.changed.emit(k))
            self._group.addButton(b)
            lay.addWidget(b)
            self._btns[key] = b

    def value(self):
        return next((k for k, b in self._btns.items() if b.isChecked()), "")

    def set_value(self, key):
        if key in self._btns:
            self._btns[key].setChecked(True)


def gradient_tile(icon_name: str, size: int = 30, radius: float = 9, ratio: float = 2.0) -> QPixmap:
    px = QPixmap(int(size * ratio), int(size * ratio))
    px.setDevicePixelRatio(ratio)
    px.fill(Qt.transparent)
    p = QPainter(px)
    p.setRenderHint(QPainter.Antialiasing)
    g = QLinearGradient(QPointF(0, 0), QPointF(size, size))
    g.setColorAt(0, QColor(theme.c["acc"]))
    g.setColorAt(1, QColor(theme.c["acc2"]))
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, size, size), radius, radius)
    p.fillPath(path, g)
    ic = icons.pixmap(icon_name, "#ffffff", int(size * 0.58))
    off = (size - size * 0.58) / 2
    p.drawPixmap(QPointF(off, off), ic)
    p.end()
    return px


class Tile(QLabel):
    """Квадрат с градиентом и значком (логотип, аватар ассистента)."""

    def __init__(self, icon_name, size=30, radius=9, parent=None):
        super().__init__(parent)
        self._i, self._s, self._r = icon_name, size, radius
        self.setFixedSize(size, size)
        theme.changed.connect(lambda *_: self.refresh())
        self.refresh()

    def refresh(self):
        self.setPixmap(gradient_tile(self._i, self._s, self._r))


class StatusDot(QWidget):
    def __init__(self, role="ok", parent=None):
        super().__init__(parent)
        self._role = role
        self.setFixedSize(12, 12)

    def set_role(self, role):
        self._role = role
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        col = QColor(theme.c[self._role])
        glow = QColor(col)
        glow.setAlpha(60)
        p.setBrush(glow)
        p.setPen(Qt.NoPen)
        p.drawEllipse(0, 0, 12, 12)
        p.setBrush(col)
        p.drawEllipse(2, 2, 8, 8)


def label(text="", name=None, wrap=False, translate=True):
    lb = QLabel(T(text) if translate else text)
    if name:
        lb.setObjectName(name)
    lb.setWordWrap(wrap)
    return lb


class MessageRow(QWidget):
    """Одно сообщение чата: пользователь — справа (градиент), ассистент — слева (аватар + карточка + подпись)."""

    def __init__(self, role, text, meta="", parent=None):
        super().__init__(parent)
        self.role = role
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(12)
        self.bubble = QFrame()
        self.bubble.setObjectName("bubU" if role == "user" else "bubB")
        bl = QVBoxLayout(self.bubble)
        bl.setContentsMargins(16, 12, 16, 12)
        self.text = QLabel(text)
        self.text.setObjectName("bubUt" if role == "user" else "")
        self.text.setWordWrap(True)
        self.text.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.text.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Minimum)
        self._fit(text)
        bl.addWidget(self.text)
        if role == "user":
            lay.addStretch(1)
            lay.addWidget(self.bubble, 0, Qt.AlignRight | Qt.AlignTop)
        else:
            self.avatar = Tile("sparkles", 30, 9)
            lay.addWidget(self.avatar, 0, Qt.AlignTop)
            col = QVBoxLayout()
            col.setSpacing(5)
            col.addWidget(self.bubble, 0, Qt.AlignLeft)
            self.meta = label(meta, "small", translate=False)
            self.meta.setVisible(bool(meta))
            col.addWidget(self.meta)
            lay.addLayout(col)
            lay.addStretch(1)

    def _fit(self, text):
        """Ширина пузыря по тексту, но не больше 620 px (без этого перенос слов идёт по минимальной ширине)."""
        fm = self.text.fontMetrics()
        widest = max((fm.horizontalAdvance(line) for line in text.split("\n")), default=0)
        self.text.setFixedWidth(max(60, min(620, widest + 8)))

    def set_text(self, text, meta=None):
        self._fit(text)
        self.text.setText(text)
        if meta is not None and hasattr(self, "meta"):
            self.meta.setText(meta)
            self.meta.setVisible(bool(meta))


class AutoTextEdit(QTextEdit):
    """Поле ввода: растёт до 6 строк, Enter — отправить, Shift+Enter — новая строка."""
    submitted = Signal()
    focus_changed = Signal(bool)

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setObjectName("field")
        self.setPlaceholderText(T(placeholder))
        self.setAcceptRichText(False)
        self.setFrameShape(QFrame.NoFrame)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def _fit(self):
        lines = min(6, max(1, int(self.document().size().height() / max(1, self.fontMetrics().lineSpacing()))))
        self.setFixedHeight(lines * self.fontMetrics().lineSpacing() + 14)

    files_pasted = Signal(list)      # пути файлов (Ctrl+V из проводника)
    image_pasted = Signal(object)    # QImage (Ctrl+V скриншота)

    def canInsertFromMimeData(self, source):
        return source.hasImage() or source.hasUrls() or super().canInsertFromMimeData(source)

    def insertFromMimeData(self, source):
        if source.hasUrls() and any(u.isLocalFile() for u in source.urls()):
            self.files_pasted.emit([u.toLocalFile() for u in source.urls() if u.isLocalFile()])
            return
        if source.hasImage() and not source.hasText():
            self.image_pasted.emit(source.imageData())
            return
        super().insertFromMimeData(source)

    def keyPressEvent(self, e):
        if e.key() in (Qt.Key_Return, Qt.Key_Enter) and not (e.modifiers() & Qt.ShiftModifier):
            self.submitted.emit()
            return
        super().keyPressEvent(e)

    def focusInEvent(self, e):
        super().focusInEvent(e)
        self.focus_changed.emit(True)

    def focusOutEvent(self, e):
        super().focusOutEvent(e)
        self.focus_changed.emit(False)
