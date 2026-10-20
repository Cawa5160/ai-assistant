"""Профили игр: подсказки для ИИ-«глаз» по имени процесса. Файл game_profiles.json в папке данных:
{"robloxplayerbeta.exe": {"title": "Roblox", "hint": "текст подсказки"}}. Встроенных профилей нет — добавляй свои."""
import os

import atomic_io
from app_paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "game_profiles.json")


def load() -> dict:
    d = atomic_io.read_json(PATH, {})
    return {str(k).lower(): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def hint_for(process: str) -> str:
    return str(load().get((process or "").lower(), {}).get("hint", ""))[:1500]


def save_hint(process: str, title: str, hint: str):
    d = load()
    d[(process or "").lower()] = {"title": title, "hint": hint[:1500]}
    atomic_io.write_json(PATH, d)
