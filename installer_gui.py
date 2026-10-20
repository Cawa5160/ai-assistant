"""Запуск установщика. Сначала нужен PySide6 (на нём окно установщика): если его нет, ставим только его
(в консоли), затем открывается окно на Qt. Если PySide6 не поставился — запасное окно на Tk (installer_tk.py)."""
import os
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)


def _qt_ok():
    try:
        import PySide6.QtWidgets  # noqa: F401
        return True
    except Exception:
        return False


def _qt_spec():
    try:
        with open(os.path.join(BASE_DIR, "requirements.txt"), encoding="utf-8") as f:
            for line in f:
                line = line.split("#")[0].strip()
                if line.lower().startswith("pyside6"):
                    return line
    except Exception:
        pass
    return "PySide6-Essentials"


if __name__ == "__main__":
    if not _qt_ok():
        print("Готовлю окно установщика (скачиваю PySide6, 1–2 минуты)...")
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", _qt_spec()])
    if _qt_ok():
        import installer_qt
        sys.exit(installer_qt.main())
    from installer_tk import InstallerApp
    InstallerApp().run()
