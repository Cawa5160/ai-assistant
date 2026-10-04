"""
Добавление/удаление приложения из автозагрузки Windows через реестр
(ветка HKEY_CURRENT_USER — не требует прав администратора).

winreg импортируется ЛЕНИВО (внутри функций), т.к. модуль доступен
только на Windows — на других ОС функции просто вернут False/выведут ошибку в лог.
"""
import os
import sys

from logger import log

APP_NAME = "AIAssistant"


def _launch_command() -> str:
    if getattr(sys, "frozen", False):
        # Приложение собрано в .exe (PyInstaller) — Python для запуска не нужен, зовём сам .exe.
        return f'"{sys.executable}" --background'
    base_dir = os.path.dirname(os.path.abspath(__file__))
    main_path = os.path.join(base_dir, "main.py")
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return f'"{pythonw}" "{main_path}" --background'


def is_enabled() -> bool:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_READ,
        ) as key:
            try:
                winreg.QueryValueEx(key, APP_NAME)
                return True
            except FileNotFoundError:
                return False
    except Exception:
        return False


def enable() -> bool:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        ) as key:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _launch_command())
        log("Добавлено в автозагрузку Windows.")
        return True
    except Exception as e:
        log(f"Не удалось добавить в автозагрузку: {e}")
        return False


def disable() -> bool:
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Run",
            0, winreg.KEY_SET_VALUE,
        ) as key:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
        log("Убрано из автозагрузки Windows.")
        return True
    except Exception as e:
        log(f"Не удалось убрать из автозагрузки: {e}")
        return False
