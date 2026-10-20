"""Диалоги и окна автопилота на Qt (main.py подключает этот модуль как `gui`: gui.ask_yes_no, gui.confirm_plan,
gui.open_agent_window…). runtime.QtShell вызывает install(). Все публичные функции безопасно звать из
любого потока: они сами переходят в поток Qt. Методы окон (add_step, finish, mark_step…) тоже потокобезопасны.
"""
import base64
import io
import threading
import webbrowser

from PySide6.QtCore import QObject, QPoint, Qt, QThread, QTimer
from PySide6.QtGui import QGuiApplication, QImage, QPixmap
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QVBoxLayout, QWidget)

from .theme_manager import theme
from .widgets import IconButton, T, Tile, label

_post = None            # post(fn): выполнить в потоке Qt (задаёт runtime.install)
RISK_ICON = {"low": "🟢", "normal": "🟡", "high": "🔴"}


def _format_step_args(step: dict) -> str:
    """Текст args для показа пользователю — с маскировкой чувствительного текста."""
    args = dict(step.get("args", {}))
    if step.get("action") == "type_text" and args.get("sensitive"):
        return "{'text': '••••••• (скрыто)'}"
    return str(args)


def _step_line(i: int, step: dict) -> str:
    import plan_validator
    risk = RISK_ICON.get(plan_validator.classify_risk(step), "")
    try:
        import agent_loop
        text = agent_loop.describe_step(step)
    except Exception:
        text = f"{step.get('action')}: {_format_step_args(step)}"
    return f"{risk} {i + 1}. {text}"


# ---------- общие помощники ----------

def on_qt(fn, *args, **kwargs):
    """Выполняет fn в потоке Qt и возвращает результат (из другого потока — блокируется до ответа)."""
    app = QApplication.instance()
    if app is None or QThread.currentThread() is app.thread() or _post is None:
        return fn(*args, **kwargs)
    done, box = threading.Event(), {}

    def run():
        try:
            box["v"] = fn(*args, **kwargs)
        except BaseException as e:                      # noqa: BLE001
            box["e"] = e
        finally:
            done.set()
    _post(run)
    done.wait()
    if "e" in box:
        raise box["e"]
    return box.get("v")


def later(fn):
    """Выполнить в потоке Qt, не ожидая (для методов окон, которые зовут рабочие потоки)."""
    if _post is None:
        fn()
    else:
        _post(fn)


def exclude_from_capture(widget):
    """Окно видно человеку, но не попадает в скриншоты (Windows 10 2004+): ИИ не видит панель автопилота."""
    try:
        import ctypes
        ctypes.windll.user32.SetWindowDisplayAffinity(int(widget.winId()), 0x00000011)   # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


