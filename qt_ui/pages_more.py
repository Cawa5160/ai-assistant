"""Страницы «История», «Расписание» и «Проверка системы» нового интерфейса.

Конструктор: Page(backend). Методы бэкенда — см. qt_ui/more_backend.py. У каждой страницы есть refresh();
он же вызывается при показе страницы. Свои стили (поверх общего QSS) строятся из theme.c и обновляются по theme.changed.
"""
import datetime

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import (QApplication, QButtonGroup, QComboBox, QFrame, QHBoxLayout, QLineEdit, QPlainTextEdit,
                               QPushButton, QScrollArea, QSpinBox, QVBoxLayout, QWidget, QCheckBox)

from .theme_manager import theme
from .tokens import _rgba
from .widgets import Card, IconButton, Segmented, StatusDot, T, label

PAD = (40, 28, 40, 24)
DAYS = [("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"), ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс")]
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")


def _qss() -> str:
    c = {k: _rgba(v) for k, v in theme.c.items()}
    return f"""
QLabel#stOk {{ color: {c['ok']}; font-weight: 600; font-size: 13px; }}
QLabel#stWarn {{ color: {c['warn']}; font-weight: 600; font-size: 13px; }}
QLabel#stMut {{ color: {c['mut']}; font-weight: 600; font-size: 13px; }}
QLabel#clock {{ color: {c['acc']}; font-size: 24px; font-weight: 700; }}
QLabel#clockOff {{ color: {c['mut']}; font-size: 24px; font-weight: 700; }}
QLabel#cmdOff {{ color: {c['mut']}; font-weight: 600; }}
QLabel#cmdOn {{ font-weight: 600; }}
QLabel#err {{ color: {c['err']}; font-size: 12px; }}
QFrame#fld {{ background: {c['seg']}; border: 1px solid {c['line']}; border-radius: 11px; }}
QFrame#fld[focus="true"] {{ border: 1px solid {c['acc']}; }}
QLineEdit#fldedit {{ background: transparent; border: none; padding: 7px 4px; selection-background-color: {c['acc']}; }}
QSpinBox#time {{ background: {c['seg']}; color: {c['text']}; border: 1px solid {c['line']}; border-radius: 11px;
    padding: 0px; font-size: 20px; font-weight: 700; selection-background-color: {c['acc']}; }}
QSpinBox#time:focus {{ border: 1px solid {c['acc']}; }}
QComboBox#combo {{ background: {c['seg']}; border: 1px solid {c['line']}; border-radius: 11px; padding: 8px 12px; }}
QComboBox#combo:focus {{ border: 1px solid {c['acc']}; }}
QComboBox#combo::drop-down {{ border: none; width: 24px; }}
QComboBox#combo QAbstractItemView {{ background: {c['card']}; color: {c['text']}; border: 1px solid {c['line']};
    selection-background-color: {c['sel']}; selection-color: {c['text']}; outline: none; padding: 4px; }}
QPushButton#day {{ background: {c['seg']}; border: 1px solid {c['line']}; border-radius: 17px; min-width: 34px;
    min-height: 34px; max-width: 34px; max-height: 34px; color: {c['mut']}; font-weight: 600; font-size: 13px; }}
QPushButton#day:hover {{ border: 1px solid {c['acc']}; }}
QPushButton#day:checked {{ background: {c['sel']}; border: 1px solid {c['acc']}; color: {c['text']}; }}
QPlainTextEdit#mono {{ background: {c['seg']}; border: 1px solid {c['line']}; border-radius: 12px; padding: 10px;
    font-family: Consolas, 'Cascadia Mono', 'DejaVu Sans Mono', monospace; font-size: 12px; color: {c['text']};
    selection-background-color: {c['acc']}; }}
QPushButton#dangerbtn {{ color: {c['err']}; padding: 8px 12px; border-radius: 10px; border: 1px solid transparent; }}
QPushButton#dangerbtn:hover {{ background: {c['hover']}; }}
QPushButton#soft {{ color: {c['text']}; padding: 8px 14px; border-radius: 10px; background: {c['seg']};
    border: 1px solid {c['line']}; }}
QPushButton#soft:hover {{ border: 1px solid {c['acc']}; }}
QCheckBox::indicator {{ width: 18px; height: 18px; border-radius: 5px; border: 1px solid {c['line']}; background: {c['seg']}; }}
QCheckBox::indicator:checked {{ background: {c['acc']}; border: 1px solid {c['acc']}; }}
"""


class _TwoDigit(QSpinBox):
    def textFromValue(self, v):
        return f"{v:02d}"


class _Page(QWidget):
    """Общая основа: заголовок, подзаголовок, стили из темы, refresh() при показе."""

    def __init__(self, backend, title, subtitle, parent=None):
        super().__init__(parent)
        self.backend = backend
        self.lay = QVBoxLayout(self)
        self.lay.setContentsMargins(*PAD)
        self.lay.setSpacing(0)
        self.lay.addWidget(label(title, "h1"))
        self.lay.addSpacing(4)
        self.lay.addWidget(label(subtitle, "sub", wrap=True))
        self.lay.addSpacing(16)
        self._restyle()
        theme.changed.connect(lambda *_: self._restyle())

    def _restyle(self):
        self.setStyleSheet(_qss())

    def showEvent(self, e):
        super().showEvent(e)
        self.refresh()

    def refresh(self):
        pass

    def _scroll(self):
        """Прокручиваемая область; возвращает (scroll, layout внутри)."""
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setFrameShape(QFrame.NoFrame)
        inner = QWidget()
        inner.setObjectName("scrollinner")
        inner.setStyleSheet("QWidget#scrollinner { background: transparent; }")
        lay = QVBoxLayout(inner)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(10)
        lay.setSizeConstraint(QVBoxLayout.SetMinimumSize)
        sc.setWidget(inner)
        return sc, lay

    @staticmethod
    def _clear(lay):
        while lay.count():
            it = lay.takeAt(0)
            w = it.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
            elif it.layout() is not None:
                _Page._clear(it.layout())

    @staticmethod
    def _sep():
        s = QFrame()
        s.setObjectName("sep")
        s.setFixedHeight(1)
        return s


class _Field(QFrame):
    """Однострочное поле в стиле карточек (рамка подсвечивается при фокусе)."""
    changed = Signal(str)
    submitted = Signal()

    def __init__(self, placeholder="", parent=None):
        super().__init__(parent)
        self.setObjectName("fld")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 0, 10, 0)
        lay.setSpacing(6)
        self.icon = None
        self.edit = QLineEdit()
        self.edit.setObjectName("fldedit")
        self.edit.setPlaceholderText(T(placeholder))
        self.edit.textChanged.connect(self.changed)
        self.edit.returnPressed.connect(self.submitted)
        self.edit.installEventFilter(self)
        lay.addWidget(self.edit)

    def eventFilter(self, o, e):
        if o is self.edit and e.type() in (e.Type.FocusIn, e.Type.FocusOut):
            self.setProperty("focus", e.type() == e.Type.FocusIn)
            self.style().unpolish(self)
            self.style().polish(self)
        return super().eventFilter(o, e)

    def text(self):
        return self.edit.text()

    def clear(self):
        self.edit.clear()


