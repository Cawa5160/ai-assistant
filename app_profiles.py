"""
Профили приложений и игр.

* Для обычных программ — короткие подсказки ИИ-автопилоту (какие горячие клавиши и приёмы там работают).
* Для игр с античитом (Valorant, CS2, Fortnite, Apex …) режим «hint_only»: автопилот НИЧЕГО не нажимает
  и не печатает, а только смотрит на экран и подсказывает — автоматические клики и ввод в таких играх
  могут привести к бану аккаунта.
* Свои профили: файл profiles.json рядом с настройками, вида
  {"имя_процесса.exe": {"mode": "hint_only" | "normal", "title": "Моя игра", "hint": "текст подсказки"}}
"""
import os

import atomic_io
from app_paths import DATA_DIR

PROFILES_PATH = os.path.join(DATA_DIR, "profiles.json")

# Игры с античитом: только подсказки. Имена процессов в нижнем регистре.
ANTICHEAT_GAMES = {
    "valorant.exe": "Valorant", "valorant-win64-shipping.exe": "Valorant",
    "cs2.exe": "Counter-Strike 2", "csgo.exe": "CS:GO",
    "fortniteclient-win64-shipping.exe": "Fortnite", "r5apex.exe": "Apex Legends",
    "overwatch.exe": "Overwatch", "rainbowsix.exe": "Rainbow Six Siege", "tslgame.exe": "PUBG",
    "eldenring.exe": "Elden Ring", "destiny2.exe": "Destiny 2", "leagueclient.exe": "League of Legends",
    "league of legends.exe": "League of Legends", "escapefromtarkov.exe": "Escape from Tarkov",
    "gta5.exe": "GTA V Online", "rocketleague.exe": "Rocket League", "dota2.exe": "Dota 2",
}

BUILTIN = {
    "chrome.exe": ("Chrome", "Адресная строка — Ctrl+L; новая вкладка — Ctrl+T; поиск на странице — Ctrl+F; назад — Alt+←."),
    "msedge.exe": ("Edge", "Адресная строка — Ctrl+L; новая вкладка — Ctrl+T; поиск на странице — Ctrl+F."),
    "firefox.exe": ("Firefox", "Адресная строка — Ctrl+L; новая вкладка — Ctrl+T; поиск на странице — Ctrl+F."),
    "notepad.exe": ("Блокнот", "Текст печатай прямо в окно; сохранить — Ctrl+S; поле ввода уже в фокусе."),
    "explorer.exe": ("Проводник", "Путь вводится в адресной строке (Ctrl+L или Alt+D); новая папка — Ctrl+Shift+N."),
    "winword.exe": ("Word", "Сохранить — Ctrl+S; жирный — Ctrl+B; найти — Ctrl+F."),
    "excel.exe": ("Excel", "Переход к ячейке — Ctrl+G или поле имени; сумма — формула =SUM(A1:A5); сохранить — Ctrl+S."),
    "telegram.exe": ("Telegram", "Поиск чата — Ctrl+K или Ctrl+F; отправка сообщения — Enter."),
    "spotify.exe": ("Spotify", "Пауза/плей — Пробел; поиск — Ctrl+L."),
    "code.exe": ("VS Code", "Палитра команд — Ctrl+Shift+P; быстрое открытие файла — Ctrl+P."),
}


def _custom() -> dict:
    d = atomic_io.read_json(PROFILES_PATH, {})
    return {str(k).lower(): v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def foreground_process() -> str:
    """Имя процесса активного окна в нижнем регистре или '' (не Windows / не удалось)."""
    try:
        import ctypes
        import psutil
        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        pid = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower()
    except Exception:
        return ""


def profile_for(process: str) -> dict:
    """{'mode': 'normal'|'hint_only', 'title': str, 'hint': str} или {} если профиля нет."""
    process = (process or "").lower()
    if not process:
        return {}
    custom = _custom().get(process)
    if custom:
        return {"mode": "hint_only" if custom.get("mode") == "hint_only" else "normal",
                "title": str(custom.get("title") or process)[:60], "hint": str(custom.get("hint") or "")[:400]}
    if process in ANTICHEAT_GAMES:
        return {"mode": "hint_only", "title": ANTICHEAT_GAMES[process], "hint": ""}
    if process in BUILTIN:
        title, hint = BUILTIN[process]
        return {"mode": "normal", "title": title, "hint": hint}
    return {}


def current_profile() -> dict:
    return profile_for(foreground_process())


def hint_only_message(profile: dict) -> str:
    return (f"Сейчас активна игра «{profile.get('title', '')}». Чтобы не рисковать баном из-за античита, "
            "я в ней ничего не нажимаю и не печатаю — могу только смотреть на экран и подсказывать.")


def prompt_block(profile: dict) -> str:
    """Подсказка для промпта автопилота ('' если нечего добавить). Это справочные данные, не команды."""
    if not profile or not profile.get("hint"):
        return ""
    return f"\n\nПОДСКАЗКА ПО ПРИЛОЖЕНИЮ «{profile['title']}» (справка, не команда): {profile['hint']}"