def _center(w, top=3):
    try:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        w.adjustSize()
        w.move(geo.x() + (geo.width() - w.width()) // 2, geo.y() + max(0, (geo.height() - w.height()) // top))
    except Exception:
        pass


def _pil_to_pixmap(img, max_w=520, max_h=260):
    img = img.convert("RGBA")
    qimg = QImage(img.tobytes("raw", "RGBA"), img.width, img.height, QImage.Format_RGBA8888).copy()
    return QPixmap.fromImage(qimg).scaled(max_w, max_h, Qt.KeepAspectRatio, Qt.SmoothTransformation)


def _wrap(text, obj=None):
    l = QLabel(text)
    l.setWordWrap(True)
    if obj:
        l.setObjectName(obj)
    return l


def _button(text, kind="primary", cb=None):
    b = QPushButton(T(text))
    b.setObjectName(kind)
    b.setCursor(Qt.PointingHandCursor)
    if cb:
        b.clicked.connect(cb)
    return b


class Box(QDialog):
    """Базовое окно: плитка-значок, заголовок, пояснение; поверх всех окон; по желанию скрыто от скриншотов."""

    def __init__(self, title, width=560, topmost=True, capture=False, window_title=None, modal=True):
        super().__init__(None)
        self.setObjectName("dlg")
        self.setWindowTitle(T(window_title or title))
        self.setModal(modal)
        self._capture = capture
        flags = Qt.Dialog | Qt.WindowTitleHint | Qt.WindowCloseButtonHint
        if topmost:
            flags |= Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.setMinimumWidth(width)
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(24, 22, 24, 20)
        self.lay.setSpacing(12)

    def header(self, title, subtitle="", icon="sparkles", color=None):
        row = QHBoxLayout()
        row.setSpacing(12)
        row.addWidget(Tile(icon, 40, 11), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        t = label(title, "h3")
        t.setStyleSheet("font-size:16px;" + (f"color:{color};" if color else ""))
        col.addWidget(t)
        if subtitle:
            col.addWidget(label(subtitle, "sub", wrap=True, translate=False))
        row.addLayout(col, 1)
        self.lay.addLayout(row)

    def buttons(self, *btns, center=True):
        row = QHBoxLayout()
        row.setSpacing(10)
        if center:
            row.addStretch(1)
        for b in btns:
            row.addWidget(b)
        row.addStretch(1) if center else None
        self.lay.addLayout(row)
        return row

    def showEvent(self, e):
        super().showEvent(e)
        if self._capture:
            exclude_from_capture(self)
        if not getattr(self, "_placed", False):
            self._placed = True
            _center(self)
        self.raise_()
        self.activateWindow()


def _run(dlg):
    """Показывает окно модально и возвращает код."""
    return dlg.exec()


# ---------- быстрый ввод ----------

def ask_command() -> str:
    def go():
        dlg = Box("Что сделать?", 560, window_title="AI Ассистент — команда")
        dlg.header("Что сделать?", T("Напиши команду и нажми Enter"), "sparkles")
        edit = QLineEdit()
        edit.setMinimumHeight(40)
        edit.setPlaceholderText(T("Скажи или напиши команду…"))
        dlg.lay.addWidget(edit)
        edit.returnPressed.connect(dlg.accept)
        dlg.buttons(_button("Выполнить", "primary", dlg.accept))
        edit.setFocus()
        return edit.text() if _run(dlg) else ""
    return on_qt(go)


# ---------- подтверждение плана ----------

def confirm_plan(summary: str, steps: list, screenshot_b64=None, auto_seconds: int = 0, manual: bool = False):
    import action_executor
    import plan_validator

    def go():
        risk = plan_validator.highest_risk(steps)
        title = {"low": "Безопасные действия", "normal": "Подтверди действия", "high": "Внимание: высокий риск"}.get(risk, "Подтверди действия")
        dlg = Box(title, 620, window_title="Подтверди план действий")
        dlg.header(title, summary, {"low": "check", "normal": "sparkles", "high": "shield"}.get(risk, "sparkles"),
                   theme.c["err"] if risk == "high" else (theme.c["ok"] if risk == "low" else None))
        click_steps = [s for s in steps if s.get("action") in plan_validator.POINTER_ACTIONS and "x" in s.get("args", {})]
        if screenshot_b64 and click_steps:
            try:
                from PIL import Image, ImageDraw
                img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64))).convert("RGB")
                draw = ImageDraw.Draw(img)
                for i, s in enumerate(click_steps, 1):
                    x, y, r = s["args"]["x"], s["args"]["y"], 14
                    draw.ellipse((x - r, y - r, x + r, y + r), outline="red", width=4)
                    draw.text((x + r + 2, y - 8), str(i), fill="red")
                pic = QLabel()
                pic.setPixmap(_pil_to_pixmap(img))
                pic.setAlignment(Qt.AlignCenter)
                dlg.lay.addWidget(pic)
            except Exception:
                pass
        dlg.lay.addWidget(label("Шаги (🟢 безопасно · 🟡 обычное · 🔴 высокий риск). Сними галочку, чтобы пропустить шаг:", "small", wrap=True))
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(150)
        scroll.setMaximumHeight(260)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setSpacing(4)
        checks, blocked = [], False
        for i, step in enumerate(steps):
            action, args, warn = step.get("action"), step.get("args", {}), ""
            if action == "create_file" and not action_executor.is_path_allowed(args.get("path", "")):
                warn, blocked = "  ⚠️ " + T("путь вне разрешённых папок"), True
            elif action == "move_file" and not (action_executor.is_path_allowed(args.get("src", "")) and action_executor.is_path_allowed(args.get("dst", ""))):
                warn, blocked = "  ⚠️ " + T("путь вне разрешённых папок"), True
            cb = QCheckBox(_step_line(i, step) + warn)
            cb.setChecked(True)
            checks.append(cb)
            bl.addWidget(cb)
        bl.addStretch(1)
        scroll.setWidget(body)
        dlg.lay.addWidget(scroll)
        if blocked:
            dlg.lay.addWidget(_wrap(T("⚠️ Часть шагов затронет папки вне разрешённого списка — при выполнении они всё равно будут заблокированы, независимо от галочки."), "warn"))
        status = _wrap("")
        dlg.lay.addWidget(status)
        if risk == "high":
            dlg.lay.addWidget(_wrap(T("⚠️ Этот план содержит действия, которые могут быть необратимы (файлы, системные изменения, опасные формулировки)."), "err"))
            dlg.buttons(_button("Отмена", "primary", dlg.reject), _button("Я понимаю риск, разрешить", "danger", dlg.accept))
        elif not manual and (risk == "low" or (risk == "normal" and auto_seconds > 0)):
            state = {"n": auto_seconds if auto_seconds > 0 else 3}
            label_txt = "🟢 " + T("Безопасные действия") if risk == "low" else "⏱ " + T("Простая команда")
            timer = QTimer(dlg)

            def tick():
                if state["n"] <= 0:
                    timer.stop()
                    dlg.accept()
                    return
                status.setText(f"{label_txt} — " + T("выполнится само через") + f" {state['n']} " + T("с… (Отмена — остановить)"))
                state["n"] -= 1
            timer.timeout.connect(tick)
            dlg.buttons(_button("Выполнить сейчас", "success", dlg.accept), _button("Отмена", "neutral", dlg.reject))
            tick()
            timer.start(1000)
        else:
            dlg.buttons(_button("Разрешить", "primary", dlg.accept), _button("Отмена", "neutral", dlg.reject))
        if not _run(dlg):
            return None
        return [s for s, c in zip(steps, checks) if c.isChecked()]
    return on_qt(go)


