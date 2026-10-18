"""
Быстрые действия без ИИ, которые выполняет сама программа: таймер/напоминание, калькулятор,
открыть папку. Возвращают (описание, функция) — функция выполняется в process_command.
"""
import ast
import operator
import os
import re
import threading

from logger import log

_UNIT = r"(?P<unit>секунд\w*|сек\w*|минут\w*|мин\w*|час\w*|секунд\w*|хвилин\w*|хв\w*|годин\w*|год\w*|seconds?|secs?|minutes?|mins?|hours?|hrs?|ч|с|м|s|m|h)"
_TIMER = re.compile(
    r"^\s*(?:поставь|установи|запусти|засеки|включи|заведи|постав|встанови|set|start)?\s*(?:мне\s+|мені\s+|me\s+|a\s+|an\s+)?(?:таймер|будильник|timer|alarm)\s*(?:на|через|на|for|in)?\s*"
    r"(?P<n>\d+(?:[.,]\d+)?)\s*" + _UNIT + r"\s*[.!]*\s*$", re.I)
_REMIND = re.compile(
    r"^\s*(?:напомни(?:\s+мне)?|нагадай(?:\s+мені)?|remind\s+me)\s+(?:через|in)\s+(?P<n>\d+(?:[.,]\d+)?)\s*" + _UNIT +
    r"\s*,?\s*(?:(?:о том,?\s+что|что|чтобы|про|о|об|що|щоб|to|that|about)\b\s*)?(?P<text>.*?)\s*[.!]*\s*$", re.I)
_CALC = re.compile(r"^\s*(?:сколько будет|посчитай|вычисли|реши|calc|calculate|compute|what is|what's|how much is|скільки буде|порахуй|обчисли|розв'яжи)\s+(?P<expr>.+?)\s*[?=.!]*\s*$", re.I)
_FOLDER = re.compile(
    r"^\s*(?:открой|открыть|покажи|відкрий|open|show)\s+(?:папку\s+|папку\s+|folder\s+|my\s+|the\s+)?(?P<f>загрузки|загрузк\w*|downloads|документы|документи|documents|"
    r"рабочий стол|desktop|изображения|картинки|фото|pictures|музыку|музыка|видео|диск c|диск с|завантаження|робочий стіл|зображення|картинки|музику|відео|music|videos|photos)\s*[.!]*\s*$", re.I)

_FOLDERS = {
    "загрузки": "downloads", "загрузк": "downloads", "downloads": "downloads",
    "документы": "documents", "документи": "documents", "documents": "documents",
    "рабочий стол": "desktop", "desktop": "desktop",
    "изображения": "pictures", "картинки": "pictures", "фото": "pictures", "pictures": "pictures",
    "музыку": "music", "музыка": "music", "видео": "video", "диск c": "c", "диск с": "c",
    "завантаження": "downloads", "робочий стіл": "desktop", "зображення": "pictures", "музику": "music", "відео": "video",
    "music": "music", "videos": "video", "photos": "pictures",
}
_REG = {"documents": "Personal", "downloads": "{374DE290-123F-4565-9164-39C4925E467B}", "desktop": "Desktop",
        "pictures": "My Pictures", "music": "My Music", "video": "My Video"}
_PLAIN = {"documents": "Documents", "downloads": "Downloads", "desktop": "Desktop",
          "pictures": "Pictures", "music": "Music", "video": "Videos"}


def known_folder(kind: str) -> str:
    """Настоящий путь к папке пользователя (учитывает OneDrive и перенос папок)."""
    if kind == "c":
        return "C:\\"
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            value, _ = winreg.QueryValueEx(key, _REG[kind])
        path = os.path.expandvars(value)
        if os.path.isdir(path):
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), _PLAIN[kind])


def _seconds(n: str, unit: str) -> float:
    value = float(n.replace(",", "."))
    u = unit.lower()
    if u.startswith(("мин", "м", "хв", "min", "m")) and not u.startswith(("мс", "ms")):
        return value * 60
    if u.startswith(("час", "ч", "год", "hour", "hr", "h")):
        return value * 3600
    return value


def _human(seconds: float) -> str:
    seconds = int(seconds)
    if seconds >= 3600 and seconds % 3600 == 0:
        return f"{seconds // 3600} ч"
    if seconds >= 60 and seconds % 60 == 0:
        return f"{seconds // 60} мин"
    return f"{seconds} с"


def _ends_at(seconds: float) -> str:
    import datetime
    return (datetime.datetime.now() + datetime.timedelta(seconds=seconds)).strftime("%H:%M:%S")


def _alarm(title: str, text: str, notify):
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(1000, 250)
    except Exception:
        pass
    notify(title, text)


def _start_timer(seconds: float, text: str, notify):
    timer = threading.Timer(seconds, _alarm, args=("⏰ " + text, f"Прошло {_human(seconds)}.", notify))
    timer.daemon = True
    timer.start()
    log(f"Таймер на {_human(seconds)} запущен")


# ---- калькулятор: безопасное вычисление арифметики ----
_OPS = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
        ast.Mod: operator.mod, ast.FloorDiv: operator.floordiv, ast.Pow: operator.pow}


def _eval(node):
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(b) > 100:
            raise ValueError("слишком большая степень")
        return _OPS[type(node.op)](a, b)
    raise ValueError("недопустимое выражение")


