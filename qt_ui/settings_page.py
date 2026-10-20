"""Страница «Настройки» нового интерфейса.

Конструктор: SettingsPage(backend). Методы бэкенда — см. qt_ui/settings_backend.py (SettingsBackend / StubSettings).
Слева список разделов, справа прокручиваемая форма, внизу панель «Сохранить настройки» (всегда видна).
Сигналы: saved(dict) — после успешного сохранения (значения формы); language_changed(code) — язык интерфейса
изменился и сохранён: окно нужно перестроить, чтобы подписи перевелись (решает MainWindow).
Тема применяется сразу при выборе (theme.set), сохраняется кнопкой «Сохранить».
"""
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QDialog, QFileDialog, QGridLayout, QHBoxLayout,
                               QLabel, QPlainTextEdit, QPushButton, QStackedWidget, QVBoxLayout, QWidget)

from .pages_more import PAD, Switch, _Field, _Page, _qss
from .settings_backend import LANGS, VOICE_LANGS, keys_from_clipboard_text, split_keys
from .theme_manager import theme
from .tokens import _rgba
from .widgets import Card, IconButton, NavButton, Segmented, T, Tile, label, repolish

TABS = [("ai", "sparkles", "ИИ и ключи"), ("local", "layers", "Локальные модели"), ("look", "sun", "Внешний вид"),
        ("ctrl", "sliders", "Управление"), ("voice", "mic", "Голос"), ("tg", "send", "Telegram"),
        ("sys", "bolt", "Запуск"), ("priv", "shield", "Приватность")]


def _extra_qss() -> str:
    c = {k: _rgba(v) for k, v in theme.c.items()}
    return f"""
QLabel#stErr {{ color: {c['err']}; font-weight: 600; font-size: 13px; }}
QLabel#cap {{ color: {c['mut']}; font-size: 13px; }}
QPushButton#eye {{ color: {c['mut']}; padding: 4px 8px; border-radius: 8px; font-size: 12px; }}
QPushButton#eye:hover {{ background: {c['hover']}; color: {c['text']}; }}
QFrame#savebar {{ background: {c['side']}; border: none; border-top: 1px solid {c['line']}; }}
"""


class _ClickLabel(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()


class SecretField(_Field):
    """Поле для секретов: символы скрыты (echo password), справа кнопка «Показать»."""

    def __init__(self, placeholder="", parent=None):
        super().__init__(placeholder, parent)
        self.edit.setEchoMode(self.edit.EchoMode.Password)
        self.btn = QPushButton(T("Показать"))
        self.btn.setObjectName("eye")
        self.btn.setCursor(Qt.PointingHandCursor)
        self.btn.setFocusPolicy(Qt.NoFocus)
        self.btn.clicked.connect(lambda: self.set_visible(self.edit.echoMode() == self.edit.EchoMode.Password))
        self.layout().addWidget(self.btn)

    def set_visible(self, on: bool):
        self.edit.setEchoMode(self.edit.EchoMode.Normal if on else self.edit.EchoMode.Password)
        self.btn.setText(T("Скрыть") if on else T("Показать"))


class KeysEditor(QWidget):
    """Ключи Gemini: по строке на ключ (скрыты точками), общая кнопка «Показать ключи»; можно вставить сразу несколько."""
    changed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._visible = False
        self._rows = []
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        self.box = QVBoxLayout()
        self.box.setSpacing(6)
        lay.addLayout(self.box)

    def add_row(self, text="", focus=False):
        row = QWidget()
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 0, 0, 0)
        r.setSpacing(6)
        f = SecretField("Вставь ключ (AIza… или AQ.…)")
        f.setFixedHeight(40)
        f.edit.setText(text)
        f.set_visible(self._visible)
        f.btn.setVisible(False)                   # показ — общей кнопкой, у строки её нет
        f.changed.connect(lambda *_: self.changed.emit())
        d = IconButton("trash", "err", size=16)
        d.setToolTip(T("Удалить ключ"))
        d.clicked.connect(lambda _=False, w=row: self._drop(w))
        r.addWidget(f, 1)
        r.addWidget(d)
        self.box.addWidget(row)
        self._rows.append((row, f))
        if focus:
            f.edit.setFocus()
        return f

    def _drop(self, row):
        self._rows = [(w, f) for w, f in self._rows if w is not row]
        row.setParent(None)
        row.deleteLater()
        self.changed.emit()

    def set_keys(self, keys):
        for w, _ in self._rows:
            w.setParent(None)
            w.deleteLater()
        self._rows = []
        for k in keys:
            self.add_row(k)
        if not keys:
            self.add_row()

    def keys(self) -> list:
        return split_keys(" ".join(f.text() for _, f in self._rows))

    def add_keys(self, new) -> int:
        have = self.keys()
        added = [k for k in new if k not in have]
        if self._rows and not self._rows[-1][1].text().strip():       # пустую строку-заготовку заменяем
            self._drop(self._rows[-1][0])
        for k in added:
            self.add_row(k)
        self.changed.emit()
        return len(added)

    def set_visible(self, on):
        self._visible = on
        for _, f in self._rows:
            f.set_visible(on)


class _ConfirmAccessDialog(QDialog):
    """Подтверждение выдачи доступа Telegram-пользователю (как gui.ask_yes_no в Tk-версии)."""

    def __init__(self, who, parent=None):
        super().__init__(parent)
        self.setObjectName("dlg")
        self.setWindowTitle(T("Выдать доступ из Telegram?"))
        self.setModal(True)
        self.setMinimumWidth(460)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(Tile("shield", 38, 11))
        head.addWidget(label("Выдать доступ из Telegram?", "h3"), 1)
        lay.addLayout(head)
        lay.addWidget(label(f"{T('Пользователь Telegram')} «{who[1]}» (id {who[0]}) " + T(
            "сможет присылать команды этому компьютеру (каждую — с подтверждением) и запрашивать скриншот экрана.")
            + "\n\n" + T("Выдавай доступ только тому, кого знаешь."), wrap=True, translate=False))
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton(T("Нет"))
        no.setObjectName("ghost")
        no.clicked.connect(self.reject)
        yes = QPushButton(T("Да, выдать"))
        yes.setObjectName("primary")
        yes.setDefault(True)
        yes.clicked.connect(self.accept)
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)