# ---------- уведомление ----------

def show_notification(message: str):
    def go():
        dlg = Box("AI Ассистент", 420, modal=False)
        dlg.setAttribute(Qt.WA_DeleteOnClose)
        dlg.lay.addWidget(_wrap(message))
        dlg.buttons(_button("OK", "primary", dlg.close))
        dlg.show()
        QTimer.singleShot(9000, dlg.close)
    later(go)


# ---------- первый запуск ----------

def first_run_wizard() -> list:
    def go():
        dlg = Box("Добро пожаловать!", 580, window_title="Добро пожаловать — настройка")
        dlg.header("Добро пожаловать!", T("Для работы нужен бесплатный ключ Google Gemini — это занимает минуту."), "sparkles")
        dlg.lay.addWidget(_wrap(T("1.  Открой aistudio.google.com\n2.  Войди через свой Google-аккаунт (Gmail)\n3.  Нажми «Get API key» → «Create API key»\n4.  Скопируй ключ и вставь его ниже\n\nМожно вставить НЕСКОЛЬКО ключей (с разных Google-аккаунтов), по одному на строку — тогда при лимите на одном программа сама переключится на следующий.")))
        dlg.lay.addWidget(_button("Открыть aistudio.google.com", "neutral", lambda: webbrowser.open("https://aistudio.google.com/apikey")))
        box = QPlainTextEdit()
        box.setObjectName("log")
        box.setMinimumHeight(110)
        dlg.lay.addWidget(box)
        dlg.buttons(_button("Сохранить и продолжить", "primary", dlg.accept), _button("Пропустить", "neutral", dlg.reject))
        dlg.lay.addWidget(label("Ключи можно добавить/изменить позже: Настройки в главном окне", "small", wrap=True))
        if not _run(dlg):
            return []
        return [l.strip() for l in box.toPlainText().splitlines() if l.strip()]
    return on_qt(go)


# ---------- автопилот: старт ----------

def confirm_agent_start(goal: str, intent: str, steps_text: list, max_steps: int, auto_seconds: int = 0) -> bool:
    def go():
        dlg = Box("🤖 Автопилот", 600, window_title="Автопилот — подтверждение")
        dlg.header("🤖 " + T("Автопилот"), T("Цель:") + f" {goal}", "sparkles")
        dlg.lay.addWidget(label("ИИ посмотрел на экран и предлагает начать так:", "h3", wrap=True))
        if intent:
            dlg.lay.addWidget(_wrap(intent))
        for i, text in enumerate(steps_text, 1):
            dlg.lay.addWidget(_wrap(f"   {i}. {text}"))
        limit = (T("(не больше") + f" {max_steps})") if max_steps < 1000 else T("(без ограничения числа шагов)")
        dlg.lay.addWidget(_wrap(T("Дальше ИИ сам будет смотреть на экран и делать по одному шагу") + f" {limit}.\n"
                                + T("Потенциально опасные действия — файлы, установка, удаление — он спросит отдельно.") + "\n"
                                + T("Остановить в любой момент: кнопка 🛑 в окне автопилота или резкий рывок мыши в левый верхний угол экрана."), "small"))
        status = _wrap("")
        dlg.lay.addWidget(status)
        dlg.buttons(_button("▶ Запустить автопилот", "primary", dlg.accept), _button("Отмена", "neutral", dlg.reject))
        if auto_seconds > 0:
            state, timer = {"n": auto_seconds}, QTimer(dlg)

            def tick():
                if state["n"] <= 0:
                    timer.stop()
                    dlg.accept()
                    return
                status.setText(T("Безопасная задача — запустится сама через") + f" {state['n']}…")
                state["n"] -= 1
            timer.timeout.connect(tick)
            tick()
            timer.start(1000)
        return bool(_run(dlg))
    return on_qt(go)


