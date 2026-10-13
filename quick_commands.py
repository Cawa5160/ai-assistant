"""
Быстрые команды без ИИ: «открой блокнот», «открой блокнот и напиши привет». Разбираются по шаблону —
не тратят лимит Gemini и выполняются сразу. Всё остальное идёт обычным путём (локальная модель / ИИ).
План проходит ту же проверку и подтверждение, что и любой другой.
"""
import difflib
import re

APPS = {
    "блокнот": "notepad", "notepad": "notepad",
    "калькулятор": "calc", "calculator": "calc", "calc": "calc",
    "проводник": "explorer", "explorer": "explorer",
    "пейнт": "mspaint", "paint": "mspaint", "ножницы": "snippingtool",
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
    "инстаграм": "https://www.instagram.com", "instagram": "https://www.instagram.com",
    "фейсбук": "https://www.facebook.com", "facebook": "https://www.facebook.com",
    "ватсап": "https://web.whatsapp.com", "whatsapp": "https://web.whatsapp.com",
    "твич": "https://www.twitch.tv", "twitch": "https://www.twitch.tv",
    "спотифай": "https://open.spotify.com", "spotify": "https://open.spotify.com",
    "википедию": "https://ru.wikipedia.org", "википедия": "https://ru.wikipedia.org",
    "гемини": "https://gemini.google.com", "gemini": "https://gemini.google.com",
    "диск": "https://drive.google.com", "гугл диск": "https://drive.google.com",
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
_SCREENSHOT = re.compile(
    r"^\s*(?:пожалуйста[,\s]+)?(?:сделай\s+|зроби\s+|сними\s+)?(?:скриншот|скрин|снимок экрана|знімок екрана)(?:\s+экрана)?"
    r"(?:\s+и\s+(?:сохрани|збережи|положи)(?:\s+его|\s+її)?)?"
    r"(?P<where>\s+(?:на\s+)?(?:рабочем\s+столе|рабочий\s+стол|главном\s+экране|главный\s+экран|столе|робочому\s+столі))?\s*[.!]*\s*$", re.I)
_VOL_UP = re.compile(r"^\s*(?:сделай\s+)?(?:громче|прибавь (?:звук|громкость)|увеличь громкость|звук погромче)\s*[.!]*\s*$", re.I)
_VOL_DOWN = re.compile(r"^\s*(?:сделай\s+)?(?:тише|убавь (?:звук|громкость)|уменьши громкость|звук потише)\s*[.!]*\s*$", re.I)
_MUTE = re.compile(r"^\s*(?:выключи звук|включи звук|без звука|заглуши|mute|отключи звук)\s*[.!]*\s*$", re.I)
_MEDIA = [
    (re.compile(r"^\s*(?:пауза|поставь на паузу|продолжи музыку|играй|play|pause)\s*[.!]*\s*$", re.I), "playpause", "Пауза / воспроизведение"),
    (re.compile(r"^\s*(?:следующий трек|следующая песня|дальше трек|next)\s*[.!]*\s*$", re.I), "nexttrack", "Следующий трек"),
    (re.compile(r"^\s*(?:предыдущий трек|предыдущая песня|прошлый трек|previous)\s*[.!]*\s*$", re.I), "prevtrack", "Предыдущий трек"),
]


_SHOW_DESKTOP = re.compile(r"^\s*(?:покажи рабочий стол|открой рабочий стол|покажи робочий стіл|відкрий робочий стіл|"
                           r"сверни все окна|сверни всё|сверни все|покажи десктоп)\s*[.!]*\s*$", re.I)
_CLOSE_WINDOW = re.compile(r"^\s*(?:закрой|закрыть)\s+(?:это\s+|текущее\s+)?окно\s*[.!]*\s*$", re.I)


# (шаблон, клавиши, описание, trusted) — горячие клавиши в текущем окне. «Опасные» (закрыть, заблокировать) — без автозапуска
def _hk(pat):
    return re.compile(r"^\s*(?:пожалуйста[,\s]+)?(?:" + pat + r")\s*[.!]*\s*$", re.I)


HOTKEY_COMMANDS = [
    (_hk(r"новая вкладка|открой новую вкладку"), ["ctrl", "t"], "Новая вкладка (Ctrl+T)", True),
    (_hk(r"закрой вкладку|закрыть вкладку"), ["ctrl", "w"], "Закрыть вкладку (Ctrl+W)", False),
    (_hk(r"обнови страницу|обнови|перезагрузи страницу"), ["f5"], "Обновить страницу (F5)", True),
    (_hk(r"на весь экран|полноэкранный режим"), ["f11"], "Во весь экран (F11)", True),
    (_hk(r"найди на странице|поиск на странице"), ["ctrl", "f"], "Поиск на странице (Ctrl+F)", True),
    (_hk(r"скопируй|копировать"), ["ctrl", "c"], "Копировать (Ctrl+C)", True),
    (_hk(r"вставь|вставить"), ["ctrl", "v"], "Вставить (Ctrl+V)", True),
    (_hk(r"выдели всё|выдели все|выделить всё"), ["ctrl", "a"], "Выделить всё (Ctrl+A)", True),
    (_hk(r"отмени|отменить действие"), ["ctrl", "z"], "Отменить (Ctrl+Z)", True),
    (_hk(r"сохрани файл|сохрани документ"), ["ctrl", "s"], "Сохранить (Ctrl+S)", False),
    (_hk(r"диспетчер задач|открой диспетчер задач"), ["ctrl", "shift", "esc"], "Диспетчер задач", True),
    (_hk(r"сверни окно|свернуть окно"), ["win", "down"], "Свернуть окно (Win+↓)", True),
    (_hk(r"разверни окно|развернуть окно"), ["win", "up"], "Развернуть окно (Win+↑)", True),
    (_hk(r"переключи окно|следующее окно"), ["alt", "tab"], "Переключить окно (Alt+Tab)", True),
    (_hk(r"заблокируй (?:компьютер|экран)|заблокировать компьютер"), ["win", "l"], "Заблокировать компьютер (Win+L)", False),
    (_hk(r"буфер обмена|открой буфер обмена"), ["win", "v"], "Журнал буфера обмена (Win+V)", True),
]


def _plan(summary, steps, trusted=True):
    # trusted=True — шаблонная команда без ИИ: можно выполнить после короткого таймера
    return {"summary": summary, "steps": steps, "need_vision": False, "trusted": trusted}


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
    m = _SCREENSHOT.match(command)
    if m:
        where = "desktop" if (m.group("where") or re.search(r"сохрани|збережи", command, re.I)) else "pictures"
        title = "на рабочий стол" if where == "desktop" else "в Изображения → Screenshots"
        return _plan(f"Сделать скриншот и сохранить {title}",
                     [{"action": "save_screenshot", "args": {"where": where}}])
    for pattern, keys, title, trusted in HOTKEY_COMMANDS:
        if pattern.match(command):
            return _plan(title, [{"action": "hotkey", "args": {"keys": keys}}], trusted=trusted)
    if _SHOW_DESKTOP.match(command):
        return _plan("Показать рабочий стол (Win+D)", [{"action": "hotkey", "args": {"keys": ["win", "d"]}}])
    if _CLOSE_WINDOW.match(command):
        return _plan("Закрыть текущее окно (Alt+F4)", [{"action": "hotkey", "args": {"keys": ["alt", "f4"]}}], trusted=False)
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


_LOOSE = re.compile(r"^\s*(?:пожалуйста[,\s]+|будь\s+ласка[,\s]+)?(?P<verb>открой\w*|открыть|запусти\w*|запустить|включи|"
                    r"відкрий\w*|відкрити|запусти\w*|запустити|увімкни|open|launch|start)\s+"
                    r"(?:программу\s+|приложение\s+|програму\s+|застосунок\s+|сайт\s+)?(?P<w>[\w.\-]{3,20})", re.I)


def _normalize(command: str) -> str:
    """Приводит команду к виду, который понимают шаблоны: синонимы глагола, «пожалуйста» в конце, опечатки в названии."""
    c = re.sub(r"[\s,]+(?:пожалуйста|будь\s+ласка|please)\s*[.!]*\s*$", "", command or "", flags=re.I)
    m = _LOOSE.match(c)
    if not m:
        return c
    word = m.group("w")
    known = list(APPS) + list(SITES)
    if word.lower() not in known:
        close = difflib.get_close_matches(word.lower(), known, n=1, cutoff=0.8)
        if close:
            c = c[:m.start("w")] + close[0] + c[m.end("w"):]
            m = _LOOSE.match(c)
    return c[:m.start("verb")] + "открой" + c[m.end("verb"):]


def try_quick_plan(command: str):
    """Возвращает план {"summary", "steps"} или None, если команда не простая."""
    command = _normalize(command or "")
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
    return {"summary": summary, "steps": steps, "need_vision": False, "trusted": True}
