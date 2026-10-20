"""Значок в трее на Qt (QSystemTrayIcon) вместо pystray: меню в стиле программы, цветной бейдж статуса.

Интерфейс, который ждёт main.py: .icon = PIL-картинка (из любого потока), .stop(), .run() (пусто), .tooltip.
Пункты меню: (подпись, функция) | None (разделитель) | (подпись, [пункты]) для подменю. default=True — двойной клик.
"""
from PySide6.QtGui import QAction, QIcon, QImage, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from logger import log


def pil_to_icon(img) -> QIcon:
    img = img.convert("RGBA")
    qimg = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888).copy()
    return QIcon(QPixmap.fromImage(qimg))


class QtTray:
    def __init__(self, items, image, tooltip="AI Ассистент", post=None, on_open=None):
        self._post = post or (lambda fn: fn())
        self.tray = QSystemTrayIcon(pil_to_icon(image))
        self.tray.setToolTip(tooltip)
        self.menu = QMenu()
        self._build(self.menu, items)
        self.tray.setContextMenu(self.menu)
        self._on_open = on_open
        self.tray.activated.connect(self._activated)
        self.tray.show()
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log("Трей Windows недоступен — значок может не появиться.")

    def _build(self, menu, items):
        for it in items:
            if it is None:
                menu.addSeparator()
            elif isinstance(it[1], list):
                sub = menu.addMenu(it[0])
                self._build(sub, it[1])
            else:
                act = QAction(it[0], menu)
                act.triggered.connect(lambda _=False, f=it[1]: f())
                menu.addAction(act)
                if len(it) > 2 and it[2]:
                    self._default = act

    def _activated(self, reason):
        if reason in (QSystemTrayIcon.DoubleClick, QSystemTrayIcon.Trigger) and self._on_open:
            self._on_open()

    # --- совместимость с pystray ---
    @property
    def icon(self):
        return None

    @icon.setter
    def icon(self, image):
        def go():
            self.tray.setIcon(pil_to_icon(image))
        self._post(go)

    def run(self):
        pass

    def stop(self):
        self._post(self.tray.hide)