# ---------- да / нет ----------

def ask_yes_no(title: str, text: str, yes: str = "Да", no: str = "Нет") -> bool:
    def go():
        dlg = Box(title, 520)
        dlg.header(title, "", "sparkles")
        dlg.lay.addWidget(_wrap(text))
        dlg.buttons(_button(yes, "primary", dlg.accept), _button(no, "neutral", dlg.reject))
        return bool(_run(dlg))
    return on_qt(go)


# ---------- окно выполнения плана ----------

class ExecutionProgressWindow:
    STATUS_ICON = {"pending": "⏳", "running": "🔄", "done": "✅", "error": "❌", "skipped": "⏭️", "warning": "⚠️"}

    def __init__(self, steps: list):
        self.steps = steps
        self.abort_event = threading.Event()
        self.pause_event = threading.Event()
        self._finished = False
        self._labels = []

        class W(Box):
            def closeEvent(w, e):
                if self._finished:
                    e.accept()
                else:
                    e.ignore()
                    self._on_stop()
        self.win = W("Выполняю план действий", 560, topmost=False, capture=True, window_title="Выполнение плана", modal=False)
        self.win.header("Выполняю план действий", T("Окно можно не закрывать — оно покажет результат"), "sparkles")
        for i, step in enumerate(steps):
            l = _wrap(f"{self.STATUS_ICON['pending']}  {i + 1}. {self._describe(step)}")
            self._labels.append(l)
            self.win.lay.addWidget(l)
        self.status = _wrap(T("Выполняется..."))
        self.win.lay.addWidget(self.status)
        self.btn = _button("Остановить", "primary", self._on_stop)
        self.win.buttons(self.btn)
        self.win.show()

    @staticmethod
    def _describe(step):
        try:
            import agent_loop
            return agent_loop.describe_step(step)
        except Exception:
            return str(step.get("action"))

    def mark_step(self, index: int, status: str):
        def go():
            self._labels[index].setText(f"{self.STATUS_ICON.get(status, '•')}  {index + 1}. {self._describe(self.steps[index])}")
        later(go)

    def finish(self, message: str):
        def go():
            self._finished = True
            self.status.setText(message)
            self.btn.setText(T("Закрыть"))
            self.btn.setObjectName("neutral")
            self.btn.style().unpolish(self.btn)
            self.btn.style().polish(self.btn)
            self.btn.clicked.disconnect()
            self.btn.clicked.connect(self.win.close)
        later(go)

    def _on_stop(self):
        self.abort_event.set()
        self.status.setText(T("Останавливаю после текущего шага..."))


def open_progress_window(steps: list) -> ExecutionProgressWindow:
    return on_qt(ExecutionProgressWindow, steps)


# ---------- окно «Слушаю…» ----------

class VoiceWindow:
    def __init__(self):
        self.cancelled = threading.Event()

        class W(Box):
            def closeEvent(w, e):
                self.cancelled.set()
                e.accept()
        self.win = W("Голосовая команда", 440, modal=False)
        self.win.header("Голосовая команда", T("Говори — я слушаю"), "mic")
        self.state = _wrap(T("Подключаю микрофон…"))
        self.state.setAlignment(Qt.AlignCenter)
        self.win.lay.addWidget(self.state)
        self.level = QLabel("▯" * 14)
        self.level.setAlignment(Qt.AlignCenter)
        self.level.setStyleSheet(f"color:{theme.c['ok']}; font-size:18px;")
        self.win.lay.addWidget(self.level)
        self.win.lay.addWidget(label("Скажи, что нужно сделать. Запись остановится сама, когда договоришь.", "small", wrap=True))
        self.win.buttons(_button("Отмена", "neutral", self._cancel))
        self.win.show()

    def _cancel(self):
        self.cancelled.set()
        self.state.setText(T("Отменяю…"))

    def set_state(self, text: str):
        later(lambda: self.state.setText(text))

    def set_level(self, value: float):
        n = max(0, min(14, int(value * 14)))
        later(lambda: self.level.setText("█" * n + "▯" * (14 - n)))

    def close(self):
        later(lambda: (setattr(self.win, "_quiet", True), self.win.close()))


