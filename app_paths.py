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


def _app_dir() -> str:
    if _is_frozen:
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """Где хранить пользовательские данные (настройки, история, макросы, токен Telegram, логи).
    В Windows это %APPDATA%\\AI_Assistant — постоянная папка: данные не теряются при обновлении или
    запуске установщика из другой папки. Если её создать нельзя — папка рядом с программой (как раньше)."""
    if os.name == "nt" and os.environ.get("APPDATA"):
        d = os.path.join(os.environ["APPDATA"], "AI_Assistant")
        try:
            os.makedirs(d, exist_ok=True)
            return d
        except OSError:
            pass
    return _app_dir()


RESOURCE_DIR = resource_dir()
APP_DIR = _app_dir()
DATA_DIR = data_dir()

# Файлы данных, которые раньше лежали рядом с программой: один раз переносим в постоянную папку
_LEGACY_FILES = ("settings.json", "history.json", "macros.json", "notes.json", "templates.json", "telegram.json",
                 "scheduled_tasks.json", "profiles.json", "audit_log.jsonl", "action_log.txt")


def _old_install_dirs():
    """Папки, где могла лежать прежняя копия программы с данными: соседи по родительской папке и их дочерние (неглубоко)."""
    found = []
    try:
        parent = os.path.dirname(APP_DIR)
        cands = [parent]
        for name in os.listdir(parent)[:60]:
            p = os.path.join(parent, name)
            if os.path.isdir(p):
                cands.append(p)
                try:
                    cands += [os.path.join(p, n) for n in os.listdir(p)[:30] if os.path.isdir(os.path.join(p, n))]
                except OSError:
                    pass
        for d in cands:
            if os.path.isfile(os.path.join(d, "settings.json")) and os.path.isfile(os.path.join(d, "main.py")):
                found.append(d)
    except OSError:
        pass
    return sorted(set(found), key=lambda d: os.path.getmtime(os.path.join(d, "settings.json")), reverse=True)


def migrate_legacy():
    """Один раз переносит данные из папки программы (и из соседних старых копий) в постоянную папку."""
    if os.path.abspath(DATA_DIR) == os.path.abspath(APP_DIR):
        return
    import shutil
    sources = [APP_DIR]
    if not os.path.exists(os.path.join(DATA_DIR, "settings.json")):
        sources += [d for d in _old_install_dirs() if os.path.abspath(d) != os.path.abspath(APP_DIR)][:1]
    for src in sources:
        for name in _LEGACY_FILES:
            old, new = os.path.join(src, name), os.path.join(DATA_DIR, name)
            try:
                if os.path.isfile(old) and not os.path.exists(new):
                    shutil.copy2(old, new)
            except OSError:
                pass


migrate_legacy()

try:                       # если данные пропали (чистка, сбой) — вернуть из резервной копии
    import backup as _backup
    _backup.restore_missing_files()
except Exception:
    pass
