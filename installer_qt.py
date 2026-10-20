"""Графический установщик на Qt (в стиле нового интерфейса). Запускается из installer_gui.py,
когда PySide6 уже есть. Ставит зависимости из requirements.txt, проверяет импорты, создаёт ярлыки."""
import os
import re
import subprocess
import sys
import threading

from PySide6.QtCore import Qt, QTimer, Signal, QObject
from PySide6.QtGui import QFont, QIcon, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QProgressBar,
                               QPushButton, QVBoxLayout, QWidget)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO = os.path.join(BASE_DIR, "assets", "logo_128.png")
ICON = os.path.join(BASE_DIR, "assets", "app.ico")
REQUIREMENTS = os.path.join(BASE_DIR, "requirements.txt")
CRITICAL = {"pyautogui", "keyboard", "pyside6-essentials", "pillow", "pyperclip", "psutil", "requests"}
IMPORT_NAME = {"pillow": "PIL", "speechrecognition": "speech_recognition", "pyside6-essentials": "PySide6", "onnxruntime-directml": "onnxruntime"}
STEPS = ["Подготовка", "Компоненты", "Проверка", "Готово"]

try:
    import i18n
except Exception:  # без переводов — по-русски
    i18n = None


def T(s):
    try:
        return i18n.t(s) if i18n else s
    except Exception:
        return s


C = dict(bg="#0b0b10", side="#0e0e15", card="#13131c", line="#22222f", text="#ececf4", mut="#8a8aa3", acc="#6366f1",
         ok="#34d399", warn="#fbbf24", err="#f87171")
QSS = f"""
QWidget {{ background: transparent; color: {C['text']}; font-family: 'Segoe UI', 'DejaVu Sans'; font-size: 14px; }}
QWidget#root {{ background: {C['bg']}; }}
QFrame#side {{ background: {C['side']}; border-right: 1px solid {C['line']}; }}
QLabel#h1 {{ font-size: 26px; font-weight: 700; }}
QLabel#mut {{ color: {C['mut']}; }}
QLabel#small {{ color: {C['mut']}; font-size: 12px; }}
QLabel#logo {{ font-size: 17px; font-weight: 700; }}
QProgressBar {{ background: {C['card']}; border: 1px solid {C['line']}; border-radius: 6px; height: 12px; max-height: 12px; }}
QProgressBar::chunk {{ background: {C['acc']}; border-radius: 5px; }}
QPlainTextEdit {{ background: {C['card']}; border: 1px solid {C['line']}; border-radius: 12px; padding: 10px;
    font-family: Consolas, 'DejaVu Sans Mono', monospace; font-size: 12px; color: {C['mut']}; }}
QPushButton {{ background: {C['card']}; border: 1px solid {C['line']}; border-radius: 10px; padding: 9px 18px; }}
QPushButton:hover {{ border: 1px solid {C['acc']}; }}
QPushButton#primary {{ background: {C['acc']}; border: 1px solid {C['acc']}; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #7a7df5; }}
QCheckBox {{ spacing: 10px; padding: 3px 0; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {C['line']}; background: {C['card']}; }}
QCheckBox::indicator:checked {{ background: {C['acc']}; border: 1px solid {C['acc']}; }}
"""


