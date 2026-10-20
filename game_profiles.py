"""Профили игр: подсказки для ИИ-«глаз» по имени процесса. Файл game_profiles.json в папке данных:
{"robloxplayerbeta.exe": {"title": "Roblox", "hint": "текст подсказки"}}. Встроенных профилей нет — добавляй свои."""
import os

import atomic_io
from app_paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "game_profiles.json")


def load() -> dict:
    d = atomic_io.read_json(PATH, {})
    return {str(k).lower(): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


MAX_LESSONS = 15


def lessons_for(process: str) -> list:
    v = load().get((process or "").lower(), {}).get("lessons", [])
    return [str(x)[:200] for x in v if isinstance(x, str)][-MAX_LESSONS:]


def hint_for(process: str) -> str:
    """Подсказка владельца + то, чему бот научился сам в прошлых играх (уроки)."""
    entry = load().get((process or "").lower(), {})
    text = str(entry.get("hint", ""))[:1500]
    lessons = lessons_for(process)
    if lessons:
        text += ("\nЗНАНИЯ, НАКОПЛЕННЫЕ В ПРОШЛЫХ ИГРАХ (проверенные наблюдения):\n" + "\n".join(f"- {x}" for x in lessons))
    return text.strip()


def save_hint(process: str, title: str, hint: str):
    d = load()
    key = (process or "").lower()
    entry = d.get(key, {})
    entry.update({"title": title, "hint": hint[:1500]})
    d[key] = entry
    atomic_io.write_json(PATH, d)


def add_lesson(process: str, lesson: str):
    """Запоминает урок (короткое наблюдение об игре); дубликаты не пишет, хранит последние MAX_LESSONS."""
    lesson = " ".join(str(lesson or "").split())[:200]
    if not process or len(lesson) < 8:
        return False
    d = load()
    key = process.lower()
    entry = d.get(key, {})
    items = [str(x) for x in entry.get("lessons", []) if isinstance(x, str)]
    if any(x.lower() == lesson.lower() for x in items):
        return False
    items.append(lesson)
    entry["lessons"] = items[-MAX_LESSONS:]
    entry.setdefault("title", process)
    d[key] = entry
    atomic_io.write_json(PATH, d)
    return True
