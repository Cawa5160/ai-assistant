"""
Хранит ключи Gemini через keyring (например, Windows Credential Manager)
вместо открытого текста в settings.json. Если keyring недоступен (другая
ОС, ограничения политики и т.п.) — тихо откатывается на обычный файл
настроек, чтобы приложение не переставало работать.
"""
import json

SERVICE = "ai_assistant_gemini"


def _log(message: str):
    # Ленивый импорт — чтобы не создавать цикл config -> secure_store -> logger -> config.
    try:
        from logger import log
        log(message)
    except Exception:
        print(message)


def _get_backend():
    try:
        import keyring
        keyring.get_keyring()  # бросит исключение, если рабочего бэкенда нет
        return keyring
    except Exception:
        return None


def is_available() -> bool:
    return _get_backend() is not None


def save_keys(keys: list) -> bool:
    """Сохраняет список ключей в защищённое хранилище. True при успехе."""
    kr = _get_backend()
    if not kr:
        return False
    try:
        kr.set_password(SERVICE, "keys", json.dumps(keys))
        return True
    except Exception as e:
        _log(f"Не удалось сохранить ключи в защищённое хранилище: {e}")
        return False


def load_keys():
    """Список ключей из защищённого хранилища, либо None если недоступно/пусто."""
    kr = _get_backend()
    if not kr:
        return None
    try:
        raw = kr.get_password(SERVICE, "keys")
        return json.loads(raw) if raw else None
    except Exception as e:
        _log(f"Не удалось прочитать ключи из защищённого хранилища: {e}")
        return None


def get_secret(name: str):
    """Один произвольный секрет (например, ключ OpenRouter) из защищённого хранилища, либо None."""
    kr = _get_backend()
    if not kr:
        return None
    try:
        return kr.get_password(SERVICE, name) or None
    except Exception as e:
        _log(f"Не удалось прочитать секрет «{name}»: {e}")
        return None


def set_secret(name: str, value: str) -> bool:
    """Сохраняет секрет; пустое значение удаляет его. True при успехе."""
    kr = _get_backend()
    if not kr:
        return False
    try:
        if value:
            kr.set_password(SERVICE, name, value)
        else:
            try:
                kr.delete_password(SERVICE, name)
            except Exception:
                pass
        return True
    except Exception as e:
        _log(f"Не удалось сохранить секрет «{name}»: {e}")
        return False
