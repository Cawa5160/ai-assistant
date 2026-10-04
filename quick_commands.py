"""
Быстрые команды без ИИ: «открой блокнот», «открой блокнот и напиши привет». Разбираются по шаблону —
не тратят лимит Gemini и выполняются сразу. Всё остальное идёт обычным путём (локальная модель / ИИ).
План проходит ту же проверку и подтверждение, что и любой другой.
"""
import re

APPS = {
    "блокнот": "notepad", "notepad": "notepad",
    "калькулятор": "calc", "calculator": "calc", "calc": "calc",
    "проводник": "explorer", "explorer": "explorer",
    "хром": "chrome", "chrome": "chrome", "браузер": "chrome",
}

_PATTERN = re.compile(
    r"^\s*(?:пожалуйста[,\s]+)?(?:открой|открыть|запусти|запустить|включи|відкрий|запусти|open|launch|start)\s+"
    r"(?:программу\s+|приложение\s+|програму\s+|застосунок\s+)?"
    r"(?P<app>" + "|".join(sorted(map(re.escape, APPS), key=len, reverse=True)) + r")\b"
    r"(?:\s+и\s+(?:там\s+)?(?:напиши|напечатай|введи|набери|впиши|write|type)\s+(?:там\s+)?(?P<text>.+?))?"
    r"\s*[.!]*\s*$",
    re.IGNORECASE | re.DOTALL,
)


SITES = {
    "ютуб": "https://www.youtube.com", "youtube": "https://www.youtube.com", "ютьюб": "https://www.youtube.com",
    "гугл": "https://www.google.com", "google": "https://www.google.com",
    "почту": "https://mail.google.com", "почта": "https://mail.google.com", "gmail": "https://mail.google.com",
    "телеграм": "https://web.telegram.org", "telegram": "https://web.telegram.org",
    "дискорд": "https://discord.com/app", "discord": "https://discord.com/app",
    "гитхаб": "https://github.com", "github": "https://github.com",
    "карты": "https://maps.google.com", "переводчик": "https://translate.google.com",
}
_SITE = re.compile(
    r"^\s*(?:пожалуйста[,\s]+)?(?:открой|открыть|зайди на|зайди в|перейди на|відкрий|open)\s+(?:сайт\s+)?"
    r"(?P<site>" + "|".join(sorted(map(re.escape, SITES), key=len, reverse=True)) + r")\s*[.!]*\s*$", re.I)
_YT = re.compile(
    r"^\s*(?:найди|поищи|включи|покажи|знайди|search)\s+(?:на|в|у)\s+(?:ютубе|ютуб|youtube|ютьюбе)\s+(?P<q>.+?)\s*[.!]*\s*$", re.I)
_GOOGLE = re.compile(
    r"^\s*(?:(?:найди|поищи|знайди|search)\s+(?:в|у|на)\s+(?:гугле|гугл|google|интернете|інтернеті)|загугли|погугли|google)\s+"
    r"(?P<q>.+?)\s*[.!]*\s*$", re.I)
_SCREENSHOT = re.compile(r"^\s*(?:сделай\s+|зроби\s+|сними\s+)?(?:скриншот|скрин|снимок экрана|знімок екрана)(?:\s+экрана)?\s*[.!]*\s*$", re.I)
_VOL_UP = re.compile(r"^\s*(?:сделай\s+)?(?:громче|прибавь (?:звук|громкость)|увеличь громкость|звук погромче)\s*[.!]*\s*$", re.I)
_VOL_DOWN = re.compile(r"^\s*(?:сделай\s+)?(?:тише|убавь (?:звук|громкость)|уменьши громкость|звук потише)\s*[.!]*\s*$", re.I)
_MUTE = re.compile(r"^\s*(?:выключи звук|включи звук|без звука|заглуши|mute|отключи звук)\s*[.!]*\s*$", re.I)
_MEDIA = [
    (re.compile(r"^\s*(?:пауза|поставь на паузу|продолжи музыку|играй|play|pause)\s*[.!]*\s*$", re.I), "playpause", "Пауза / воспроизведение"),
    (re.compile(r"^\s*(?:следующий трек|следующая песня|дальше трек|next)\s*[.!]*\s*$", re.I), "nexttrack", "Следующий трек"),
    (re.compile(r"^\s*(?:предыдущий трек|предыдущая песня|прошлый трек|previous)\s*[.!]*\s*$", re.I), "prevtrack", "Предыдущий трек"),
]


def _plan(summary, steps):
    return {"summary": summary, "steps": steps, "need_vision": False}


def _simple_plan(command: str):
    """Команды без приложения: сайты, поиск, скриншот, звук, плеер."""
    from urllib.parse import quote_plus
    m = _SITE.match(command)
    if m:
        url = SITES[m.group("site").lower()]
        return _plan(f"Открыть {url}", [{"action": "open_url", "args": {"url": url}}])
    m = _YT.match(command)
    if m:
        q = m.group("q")
        return _plan(f"Искать на YouTube: {q[:60]}", [{"action": "open_url", "args": {
            "url": "https://www.youtube.com/results?search_query=" + quote_plus(q)}}])
    m = _GOOGLE.match(command)
    if m:
        q = m.group("q")
        return _plan(f"Искать в Google: {q[:60]}", [{"action": "open_url", "args": {
            "url": "https://www.google.com/search?q=" + quote_plus(q)}}])
    if _SCREENSHOT.match(command):
        return _plan("Сделать скриншот (сохранится в Изображения → Снимки экрана)",
                     [{"action": "hotkey", "args": {"keys": ["win", "printscreen"]}}])
    if _VOL_UP.match(command):
        return _plan("Сделать звук громче", [{"action": "press_key", "args": {"key": "volumeup"}}] * 5)
    if _VOL_DOWN.match(command):
        return _plan("Сделать звук тише", [{"action": "press_key", "args": {"key": "volumedown"}}] * 5)
    if _MUTE.match(command):
        return _plan("Выключить / включить звук", [{"action": "press_key", "args": {"key": "volumemute"}}])
    for pattern, key, title in _MEDIA:
        if pattern.match(command):
            return _plan(title, [{"action": "press_key", "args": {"key": key}}])
    return None


def try_quick_plan(command: str):
    """Возвращает план {"summary", "steps"} или None, если команда не простая."""
    command = command or ""
    m = _PATTERN.match(command)
    if not m:
        return _simple_plan(command)
    app = APPS[m.group("app").lower()]
    steps = [{"action": "open_app", "args": {"app_name": app}}]
    summary = f"Открыть «{m.group('app')}»"
    text = (m.group("text") or "").strip().strip("«»\"'“”").strip()
    if text:
        steps += [{"action": "wait", "args": {"seconds": 2.5}},
                  {"action": "type_text", "args": {"text": text}}]
        summary += f" и напечатать: {text[:60]}"
    return {"summary": summary, "steps": steps, "need_vision": False}
