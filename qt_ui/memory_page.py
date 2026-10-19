"""Страница «Макросы, заметки и шаблоны» нового интерфейса. Конструктор: MemoryPage(backend).
Методы бэкенда — qt_ui/memory_backend.py. refresh() вызывается при показе страницы."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout, QPushButton, QVBoxLayout, QWidget

from .pages_more import _Field, _Page
from .widgets import Card, IconButton, T, label


class _Row(QWidget):
    """Строка списка: заголовок + подпись слева, кнопки справа."""

    def __init__(self, title, sub="", translate_title=False):
        super().__init__()
        r = QHBoxLayout(self)
        r.setContentsMargins(16, 12, 12, 12)
        r.setSpacing(10)
        info = QVBoxLayout()
        info.setSpacing(3)
        info.addWidget(label(title, "cmdOn", wrap=True, translate=translate_title))
        if sub:
            info.addWidget(label(sub, "muted", wrap=True, translate=False))
        r.addLayout(info, 1)
        self.buttons = QHBoxLayout()
        self.buttons.setSpacing(4)
        r.addLayout(self.buttons)

    def add(self, btn):
        self.buttons.addWidget(btn, 0, Qt.AlignVCenter)


def _btn(icon, text, kind, cb):
    if kind == "primary":
        b = QPushButton(T(text))
        b.setObjectName("primary")
        b.setCursor(Qt.PointingHandCursor)
    else:
        b = IconButton(icon, "err" if kind == "danger" else "mut", text=text,
                       object_name="dangerbtn" if kind == "danger" else "ghost", size=16)
    b.clicked.connect(lambda _=False: cb())
    return b


class MemoryPage(_Page):
    def __init__(self, backend, parent=None):
        super().__init__(backend, "Макросы, заметки и шаблоны",
                         "Всё, что ассистент запомнил. Макрос — шаги последней команды («запомни как утро»); "
                         "заметка — факт о тебе; шаблон — набор быстрых команд.", parent)
        self.scroll, self.body = self._scroll()
        self.lay.addWidget(self.scroll, 1)
        self.box = QVBoxLayout()
        self.box.setSpacing(10)
        self.body.addLayout(self.box)
        self.body.addStretch(1)

    # --- общие куски ---
    def _title(self, text, hint=""):
        self.box.addSpacing(10)
        self.box.addWidget(label(text, "h3"))
        if hint:
            self.box.addWidget(label(hint, "small", wrap=True))

    def _list_card(self, rows):
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(6, 4, 6, 4)
        cl.setSpacing(0)
        for n, r in enumerate(rows):
            if n:
                cl.addWidget(self._sep())
            cl.addWidget(r)
        self.box.addWidget(card)

    def _form_card(self, fields, button_text, on_click, intro=""):
        """fields: [(поле, stretch)], возвращает метку ошибки. on_click(err_label)."""
        card = Card()
        f = QVBoxLayout(card)
        f.setContentsMargins(16, 14, 16, 12)
        f.setSpacing(8)
        if intro:
            f.addWidget(label(intro, "small", wrap=True))
        row = QHBoxLayout()
        row.setSpacing(8)
        for fld, st in fields:
            fld.setFixedHeight(40)
            row.addWidget(fld, st)
        b = QPushButton(T(button_text))
        b.setObjectName("primary")
        b.setCursor(Qt.PointingHandCursor)
        row.addWidget(b)
        f.addLayout(row)
        err = label("", "err", wrap=True, translate=False)
        err.setVisible(False)
        f.addWidget(err)
        b.clicked.connect(lambda _=False: on_click(err))
        for fld, _ in fields:
            fld.submitted.connect(lambda: on_click(err))
        self.box.addWidget(card)

    @staticmethod
    def _show(err, msg):
        err.setText(T(msg))
        err.setVisible(bool(msg))

    # --- страница ---
    def refresh(self):
        self._clear(self.box)
        be = self.backend
        self._build_macros(be)
        self._build_notes(be)
        self._build_templates(be)

    def _build_macros(self, be):
        self._title("Макросы", "Выполни команду, потом впиши название ниже (или скажи «запомни как <имя>»). "
                               "Клики в макросе привязаны к местам на экране.")
        n = be.mem_recent_steps()
        name = _Field("Название макроса")

        def save(err):
            ok, msg = be.mem_save_macro(name.text())
            if ok:
                self.refresh()
            else:
                self._show(err, msg)
        intro = (T("Последняя команда: {n} шаг.").replace("{n}", str(n)) if n
                 else T("Сначала выполни любую команду — её шаги можно сохранить."))
        self._form_card([(name, 1)], "Сохранить как макрос", save, intro)
        macros = be.mem_macros() or []
        if not macros:
            self.box.addWidget(label("Макросов пока нет.", "muted"))
            return
        rows = []
        for m in macros:
            sub = T("{n} шаг.").replace("{n}", str(m.get("steps", 0))) + (": " + m["desc"] if m.get("desc") else "")
            r = _Row(m["name"], sub)
            r.add(_btn("trash", "Удалить", "danger", lambda k=m["name"]: (be.mem_delete_macro(k), self.refresh())))
            r.add(_btn("bolt", "Запустить", "primary", lambda k=m["name"]: be.mem_run_macro(k)))
            rows.append(r)
        self._list_card(rows)

    def _build_notes(self, be):
        self._title("Заметки", "Факты о тебе — ИИ видит их при выполнении команд (как данные, не как приказы).")
        notes = be.mem_notes() or []
        if notes:
            rows = []
            for i, text in enumerate(notes):
                r = _Row(text)
                r.add(_btn("trash", "Удалить", "danger", lambda k=i: (be.mem_delete_note(k), self.refresh())))
                rows.append(r)
            self._list_card(rows)
        else:
            self.box.addWidget(label("Заметок пока нет.", "muted"))
        fld = _Field("Например: мой город — Киев")

        def add(err):
            ok, msg = be.mem_add_note(fld.text())
            if ok:
                self.refresh()
            else:
                self._show(err, msg)
        self._form_card([(fld, 1)], "Запомнить", add)

    def _build_templates(self, be):
        self._title("Шаблоны", "Несколько быстрых команд под одним именем и с одним подтверждением. "
                               "Запуск: «шаблон <имя>» (и в Telegram, и в расписании).")
        rows = []
        for t in be.mem_templates() or []:
            r = _Row(t["name"] + ("  (" + T("встроенный") + ")" if t.get("builtin") else ""), "; ".join(t.get("items", [])))
            if not t.get("builtin"):
                r.add(_btn("trash", "Удалить", "danger", lambda k=t["name"]: (be.mem_delete_template(k), self.refresh())))
            r.add(_btn("bolt", "Запустить", "primary", lambda k=t["name"]: be.mem_run_template(k)))
            rows.append(r)
        if rows:
            self._list_card(rows)
        name, items = _Field("Имя"), _Field("Команды через «;»")
        name.setMaximumWidth(180)

        def add(err):
            ok, msg = be.mem_add_template(name.text(), items.text())
            if ok:
                self.refresh()
            else:
                self._show(err, msg)
        self._form_card([(name, 0), (items, 1)], "Создать", add, "Новый шаблон: имя и команды через «;»")