class SettingsPage(_Page):
    saved = Signal(dict)
    language_changed = Signal(str)

    def __init__(self, backend, parent=None):
        super().__init__(backend, "Настройки",
                         "Ключи Gemini, внешний вид, управление, голос и Telegram. Изменения применяются кнопкой «Сохранить».",
                         parent)
        self.f, self.sw, self.seg = {}, {}, {}
        self._rowcount = {}
        self._loading = False
        self._dirty = False
        self._loaded = False
        self._lm_scanned = False
        self._hw = None
        self._installed = set()
        self._cfg = {}
        self._start_lang = "ru"

        body = QHBoxLayout()
        body.setSpacing(18)
        nav = QVBoxLayout()
        nav.setSpacing(2)
        nav_w = QWidget()
        nav_w.setFixedWidth(220)
        nav_w.setLayout(nav)
        self.stack = QStackedWidget()
        self.tabs, self.nav = {}, {}
        for key, icon, title in TABS:
            b = NavButton(icon, title)
            b.setMinimumHeight(38)
            b.clicked.connect(lambda _=False, k=key: self.show_tab(k))
            nav.addWidget(b)
            self.nav[key] = b
            sc, lay = self._scroll()
            sc.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.tabs[key] = (self.stack.addWidget(sc), lay)
        nav.addStretch(1)
        body.addWidget(nav_w)
        body.addWidget(self.stack, 1)
        self.lay.addLayout(body, 1)

        self._build_ai(self.tabs["ai"][1])
        self._build_local(self.tabs["local"][1])
        self._build_look(self.tabs["look"][1])
        self._build_ctrl(self.tabs["ctrl"][1])
        self._build_voice(self.tabs["voice"][1])
        self._build_tg(self.tabs["tg"][1])
        self._build_sys(self.tabs["sys"][1])
        self._build_priv(self.tabs["priv"][1])
        for _, lay in self.tabs.values():
            lay.addStretch(1)

        # панель сохранения (всегда видна)
        self.lay.addSpacing(8)
        bar = QWidget()
        br = QHBoxLayout(bar)
        br.setContentsMargins(0, 6, 0, 0)
        br.setSpacing(14)
        self.btn_save = QPushButton(T("Сохранить настройки"))
        self.btn_save.setObjectName("primary")
        self.btn_save.setCursor(Qt.PointingHandCursor)
        self.btn_save.clicked.connect(self.save)
        br.addWidget(self.btn_save)
        self.status = label("", "ok", wrap=True, translate=False)
        br.addWidget(self.status, 1)
        self.lay.addWidget(bar)

        theme.changed.connect(self._theme_changed)
        self.show_tab("ai")

    # ---------------------------------------------------------------- вспомогательное
    def _restyle(self):
        self.setStyleSheet(_qss() + _extra_qss())

    def _touch(self, *_):
        if not self._loading:
            self._dirty = True

    def show_tab(self, key):
        self.nav[key].setChecked(True)
        self.stack.setCurrentIndex(self.tabs[key][0])
        self._tab = key
        if key == "local" and not self._lm_scanned:
            self._lm_scanned = True
            self._lm_scan()
        if key == "voice" and not self._mics_loaded:
            self._load_mics()

    def _section(self, lay, title, note=None):
        lay.addSpacing(8)
        if title:
            lay.addWidget(label(title, "h3"))
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(20, 16, 20, 16)
        cl.setSpacing(6)
        if note:
            cl.addWidget(label(note, "small", wrap=True))
        lay.addWidget(card)
        return cl

    def _sep_before(self, cl):
        n = self._rowcount.get(id(cl), 0)
        self._rowcount[id(cl)] = n + 1
        if n:
            cl.addWidget(self._sep())

    def _switch(self, cl, text, key, note=None):
        self._sep_before(cl)
        row = QWidget()
        r = QHBoxLayout(row)
        r.setContentsMargins(0, 8, 0, 8)
        r.setSpacing(14)
        col = QVBoxLayout()
        col.setSpacing(2)
        lb = _ClickLabel(T(text))
        lb.setWordWrap(True)
        lb.setCursor(Qt.PointingHandCursor)
        col.addWidget(lb)
        if note:
            col.addWidget(label(note, "small", wrap=True))
        sw = Switch()
        lb.clicked.connect(lambda: (sw.setChecked(not sw.isChecked()), sw.toggled.emit(sw.isChecked())))
        sw.toggled.connect(self._touch)
        r.addLayout(col, 1)
        r.addWidget(sw, 0, Qt.AlignVCenter)
        cl.addWidget(row)
        self.sw[key] = sw
        return sw

    def _field(self, cl, caption, key, width=None, placeholder="", numeric=None, secret=False, note=None):
        cl.addSpacing(4)
        cl.addWidget(label(caption, "h3", wrap=True))
        if note:
            cl.addWidget(label(note, "small", wrap=True))
        f = SecretField(placeholder) if secret else _Field(placeholder)
        f.setFixedHeight(40)
        if width:
            f.setFixedWidth(width)
        if numeric:
            f.edit.setValidator(QIntValidator(numeric[0], numeric[1]))
        f.changed.connect(self._touch)
        cl.addWidget(f, 0, Qt.AlignLeft)
        self.f[key] = f
        return f

    def _segmented(self, cl, caption, key, options, note=None):
        cl.addSpacing(4)
        cl.addWidget(label(caption, "h3", wrap=True))
        if note:
            cl.addWidget(label(note, "small", wrap=True))
        sg = Segmented(options, options[0][0])
        sg.changed.connect(self._touch)
        cl.addWidget(sg, 0, Qt.AlignLeft)
        self.seg[key] = sg
        return sg

    @staticmethod
    def _soft(text, fn, primary=False, icon=None):
        b = IconButton(icon, "white" if primary else "mut", size=16, text=text,
                       object_name="primary" if primary else "soft") if icon else QPushButton(T(text))
        if not icon:
            b.setObjectName("primary" if primary else "soft")
        b.setCursor(Qt.PointingHandCursor)
        b.clicked.connect(lambda _=False: fn())
        return b

    @staticmethod
    def _msg(lb, text, kind="small"):
        """Подпись-результат: kind small/stOk/stWarn/stErr."""
        lb.setObjectName(kind)
        repolish(lb)
        lb.setText(text)
        lb.setVisible(bool(text))

    def _result(self, cl):
        lb = label("", "small", wrap=True, translate=False)
        lb.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lb.setVisible(False)
        cl.addWidget(lb)
        return lb

    @staticmethod
    def _flow_row(*widgets):
        r = QHBoxLayout()
        r.setSpacing(8)
        for w in widgets:
            r.addWidget(w)
        r.addStretch(1)
        return r

    # ---------------------------------------------------------------- ИИ и ключи
    def _build_ai(self, lay):
        self._secure_note = label("", "small", wrap=True, translate=False)
        cl = self._section(lay, "Gemini (ИИ)")
        cl.addWidget(self._secure_note)
        cl.addSpacing(4)
        head = QHBoxLayout()
        head.addWidget(label("Ключи Gemini — по одному в строке (можно с разных аккаунтов, при лимите ключи меняются сами)",
                             "h3", wrap=True), 1)
        self.btn_eye = QPushButton(T("Показать ключи"))
        self.btn_eye.setObjectName("eye")
        self.btn_eye.setCursor(Qt.PointingHandCursor)
        self.btn_eye.clicked.connect(self._toggle_keys)
        head.addWidget(self.btn_eye, 0, Qt.AlignTop)
        cl.addLayout(head)
        self.keys = KeysEditor()
        self.keys.changed.connect(self._touch)
        cl.addWidget(self.keys)
        cl.addSpacing(4)
        cl.addLayout(self._flow_row(
            self._soft("Вставить ключ из буфера", self._paste_key, primary=True, icon="plus"),
            self._soft("Добавить ключ", lambda: self.keys.add_row(focus=True))))
        cl.addLayout(self._flow_row(self._soft("Проверить все ключи", self._test_keys),
                                    self._soft("Статус ротации", self._show_rotation)))
        self.keys_result = QPlainTextEdit()
        self.keys_result.setObjectName("mono")
        self.keys_result.setReadOnly(True)
        self.keys_result.setFixedHeight(96)
        self.keys_result.setVisible(False)
        cl.addWidget(self.keys_result)

        cl = self._section(lay, "Модель")
        self._field(cl, "Модель (для навигации по экрану нужна модель посильнее lite; имя можно поменять, "
                        "если Google переименует)", "model", 300)
        cl.addLayout(self._flow_row(self._soft("Найти доступные модели", self._find_models, icon="search")))
        self.models_grid = QGridLayout()
        self.models_grid.setSpacing(6)
        cl.addLayout(self.models_grid)
        self.models_msg = self._result(cl)
        self._field(cl, "Запросов в минуту на ключ — свой «светофор», чтобы не упереться в лимит Google "
                        "(бесплатный тариф: обычно 5–10 на проект; 0 = без ограничения)", "requests_per_minute",
                    90, numeric=(0, 120))
        self._build_openrouter(lay)

    def _build_openrouter(self, lay):
        cl = self._section(lay, "Запасной ИИ (OpenRouter)",
                           "Если Gemini недоступен (лимиты на всех ключах, сбой), запрос уйдёт на OpenRouter. "
                           "Нужен ключ с openrouter.ai; бесплатные модели есть, но с ограничениями. Скриншоты при этом "
                           "тоже уходят туда — включай, только если тебя это устраивает.")
        self._switch(cl, "Использовать OpenRouter, когда Gemini не отвечает", "openrouter_enabled")
        self.or_caption = None
        self._field(cl, "Ключ OpenRouter", "openrouter_key", 360, "sk-or-…", secret=True)
        self._field(cl, "Модель (пусто — подберу первую бесплатную сама)", "openrouter_model", 360, "например, имя из списка ниже")
        cl.addLayout(self._flow_row(self._soft("Найти бесплатные модели", self._find_or_models, icon="search")))
        self.or_grid = QGridLayout()
        self.or_grid.setSpacing(6)
        cl.addLayout(self.or_grid)
        self.or_msg = self._result(cl)

    def _find_or_models(self):
        self._msg(self.or_msg, T("Спрашиваю у OpenRouter список моделей…"))
        self.backend.settings_or_models(self.f["openrouter_key"].text().strip(), self._show_or_models)

    def _show_or_models(self, ok, result):
        while self.or_grid.count():
            it = self.or_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not ok:
            return self._msg(self.or_msg, str(result), "stErr")
        free = [m for m in result if m[1]][:12]
        if not free:
            return self._msg(self.or_msg, T("Бесплатных моделей не нашлось."))
        self._msg(self.or_msg, T("Нажми на модель, чтобы подставить её (👁 — видит картинки), потом «Сохранить настройки»:"))
        for n, (mid, _free, vision) in enumerate(free):
            b = QPushButton(("👁 " if vision else "") + mid)
            b.setObjectName("soft")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, m=mid: self.f["openrouter_model"].edit.setText(m))
            self.or_grid.addWidget(b, n // 2, n % 2)

    def _toggle_keys(self):
        on = not self.keys._visible
        self.keys.set_visible(on)
        self.btn_eye.setText(T("Скрыть ключи") if on else T("Показать ключи"))

    def _set_keys_result(self, text):
        self.keys_result.setVisible(bool(text))
        self.keys_result.setPlainText(text)

    def _paste_key(self):
        clip = QApplication.clipboard().text() or ""
        if not clip.strip():
            return self._set_keys_result(T("Буфер обмена пуст — сначала скопируй ключ на странице Google AI Studio."))
        new = keys_from_clipboard_text(clip)
        if not new:
            return self._set_keys_result(T("В буфере не похоже на ключ. Скопируй ключ целиком на странице Google AI Studio."))
        n = self.keys.add_keys(new)
        self._set_keys_result(f"{T('Добавлено ключей')}: {n}. {T('Всего')}: {len(self.keys.keys())}. "
                              + T("Нажми «Сохранить» внизу."))

    def _test_keys(self):
        keys = self.keys.keys()
        if not keys:
            return self._set_keys_result(T("Сначала вставь хотя бы один ключ выше."))
        self._set_keys_result(T("Проверяю ключи…"))
        self.backend.settings_keys_test(keys, lambda res: self._set_keys_result(
            "\n".join(f"{'✅' if ok else '❌'} {tail}: {T(msg)}" for ok, tail, msg in res)))

    def _show_rotation(self):
        rows = self.backend.settings_keys_rotation()
        self._set_keys_result("\n".join(f"{m}: {T(s)}" for m, s in rows) or T("Ключей пока нет."))

    def _find_models(self):
        keys = self.keys.keys()
        if not keys:
            return self._msg(self.models_msg, T("Сначала вставь хотя бы один ключ выше."))
        self._msg(self.models_msg, T("Спрашиваю у Google список моделей…"))
        self.backend.settings_find_models(keys[0], self._show_models)

    def _show_models(self, ok, result):
        while self.models_grid.count():
            it = self.models_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if not ok:
            return self._msg(self.models_msg, str(result), "stErr")
        self._msg(self.models_msg, T("Нажми на модель, чтобы подставить её (потом «Сохранить настройки»):") if result
                  else T("Подходящих моделей для этого ключа не нашлось."))
        for i, name in enumerate(result):
            b = QPushButton(name)
            b.setObjectName("soft")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, n=name: self.f["model"].edit.setText(n))
            self.models_grid.addWidget(b, i // 3, i % 3)

    # ---------------------------------------------------------------- локальные модели
    def _build_local(self, lay):
        cl = self._section(lay, "Ваш компьютер", "Программа читает характеристики локально, в интернет они не "
                           "отправляются. Видеопамять надёжно определяется у видеокарт NVIDIA.")
        self.hw_grid = QGridLayout()
        self.hw_grid.setHorizontalSpacing(16)
        self.hw_grid.setVerticalSpacing(4)
        cl.addLayout(self.hw_grid)
        self._hw_fill(None)
        cl.addLayout(self._flow_row(self._soft("Определить снова", self._lm_scan, icon="history")))

        cl = self._section(lay, "Ollama (локальная модель) — необязательно",
                           "Ollama — отдельная бесплатная программа (ollama.com/download). Модель работает на вашем ПК, "
                           "без интернета и без расхода лимитов Gemini. Используется для простых команд и в чате «Ассистент».")
        self.ollama_lb = label("Проверяю…", "stMut", wrap=True)
        cl.addWidget(self.ollama_lb)
        self._switch(cl, "Использовать для простых команд", "local_model_enabled")
        self._field(cl, "Выбранная модель (имя из списка ниже или своё; после выбора нажми «Сохранить»)",
                    "local_model_name", 260)

        cl = self._section(lay, "Подходящие модели", "Размер каждой модели проверяется в реестре Ollama, затем "
                           "сравнивается с вашей памятью. Это совет по размеру, а не замер скорости; качество "
                           "по-русски стоит проверить самому.")
        cl.addLayout(self._flow_row(self._soft("Подобрать модели", lambda: self._lm_find(False), primary=True, icon="search"),
                                    self._soft("Спросить у Gemini", lambda: self._lm_find(True), icon="sparkles")))
        self.lm_msg = self._result(cl)
        self.lm_list = QVBoxLayout()
        self.lm_list.setSpacing(0)
        cl.addLayout(self.lm_list)

    def _hw_fill(self, hw):
        while self.hw_grid.count():
            it = self.hw_grid.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        if hw is None:
            self.hw_grid.addWidget(label("Определяю…", "muted"), 0, 0)
            return
        gpus = "; ".join(g["name"] + (f" — {g['vram_gb']} {T('ГБ видеопамяти')}" if g.get("vram_gb")
                                      else " — " + T("видеопамять неизвестна")) for g in hw.get("gpus", [])) \
            or T("видеокарта не определена")
        gb = T("ГБ")
        rows = [("Оперативная память", f"{hw.get('ram_gb') or '?'} {gb}"),
                ("Свободно на диске", f"{hw.get('disk_free_gb') or '?'} {gb}"),
                ("Процессор", f"{hw.get('cpu') or '?'} ({hw.get('cores')} {T('потоков')})"),
                ("Видеокарта", gpus)]
        for i, (k, v) in enumerate(rows):
            self.hw_grid.addWidget(label(k, "cap"), i, 0, Qt.AlignTop)
            self.hw_grid.addWidget(label(v, "cmdOn", wrap=True, translate=False), i, 1)
        self.hw_grid.setColumnStretch(1, 1)

    def _lm_scan(self):
        self._hw_fill(None)
        self.backend.settings_hw_scan(self._lm_scanned_done)

    def _lm_scanned_done(self, hw, running, installed):
        self._hw = hw or None
        self._installed = {m["name"] for m in installed}
        if hw:
            self._hw_fill(hw)
        if running:
            names = ", ".join(f"{m['name']} ({m['size_gb']} {T('ГБ')})" for m in installed) or T("моделей пока нет")
            self._msg(self.ollama_lb, f"● {T('Ollama запущена. Скачано:')} {names}", "stOk")
        else:
            self._msg(self.ollama_lb, "● " + T("Ollama не найдена или не запущена. Установи с ollama.com/download "
                                               "и запусти — программа подхватит сама."), "stWarn")

    def _lm_find(self, use_gemini):
        self._msg(self.lm_msg, T("Ищу и проверяю модели в реестре Ollama…"))
        self.backend.settings_lm_find(use_gemini, self._hw, self._lm_rows)

    def _lm_rows(self, rows, offline, extra):
        if offline:
            self._msg(self.lm_msg, T("Реестр Ollama недоступен (нет интернета?) — показываю встроенный список без "
                                     "размеров.") + extra, "stWarn")
        else:
            self._msg(self.lm_msg, T("Готово. Сверху — то, что целиком влезет в видеопамять.") + extra)
        self._clear(self.lm_list)
        verdict = {"gpu": ("целиком в видеопамяти — быстро", "stOk"), "cpu": ("только в оперативной памяти — медленно", "stWarn"),
                   "no": ("не потянет этот ПК", "stErr"), "?": ("размер неизвестен", "small")}
        for n, r in enumerate(rows):
            if n:
                self.lm_list.addWidget(self._sep())
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 9, 0, 9)
            h.setSpacing(8)
            col = QVBoxLayout()
            col.setSpacing(1)
            have = "  ✓ " + T("скачана") if r["name"] in self._installed else ""
            size = f" · {r['size_gb']} {T('ГБ')}" if r["size_gb"] else ""
            col.addWidget(label(r["name"] + size + have, "cmdOn", translate=False))
            text, kind = verdict.get(r["fit"], verdict["?"])
            col.addWidget(label(T(text) + (f" · {T(r['note'])}" if r.get("note") else ""), kind, wrap=True, translate=False))
            h.addLayout(col, 1)
            h.addWidget(self._soft("Выбрать", lambda nm=r["name"]: self._lm_choose(nm)))
            h.addWidget(self._soft("Копировать команду", lambda nm=r["name"]: self._lm_copy(nm), icon="copy"))
            self.lm_list.addWidget(row)

    def _lm_choose(self, name):
        self.f["local_model_name"].edit.setText(name)
        self._msg(self.lm_msg, f"{T('Выбрано')}: {name}. {T('Нажми «Сохранить» внизу.')}")

    def _lm_copy(self, name):
        QApplication.clipboard().setText(f"ollama pull {name}")
        self._msg(self.lm_msg, f"{T('Скопировано')}: ollama pull {name} — {T('вставь в командную строку.')}")

    # ---------------------------------------------------------------- внешний вид
    def _build_look(self, lay):
        cl = self._section(lay, "Внешний вид")
        sg = self._segmented(cl, "Тема оформления", "ui_theme", [("light", "Светлая"), ("dark", "Тёмная")],
                             "Применяется сразу; запоминается кнопкой «Сохранить настройки».")
        sg.changed.connect(lambda name: theme.set(name, QApplication.instance()))
        self._segmented(cl, "Language / Мова / Язык интерфейса (применится после «Сохранить настройки»)",
                        "ui_language", list(LANGS.items()))
        # названия языков не переводим
        for k, b in self.seg["ui_language"]._btns.items():
            b.setText(LANGS[k])

    def _theme_changed(self, name):
        sg = self.seg.get("ui_theme")
        if sg is not None and sg.value() != name:
            sg.set_value(name)

    # ---------------------------------------------------------------- управление
    def _build_ctrl(self, lay):
        cl = self._section(lay, "Управление")
        self._switch(cl, "Режим «только смотреть»: ИИ отвечает про экран, но ничего не нажимает и не печатает", "view_only")
        self._switch(cl, "При лимите основной модели пробовать запасные (у каждой модели свой бесплатный лимит)",
                     "use_fallback_models")
        self._field(cl, "Запасные модели (через запятую; доступные — кнопка «Найти доступные модели» на вкладке «ИИ и ключи»)",
                    "fallback_models", 440)
        self._field(cl, "Секунд до автозапуска простых команд (0 — всегда ждать клика)", "auto_confirm_seconds", 90,
                    numeric=(0, 30))
        cl = self._section(lay, "Горячие клавиши")
        self._field(cl, "Быстрая команда (работает из любого места)", "hotkey_open_window", 260)
        self._field(cl, "Голосовая команда", "hotkey_voice", 260)
        self._field(cl, "Аварийная остановка автопилота из любого места (ещё: рывок мыши в левый верхний угол)",
                    "hotkey_stop", 260)
        cl = self._section(lay, "Папки, с которыми можно работать")
        cl.addWidget(label("По одной на строку. Файловые операции ассистента разрешены только здесь.", "small", wrap=True))
        self.folders = QPlainTextEdit()
        self.folders.setObjectName("mono")
        self.folders.setFixedHeight(96)
        self.folders.textChanged.connect(self._touch)
        cl.addWidget(self.folders)
        cl.addLayout(self._flow_row(self._soft("Добавить папку…", self._add_folder, icon="plus")))

    def _add_folder(self):
        p = QFileDialog.getExistingDirectory(self, T("Папка, с которой можно работать"))
        if p:
            cur = self.folders.toPlainText().rstrip("\n")
            self.folders.setPlainText((cur + "\n" if cur else "") + p)

    # ---------------------------------------------------------------- голос
    _mics_loaded = False

    def _build_voice(self, lay):
        cl = self._section(lay, "Голос")
        self._switch(cl, "Озвучивать действия голосом (офлайн)", "voice_feedback_enabled")
        self._switch(cl, "Слушать «Эй, ассистент» без кнопки (микрофон работает постоянно)", "voice_wake_enabled",
                     "Звук нигде не хранится. Каждая услышанная фраза (до 5 секунд) отправляется на бесплатное "
                     "распознавание Google, чтобы найти слово «ассистент»; без него фраза выбрасывается. Включай, "
                     "только если тебя это устраивает. Сказанная команда всё равно спросит подтверждение.")
        self._segmented(cl, "Язык голосовых команд", "voice_language", VOICE_LANGS)
        cl.addSpacing(4)
        cl.addWidget(label("Микрофон", "h3"))
        self.mic = QComboBox()
        self.mic.setObjectName("combo")
        self.mic.setMinimumWidth(360)
        self.mic.addItem(T("Системный по умолчанию"), "")
        self.mic.currentIndexChanged.connect(self._touch)
        cl.addWidget(self.mic, 0, Qt.AlignLeft)
        cl.addSpacing(4)
        cl.addLayout(self._flow_row(self._soft("Проверить микрофон", self._test_mic, icon="mic"),
                                    self._soft("Обновить список", self._load_mics, icon="history")))
        cl.addLayout(self._flow_row(self._soft("Установить / починить голосовой ввод", self._fix_voice, icon="bolt")))
        self.mic_msg = self._result(cl)

    def _load_mics(self):
        self._mics_loaded = True
        self.backend.settings_mic_devices(self._fill_mics)

    def _fill_mics(self, names):
        want = self._cfg.get("voice_device", "") if not self._dirty else (self.mic.currentData() or "")
        self._loading = True
        self.mic.clear()
        self.mic.addItem(T("Системный по умолчанию"), "")
        for n in names:
            self.mic.addItem(n[:70], n)
        if want and want not in names:
            self.mic.addItem(want[:70], want)
        i = self.mic.findData(want)
        self.mic.setCurrentIndex(max(0, i))
        self._loading = False

    def _test_mic(self):
        self._msg(self.mic_msg, T("Проверяю… скажи что-нибудь вслух в течение двух секунд"))
        self.backend.settings_mic_test(lambda ok, m: self._msg(self.mic_msg, ("✅ " if ok else "❌ ") + T(m),
                                                                "stOk" if ok else "stErr"))

    def _fix_voice(self):
        self._msg(self.mic_msg, T("Устанавливаю компоненты… (около минуты, нужен интернет)"))

        def done(ok, m):
            self._msg(self.mic_msg, ("✅ " if ok else "❌ ") + T(m), "stOk" if ok else "stErr")
            self._load_mics()
        self.backend.settings_voice_fix(done)

    # ---------------------------------------------------------------- Telegram
    def _build_tg(self, lay):
        cl = self._section(lay, "Управление из Telegram",
                           "Необязательно. Ты пишешь своему боту — ассистент выполняет команду на этом компьютере. "
                           "Команды из Telegram никогда не запускаются сами: нужно нажать «Разрешить» — в чате или на компьютере.")
        cl.addSpacing(6)
        cl.addWidget(label("Как создать бота (1 минута):\n"
                           "1. В Telegram открой @BotFather и отправь /newbot\n"
                           "2. Придумай имя и логин бота (логин оканчивается на bot)\n"
                           "3. BotFather пришлёт токен — вставь его ниже и нажми «Сохранить настройки»\n"
                           "4. Напиши своему боту /start — он выдаст код из 16 символов\n"
                           "5. Введи этот код ниже, в «Заявки на доступ»", wrap=True))
        self._switch(cl, "Включить управление из Telegram", "telegram_enabled")
        self._switch(cl, "Все вопросы и подтверждения — кнопками в Telegram (опасные действия спрашиваю дважды)",
                     "telegram_approve_in_chat")
        self._switch(cl, "Безопасные быстрые команды (открыть сайт/программу, скрин) выполнять сразу, без вопроса",
                     "telegram_trusted_auto")
        self._switch(cl, "После задачи присылать скриншот экрана", "telegram_screen_after")
        self._switch(cl, "При запуске программы писать в Telegram «ассистент на связи»", "telegram_announce")
        self.tg_token_caption = label("Токен бота", "h3", wrap=True)
        cl.addSpacing(4)
        cl.addWidget(self.tg_token_caption)
        self.tg_token = SecretField("Токен от @BotFather")
        self.tg_token.setFixedHeight(40)
        self.tg_token.setMaximumWidth(460)
        self.tg_token.changed.connect(self._touch)
        cl.addWidget(self.tg_token)
        self.tg_clear = QCheckBox(T("удалить сохранённый токен"))
        self.tg_clear.toggled.connect(self._touch)
        cl.addWidget(self.tg_clear)
        cl.addLayout(self._flow_row(self._soft("Проверить токен", self._tg_check, icon="check")))
        self.tg_status = label("", "small", wrap=True, translate=False)
        cl.addWidget(self.tg_status)

        cl = self._section(lay, "Заявки на доступ",
                           "Человек пишет боту /start и получает код из 16 символов. Введи этот код здесь — только так он "
                           "получит доступ. Вводи код только от человека, которого знаешь.")
        self.tg_requests = label("", "muted", wrap=True, translate=False)
        cl.addWidget(self.tg_requests)
        row = QHBoxLayout()
        row.setSpacing(8)
        self.tg_code = _Field("ABCD-EFGH-JKLM-NPQR")
        self.tg_code.setFixedSize(280, 40)
        self.tg_code.submitted.connect(self._tg_confirm)
        row.addWidget(self.tg_code)
        row.addWidget(self._soft("Подтвердить код", self._tg_confirm, primary=True))
        row.addWidget(self._soft("Обновить", self.refresh_tg, icon="history"))
        row.addStretch(1)
        cl.addLayout(row)
        self.tg_msg = self._result(cl)

        cl = self._section(lay, "Подтверждённые пользователи")
        self.tg_users = QVBoxLayout()
        self.tg_users.setSpacing(0)
        cl.addLayout(self.tg_users)

    def _tg_check(self):
        self._msg(self.tg_status, T("Проверяю токен…"))
        self.backend.tg_check_token(self.tg_token.text().strip(), lambda ok, info: self._msg(
            self.tg_status, (T("✓ Токен верный, бот") + " " + info) if ok else "✗ " + T(info), "stOk" if ok else "stErr"))

    def refresh_tg(self):
        pend = self.backend.tg_pending() or []
        self.tg_requests.setText((T("Ждут подтверждения") + ": " + ", ".join(p["name"] for p in pend)) if pend
                                 else T("Заявок нет. Нажми «Обновить», когда человек напишет боту."))
        self._clear(self.tg_users)
        users = self.backend.tg_users() or []
        if not users:
            self.tg_users.addWidget(label("Пока никого.", "muted"))
        for n, u in enumerate(users):
            if n:
                self.tg_users.addWidget(self._sep())
            row = QWidget()
            h = QHBoxLayout(row)
            h.setContentsMargins(0, 8, 0, 8)
            h.addWidget(label(f"{u.get('name')}  (id {u.get('id')})", "cmdOn", translate=False), 1)
            b = QPushButton(T("Отозвать доступ"))
            b.setObjectName("dangerbtn")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, uid=u.get("id"): (self.backend.tg_revoke(uid), self.refresh_tg()))
            h.addWidget(b)
            self.tg_users.addWidget(row)
        self._msg(self.tg_status, T("Статус") + ": " + T(self.backend.tg_status()), "small") \
            if not self.tg_status.text() or self.tg_status.text().startswith(T("Статус")) else None

    def _tg_confirm(self):
        code = self.tg_code.text()
        who = self.backend.tg_peek(code)
        if who is not None and _ConfirmAccessDialog(who, self).exec() != QDialog.Accepted:
            return self._msg(self.tg_msg, T("Отменено — доступ не выдан."))
        ok, msg = self.backend.tg_confirm(code)
        self._msg(self.tg_msg, ("✓ " if ok else "✗ ") + T(msg), "stOk" if ok else "stErr")
        if ok:
            self.tg_code.clear()
        self.refresh_tg()

    # ---------------------------------------------------------------- запуск
    def _build_sys(self, lay):
        cl = self._section(lay, "Запуск")
        self._switch(cl, "Запускать вместе с Windows (сразу в трее, без окна)", "autostart")
        cl.addSpacing(6)
        cl.addLayout(self._flow_row(self._soft("Создать ярлыки (рабочий стол и меню Пуск)", self._make_shortcuts,
                                               icon="layers")))
        self._build_updates(lay)

    def _build_updates(self, lay):
        cl = self._section(lay, "Обновления",
                           "Программа проверяет GitHub Releases. Обновление скачивается и ставится только после твоего нажатия.")
        self.upd_msg = self._result(cl)
        self.upd_progress = label("", "small", translate=False)
        cl.addWidget(self.upd_progress)
        self.upd_btn_install = self._soft("Скачать и установить", self._install_update, primary=True, icon="arrow-up")
        self.upd_btn_install.setVisible(False)
        cl.addLayout(self._flow_row(self._soft("Проверить обновления", self._check_update, icon="search"), self.upd_btn_install))
        self._field(cl, "Токен GitHub (только если репозиторий приватный; доступ на чтение)", "github_token", 360, "github_pat_…",
                    secret=True)

    def _check_update(self):
        self._msg(self.upd_msg, T("Проверяю…"))
        self.backend.update_check(self._update_checked)

    def _update_checked(self, state, text, url):
        self._msg(self.upd_msg, T(text), "stOk" if state == "current" else "stWarn" if state == "new" else "small")
        self.upd_btn_install.setVisible(state == "new")

    def _install_update(self):
        from PySide6.QtWidgets import QMessageBox
        if QMessageBox.question(self, T("Обновление"), T("Скачать новую версию и запустить установщик? Программа закроется.")) != QMessageBox.Yes:
            return
        self.upd_btn_install.setEnabled(False)
        self.backend.update_install(lambda p: self.upd_progress.setText(f"{T('Скачиваю')}: {int(p * 100)}%"),
                                    lambda ok, m: (self._msg(self.upd_msg, T(m), "stOk" if ok else "stErr"),
                                                   self.upd_btn_install.setEnabled(True)))

    def _make_shortcuts(self):
        self._set_status(T("Создаю ярлыки…"), "small")
        self.backend.settings_make_shortcuts(lambda ok, m: self._set_status(m, "ok" if ok else "err"))

    # ---------------------------------------------------------------- конфиденциальность
    def _build_priv(self, lay):
        cl = self._section(lay, "Отправка данных в облако",
                           "Ассистент отправляет в облако Google (Gemini) только то, что нужно для ответа. "
                           "Всё, что связано с файлами и скриншотами, включается только с твоего согласия.")
        self._switch(cl, "Отправлять файлы и фото в Gemini без вопроса каждый раз", "cloud_files_consent",
                     "Выключено — перед каждой отправкой файлов или фото программа спросит. Пароли, токены и ключи "
                     "в тексте перед отправкой скрываются; на картинках скрыть секреты нельзя.")
        self._switch(cl, "Закрашивать поля паролей на скриншотах перед отправкой в облако", "mask_password_fields",
                     "Поля находятся через Windows UI Automation; если не вышло, скриншот уходит как есть.")
        cl = self._section(lay, None)
        cl.addWidget(label("Бесплатный Gemini API вне ЕС/ЕЭЗ может использовать отправленные скриншоты и файлы для "
                           "обучения моделей. Локальная модель (вкладка «Локальные модели») читает текстовые файлы "
                           "без отправки в интернет.", "small", wrap=True))

    # ---------------------------------------------------------------- загрузка / сохранение
    def refresh(self):
        """При показе: перечитать значения (если нет несохранённых правок) и списки Telegram."""
        if not self._loaded or not self._dirty:
            self.load()
        self.refresh_tg()

    def load(self):
        cur = self.backend.settings_load()
        self._cfg = cur
        self._loading = True
        self._loaded = True
        self._start_lang = cur.get("ui_language", "ru")
        self.keys.set_keys(list(cur.get("gemini_api_keys") or []))
        self._secure_note.setText(T("🔒 Ключи хранятся в защищённом хранилище Windows.") if cur.get("secure_store")
                                  else T("⚠️ Защищённое хранилище недоступно — ключи будут лежать в обычном файле settings.json."))
        self.f["model"].edit.setText(cur.get("model", "gemini-3.5-flash"))
        self.f["requests_per_minute"].edit.setText(str(cur.get("requests_per_minute", 6)))
        self.f["openrouter_model"].edit.setText(cur.get("openrouter_model", ""))
        self.f["openrouter_key"].clear()
        self.f["openrouter_key"].edit.setPlaceholderText(T("Сохранён — оставь пустым, чтобы не менять") if cur.get("openrouter_has_key") else "sk-or-…")
        self.f["fallback_models"].edit.setText(", ".join(cur.get("fallback_models") or []))
        self.f["auto_confirm_seconds"].edit.setText(str(cur.get("auto_confirm_seconds", 5)))
        for k, d in (("hotkey_open_window", "ctrl+shift+a"), ("hotkey_voice", "ctrl+shift+v"), ("hotkey_stop", "ctrl+shift+s")):
            self.f[k].edit.setText(cur.get(k, d))
        self.f["local_model_name"].edit.setText(cur.get("local_model_name", "qwen2.5:3b"))
        self.folders.setPlainText("\n".join(cur.get("allowed_folders") or []))
        for k, sw in self.sw.items():
            sw.setChecked(bool(cur.get(k, k in ("local_model_enabled", "telegram_approve_in_chat", "telegram_trusted_auto",
                                                "telegram_screen_after", "telegram_announce", "mask_password_fields"))))
        self.seg["ui_theme"].set_value(cur.get("ui_theme", "dark") if cur.get("ui_theme") in ("dark", "light") else theme.name)
        self.seg["ui_language"].set_value(cur.get("ui_language", "ru"))
        self.seg["voice_language"].set_value(cur.get("voice_language", "ru-RU"))
        self.tg_token.clear()
        self.tg_clear.setChecked(False)
        has = bool(cur.get("telegram_has_token"))
        self.tg_clear.setVisible(has)
        self.tg_token.edit.setPlaceholderText(T("Сохранён — оставь пустым, чтобы не менять") if has else T("Токен от @BotFather"))
        self.tg_token_caption.setText(T("Токен бота") + (" (" + T("сохранён") + ")" if has else ""))
        self._msg(self.tg_status, T("Статус") + ": " + T(cur.get("telegram_status", "")), "small")
        if self._mics_loaded:
            i = self.mic.findData(cur.get("voice_device", ""))
            if i >= 0:
                self.mic.setCurrentIndex(i)
        self._loading = False
        self._dirty = False

    def collect(self) -> dict:
        f = {k: w.text().strip() for k, w in self.f.items()}
        v = {
            "gemini_api_keys": self.keys.keys(),
            "model": f["model"], "requests_per_minute": f["requests_per_minute"],
            "openrouter_key": f["openrouter_key"], "openrouter_model": f["openrouter_model"],
            "fallback_models": f["fallback_models"], "auto_confirm_seconds": f["auto_confirm_seconds"],
            "hotkey_open_window": f["hotkey_open_window"], "hotkey_voice": f["hotkey_voice"],
            "hotkey_stop": f["hotkey_stop"], "local_model_name": f["local_model_name"],
            "allowed_folders": [p.strip() for p in self.folders.toPlainText().splitlines() if p.strip()],
            "voice_device": self.mic.currentData() or "",
            "telegram_token": self.tg_token.text().strip(), "telegram_clear_token": self.tg_clear.isChecked(),
            "ui_theme": self.seg["ui_theme"].value(), "ui_language": self.seg["ui_language"].value(),
            "voice_language": self.seg["voice_language"].value()
        }
        for k, sw in self.sw.items():
            v[k] = sw.isChecked()
        return v

    def save(self):
        values = self.collect()
        tok = self.f["github_token"].text().strip()
        if tok:
            self.backend.update_set_token(tok)
            self.f["github_token"].clear()
        err = self.backend.settings_save(values)
        if err:
            return self._set_status(T(err), "err")
        lang_changed = values["ui_language"] != self._start_lang
        self.load()
        self._set_status(T("✓ Сохранено"), "ok")
        self.saved.emit(values)
        if lang_changed:
            self.language_changed.emit(values["ui_language"])

    def _set_status(self, text, kind="ok"):
        self.status.setObjectName(kind)
        repolish(self.status)
        self.status.setText(text)
