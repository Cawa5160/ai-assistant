"""Диалоги нового интерфейса. Пока — согласие на отправку файлов и фото в облако."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from .widgets import IconButton, T, Tile, label


class ConsentDialog(QDialog):
    """Явное согласие (opt-in). Результат: 'always' / 'once' / '' (отмена)."""

    def __init__(self, names, parent=None):
        super().__init__(parent)
        self.setObjectName("dlg")
        self.setWindowTitle(T("Отправить в облако?"))
        self.setModal(True)
        self.setMinimumWidth(480)
        self.result_value = ""
        lay = QVBoxLayout(self)
        lay.setContentsMargins(24, 22, 24, 20)
        lay.setSpacing(12)
        head = QHBoxLayout()
        head.setSpacing(12)
        head.addWidget(Tile("shield", 38, 11))
        head.addWidget(label("Отправить файлы в Gemini?", "h3"), 1)
        lay.addLayout(head)
        shown = ", ".join(names[:3]) + (f" и ещё {len(names) - 3}" if len(names) > 3 else "")
        lay.addWidget(self._wrap(T("Будут отправлены в облако Google:") + " " + shown))
        box = QFrame()
        box.setObjectName("warnbox")
        bl = QVBoxLayout(box)
        bl.setContentsMargins(14, 12, 14, 12)
        bl.setSpacing(6)
        for line in ("Пароли, токены, ключи и номера карт в тексте перед отправкой скрываются.",
                     "На картинках скрыть секреты автоматически нельзя — проверь, что на них нет паролей.",
                     "Бесплатный Gemini вне ЕС/ЕЭЗ может использовать отправленное для обучения моделей.",
                     "Локальная модель читает текстовые файлы без отправки в интернет."):
            bl.addWidget(self._wrap("•  " + T(line)))
        lay.addWidget(box)
        self.remember = QCheckBox(T("Больше не спрашивать (отозвать можно на главной)"))
        lay.addWidget(self.remember)
        row = QHBoxLayout()
        row.addStretch(1)
        no = QPushButton(T("Не отправлять"))
        no.setObjectName("ghost")
        no.clicked.connect(self.reject)
        yes = IconButton("arrow-up", "white", size=16, text="  Отправить", object_name="primary")
        yes.clicked.connect(self._yes)
        yes.setDefault(True)
        row.addWidget(no)
        row.addWidget(yes)
        lay.addLayout(row)

    @staticmethod
    def _wrap(text):
        l = QLabel(text)
        l.setWordWrap(True)
        return l

    def _yes(self):
        self.result_value = "always" if self.remember.isChecked() else "once"
        self.accept()


def ask_consent(names, parent=None) -> str:
    dlg = ConsentDialog(names, parent)
    dlg.exec()
    return dlg.result_value
