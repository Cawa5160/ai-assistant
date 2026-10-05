"""
Центральное хранилище настроек приложения.
Настройки хранятся в settings.json рядом со скриптом и редактируются
через GUI (трей -> Настройки), без правки кода и переменных окружения.
"""
import json
import os

import atomic_io
from app_paths import DATA_DIR

BASE_DIR = DATA_DIR  # рядом со скриптом (или рядом с .exe, если приложение собрано PyInstaller)
SETTINGS_PATH = os.path.join(BASE_DIR, "settings.json")
LOG_FILE = os.path.join(BASE_DIR, "action_log.txt")

APP_VERSION = "1.14.0"
SETTINGS_VERSION = 2  # растёт, когда меняются значения по умолчанию, которые нужно применить и старым установкам

DEFAULTS = {
    "gemini_api_keys": [],  # список ключей — по одному с каждого Google-аккаунта
    # Для навигации по экрану нужна модель посильнее lite-версии; gemini-3.5-flash бесплатна
    # (по страницам тарифов Google, сент. 2026). Модели меняются — имя можно поправить в Настройках.
    "model": "gemini-3.5-flash",
    "agent_max_steps": 15,      # максимум шагов автопилота на одну задачу (каждый шаг = 1 запрос к ИИ)
    "requests_per_minute": 8,  # свой лимит запросов к Gemini в минуту на ключ (0 = без ограничения)
    "use_fallback_models": True,   # при лимите основной модели пробовать запасные (лимит у каждой модели свой)
    "fallback_models": ["gemini-2.5-flash", "gemini-2.5-flash-lite"],
    "auto_confirm_seconds": 5,  # таймер автозапуска простых команд (0 — всегда ждать клика)
    "agent_step_delay": 0.6,    # пауза после действия, чтобы интерфейс успел обновиться, сек.
    "agent_thinking": "minimal",   # уровень "размышлений" Gemini 3.x для автопилота: minimal|low|medium|high ("" = по умолчанию)
    "view_only": False,         # режим «только смотреть»: ИИ отвечает про экран, но ничего не нажимает и не печатает
    "accent": "indigo",         # акцентный цвет: indigo|blue|green|pink|orange
    "use_ui_elements": True,    # давать ИИ список кнопок/полей окна (Windows UI Automation) вместе со скриншотом
    "theme": "light",           # light | dark | system
    "hotkey_open_window": "ctrl+shift+a",
    "allowed_folders": [
        os.path.expanduser("~/Desktop"),
        os.path.expanduser("~/Documents"),
        os.path.expanduser("~/Downloads"),
    ],
    "dangerous_keywords": [
        "удал", "delete", "format", "форматир", "реестр", "registry",
        "regedit", "diskpart", "rm -rf", "shutdown", "выключ",
        "install", "установ", "uninstall", "system32", "netsh",
        "control panel", "панель управления", "администратор", "admin",
        "runas", "powershell -command",
    ],
    "max_plan_steps": 15,
    "pyautogui_pause": 0.3,
    "pyautogui_failsafe": True,
    "local_model_enabled": True,
    "local_model_name": "qwen2.5:3b",
    "voice_feedback_enabled": False,
    "voice_language": "ru-RU",  # ru-RU | uk-UA | en-US
    "voice_device": "",         # имя микрофона; пусто = системный по умолчанию
    "hotkey_voice": "ctrl+shift+v",  # голосовая команда из любого места
    "hotkey_stop": "ctrl+shift+s",   # остановить автопилот из любого места
    "autostart": False,
}

_cache = None


def load() -> dict:
    """Загружает настройки (файл + env-переменная как запасной вариант), кэширует в памяти."""
    global _cache
    if _cache is not None:
        return _cache

    data = dict(DEFAULTS)
    saved = atomic_io.read_json(SETTINGS_PATH, {})
    data.update(saved)

    # Миграция: настройки, сохранённые старыми версиями, получают новую модель по умолчанию
    # (иначе старая, уже отключённая или слабая модель "прилипла" бы навсегда).
    if saved and saved.get("settings_version", 1) < SETTINGS_VERSION:
        data["model"] = DEFAULTS["model"]
    data["settings_version"] = SETTINGS_VERSION

    # Миграция со старого формата (один ключ -> список ключей)
    old_single_key = data.pop("gemini_api_key", None)
    if old_single_key and old_single_key not in data.get("gemini_api_keys", []):
        data["gemini_api_keys"] = [old_single_key] + data.get("gemini_api_keys", [])

    # Ключи стараемся хранить в защищённом хранилище Windows, а не в settings.json.
    from secure_store import load_keys as _load_secure_keys, save_keys as _save_secure_keys
    secure_keys = _load_secure_keys()
    if secure_keys is not None:
        data["gemini_api_keys"] = secure_keys
    elif data.get("gemini_api_keys"):
        # Ключи пока лежат в файле открытым текстом — пробуем перенести в защищённое хранилище.
        if _save_secure_keys(data["gemini_api_keys"]):
            _scrub_plaintext_keys()

    if not data.get("gemini_api_keys"):
        env_key = os.environ.get("GEMINI_API_KEY", "")
        if env_key:
            data["gemini_api_keys"] = [env_key]

    _cache = data
    return data


def _scrub_plaintext_keys():
    """Убирает ключи из settings.json после того, как они успешно перенесены в защищённое хранилище."""
    saved = atomic_io.read_json(SETTINGS_PATH, None)
    if saved and saved.get("gemini_api_keys"):
        saved["gemini_api_keys"] = []
        atomic_io.write_json(SETTINGS_PATH, saved)


def save(new_values: dict) -> dict:
    """Обновляет настройки и сразу сохраняет их на диск (ключи — в защищённое хранилище, если доступно)."""
    global _cache
    data = load()
    data.update(new_values)

    to_write = dict(data)
    if "gemini_api_keys" in new_values:
        from secure_store import save_keys as _save_secure_keys
        if _save_secure_keys(data["gemini_api_keys"]):
            to_write["gemini_api_keys"] = []  # не дублируем ключи открытым текстом в файле

    atomic_io.write_json(SETTINGS_PATH, to_write)
    _cache = data
    return data


def get(key: str):
    return load().get(key, DEFAULTS.get(key))
