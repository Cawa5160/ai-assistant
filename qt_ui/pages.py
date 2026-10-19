"""Страницы окна: Главная и Ассистент (чат). Остальные страницы пока — заглушки."""
import os
import time

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (QFrame, QHBoxLayout, QLabel, QLineEdit, QScrollArea, QVBoxLayout, QWidget, QGridLayout,
                               QFileDialog)

import i18n

from .attachments import AttachTray, save_clipboard_image
from .dialogs import ask_consent
from .theme_manager import theme
from .widgets import (AutoTextEdit, Card, IconButton, MessageRow, Segmented, StatusDot, T, label, repolish)

PAD = (40, 28, 40, 24)


def _page_layout(page):
    lay = QVBoxLayout(page)
    lay.setContentsMargins(*PAD)
    lay.setSpacing(0)
    return lay


class PlaceholderPage(QWidget):
    def __init__(self, title, parent=None):
        super().__init__(parent)
        lay = _page_layout(self)
        lay.addWidget(label(title, "h1"))
        lay.addSpacing(6)
        lay.addWidget(label("Эта страница переедет в новый интерфейс на следующем этапе.", "sub"))
        lay.addStretch(1)


class HomePage(QWidget):
    command = Signal(str)
    voice_requested = Signal()
    files_chosen = Signal(list)
    file_tools_requested = Signal()

    CHIPS = [("camera", "Скриншот", "сделай скриншот"), ("timer", "Таймер 10 минут", "поставь таймер на 10 минут"),
             ("globe", "Открыть браузер", "открой браузер"), ("note", "Блокнот", "открой блокнот"),
             ("volume", "Громче", "громче"), ("layers", "Файлы", "@files"), ("mic", "Голос", "")]

    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        lay = _page_layout(self)
        self.greeting = label("", "h1", translate=False)
        lay.addWidget(self.greeting)
        lay.addSpacing(4)
        lay.addWidget(label("Что сделать на компьютере?", "sub"))
        lay.addSpacing(20)

        self.cmd = QFrame()
        self.cmd.setObjectName("cmd")
        row = QHBoxLayout(self.cmd)
        row.setContentsMargins(16, 6, 12, 6)
        row.setSpacing(10)
        row.addWidget(IconButton("sparkles", "acc", size=20, object_name="ghost"))
        self.field = QLineEdit()
        self.field.setObjectName("field")
        self.field.setPlaceholderText(T("Скажи или напиши команду…"))
        self.field.setMinimumHeight(42)
        self.field.returnPressed.connect(self._submit)
        row.addWidget(self.field, 1)
        row.addWidget(label("Ctrl K", "small"))
        clip = IconButton("paperclip", "mut", size=20)
        clip.setToolTip(T("Прикрепить файл или фото"))
        clip.clicked.connect(self._attach)
        row.addWidget(clip)
        mic = IconButton("mic", "mut", size=20)
        mic.setToolTip(T("Команда голосом"))
        mic.clicked.connect(self.voice_requested)
        row.addWidget(mic)
        lay.addWidget(self.cmd)
        lay.addSpacing(14)

        chips = QHBoxLayout()
        chips.setSpacing(8)
        for icon_name, text, cmd in self.CHIPS:
            b = IconButton(icon_name, "mut", size=16, text="  " + text, object_name="chip")
            b.clicked.connect(lambda _=False, c=cmd: self._chip(c))
            chips.addWidget(b)
        chips.addStretch(1)
        lay.addLayout(chips)
        lay.addSpacing(18)

        grid = QGridLayout()
        grid.setSpacing(14)
        grid.setColumnStretch(0, 13)
        grid.setColumnStretch(1, 10)
        st = Card()
        sl = QVBoxLayout(st)
        sl.setContentsMargins(20, 18, 20, 18)
        sl.addWidget(label("Состояние", "h3"))
        sl.addSpacing(10)
        top = QHBoxLayout()
        self.dot = StatusDot()
        top.addWidget(self.dot, 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        self.st_title = label("", "h3", translate=False)
        self.st_title.setStyleSheet("font-size:17px")
        self.st_detail = label("", "sub", translate=False)
        col.addWidget(self.st_title)
        col.addWidget(self.st_detail)
        top.addLayout(col, 1)
        sl.addLayout(top)
        sl.addSpacing(14)
        self.voice = IconButton("mic", "white", size=18, text="  Команда голосом", object_name="primary")
        self.voice.clicked.connect(self.voice_requested)
        sl.addWidget(self.voice, 0, Qt.AlignLeft)
        self.revoke = IconButton("shield", "mut", size=16, text="  Отозвать согласие на отправку файлов", object_name="ghost")
        self.revoke.clicked.connect(self._revoke)
        sl.addWidget(self.revoke, 0, Qt.AlignLeft)
        grid.addWidget(st, 0, 0)

        today = Card()
        tl = QVBoxLayout(today)
        tl.setContentsMargins(20, 18, 20, 18)
        tl.addWidget(label("Сегодня", "h3"))
        tl.addSpacing(8)
        trow = QHBoxLayout()
        self.big = label("0", "big", translate=False)
        trow.addWidget(self.big)
        self.big_sub = label("", "sub", translate=False)
        trow.addWidget(self.big_sub, 1)
        tl.addLayout(trow)
        tl.addStretch(1)
        grid.addWidget(today, 0, 1)
        lay.addLayout(grid)
        lay.addSpacing(14)

        self.recent = Card()
        self.rl = QVBoxLayout(self.recent)
        self.rl.setContentsMargins(20, 16, 20, 10)
        self.rl.addWidget(label("Последние команды", "h3"))
        lay.addWidget(self.recent)
        lay.addStretch(1)
        self.refresh()

    def _chip(self, cmd):
        if cmd == "@files":
            self.file_tools_requested.emit()
        elif cmd:
            self.command.emit(cmd)
        else:
            self.voice_requested.emit()

    def _attach(self):
        paths, _ = QFileDialog.getOpenFileNames(self, T("Прикрепить файл или фото"), "", T("Все файлы") + " (*.*)")
        if paths:
            self.files_chosen.emit(paths)

    def _revoke(self):
        self.backend.revoke_consent()
        self.refresh()

    def _submit(self):
        text = self.field.text().strip()
        if text:
            self.field.clear()
            self.command.emit(text)

    def focus_command(self):
        self.field.setFocus()

    def refresh(self):
        h = int(time.strftime("%H"))
        hello = "Доброе утро" if 5 <= h < 12 else "Добрый день" if 12 <= h < 18 else "Добрый вечер" if 18 <= h < 23 else "Доброй ночи"
        self.greeting.setText(T(hello) + " 👋")
        self.revoke.setVisible(self.backend.consent_given())
        s = self.backend.status()
        self.dot.set_role("ok" if s["ready"] else "warn")
        self.st_title.setText(T(s["text"]))
        self.st_detail.setText(T(s["detail"]))
        self.big.setText(str(s["done_today"]))
        extra = ("\n" + T("за") + f" {s['minutes_today']} " + T("мин автопилота")) if s.get("minutes_today") else ""
        self.big_sub.setText(T("задач выполнено") + extra)
        while self.rl.count() > 1:
            item = self.rl.takeAt(1)
            if item.widget():
                item.widget().deleteLater()
        for cmd, status, when in self.backend.recent():
            sep = QFrame()
            sep.setObjectName("sep")
            self.rl.addSpacing(8)
            self.rl.addWidget(sep)
            self.rl.addSpacing(8)
            r = QHBoxLayout()
            r.addWidget(label(cmd, translate=False), 1)
            ok = label("✓ " + T(status) + " · " + when, "ok", translate=False)
            r.addWidget(ok)
            w = QWidget()
            w.setLayout(r)
            r.setContentsMargins(0, 0, 0, 0)
            self.rl.addWidget(w)
        self.rl.addSpacing(6)


class ChatPage(QWidget):
    def __init__(self, backend, parent=None):
        super().__init__(parent)
        self.backend = backend
        self._pending = None
        lay = _page_layout(self)
        lay.addWidget(label("Ассистент", "h1"))
        lay.addSpacing(4)
        lay.addWidget(label("Спросить — разговор. Выполнить — команда для компьютера.", "sub"))
        lay.addSpacing(14)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.inner = QWidget()
        self.msgs = QVBoxLayout(self.inner)
        self.msgs.setContentsMargins(0, 0, 8, 0)
        self.msgs.setSpacing(16)
        self.msgs.addStretch(1)
        self.scroll.setWidget(self.inner)
        lay.addWidget(self.scroll, 1)
        lay.addSpacing(12)

        self.box = QFrame()
        self.box.setObjectName("input")
        bl = QVBoxLayout(self.box)
        bl.setContentsMargins(16, 10, 12, 10)
        bl.setSpacing(6)
        self.edit = AutoTextEdit("Напиши вопрос или команду…")
        self.edit.submitted.connect(self.send)
        self.edit.focus_changed.connect(self._focus)
        self.tray = AttachTray()
        bl.addWidget(self.tray)
        self.edit.files_pasted.connect(self.add_files)
        self.edit.image_pasted.connect(self._paste_image)
        bl.addWidget(self.edit)
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.attach = IconButton("paperclip", "mut", size=19)
        self.attach.setToolTip(T("Прикрепить файл или фото"))
        self.attach.clicked.connect(self._attach)
        bar.addWidget(self.attach)
        self.mode = Segmented([("ask", "Спросить"), ("run", "Выполнить")], "ask")
        self.engine = Segmented([("gemini", "Gemini"), ("ollama", "Локально")], getattr(backend, "engine", "gemini"))
        self.engine.changed.connect(self._engine_changed)
        bar.addWidget(self.mode)
        bar.addWidget(self.engine)
        bar.addStretch(1)
        self.clear_btn = IconButton("trash", "mut", size=17, text="  Очистить", object_name="ghost")
        self.clear_btn.clicked.connect(self.clear)
        bar.addWidget(self.clear_btn)
        self.send_btn = IconButton("arrow-up", "white", size=17, text="  Отправить", object_name="primary")
        self.send_btn.clicked.connect(self.send)
        bar.addWidget(self.send_btn)
        bl.addLayout(bar)
        lay.addWidget(self.box)

    def _engine_changed(self, name):
        """У каждой модели своя переписка (как в старом окне)."""
        self.backend.set_engine(name)
        self.clear()
        for t in self.backend.history_of(name):
            self.add_message("user" if t["role"] == "user" else "assistant", t["text"])

    def _focus(self, on):
        self.box.setProperty("focus", on)
        repolish(self.box)

    def _attach(self):
        paths, _ = QFileDialog.getOpenFileNames(self, T("Прикрепить файл или фото"), "",
                                                T("Все файлы") + " (*.*)")
        self.add_files(paths)

    def add_files(self, paths):
        self.tray.add(paths)
        self.edit.setFocus()

    def _paste_image(self, img):
        self.tray.add([save_clipboard_image(img)], temp=True)
        self.edit.setFocus()

    def add_message(self, role, text, meta=""):
        row = MessageRow(role, text, meta)
        self.msgs.insertWidget(self.msgs.count() - 1, row)
        QTimer.singleShot(30, lambda: self.scroll.verticalScrollBar().setValue(self.scroll.verticalScrollBar().maximum()))
        return row

    def clear(self):
        while self.msgs.count() > 1:
            item = self.msgs.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def send(self, text=None):
        text = (text if isinstance(text, str) else self.edit.toPlainText()).strip()
        files = self.tray.paths()
        if (not text and not files) or self._pending is not None:
            return
        mode = "run" if self.mode._btns["run"].isChecked() else "ask"
        engine = "ollama" if self.engine._btns["ollama"].isChecked() else "gemini"
        if files:
            mode = "ask"                       # файлы обрабатываются только в режиме «Спросить»
            if self.backend.needs_consent(engine, files):
                answer = ask_consent([os.path.basename(f) for f in files], self.window())
                if not answer:
                    return
                if answer == "always":
                    self.backend.remember_consent()
        self.edit.clear()
        shown = ("▶ " if mode == "run" else "") + text
        if files:
            shown = (shown + "\n" if shown else "") + "📎 " + ", ".join(os.path.basename(f) for f in files)
        self.add_message("user", shown)
        self._pending = self.add_message("assistant", T("Думаю…"))

        def done(answer, meta=""):
            row, self._pending = self._pending, None
            if row:
                row.set_text(answer, meta)
        self.backend.ask(text, mode, engine, done, attachments=files)
        self.tray.clear()
