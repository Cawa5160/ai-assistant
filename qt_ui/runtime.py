"""Запуск нового интерфейса внутри программы.

Qt живёт в главном потоке. Старое окно Tk (диалоги, автопилот, страницы «История», «Расписание», «Макросы»,
«Настройки», «Проверка») живёт во втором потоке: оно скрыто и открывается по клику на эти пункты меню.
Рабочие потоки по-прежнему зовут ui_thread.post/call — они попадают в поток Tk; сам QtShell потокобезопасен (всё через сигналы).
"""
import os
import threading

from PySide6.QtCore import QObject, Qt, Signal, Slot
from PySide6.QtGui import QFont, QIcon
from PySide6.QtWidgets import QApplication

import config
from logger import log

from .backend import RealBackend
from .main_window import MainWindow
from .theme_manager import theme


class _Bridge(QObject):
    run = Signal(object)

    def __init__(self):
        super().__init__()
        self.run.connect(self._exec, Qt.QueuedConnection)

    @Slot(object)
    def _exec(self, fn):
        try:
            fn()
        except Exception as e:
            log(f"Qt: ошибка в задаче интерфейса: {type(e).__name__}: {e}")


class QtShell:
    """Главный объект интерфейса для main.py: show/refresh/set_busy/hide_for_agent/quit/run."""

    def __init__(self, controller):
        import i18n
        i18n.load_language()
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        font = QFont()
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans"])
        font.setPixelSize(14)
        self.app.setFont(font)
        theme.set(config.get("ui_theme") or "dark", self.app)
        self.bridge = _Bridge()
        self.backend = RealBackend(controller, self.post, self.open_classic)
        from . import dialogs_qt
        dialogs_qt.install(self.post)        # gui.ask_yes_no, confirm_plan, окно автопилота… -> версии на Qt
        self.window = MainWindow(self.backend)
        self.window.theme_saved.connect(lambda name: config.save({"ui_theme": name}))
        self._visible = False
        self._visible_before_agent = False
        ico = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "app.ico")
        if os.path.exists(ico):
            self.window.setWindowIcon(QIcon(ico))
            self.app.setWindowIcon(QIcon(ico))

    def post(self, fn):
        self.bridge.run.emit(fn)

    def _call(self, fn):
        done = threading.Event()

        def run():
            try:
                fn()
            finally:
                done.set()
        self.post(run)
        done.wait(5)

    # --- то, что зовёт main.py ---
    def run(self):
        self.window.closing.connect(self._hidden)
        self.app.exec()

    def _hidden(self):
        self._visible = False
        try:
            self.backend.controller.notify_hidden()
        except Exception:
            pass

    def show(self, page=None):
        def go():
            key = {"memory": "macros"}.get(page, page)
            if key in self.window.index:
                self.window.show_page(key)
            self._visible = True
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
        self.post(go)

    def refresh(self):
        self.post(self.window.refresh)

    def set_busy(self, busy, text=""):
        self.post(lambda: self.window.set_busy(busy, text))

    def hide_for_agent(self):
        """Прячет оба окна на время автопилота (ИИ не должен видеть приложение на скриншоте)."""
        def go():
            self._visible_before_agent = self.window.isVisible()
            self.window.hide()
        self._call(go)

    def restore_after_agent(self):
        def go():
            if self._visible_before_agent:
                self.window.show()
            self._visible_before_agent = False
        self.post(go)

    def quit(self):
        self.post(self.app.quit)

    def open_classic(self, page):
        pass


def available() -> bool:
    try:
        import PySide6.QtWidgets  # noqa: F401
        return True
    except Exception:
        return False
