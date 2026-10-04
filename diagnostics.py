"""
Проверка окружения — помогает быстро понять, чего не хватает,
без необходимости лезть в консоль и разбираться в трассировках.
"""
import sys
import os
import importlib

import config

REQUIRED_MODULES = [
    ("pyautogui", "управление мышью/клавиатурой"),
    ("keyboard", "горячие клавиши"),
    ("pystray", "иконка в трее"),
    ("PIL", "скриншоты и превью (Pillow)"),
    ("pyperclip", "ввод русского текста через буфер обмена"),
    ("psutil", "список процессов"),
    ("requests", "обращение к Gemini API"),
]

OPTIONAL_MODULES = [
    ("sounddevice", "запись с микрофона (вместо PyAudio, без компилятора)"),
    ("speech_recognition", "распознавание речи через Google (запасной путь — Gemini)"),
    ("pyttsx3", "офлайн голосовой отклик"),
    ("keyring", "защищённое хранение ключей"),
    ("winotify", "нативные Windows-уведомления"),
]


def run_checks() -> list:
    """Возвращает список (метка, ок?, подробности)."""
    results = []

    py_ok = sys.version_info >= (3, 10)
    results.append((
        "Python", py_ok,
        sys.version.split()[0] + ("" if py_ok else " — нужен 3.10 или новее"),
    ))

    for module_name, purpose in REQUIRED_MODULES:
        try:
            importlib.import_module(module_name)
            results.append((f"Модуль {module_name}", True, purpose))
        except Exception as e:
            results.append((f"Модуль {module_name}", False, f"{purpose} — НЕ установлен ({e})"))

    for module_name, purpose in OPTIONAL_MODULES:
        try:
            importlib.import_module(module_name)
            results.append((f"Модуль {module_name} (доп.)", True, purpose))
        except Exception:
            results.append((
                f"Модуль {module_name} (доп.)", False,
                f"{purpose} — не установлен, часть функций будет недоступна",
            ))

    try:
        import voice_input
        ok, message = voice_input.check_microphone()
        results.append(("Микрофон (доп.)", ok, message))
    except Exception as e:
        results.append(("Микрофон (доп.)", False, f"не удалось проверить: {e}"))

    keys = config.get("gemini_api_keys")
    results.append((
        "Ключи Gemini", bool(keys),
        f"настроено: {len(keys)}" if keys else "не настроено — добавь в Настройках",
    ))

    try:
        from local_brain import is_ollama_running
        running = is_ollama_running()
        results.append((
            "Локальная модель (Ollama)", running,
            "запущена" if running else "не запущена (необязательно — команды пойдут через Gemini)",
        ))
    except Exception as e:
        results.append(("Локальная модель (Ollama)", False, f"ошибка проверки: {e}"))

    try:
        from autostart import is_enabled
        auto = is_enabled()
        results.append(("Автозагрузка с Windows", True, "включена" if auto else "выключена"))
    except Exception as e:
        results.append(("Автозагрузка с Windows", False, f"ошибка проверки: {e}"))

    try:
        from secure_store import is_available
        avail = is_available()
        results.append((
            "Защищённое хранилище ключей", avail,
            "доступно" if avail else "недоступно — ключи хранятся в обычном файле",
        ))
    except Exception as e:
        results.append(("Защищённое хранилище ключей", False, f"ошибка проверки: {e}"))

    try:
        test_path = os.path.join(config.BASE_DIR, ".write_test.tmp")
        with open(test_path, "w") as f:
            f.write("ok")
        os.remove(test_path)
        results.append(("Доступ на запись в папку приложения", True, "ок"))
    except Exception as e:
        results.append(("Доступ на запись в папку приложения", False, str(e)))

    try:
        import updater
        state, text, url = updater.check()
        results.append(("Обновления", state != "new", text + (f" — {url}" if state == "new" else "")))
    except Exception as e:
        results.append(("Обновления", True, f"не проверено ({e})"))

    return results