class Switch(QWidget):
    """Переключатель вкл/выкл (рисуется темой)."""
    toggled = Signal(bool)

    def __init__(self, checked=False, parent=None):
        super().__init__(parent)
        self._on = checked
        self.setFixedSize(42, 24)
        self.setCursor(Qt.PointingHandCursor)
        theme.changed.connect(lambda *_: self.update())

    def isChecked(self):
        return self._on

    def setChecked(self, v):
        self._on = bool(v)
        self.update()

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self.rect().contains(e.position().toPoint()):
            self._on = not self._on
            self.update()
            self.toggled.emit(self._on)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        c = theme.c
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(c["acc"]) if self._on else QColor(c["line"]))
        p.drawRoundedRect(QRectF(0, 0, 42, 24), 12, 12)
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(31 if self._on else 11, 12), 9, 9)


def _optional(name: str) -> bool:
    return "(доп.)" in name or "Ollama" in name


def _human_time(stamp: str) -> str:
    try:
        t = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return stamp
    delta = (datetime.date.today() - t.date()).days
    day = T("сегодня") if delta == 0 else T("вчера") if delta == 1 else t.strftime("%d.%m")
    return f"{day}, {t:%H:%M}"


def _status_kind(status: str):
    """(значок, objectName метки): ok / warn / mut — как _status_mark/_status_color в Tk-версии."""
    s = (status or "").lower()
    if s.startswith("готово") or "выполнено" in s:
        return "✓ ", "stOk"
    if "ошибка" in s or "не понял" in s:
        return "! ", "stWarn"
    if "остановлено" in s or "отклон" in s or "отмен" in s:
        return "✕ ", "stWarn"
    return "", "stMut"


