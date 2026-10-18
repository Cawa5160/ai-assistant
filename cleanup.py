"""
Удаление данных программы (вызывается из uninstall.bat): автозагрузка, ярлыки, ключи в хранилище Windows
(Gemini и токен Telegram), а по желанию — папка данных %APPDATA%\\AI_Assistant вместе с резервными копиями.
Печатает итоговый отчёт: что удалено, что не найдено, что не удалось.
"""
import os
import shutil
import sys

SERVICE = "ai_assistant_gemini"


def _report(ok, title, detail=""):
    print(("  [ OK ] " if ok is True else "  [ -- ] " if ok is None else "  [FAIL] ") + title + (f" — {detail}" if detail else ""))


def _step(title, fn):
    try:
        res = fn()
        _report(True if res is not False else None, title)
    except Exception as e:
        _report(False, title, f"{type(e).__name__}: {e}")


def _delete_key(name):
    import keyring
    try:
        keyring.delete_password(SERVICE, name)
    except keyring.errors.PasswordDeleteError:
        return False          # записи не было
    return True


def run(wipe_data: bool):
    import app_paths
    import autostart
    import shortcuts
    print()
    _step("Автозагрузка Windows", autostart.disable)
    _step("Ярлыки", shortcuts.remove)
    _step("Ключи Gemini в хранилище Windows", lambda: _delete_key("keys"))
    _step("Токен Telegram в хранилище Windows", lambda: _delete_key("telegram_token"))
    if wipe_data:
        dirs = {os.path.abspath(app_paths.DATA_DIR)}
        for var in ("APPDATA", "LOCALAPPDATA"):                                     # данные (APPDATA) и резервные копии (LOCALAPPDATA)
            if os.environ.get(var):
                dirs.add(os.path.abspath(os.path.join(os.environ[var], "AI_Assistant")))
        for d in sorted(dirs):
            if os.path.abspath(d) == os.path.abspath(app_paths.APP_DIR):
                continue                                                            # папку с программой не трогаем
            if not os.path.isdir(d):
                _report(None, "Папка данных " + d, "не найдена")
                continue
            _step("Папка данных " + d, lambda d=d: shutil.rmtree(d))
        for name in ("settings.json", "history.json", "scheduled_tasks.json", "macros.json", "notes.json", "templates.json",
                     "action_log.txt", "audit_log.jsonl"):
            p = os.path.join(app_paths.APP_DIR, name)
            if os.path.exists(p):
                _step("Файл " + name, lambda p=p: os.remove(p))
    else:
        _report(None, "Настройки, история и макросы", "оставлены (в %APPDATA%\\AI_Assistant)")
    print()


if __name__ == "__main__":
    run("--wipe" in sys.argv)
