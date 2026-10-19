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
    """Подставляется вместо app_window.AppWindow в main.py: те же show/refresh/set_busy/quit/run."""

    def __init__(self, controller, tk_factory):
        self.app = QApplication.instance() or QApplication([])
        self.app.setQuitOnLastWindowClosed(False)
        font = QFont()
        font.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans"])
        font.setPixelSize(14)
        self.app.setFont(font)
        theme.set(config.get("ui_theme") or "dark", self.app)
        self.bridge = _Bridge()
        self.tk = tk_factory()              # поднимает Tk в своём потоке (см. start())
        self.backend = RealBackend(controller, self.post, self.open_classic)
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
            if page and page in ("home", "chat"):
                self.window.show_page(page)
            elif page:
                self.open_classic(page)
            self._visible = True
            self.window.showNormal()
            self.window.raise_()
            self.window.activateWindow()
        self.post(go)

    def refresh(self):
        self.post(self.window.refresh)

    def set_busy(self, busy, text=""):
        self.post(lambda: self.window.set_busy(busy, text))
        try:
            self.tk.set_busy(busy, text)
        except Exception:
            pass

    def hide_for_agent(self):
        """Прячет оба окна на время автопилота (ИИ не должен видеть приложение на скриншоте)."""
        def go():
            self._visible_before_agent = self.window.isVisible()
            self.window.hide()
        self._call(go)
        try:
            self.tk.hide_for_agent()
        except Exception:
            pass

    def restore_after_agent(self):
        def go():
            if self._visible_before_agent:
                self.window.show()
            self._visible_before_agent = False
        self.post(go)
        try:
            self.tk.restore_after_agent()
        except Exception:
            pass

    def quit(self):
        self.post(self.app.quit)
        try:
            self.tk.quit()
        except Exception:
            pass

    def open_classic(self, page):
        import ui_thread
        ui_thread.post(self.tk.show, page)


def start_tk_thread(controller):
    """Строит старое окно Tk во втором потоке и возвращает его, когда оно готово."""
    import app_window
    box, ready = {}, threading.Event()

    def work():
        try:
            box["tk"] = app_window.AppWindow(controller)
        except Exception as e:
            box["error"] = e
            ready.set()
            return
        ready.set()
        box["tk"].run()

    threading.Thread(target=work, name="tk-ui", daemon=True).start()
    ready.wait(30)
    if "error" in box:
        raise box["error"]
    return box["tk"]


def available() -> bool:
    try:
        import PySide6.QtWidgets  # noqa: F401
        return True
    except Exception:
        return False