# ======================================================================== ИСТОРИЯ

class HistoryPage(_Page):
    def __init__(self, backend, parent=None):
        super().__init__(backend, "История команд", "Последние 20 команд. Пароли и похожие данные в истории скрыты.",
                         parent)
        self._mode = "all"
        self._armed = False
        bar = QHBoxLayout()
        bar.setSpacing(10)
        self.search = _Field("Поиск по команде")
        self.search.setFixedHeight(40)
        self.search.setMaximumWidth(320)
        self.search.changed.connect(lambda *_: self._render())
        bar.addWidget(self.search)
        bar.addStretch(1)
        self.seg = Segmented([("all", "Все"), ("ok", "✓ Успешно"), ("bad", "! Не вышло")], "all")
        self.seg.changed.connect(self._set_mode)
        bar.addWidget(self.seg)
        self.btn_refresh = IconButton("history", "mut", text="Обновить", object_name="ghost", size=16)
        self.btn_refresh.clicked.connect(self.refresh)
        bar.addWidget(self.btn_refresh)
        self.btn_clear = IconButton("trash", "err", text="Очистить", object_name="dangerbtn", size=16)
        self.btn_clear.clicked.connect(self._clear_click)
        bar.addWidget(self.btn_clear)
        self.lay.addLayout(bar)
        self.lay.addSpacing(12)
        self.scroll, self.items = self._scroll()
        self.lay.addWidget(self.scroll, 1)
        self._entries = []

    def _set_mode(self, m):
        self._mode = m
        self._render()

    def _clear_click(self):
        if not self._armed:
            self._armed = True
            self.btn_clear.setText("  " + T("Точно очистить?"))
            QTimer.singleShot(4000, self._disarm)
            return
        self._disarm()
        self.backend.history_clear()
        self.refresh()

    def _disarm(self):
        self._armed = False
        self.btn_clear.setText("  " + T("Очистить"))

    def refresh(self):
        self._entries = self.backend.history_entries() or []
        self._render()

    def _render(self):
        self._clear(self.items)
        q = self.search.text().strip().lower()
        shown = []
        for e in self._entries:
            mark, kind = _status_kind(e.get("status", ""))
            if self._mode == "ok" and kind != "stOk":
                continue
            if self._mode == "bad" and kind != "stWarn":
                continue
            if q and q not in (e.get("command") or "").lower() and q not in (e.get("summary") or "").lower():
                continue
            shown.append(e)
        if not shown:
            self.items.addWidget(label("Пока пусто — здесь появятся твои команды." if not self._entries
                                       else "Ничего не найдено.", "muted"))
            self.items.addStretch(1)
            return
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(0)
        for n, e in enumerate(shown):
            if n:
                cl.addWidget(self._sep())
            cl.addWidget(self._row(e))
        self.items.addWidget(card)
        self.items.addStretch(1)

    def _row(self, e):
        w = QWidget()
        r = QHBoxLayout(w)
        r.setContentsMargins(12, 12, 8, 12)
        r.setSpacing(14)
        t = label(_human_time(e.get("time", "")), "small", translate=False)
        t.setFixedWidth(110)
        r.addWidget(t, 0, Qt.AlignTop)
        mid = QVBoxLayout()
        mid.setSpacing(3)
        mid.addWidget(label(e.get("command") or "—", "cmdOn", wrap=True, translate=False))
        if e.get("summary"):
            mid.addWidget(label(e["summary"], "muted", wrap=True))
        r.addLayout(mid, 1)
        mark, kind = _status_kind(e.get("status", ""))
        st = label(mark + T(e.get("status", "")), kind, wrap=True, translate=False)
        st.setFixedWidth(130)
        r.addWidget(st, 0, Qt.AlignTop)
        if e.get("command"):
            b = IconButton("history", "mut", text="Повторить", object_name="ghost", size=16)
            b.clicked.connect(lambda _=False, c=e["command"]: self.backend.repeat_command(c))
            r.addWidget(b, 0, Qt.AlignTop)
        return w


