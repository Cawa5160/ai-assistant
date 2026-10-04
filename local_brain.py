"""
Локальная небольшая модель (через Ollama) для простых команд —
не требует интернета и не расходует лимиты облачного API.

Установка Ollama: https://ollama.com/download (Windows)
После установки в консоли: ollama pull qwen2.5:3b
(или другое имя модели — настраивается в Настройках приложения)

Модель решает две вещи:
1. Может ли она сама справиться с командой (простое действие, без нужды видеть экран)
2. Если да — сразу отдаёт готовый план в том же JSON-формате, что и ai_brain.py
Если модель не уверена — возвращает need_vision=True, и запрос уходит в облачный ИИ.
"""
import json
import requests

import config
from logger import log

OLLAMA_URL = "http://localhost:11434/api/generate"

SYSTEM_PROMPT = """Ты помогаешь классифицировать и разбирать команды для управления компьютером Windows 11.

Простые команды — те, что не требуют видеть содержимое экрана: открыть программу,
открыть сайт, напечатать текст, нажать комбинацию клавиш, создать файл.

Сложные команды — те, где нужно кликать по конкретным элементам интерфейса,
искать что-то визуально на экране, работать внутри уже открытого окна по контексту.

Ответь СТРОГО в JSON, без пояснений:

Если команда простая:
{"need_vision": false, "summary": "...", "steps": [{"action": "open_app", "args": {"app_name": "chrome"}}]}

Действия: open_app, open_url, type_text, press_key, hotkey, create_file.

Если команда сложная (нужно видеть экран):
{"need_vision": true, "summary": ""}

Команда пользователя: """


def is_ollama_running() -> bool:
    try:
        requests.get("http://localhost:11434", timeout=1)
        return True
    except requests.exceptions.RequestException:
        return False


def try_local_plan(user_command: str):
    """
    Пытается обработать команду локальной моделью.
    Возвращает план (dict) если справилась сама, или None если нужен облачный ИИ.
    """
    if not config.get("local_model_enabled"):
        return None

    if not is_ollama_running():
        log("Ollama не запущена — пропускаю локальную модель, иду сразу в облачный ИИ.")
        return None

    model_name = config.get("local_model_name")

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": model_name,
                "prompt": SYSTEM_PROMPT + user_command,
                "stream": False,
                "format": "json",
            },
            timeout=15,
        )
        response.raise_for_status()
        raw = response.json().get("response", "").strip()
        parsed = json.loads(raw)

        if parsed.get("need_vision", True):
            log("Локальная модель решила: нужен анализ экрана -> передаю облачному ИИ.")
            return None

        log(f"Локальная модель справилась сама: {parsed.get('summary')}")
        return parsed

    except Exception as e:
        log(f"Локальная модель не справилась ({e}) -> передаю облачному ИИ.")
        return None
