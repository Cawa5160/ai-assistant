"""
Резервная копия настроек и секретов — чтобы после обновления/чистки не вводить ключи и токен заново.
  * обычные файлы (настройки, макросы, заметки, шаблоны, расписание, профили, данные Telegram) копируются в
    %LOCALAPPDATA%\\AI_Assistant\\backup;
  * ключи Gemini и токен Telegram — только в зашифрованном виде (защита Windows DPAPI: расшифровать может лишь
    тот же пользователь Windows на этом же компьютере);
  * при запуске, если основной файл пропал, он восстанавливается из копии; пустые ключи/токен — тоже.
"""
import base64
import json
import os
import shutil

from app_paths import DATA_DIR
from logger import log

FILES = ["settings.json", "telegram.json", "macros.json", "notes.json", "templates.json",
         "scheduled_tasks.json", "profiles.json", "history.json"]


def backup_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") if os.name == "nt" else None
    d = os.path.join(base, "AI_Assistant", "backup") if base else os.path.join(DATA_DIR, "backup")
    os.makedirs(d, exist_ok=True)
    return d


# ---------- DPAPI ----------

def _protect(data: bytes) -> bytes:
    if os.name != "nt":
        return base64.b64encode(data)           # вне Windows (тесты) — без шифрования
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inb = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptProtectData(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptProtectData не сработала")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _unprotect(data: bytes) -> bytes:
    if os.name != "nt":
        return base64.b64decode(data)
    import ctypes
    from ctypes import wintypes

    class BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]
    buf = ctypes.create_string_buffer(data, len(data))
    inb = BLOB(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char)))
    out = BLOB()
    if not ctypes.windll.crypt32.CryptUnprotectData(ctypes.byref(inb), None, None, None, None, 0, ctypes.byref(out)):
        raise OSError("CryptUnprotectData не сработала")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(out.pbData)


def _secrets_path() -> str:
    return os.path.join(backup_dir(), "secrets.bin")


def save_secrets(gemini_keys, tg_token) -> bool:
    """Обновляет зашифрованную копию. Пустые значения не затирают то, что уже сохранено."""
    try:
        cur = load_secrets()
        keys = [k for k in (gemini_keys or []) if k] or cur.get("gemini_keys", [])
        token = (tg_token or "") or cur.get("tg_token", "")
        if not keys and not token:
            return False
        blob = _protect(json.dumps({"gemini_keys": keys, "tg_token": token}).encode("utf-8"))
        with open(_secrets_path(), "wb") as f:
            f.write(blob)
        return True
    except Exception as e:
        log(f"Резерв секретов не сохранён: {e}")
        return False


def load_secrets() -> dict:
    try:
        with open(_secrets_path(), "rb") as f:
            d = json.loads(_unprotect(f.read()).decode("utf-8"))
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


# ---------- файлы ----------

def snapshot():
    """Копирует рабочие файлы в резерв (только существующие и непустые)."""
    try:
        dst = backup_dir()
        for name in FILES:
            src = os.path.join(DATA_DIR, name)
            if os.path.isfile(src) and os.path.getsize(src) > 2 and os.path.abspath(dst) != os.path.abspath(DATA_DIR):
                shutil.copy2(src, os.path.join(dst, name))
    except Exception as e:
        log(f"Резервная копия файлов не сделана: {e}")


def restore_missing_files() -> list:
    """Возвращает недостающие файлы из резерва. -> список восстановленных."""
    restored = []
    try:
        src_dir = backup_dir()
        for name in FILES:
            dst = os.path.join(DATA_DIR, name)
            src = os.path.join(src_dir, name)
            if (not os.path.isfile(dst) or os.path.getsize(dst) <= 2) and os.path.isfile(src):
                shutil.copy2(src, dst)
                restored.append(name)
        if restored:
            log("Восстановлено из резервной копии: " + ", ".join(restored))
    except Exception as e:
        log(f"Восстановление из резерва не удалось: {e}")
    return restored


def restore_secrets() -> list:
    """Если ключей Gemini или токена Telegram нет — берёт из зашифрованной копии. -> что восстановлено."""
    done = []
    sec = load_secrets()
    if not sec:
        return done
    try:
        import config
        import telegram_bot
        if sec.get("gemini_keys") and not config.get("gemini_api_keys"):
            config.save({"gemini_api_keys": sec["gemini_keys"]})
            done.append("ключи Gemini")
        if sec.get("tg_token") and not telegram_bot.get_token():
            telegram_bot.set_token(sec["tg_token"])
            done.append("токен Telegram")
        if done:
            log("Восстановлено из резерва: " + ", ".join(done))
    except Exception as e:
        log(f"Восстановление секретов не удалось: {e}")
    return done


def full_snapshot():
    """Файлы + зашифрованные секреты (вызывать после сохранения настроек и при запуске)."""
    try:
        import config
        import telegram_bot
        save_secrets(config.get("gemini_api_keys"), telegram_bot.get_token())
    except Exception as e:
        log(f"Резерв секретов: {e}")
    snapshot()
