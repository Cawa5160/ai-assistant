"""Рисует страницы новой темы в PNG без экрана (QT_QPA_PLATFORM=offscreen): python -m qt_ui.preview OUT_DIR"""
import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PySide6.QtGui import QFont
from PySide6.QtWidgets import QApplication


def main(out="."):
    from .main_window import MainWindow
    from .theme_manager import theme
    app = QApplication.instance() or QApplication([])
    f = QFont()
    f.setFamilies(["Segoe UI Variable Text", "Segoe UI", "Inter", "Noto Sans"])
    f.setPixelSize(14)
    app.setFont(f)
    os.makedirs(out, exist_ok=True)
    for name in ("dark", "light"):
        theme.set(name, app)
        w = MainWindow()
        w.show()
        app.processEvents()
        w.show_page("home")
        app.processEvents()
        w.grab().save(os.path.join(out, f"q_{name}_home.png"))
        w.chat.add_message("user", "Создай папку на рабочем столе и внутри неё файл блокнота с текстом «привет»")
        w.chat.add_message("assistant", "Готово ✓ Папка «Новая» создана на рабочем столе, внутри — привет.txt с текстом «привет». Выполнено за 4 шага.", "14:02 · 4 шага · Gemini")
        w.chat.add_message("user", "Поставь таймер на 10 минут")
        w.chat.add_message("assistant", "Таймер запущен — сработает в 14:13:20.", "14:03 · Gemini")
        w.show_page("chat")
        app.processEvents()
        app.processEvents()
        w.grab().save(os.path.join(out, f"q_{name}_chat.png"))
        w.close()


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else ".")