def calculate(expr: str):
    """Возвращает число или бросает ValueError. Понимает слова: плюс, минус, умножить на, разделить на, в степени."""
    s = expr.lower()
    for words, sign in (("умножить на", "*"), ("помножить на", "*"), ("разделить на", "/"), ("поделить на", "/"),
                        ("в степени", "**"), ("плюс", "+"), ("минус", "-"), ("процентов от", "*0.01*"),
                        ("помножити на", "*"), ("поділити на", "/"), ("в степені", "**"), ("мінус", "-"), ("відсотків від", "*0.01*"),
                        ("multiplied by", "*"), ("times", "*"), ("divided by", "/"), ("to the power of", "**"), ("plus", "+"), ("minus", "-"), ("percent of", "*0.01*"),
                        (" на ", "*"), ("×", "*"), ("÷", "/"), (":", "/"), ("^", "**")):
        s = s.replace(words, f" {sign} ")
    s = re.sub(r"(?<=\d),(?=\d)", ".", s)
    s = re.sub(r"(?<=\d)\s*[xх]\s*(?=\d)", "*", s)
    if not re.fullmatch(r"[0-9+\-*/().%\s]+", s):
        raise ValueError("не похоже на арифметику")
    try:
        return _eval(ast.parse(s.strip(), mode="eval"))
    except ZeroDivisionError:
        raise ValueError("деление на ноль")
    except SyntaxError:
        raise ValueError("не получилось разобрать выражение")


def _fmt(value) -> str:
    if isinstance(value, float):
        value = round(value, 10)
        if value == int(value) and abs(value) < 1e15:
            return str(int(value))
    return str(value)


_TIME = re.compile(r"^\s*(?:который\s+час|сколько\s+времени|скажи\s+время|время|what\s+time\s+is\s+it|what's\s+the\s+time|what\s+is\s+the\s+time|(?:tell\s+me\s+)?the\s+time|time|котра\s+година|скільки\s+часу|який\s+час|час)\s*[?.!]*\s*$", re.I)
_DATE = re.compile(r"^\s*(?:какое\s+сегодня\s+число|какая\s+сегодня\s+дата|какой\s+сегодня\s+день|дата|сегодняшняя\s+дата|what's\s+the\s+date|what\s+is\s+the\s+date|what\s+is\s+today's\s+date|what\s+day\s+is\s+(?:it|today)|today's\s+date|date|яка\s+сьогодні\s+дата|яке\s+сьогодні\s+число|який\s+сьогодні\s+день|сьогоднішня\s+дата)\s*[?.!]*\s*$", re.I)
_SETTINGS = re.compile(r"^\s*(?:открой|открыть|відкрий|open)\s+(?P<w>параметры|настройки(?:\s+windows)?|настройки\s+виндовс|панель управления|параметри|налаштування(?:\s+windows)?|панель керування|(?:windows\s+)?settings|control\s+panel)\s*[.!]*\s*$", re.I)
_DAYS = ["понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье"]
_MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"]


def try_quick_action(command: str, notify):
    """-> (описание, функция) или None. notify(заголовок, текст) нужен для таймера."""
    command = command or ""
    try:
        import clipboard_tools
        clip = clipboard_tools.try_clipboard_action(command)
        if clip is not None:
            return clip
        import templates
        tpl = templates.try_template_action(command)
        if tpl is not None:
            return tpl
    except Exception as e:
        log(f"Буфер обмена: {e}")
    if _TIME.match(command):
        import datetime
        return ("Сказать время", lambda: "Сейчас " + datetime.datetime.now().strftime("%H:%M"))
    if _DATE.match(command):
        import datetime
        d = datetime.date.today()
        return ("Сказать дату", lambda: f"Сегодня {_DAYS[d.weekday()]}, {d.day} {_MONTHS[d.month - 1]} {d.year}")
    m = _SETTINGS.match(command)
    if m:
        target = "control" if re.search(r"панель|control", m.group("w").lower()) else "ms-settings:"
        return ("Открыть " + m.group("w"), lambda: (os.startfile(target), "Открыто: " + m.group("w"))[1])
    m = _TIMER.match(command)
    if m:
        seconds = _seconds(m.group("n"), m.group("unit"))
        if 0 < seconds <= 86400:
            return (f"Таймер на {_human(seconds)}",
                    lambda: (_start_timer(seconds, "Таймер", notify), f"Таймер на {_human(seconds)} запущен — сработает в {_ends_at(seconds)}.")[1])
    m = _REMIND.match(command)
    if m:
        seconds = _seconds(m.group("n"), m.group("unit"))
        text = (m.group("text") or "").strip() or "Напоминание"
        if 0 < seconds <= 86400:
            return (f"Напомнить через {_human(seconds)}: {text[:60]}",
                    lambda: (_start_timer(seconds, text[:80], notify), f"Напомню через {_human(seconds)} — сработает в {_ends_at(seconds)}: {text[:60]}")[1])
    m = _CALC.match(command)
    if m:
        try:
            result = _fmt(calculate(m.group("expr")))
        except ValueError:
            return None            # не арифметика — пусть разбирает ИИ
        return (f"Посчитать: {m.group('expr')[:60]}", lambda: f"{m.group('expr').strip()} = {result}")
    m = _FOLDER.match(command)
    if m and m.group("f").lower() in ("рабочий стол", "desktop", "робочий стіл") and not re.search(r"папк|folder", command.lower()):
        return None          # «покажи рабочий стол» = свернуть окна (Win+D), а не открыть папку в проводнике
    if m:
        kind = _FOLDERS[m.group("f").lower()] if m.group("f").lower() in _FOLDERS else _FOLDERS.get(m.group("f").lower()[:7], "downloads")
        path = known_folder(kind)
        return (f"Открыть папку {path}", lambda: (os.startfile(path), f"Открыта папка: {path}")[1])
    return None