def open_voice_window() -> VoiceWindow:
    return on_qt(VoiceWindow)


# ---------- панель и окно автопилота ----------

class AgentBar(QFrame):
    """Узкая панель у верхнего края экрана: что делает автопилот, «Журнал», «Пауза», «Стоп». Не попадает в скриншоты."""

    def __init__(self, owner):
        super().__init__(None)
        self.setObjectName("bar")
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setFixedSize(560, 44)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(14, 0, 0, 0)
        lay.setSpacing(0)
        self.text = QLabel(T("🤖 Автопилот запускается…"))
        self.text.setObjectName("bartext")
        lay.addWidget(self.text, 1)
        for title, kind, cb in (("📋 Журнал", "barbtn", owner.toggle_log), ("⏸ Пауза", "barbtn", owner.toggle_pause), ("🛑 Стоп", "stop", owner._on_stop)):
            b = QPushButton(T(title))
            b.setObjectName(kind)
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(cb)
            lay.addWidget(b)
            if "Пауза" in title:
                self.pause_btn = b
        self._drag = None
        geo = QGuiApplication.primaryScreen().availableGeometry()
        self.move(geo.x() + (geo.width() - 560) // 2, geo.y() + 6)
        self.show()
        exclude_from_capture(self)

    def mousePressEvent(self, e):
        self._drag = e.globalPosition().toPoint() - self.frameGeometry().topLeft()

    def mouseMoveEvent(self, e):
        if self._drag is not None and e.buttons() & Qt.LeftButton:
            self.move(e.globalPosition().toPoint() - self._drag)

    def set_text(self, text):
        self.text.setText("🤖 " + text[:64])

    def set_paused(self, paused):
        self.pause_btn.setText(T("▶ Продолжить") if paused else T("⏸ Пауза"))


class AgentWindow:
    """Журнал автопилота (свёрнут, пока ИИ смотрит на экран) + панель «Стоп». Методы для рабочего потока:
    add_step / set_result / set_preview / request_confirm / finish."""

    def __init__(self, goal: str, max_steps: int):
        self.abort_event = threading.Event()
        self.pause_event = threading.Event()
        self._confirm_event = threading.Event()
        self._confirm_answer = False
        self._finished = False
        self._log_visible = False

        outer = self

        class W(Box):
            def closeEvent(w, e):
                if outer._finished:
                    outer.bar.close()
                    e.accept()
                else:
                    e.ignore()
                    outer._on_stop()
        self.win = W("🤖 Автопилот", 680, topmost=False, capture=True, window_title="Автопилот", modal=False)
        self.win.header("🤖 " + T("Автопилот"), T("Цель:") + f" {goal}", "sparkles")
        self.preview = _wrap(T("Здесь появится кадр, который видит ИИ (красный круг — куда он собирается нажать)"), "small")
        self.preview.setAlignment(Qt.AlignCenter)
        self.win.lay.addWidget(self.preview)
        self.log = QPlainTextEdit()
        self.log.setObjectName("log")
        self.log.setReadOnly(True)
        self.log.setMinimumHeight(180)
        self.win.lay.addWidget(self.log, 1)
        self.confirm_box = QFrame()
        self.confirm_box.setObjectName("warnbox")
        cl = QVBoxLayout(self.confirm_box)
        self.confirm_text = _wrap("", "err")
        cl.addWidget(self.confirm_text)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(_button("Нет, стоп", "primary", lambda: self._answer(False)))
        row.addWidget(_button("Да, разрешаю", "danger", lambda: self._answer(True)))
        row.addStretch(1)
        cl.addLayout(row)
        self.confirm_box.hide()
        self.win.lay.addWidget(self.confirm_box)
        self.status = _wrap(T("Работаю…") + ((" (" + T("не больше") + f" {max_steps} " + T("шагов") + ")") if max_steps < 1000 else ""))
        self.win.lay.addWidget(self.status)
        self.stop_btn = _button("🛑 Остановить", "danger", self._on_stop)
        copy = _button("📋 Копировать журнал", "ghost", self._copy_log)
        self.win.buttons(self.stop_btn, copy)
        self.bar = AgentBar(self)
        self.win.showMinimized()

    # --- из рабочего потока ---
    def add_step(self, n: int, text: str):
        def go():
            self.log.appendPlainText(f"\n{n}. {text}")
            self.bar.set_text(f"{T('Шаг')} {n}: {text}")
        later(go)

    def set_result(self, text: str):
        def go():
            self.log.appendPlainText(f"     → {text}")
            if text.startswith("⏳"):
                self.bar.set_text(text)
        later(go)

    def set_preview(self, image):
        def go():
            try:
                self.preview.setPixmap(_pil_to_pixmap(image, 640, 300))
            except Exception:
                pass
        later(go)

    def request_confirm(self, text: str) -> bool:
        self._confirm_event.clear()
        self._confirm_answer = False

        def go():
            self.confirm_text.setText(text)
            self.confirm_box.show()
            self._show_log()
        later(go)
        while not self._confirm_event.wait(0.2):
            if self.abort_event.is_set():
                return False
        return self._confirm_answer

    def finish(self, ok: bool, message: str):
        def go():
            self._finished = True
            self.log.appendPlainText(f"\n{'✅' if ok else '⛔'} {message}")
            self.status.setText(T("Готово.") if ok else T("Остановлено."))
            self.stop_btn.setText(T("Закрыть"))
            self.stop_btn.setObjectName("neutral")
            self.stop_btn.style().unpolish(self.stop_btn)
            self.stop_btn.style().polish(self.stop_btn)
            self.stop_btn.clicked.disconnect()
            self.stop_btn.clicked.connect(self.win.close)
            self.bar.close()
            self._show_log()
        later(go)

    # --- в потоке Qt ---
    def _show_log(self):
        self._log_visible = True
        self.win.showNormal()
        self.win.raise_()
        self.win.activateWindow()

    def toggle_log(self):
        if self._log_visible:
            self._log_visible = False
            self.win.showMinimized()
        else:
            self._show_log()

    def _answer(self, value: bool):
        self._confirm_answer = value
        self.confirm_box.hide()
        self._confirm_event.set()
        if not self._finished:
            self._log_visible = False
            self.win.showMinimized()          # ответил — снова освобождаем экран для ИИ

    def _copy_log(self):
        QGuiApplication.clipboard().setText(self.log.toPlainText().strip())
        self.status.setText(T("Журнал скопирован в буфер обмена."))

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.bar.set_paused(False)
            self.bar.set_text(T("Продолжаю…"))
        else:
            self.pause_event.set()
            self.bar.set_paused(True)
            self.bar.set_text(T("Пауза — нажми «Продолжить»"))

    def _on_stop(self):
        self.abort_event.set()
        self.pause_event.clear()
        self.status.setText(T("Останавливаю после текущего действия…"))
        self.bar.set_text(T("Останавливаю…"))
        if not self._confirm_event.is_set():
            self._answer(False)


def open_agent_window(goal: str, max_steps: int) -> AgentWindow:
    return on_qt(AgentWindow, goal, max_steps)


# ---------- «Ассистент думает…» ----------

class ThinkingWindow:
    def __init__(self, text: str, on_stop):
        self._on_stop = on_stop
        outer = self

        class W(Box):
            def closeEvent(w, e):
                outer._stop(close=False)
                e.accept()
        self.win = W("🤖 Ассистент думает…", 400, capture=True, window_title="Ассистент", modal=False)
        self.win.header("🤖 " + T("Ассистент думает…"), "", "sparkles")
        self.text = _wrap(text)
        self.win.lay.addWidget(self.text)
        self.win.buttons(_button("🛑 Остановить", "danger", self._stop))
        self.win.show()
        try:
            geo = QGuiApplication.primaryScreen().availableGeometry()
            self.win.move(geo.right() - self.win.width() - 30, geo.y() + 60)
        except Exception:
            pass

    def _stop(self, close=True):
        try:
            self._on_stop()
        except Exception:
            pass
        if close:
            self.win.close()

    def set_text(self, text: str):
        later(lambda: self.text.setText(text))

    def close(self):
        later(self.win.close)


def open_thinking_window(text: str, on_stop) -> ThinkingWindow:
    return on_qt(ThinkingWindow, text, on_stop)


# ---------- подмена функций gui.py ----------

def install(post):
    """Запоминает, как выполнять код в потоке Qt (его даёт runtime.QtShell)."""
    global _post
    _post = post
