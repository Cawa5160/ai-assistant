"""Диалог файловых операций: список «было → стало», одно подтверждение, «Отменить» после применения."""
import os

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QScrollArea, QVBoxLayout, QWidget

import file_ops

from . import icons
from .theme_manager import theme
from .widgets import IconButton, T, Tile, label

_ICON = {"rename": "note", "move": "send", "delete": "trash"}
_MAX_ROWS = 200


class FileOpsDialog(QDialog):
    """Результат: .result (ApplyResult или None), .undone (UndoResult или None); сигналы applied/undone."""
    applied = Signal(object)
    undone = Signal(object)

    def __init__(self, plan, parent=None, undo_log_path=None, allowed_roots=None, trash_dir=None):
        super().__init__(parent)
        self.plan = plan
        self.result = None
        self.undone_result = None
        self._kw = dict(allowed_roots=allowed_roots, trash_dir=trash_dir)
        self._log = undo_log_path
        self.setObjectName("dlg")
        self.setWindowTitle(T(plan.title or "Файловые операции"))
        self.setModal(True)
        self.setMinimumWidth(560)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(Tile("layers", 38, 11))
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label(plan.title or "Файловые операции", "h3"))
        n, b = len(plan.runnable), len(plan.blocked)
        self.summary = label("", "small", translate=False)
        self.summary.setText(f"{T('Будет выполнено:')} {n}" + (f"  ·  {T('пропущено:')} {b}" if b else ""))
        col.addWidget(self.summary)
        head.addLayout(col, 1)
        lay.addLayout(head)

        for w in plan.warnings:
            lay.addWidget(self._err(w))

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(180)
        scroll.setMaximumHeight(360)
        body = QWidget()
        bl = QVBoxLayout(body)
        bl.setContentsMargins(0, 0, 6, 0)
        bl.setSpacing(6)
        for op in plan.ops[:_MAX_ROWS]:
            bl.addWidget(self._row(op))
        if len(plan.ops) > _MAX_ROWS:
            bl.addWidget(label(f"{T('…и ещё')} {len(plan.ops) - _MAX_ROWS}", "small", translate=False))
        bl.addStretch(1)
        scroll.setWidget(body)
        lay.addWidget(scroll, 1)

        self.banner = QFrame()                              # «Готово: N операций» + «Отменить»
        self.banner.setObjectName("warnbox")
        brow = QHBoxLayout(self.banner)
        brow.setContentsMargins(14, 10, 14, 10)
        self.banner_text = QLabel("")
        self.banner_text.setObjectName("ok")
        self.banner_text.setWordWrap(True)
        brow.addWidget(self.banner_text, 1)
        self.undo_btn = IconButton("history", "text", size=16, text="  Отменить", object_name="ghost")
        self.undo_btn.clicked.connect(self._undo)
        brow.addWidget(self.undo_btn)
        self.banner.hide()
        lay.addWidget(self.banner)

        row = QHBoxLayout()
        row.addStretch(1)
        self.cancel_btn = QPushButton(T("Отмена"))
        self.cancel_btn.setObjectName("ghost")
        self.cancel_btn.clicked.connect(self.reject)
        self.apply_btn = IconButton("check", "white", size=16, text="  Применить", object_name="primary")
        self.apply_btn.clicked.connect(self._apply)
        self.apply_btn.setDefault(True)
        self.apply_btn.setEnabled(bool(plan.runnable))
        row.addWidget(self.cancel_btn)
        row.addWidget(self.apply_btn)
        lay.addLayout(row)

    # --- строки
    @staticmethod
    def _err(text):
        l = QLabel(T(text))
        l.setWordWrap(True)
        l.setStyleSheet(f"color: {theme.c['err']}; font-size: 12px;")
        return l

    @staticmethod
    def _names(op):
        old = os.path.basename(op.src.rstrip("\\/")) or op.src
        if op.kind == "delete":
            return old, T("в Корзину")
        if op.kind == "move":
            return old, os.path.basename(os.path.dirname(op.dst)) + "/" + os.path.basename(op.dst)
        return old, os.path.basename(op.dst)

    def _row(self, op):
        box = QFrame()
        box.setObjectName("attach")
        box.setProperty("bad", "true" if op.blocked else "false")
        box.setToolTip(op.src + (("\n→ " + op.dst) if op.dst else ""))
        h = QHBoxLayout(box)
        h.setContentsMargins(10, 8, 10, 8)
        h.setSpacing(10)
        ic = QLabel()
        color = theme.c["err"] if op.blocked else theme.c["mut"]
        ic.setPixmap(icons.pixmap(_ICON.get(op.kind, "file"), color, 16))
        ic.setFixedWidth(20)
        h.addWidget(ic, 0, Qt.AlignTop)
        v = QVBoxLayout()
        v.setSpacing(2)
        old, new = self._names(op)
        t = QLabel(f"{old}  →  {new}")
        t.setWordWrap(True)
        t.setTextInteractionFlags(Qt.NoTextInteraction)
        v.addWidget(t)
        if op.reason:
            v.addWidget(self._err(op.reason))
        elif op.note:
            v.addWidget(label(op.note, "small"))
        h.addLayout(v, 1)
        return box

    # --- действия
    def _apply(self):
        self.apply_btn.setEnabled(False)
        self.result = file_ops.apply(self.plan, self._log, **self._kw)
        r = self.result
        text = f"{T('Готово:')} {r.done} {T('операций')}"
        if r.failed:
            text += f"  ·  {T('не удалось:')} {len(r.failed)}"
        self.banner_text.setText(text)
        self.undo_btn.setVisible(r.done > 0)
        self.undo_btn.setEnabled(True)
        self.banner.show()
        self.apply_btn.hide()
        self.cancel_btn.setText(T("Закрыть"))
        self.applied.emit(r)
        self.adjustSize()

    def _undo(self):
        self.undo_btn.setEnabled(False)
        self.undone_result = file_ops.undo(self._log)
        u = self.undone_result
        self.banner_text.setText(f"{T('Отменено:')} {u.restored}" + (f"  ·  {T('ошибок:')} {len(u.errors)}" if u.errors else ""))
        self.undo_btn.hide()
        self.undone.emit(u)

    def reject(self):
        if self.result is not None:
            self.accept()                                   # после применения «Закрыть» = успешное завершение
        else:
            super().reject()


def run_file_ops(plan, parent=None, **kw):
    """Показывает диалог; возвращает ApplyResult (если применили) или None. Отмена после применения
    возможна в самом диалоге (смотри dlg.undone_result, если нужен вызывающему)."""
    dlg = FileOpsDialog(plan, parent, **kw)
    dlg.exec()
    return dlg.result
