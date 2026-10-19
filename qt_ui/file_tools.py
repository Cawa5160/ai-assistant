"""«Файловые операции»: выбираешь действие и файлы -> видишь список «было → стало» -> одно подтверждение -> можно отменить."""
import os

from PySide6.QtWidgets import (QDialog, QFileDialog, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout)

import file_ops

from .file_ops_dialog import run_file_ops
from .widgets import IconButton, Segmented, T, Tile, label

OPS = [("rename", "Переименовать"), ("move", "Переместить"), ("organize", "Разложить по типам"), ("delete", "В Корзину")]


class FileToolsDialog(QDialog):
    def __init__(self, parent=None, undo_log_path=None):
        super().__init__(parent)
        self.setObjectName("dlg")
        self.setWindowTitle(T("Файловые операции"))
        self.setMinimumWidth(540)
        self._paths, self._folder, self._log = [], "", undo_log_path
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(Tile("layers", 38, 11))
        head.addWidget(label("Файловые операции", "h3"), 1)
        lay.addLayout(head)
        lay.addWidget(label("Сначала покажу, что изменится. Работаю только в Рабочем столе, Документах, Загрузках, Изображениях, "
                            "Видео и Музыке; удаление — только в Корзину.", "sub", wrap=True))
        self.op = Segmented(OPS, "rename")
        self.op.changed.connect(self._mode)
        lay.addWidget(self.op)

        self.pick = IconButton("plus", "mut", size=16, text="  Выбрать файлы…", object_name="ghost")
        self.pick.clicked.connect(self._choose)
        lay.addWidget(self.pick)
        self.chosen = label("", "small", translate=False)
        lay.addWidget(self.chosen)

        self.pattern = QLineEdit()
        self.pattern.setObjectName("field")
        self.pattern.setMinimumHeight(38)
        lay.addWidget(self.pattern)
        self.hint = label("", "small", wrap=True)
        lay.addWidget(self.hint)

        row = QHBoxLayout()
        row.addStretch(1)
        cancel = QPushButton(T("Закрыть"))
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        self.go = IconButton("check", "white", size=16, text="  Предпросмотр", object_name="primary")
        self.go.clicked.connect(self._preview)
        row.addWidget(cancel)
        row.addWidget(self.go)
        lay.addLayout(row)
        self._mode("rename")

    def _mode(self, key):
        self._paths, self._folder = [], ""
        self.chosen.setText("")
        folder_mode = key == "organize"
        self.pick.setText("  " + T("Выбрать папку…" if folder_mode else "Выбрать файлы…"))
        self.pattern.setVisible(key in ("rename", "move"))
        if key == "rename":
            self.pattern.setPlaceholderText(T("Шаблон, например: Отпуск {n:03d}"))
            self.hint.setText(T("{n} — номер по порядку. Расширение файла сохранится."))
        elif key == "move":
            self.pattern.setPlaceholderText(T("Папка назначения (полный путь)"))
            self.hint.setText(T("Если такая папка есть — файлы переедут в неё, иначе она будет создана."))
        elif key == "organize":
            self.hint.setText(T("Файлы папки разложатся по подпапкам: Изображения, Документы, Видео…"))
        else:
            self.hint.setText(T("Файлы уйдут в Корзину — их можно будет вернуть."))

    def _choose(self):
        if self.op.value() == "organize":
            d = QFileDialog.getExistingDirectory(self, T("Выбрать папку…"))
            if d:
                self._folder = d
                self.chosen.setText(d)
        else:
            files, _ = QFileDialog.getOpenFileNames(self, T("Выбрать файлы…"))
            if files:
                self._paths = files
                self.chosen.setText(f"{len(files)}: " + ", ".join(os.path.basename(f) for f in files[:3]) + ("…" if len(files) > 3 else ""))

    def _preview(self):
        key, text = self.op.value(), self.pattern.text().strip()
        try:
            if key == "organize":
                plan = file_ops.plan_organize(self._folder, "type") if self._folder else None
            elif not self._paths:
                plan = None
            elif key == "rename":
                plan = file_ops.plan_rename(self._paths, numbering=text or "{n:03d}")
            elif key == "move":
                plan = file_ops.plan_move(self._paths, text) if text else None
            else:
                plan = file_ops.plan_delete(self._paths)
        except Exception as e:
            self.chosen.setText(T("Не получилось составить план:") + f" {e}")
            return
        if plan is None or not plan:
            self.chosen.setText(T("Нечего делать: выбери файлы и заполни поле."))
            return
        run_file_ops(plan, self, undo_log_path=self._log)
