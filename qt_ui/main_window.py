"""Главное окно: боковое меню + страницы, переключение тёмной/светлой темы."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QShortcut, QKeySequence
from PySide6.QtWidgets import QFrame, QHBoxLayout, QStackedWidget, QVBoxLayout, QWidget

from .backend import StubBackend
from .pages import ChatPage, HomePage, PlaceholderPage
from .theme_manager import theme
from .widgets import IconButton, NavButton, StatusDot, T, Tile, label

NAV = [("home", "home", "Главная"), ("chat", "sparkles", "Ассистент"), ("history", "history", "История"),
       ("schedule", "calendar", "Расписание"), ("macros", "layers", "Макросы и заметки"),
       ("settings", "sliders", "Настройки"), ("diag", "pulse", "Проверка системы")]


class MainWindow(QWidget):
    def __init__(self, backend=None):
        super().__init__()
        self.backend = backend or StubBackend()
        self.setObjectName("root")
        self.setWindowTitle(T("AI Ассистент"))
        self.resize(1280, 800)
        self.setMinimumSize(980, 640)
        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        side = QFrame()
        side.setObjectName("side")
        side.setFixedWidth(250)
        sl = QVBoxLayout(side)
        sl.setContentsMargins(12, 18, 12, 14)
        sl.setSpacing(4)
        logo = QHBoxLayout()
        logo.setContentsMargins(8, 0, 0, 14)
        logo.setSpacing(10)
        logo.addWidget(Tile("bolt", 30, 9))
        logo.addWidget(label("AI Ассистент", "logo"))
        logo.addStretch(1)
        sl.addLayout(logo)

        self.pages = QStackedWidget()
        self.pages.setObjectName("pages")
        self.index = {}
        self.nav = {}
        self.home = HomePage(self.backend)
        self.chat = ChatPage(self.backend)
        widgets = {"home": self.home, "chat": self.chat}
        for key, icon_name, title in NAV:
            btn = NavButton(icon_name, title)
            btn.clicked.connect(lambda _=False, k=key: self.show_page(k))
            sl.addWidget(btn)
            self.nav[key] = btn
            page = widgets.get(key) or PlaceholderPage(title)
            self.index[key] = self.pages.addWidget(page)
        sl.addStretch(1)

        status = QFrame()
        status.setObjectName("status")
        st = QHBoxLayout(status)
        st.setContentsMargins(14, 12, 14, 12)
        st.setSpacing(10)
        st.addWidget(StatusDot(), 0, Qt.AlignTop)
        col = QVBoxLayout()
        col.setSpacing(2)
        col.addWidget(label("Готов к работе", "ok"))
        col.addWidget(label(f"v{self.backend.version} · " + T("в фоне"), "small", translate=False))
        st.addLayout(col, 1)
        self.theme_btn = IconButton("sun", "mut", size=18)
        self.theme_btn.setToolTip(T("Сменить тему"))
        self.theme_btn.clicked.connect(self.toggle_theme)
        st.addWidget(self.theme_btn)
        sl.addWidget(status)

        outer.addWidget(side)
        outer.addWidget(self.pages, 1)
        self.home.command.connect(self._home_command)
        QShortcut(QKeySequence("Ctrl+K"), self, activated=self._focus_command)
        theme.changed.connect(self._theme_changed)
        self.show_page("home")

    def show_page(self, key):
        self.nav[key].setChecked(True)
        self.pages.setCurrentIndex(self.index[key])
        if key == "home":
            self.home.refresh()

    def _home_command(self, text):
        self.show_page("chat")
        self.chat.mode._btns["run"].setChecked(True)
        self.chat.send(text)
        self.chat.mode._btns["ask"].setChecked(True)

    def _focus_command(self):
        self.show_page("home")
        self.home.focus_command()

    def toggle_theme(self):
        theme.set("light" if theme.name == "dark" else "dark", self._app())

    def _theme_changed(self, name):
        self.theme_btn._name = "sun" if name == "dark" else "moon"
        self.theme_btn.refresh()

    @staticmethod
    def _app():
        from PySide6.QtWidgets import QApplication
        return QApplication.instance()
