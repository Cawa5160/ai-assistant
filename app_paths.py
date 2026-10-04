"""
Определяет пути приложения — по-разному в зависимости от того, запущено
ли оно как обычный .py-скрипт или собрано в один .exe через PyInstaller
(см. build_exe.bat). Это важно, потому что PyInstaller --onefile
распаковывает всё во ВРЕМЕННУЮ папку заново при КАЖДОМ запуске:

- read-only ресурсы (иконки/логотип), вложенные через --add-data, лежат
  именно в этой временной папке (sys._MEIPASS) — их нужно искать там;
- а пользовательские данные (settings.json, history.json, логи, лок-файл)
  НЕЛЬЗЯ хранить во временной папке — они "слетали" бы при каждом
  перезапуске. Их нужно держать рядом с самим .exe.

При обычном запуске (python main.py) оба пути совпадают — папка со скриптом.
"""
import os
import sys

_is_frozen = getattr(sys, "frozen", False)


def resource_dir() -> str:
    """Где искать вложенные read-only ресурсы (assets/...)."""
    if _is_frozen:
        return getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Где хранить пользовательские данные (settings/history/логи/лок-файл)."""
    if _is_frozen:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


RESOURCE_DIR = resource_dir()
DATA_DIR = data_dir()