# ======================================================================== РАСПИСАНИЕ

def days_text(days) -> str:
    d = set(days or [])
    if d == {k for k, _ in DAYS}:
        return T("Каждый день")
    if d == set(WEEKDAYS):
        return T("По будням")
    if d == {"sat", "sun"}:
        return T("По выходным")
    return ", ".join(T(n) for k, n in DAYS if k in d) or "—"


class SchedulePage(_Page):
    def __init__(self, backend, parent=None):
        super().__init__(backend, "Расписание задач",
                         "Работает, пока приложение запущено (оно живёт в трее). Каждая запланированная задача "
                         "всё равно спросит подтверждение перед выполнением.", parent)
        self.scroll, body = self._scroll()
        self.lay.addWidget(self.scroll, 1)
        self.list_lay = QVBoxLayout()
        self.list_lay.setSpacing(10)
        body.addLayout(self.list_lay)
        body.addSpacing(10)
        body.addWidget(label("Новая задача", "h3"))
        body.addWidget(self._form())
        body.addStretch(1)

    # --- форма ---
    def _form(self):
        card = Card()
        f = QVBoxLayout(card)
        f.setContentsMargins(20, 18, 20, 18)
        f.setSpacing(6)
        f.addWidget(label("Что сделать", "h3"))
        self.cmd = _Field("Например: открой почту · сделай скриншот · включи youtube")
        self.cmd.setFixedHeight(42)
        self.cmd.submitted.connect(self._add)
        f.addWidget(self.cmd)
        f.addSpacing(10)

        row = QHBoxLayout()
        row.setSpacing(24)
        tcol = QVBoxLayout()
        tcol.setSpacing(6)
        tcol.addWidget(label("Во сколько", "h3"))
        tr = QHBoxLayout()
        tr.setSpacing(6)
        self.hh, self.mm = _TwoDigit(), _TwoDigit()
        for sp, mx, v in ((self.hh, 23, 9), (self.mm, 59, 0)):
            sp.setObjectName("time")
            sp.setRange(0, mx)
            sp.setWrapping(True)
            sp.setValue(v)
            sp.setAlignment(Qt.AlignCenter)
            sp.setButtonSymbols(QSpinBox.NoButtons)
            sp.setFixedSize(58, 44)
        tr.addWidget(self.hh)
        tr.addWidget(label(":", "h1", translate=False))
        tr.addWidget(self.mm)
        tr.addStretch(1)
        tcol.addLayout(tr)
        row.addLayout(tcol)
        ccol = QVBoxLayout()
        ccol.setSpacing(6)
        ccol.addWidget(label("Условие запуска", "h3"))
        self.cond = QComboBox()
        self.cond.setObjectName("combo")
        self.cond.setMinimumWidth(320)
        for key, text in self.backend.schedule_conditions():
            self.cond.addItem(T(text), key)
        ccol.addWidget(self.cond)
        ccol.addStretch(1)
        row.addLayout(ccol, 1)
        f.addLayout(row)
        f.addWidget(label(T("Если условие не выполнено в назначенное время, подожду до {n} минут.").replace(
            "{n}", str(self.backend.schedule_wait_min())), "small", translate=False))
        f.addSpacing(10)

        f.addWidget(label("В какие дни", "h3"))
        pr = QHBoxLayout()
        pr.setSpacing(6)
        for text, keys in (("Будни", WEEKDAYS), ("Каждый день", tuple(k for k, _ in DAYS)), ("Выходные", ("sat", "sun"))):
            b = QPushButton(T(text))
            b.setObjectName("soft")
            b.setCursor(Qt.PointingHandCursor)
            b.clicked.connect(lambda _=False, ks=keys: self._pick(ks))
            pr.addWidget(b)
        pr.addStretch(1)
        f.addLayout(pr)
        dr = QHBoxLayout()
        dr.setSpacing(8)
        self.days = {}
        for key, text in DAYS:
            b = QPushButton(T(text))
            b.setObjectName("day")
            b.setCheckable(True)
            b.setChecked(key in WEEKDAYS)
            b.setCursor(Qt.PointingHandCursor)
            dr.addWidget(b)
            self.days[key] = b
        dr.addStretch(1)
        f.addLayout(dr)
        self.once = QCheckBox(T("Выполнить один раз и удалить"))
        f.addSpacing(6)
        f.addWidget(self.once)
        self.err = label("", "err", translate=False)
        self.err.setVisible(False)
        f.addWidget(self.err)
        f.addSpacing(6)
        add = QPushButton(T("Добавить задачу"))
        add.setObjectName("primary")
        add.setCursor(Qt.PointingHandCursor)
        add.clicked.connect(self._add)
        f.addWidget(add, 0, Qt.AlignLeft)
        return card

    def _pick(self, keys):
        for k, b in self.days.items():
            b.setChecked(k in keys)

    def _add(self):
        days = [k for k, _ in DAYS if self.days[k].isChecked()]
        err = self.backend.schedule_add(self.cmd.text(), days, f"{self.hh.value():02d}:{self.mm.value():02d}",
                                        self.once.isChecked(), self.cond.currentData() or "")
        self.err.setText(T(err))
        self.err.setVisible(bool(err))
        if not err:
            self.cmd.clear()
            self.once.setChecked(False)
            self.refresh()

    # --- список ---
    def refresh(self):
        self._clear(self.list_lay)
        tasks = self.backend.schedule_tasks() or []
        if not tasks:
            self.list_lay.addWidget(label("Пока нет задач — добавь первую ниже.", "muted"))
            return
        conds = dict(self.backend.schedule_conditions())
        for t in tasks:
            self.list_lay.addWidget(self._task_card(t, conds))

    def _task_card(self, t, conds):
        enabled = bool(t.get("enabled", True))
        card = Card()
        r = QHBoxLayout(card)
        r.setContentsMargins(20, 14, 16, 14)
        r.setSpacing(18)
        r.addWidget(label(str(t.get("time", "")), "clock" if enabled else "clockOff", translate=False))
        info = QVBoxLayout()
        info.setSpacing(3)
        info.addWidget(label(t.get("command", ""), "cmdOn" if enabled else "cmdOff", wrap=True, translate=False))
        sub = ((T("Один раз") + " · ") if t.get("once") else "") + days_text(t.get("days", []))
        if t.get("condition"):
            sub += " · " + T(conds.get(t["condition"], t["condition"]))
        if not enabled:
            sub += " · " + T("выключено")
        info.addWidget(label(sub, "muted", wrap=True, translate=False))
        r.addLayout(info, 1)
        sw = Switch(enabled)
        sw.setToolTip(T("Задача включена"))
        sw.toggled.connect(lambda v, tid=t["id"]: (self.backend.schedule_set_enabled(tid, v),
                                                   QTimer.singleShot(30, self.refresh)))
        r.addWidget(sw, 0, Qt.AlignVCenter)
        d = IconButton("trash", "err", text="Удалить", object_name="dangerbtn", size=16)
        d.clicked.connect(lambda _=False, tid=t["id"]: (self.backend.schedule_remove(tid), self.refresh()))
        r.addWidget(d, 0, Qt.AlignVCenter)
        return card