def read_requirements():
    out = []
    with open(REQUIREMENTS, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                out.append(line)
    return out


def package_name(spec):
    return re.split(r"[<>=!~\[ ;]", spec)[0]


class Bus(QObject):
    progress = Signal(int)
    status = Signal(str)
    step = Signal(int)
    log = Signal(str)
    done = Signal()


class Installer(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle(T("AI Ассистент — установка"))
        self.setFixedSize(820, 540)
        if os.path.exists(ICON):
            self.setWindowIcon(QIcon(ICON))
        self.bus = Bus()
        self.failed, self.missing = {}, []
        self._build()
        b = self.bus
        b.progress.connect(self.bar.setValue)
        b.status.connect(lambda s: self._status(T(s)))
        b.step.connect(self._set_step)
        b.log.connect(self._log)
        b.done.connect(self._on_done)
        self._log(T("Начинаю установку."))
        QTimer.singleShot(200, self._start)

    # ---- интерфейс ----
    def _build(self):
        root = QHBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(240)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(22, 26, 18, 18)
        sl.setSpacing(6)
        head = QHBoxLayout()
        pic = QLabel()
        if os.path.exists(LOGO):
            pic.setPixmap(QPixmap(LOGO).scaled(34, 34, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        head.addWidget(pic)
        lg = QLabel("AI Ассистент")
        lg.setObjectName("logo")
        head.addWidget(lg)
        head.addStretch()
        sl.addLayout(head)
        sl.addSpacing(20)
        self.step_labels = []
        for s in STEPS:
            lb = QLabel()
            sl.addWidget(lb)
            self.step_labels.append((lb, T(s)))
        sl.addStretch()
        where = QLabel(f"{T('Папка установки:')}\n{BASE_DIR}")
        where.setObjectName("small")
        where.setWordWrap(True)
        sl.addWidget(where)
        root.addWidget(side)

        main = QVBoxLayout()
        main.setContentsMargins(32, 28, 32, 22)
        main.setSpacing(0)
        self.title = QLabel(T("Установка"))
        self.title.setObjectName("h1")
        main.addWidget(self.title)
        main.addSpacing(4)
        self.stat = QLabel(T("Подготовка…"))
        self.stat.setObjectName("mut")
        self.stat.setWordWrap(True)
        main.addWidget(self.stat)
        main.addSpacing(14)
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setTextVisible(False)
        main.addWidget(self.bar)
        main.addSpacing(14)
        self.logbox = QPlainTextEdit()
        self.logbox.setReadOnly(True)
        main.addWidget(self.logbox, 1)
        main.addSpacing(12)
        self.actions = QVBoxLayout()
        main.addLayout(self.actions)
        root.addLayout(main, 1)
        self._set_step(0)

    def _status(self, text, color=None):
        self.stat.setText(text)
        self.stat.setStyleSheet(f"color: {color};" if color else "")

    def _set_step(self, n):
        for i, (lb, text) in enumerate(self.step_labels):
            if i < n:
                lb.setText(f"✓  {text}")
                lb.setStyleSheet(f"color: {C['ok']}; padding: 6px 0;")
            elif i == n:
                lb.setText(f"●  {text}")
                lb.setStyleSheet("font-weight: 700; padding: 6px 0;")
            else:
                lb.setText(f"○  {text}")
                lb.setStyleSheet(f"color: {C['mut']}; padding: 6px 0;")

    def _log(self, text):
        self.logbox.appendPlainText(T(text))

    def _clear_actions(self):
        while self.actions.count():
            it = self.actions.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
            elif it.layout():
                while it.layout().count():
                    w = it.layout().takeAt(0).widget()
                    if w:
                        w.deleteLater()

    def _btn(self, text, fn, primary=False):
        b = QPushButton(T(text))
        if primary:
            b.setObjectName("primary")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(fn)
        return b

    # ---- установка (фоновый поток) ----
    def _start(self):
        self.bar.setValue(0)
        threading.Thread(target=self._run, daemon=True).start()

    def _close_running_app(self):
        try:
            import json
            import app_paths
            with open(os.path.join(app_paths.DATA_DIR, ".instance.lock"), encoding="utf-8") as f:
                pid = json.loads(f.read()).get("pid")
            if isinstance(pid, int) and pid != os.getpid():
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15,
                               creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception:
            pass

    def _pip(self, specs, timeout):
        return subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *specs],
                              capture_output=True, text=True, timeout=timeout)

    def _run(self):
        b = self.bus
        self._close_running_app()
        self.failed, self.missing = {}, []
        b.progress.emit(8)
        b.step.emit(1)
        b.status.emit("Ставлю компоненты программы (1–3 минуты)…")
        b.log.emit("Устанавливаю компоненты из requirements.txt…")
        self._install_all()
        b.progress.emit(85)
        b.step.emit(2)
        b.status.emit("Проверяю, что всё запускается…")
        b.log.emit("Проверяю, что все компоненты импортируются…")
        self._verify()
        b.progress.emit(100)
        b.step.emit(3)
        b.done.emit()

    def _install_all(self):
        b = self.bus
        specs = read_requirements()
        try:
            if self._pip(specs, 900).returncode == 0:
                b.log.emit(f"✓ Все компоненты поставились разом ({len(specs)}).")
                return
        except Exception:
            pass
        b.log.emit("Общая установка не прошла — ставлю по одному.")
        for i, spec in enumerate(specs, 1):
            name = package_name(spec)
            b.status.emit(f"Ставлю {name}… ({i}/{len(specs)})")
            b.progress.emit(8 + int(75 * i / len(specs)))
            try:
                p = self._pip([spec], 400)
                if p.returncode != 0:
                    tail = " ".join((p.stderr or p.stdout or "").strip().splitlines()[-2:])
                    self.failed[name] = tail[:200] or "pip завершился с ошибкой"
                    b.log.emit(f"✕ {name}: {self.failed[name]}")
                else:
                    b.log.emit(f"✓ {name}")
            except Exception as e:
                self.failed[name] = str(e)[:200]
                b.log.emit(f"✕ {name}: {self.failed[name]}")

    def _verify(self):
        names = [IMPORT_NAME.get(package_name(s).lower(), package_name(s)) for s in read_requirements()]
        code = ("import importlib\nbad = []\n"
                f"for n in {names!r}:\n    try: importlib.import_module(n)\n    except Exception: bad.append(n)\n"
                "print(','.join(bad))\n")
        try:
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120).stdout
            self.missing = [n for n in out.strip().split(",") if n]
            self.bus.log.emit(("✕ Не запускаются: " + ", ".join(self.missing)) if self.missing else "✓ Всё запускается.")
        except Exception:
            pass

    # ---- итог ----
    def _on_done(self):
        reverse = {IMPORT_NAME.get(package_name(s).lower(), package_name(s)): package_name(s) for s in read_requirements()}
        bad = set(self.failed) | {reverse.get(m, m) for m in self.missing}
        critical = sorted(n for n in bad if n.lower() in CRITICAL)
        optional = sorted(n for n in bad if n.lower() not in CRITICAL)
        if critical:
            details = "; ".join(f"{n}: {self.failed[n]}" for n in critical if n in self.failed)
            self._status(f"{T('Не встали обязательные компоненты:')} {', '.join(critical)}.\n{details}\n"
                         f"{T('Без них программа может не запуститься.')}", C["err"])
            self.title.setText(T("Есть проблема"))
            self._clear_actions()
            row = QHBoxLayout()
            row.addWidget(self._btn("Повторить установку", self._retry, True))
            row.addWidget(self._btn("Продолжить всё равно", self._finish_screen))
            row.addStretch()
            self.actions.addLayout(row)
            return
        if optional:
            self._status(f"{T('Не встали необязательные компоненты:')} {', '.join(optional)}.\n"
                         f"{T('Программа работает; для голоса есть кнопка «Починить» в самом приложении.')}", C["warn"])
        else:
            self._status(T("Все компоненты установлены и проверены."), C["ok"])
        self._finish_screen()

    def _retry(self):
        self._clear_actions()
        self.title.setText(T("Установка"))
        self._status(T("Подготовка…"))
        self._log("— Повторная установка —")
        self._set_step(0)
        self._start()

    def _finish_screen(self):
        self._set_step(4)
        self.title.setText(T("Готово"))
        self._clear_actions()
        self.cb_desktop = QCheckBox(T("Ярлык на рабочем столе"))
        self.cb_start = QCheckBox(T("Ярлык в меню Пуск"))
        self.cb_auto = QCheckBox(T("Запускать вместе с Windows (сразу в трее)"))
        self.cb_desktop.setChecked(True)
        self.cb_start.setChecked(True)
        for cb in (self.cb_desktop, self.cb_start, self.cb_auto):
            self.actions.addWidget(cb)
        row = QHBoxLayout()
        row.addWidget(self._btn("Запустить AI Ассистент", lambda: self._finish(True), True))
        row.addWidget(self._btn("Только закрыть", lambda: self._finish(False)))
        row.addStretch()
        self.actions.addSpacing(8)
        self.actions.addLayout(row)

    def _finish(self, launch):
        problems = []
        if self.cb_desktop.isChecked() or self.cb_start.isChecked():
            try:
                import shortcuts
                shortcuts.create(desktop=self.cb_desktop.isChecked(), start_menu=self.cb_start.isChecked())
            except Exception as e:
                problems.append(f"{T('Ярлыки не созданы:')} {e}")
        if self.cb_auto.isChecked():
            try:
                import autostart
                if not autostart.enable():
                    problems.append(T("Автозапуск не включился (подробности — в action_log.txt)."))
            except Exception as e:
                problems.append(f"{T('Автозапуск не включился:')} {e}")
        if launch:
            pw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pw):
                pw = sys.executable
            try:
                subprocess.Popen([pw, os.path.join(BASE_DIR, "main.py")], cwd=BASE_DIR)
            except Exception as e:
                problems.append(f"{T('Не удалось запустить приложение:')} {e}")
        if problems:
            self._status("\n".join(problems), C["err"])
            self._clear_actions()
            self.actions.addWidget(self._btn("Закрыть", self.close, True), 0, Qt.AlignLeft)
        else:
            self.close()


def main():
    try:
        if i18n and os.path.exists(os.path.join(BASE_DIR, "lang_en.py")):
            try:
                import config
                saved = config.load().get("ui_language") if os.path.exists(config.SETTINGS_PATH) else None
            except Exception:
                saved = None
            i18n.set_language(saved or i18n.detect_system_language())
    except Exception:
        pass
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyleSheet(QSS)
    w = Installer()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