# ======================================================================== ПРОВЕРКА СИСТЕМЫ

class DiagPage(_Page):
    def __init__(self, backend, parent=None):
        super().__init__(backend, "Проверка системы",
                         "Если что-то не работает (голос, ключи, автозагрузка) — причина обычно видна здесь.", parent)
        self._results = []
        self._log = ""
        self._busy = False
        bar = QHBoxLayout()
        bar.setSpacing(8)
        for icon, text, fn, name in (("note", "Журнал действий", self._open_audit, "soft"),
                                     ("pulse", "Проверить снова", self.refresh, "soft"),
                                     ("copy", "Копировать", self._copy, "soft")):
            b = IconButton(icon, "mut", text=text, object_name=name, size=16)
            b.clicked.connect(lambda _=False, f=fn: f())
            bar.addWidget(b)
        self.btn_mouse = IconButton("bolt", "white", text="Тест мыши", object_name="primary", size=16)
        self.btn_mouse.clicked.connect(self._mouse)
        bar.addWidget(self.btn_mouse)
        bar.addStretch(1)
        self.lay.addLayout(bar)
        self.lay.addSpacing(12)
        self.scroll, body = self._scroll()
        self.lay.addWidget(self.scroll, 1)
        self.summary = label("", "h3", translate=False)
        body.addWidget(self.summary)
        self.checks = QVBoxLayout()
        body.addLayout(self.checks)
        self.log_title = label("Журнал приложения (последние записи)", "h3")
        body.addSpacing(6)
        body.addWidget(self.log_title)
        self.box = QPlainTextEdit()
        self.box.setObjectName("mono")
        self.box.setReadOnly(True)
        self.box.setMinimumHeight(260)
        body.addWidget(self.box)
        body.addStretch(1)

    def refresh(self):
        if self._busy:
            return
        self._busy = True
        self.summary.setText(T("Проверяю…"))
        self.box.setPlainText(T("Проверяю…"))
        self.backend.diag_run(self._show)

    def _show(self, results, log):
        self._busy = False
        self._results, self._log = list(results), log
        self._clear(self.checks)
        bad_req = [r for r in results if not r[1] and not _optional(r[0])]
        bad_opt = [r for r in results if not r[1] and _optional(r[0])]
        if not bad_req and not bad_opt:
            self.summary.setText(T("Всё в порядке"))
        else:
            parts = []
            if bad_req:
                parts.append(f"{T('Нужно исправить')}: {len(bad_req)}")
            if bad_opt:
                parts.append(f"{T('Необязательное недоступно')}: {len(bad_opt)}")
            self.summary.setText(" · ".join(parts))
        card = Card()
        cl = QVBoxLayout(card)
        cl.setContentsMargins(6, 6, 6, 6)
        cl.setSpacing(0)
        for n, (name, ok, detail) in enumerate(results):
            if n:
                cl.addWidget(self._sep())
            row = QWidget()
            r = QHBoxLayout(row)
            r.setContentsMargins(12, 10, 12, 10)
            r.setSpacing(12)
            row.setMinimumHeight(46)
            role = "ok" if ok else ("warn" if _optional(name) else "err")
            r.addWidget(StatusDot(role), 0, Qt.AlignTop)
            nm = label(name, "cmdOn", translate=True)
            nm.setMinimumWidth(230)
            nm.setMaximumWidth(260)
            nm.setWordWrap(True)
            r.addWidget(nm, 0, Qt.AlignTop)
            r.addWidget(label(str(detail), "muted", wrap=True), 1)
            cl.addWidget(row)
        self.checks.addWidget(card)
        self.log_title.setText(T("Журнал приложения (последние записи)"))
        self.box.setPlainText(log)
        self.box.verticalScrollBar().setValue(self.box.verticalScrollBar().maximum())

    def _open_audit(self):
        err = self.backend.diag_open_audit()
        if err:
            self.box.setPlainText(err)

    def _mouse(self):
        self.btn_mouse.setEnabled(False)
        self.log_title.setText(T("Тест мыши"))
        self.box.setPlainText(T("Тест мыши: не трогай мышь ~10 секунд…"))
        self.backend.diag_mouse_test(self._mouse_done)

    def _mouse_done(self, text):
        self.btn_mouse.setEnabled(True)
        self.box.setPlainText(text)

    def _copy(self):
        checks = "\n".join(f"{'OK' if ok else 'ОШИБКА'} {n}: {d}" for n, ok, d in self._results)
        QApplication.clipboard().setText((checks + "\n\n" if checks else "") + self.box.toPlainText())
