"""
Проверяет и классифицирует план, полученный от ИИ, ДО того как он попадёт
в окно подтверждения или на выполнение. Не доверяем JSON от модели вслепую:
проверяем набор действий, типы АРГУМЕНТОВ, диапазоны/длины/допустимые
значения, а также определяем уровень риска каждого шага — это и защита,
и основа "умного" авто-подтверждения безопасных действий (см. gui.confirm_plan).
"""
from urllib.parse import urlparse

import config
from logger import log

ALLOWED_ACTIONS = {
    "open_app": {"app_name": str},
    "open_url": {"url": str},
    "click": {"x": (int, float), "y": (int, float)},
    "double_click": {"x": (int, float), "y": (int, float)},
    "right_click": {"x": (int, float), "y": (int, float)},
    "move_mouse": {"x": (int, float), "y": (int, float)},
    "scroll": {"amount": (int, float)},
    "wait": {"seconds": (int, float)},
    "save_screenshot": {"where": str},
    "type_text": {"text": str},
    "press_key": {"key": str},
    "hold_key": {"key": str, "seconds": (int, float)},
    "look": {"dx": (int, float), "dy": (int, float)},
    "move_turn": {"dx": (int, float), "seconds": (int, float)},
    "hotkey": {"keys": list},
    "create_file": {"path": str},
    "move_file": {"src": str, "dst": str},
}

# Необязательные параметры (если переданы — тоже проверяются по типу). Всё, чего нет ни тут,
# ни в ALLOWED_ACTIONS, считается лишним и план отклоняется: неожиданные параметры —
# либо ошибка модели, либо попытка протащить что-то сверх схемы.
OPTIONAL_ARGS = {
    "type_text": {"sensitive": bool},
    "create_file": {"content": str},
    "move_turn": {"key": str},
}

POINTER_ACTIONS = ("click", "double_click", "right_click", "move_mouse")

# Необратимые/файловые операции — НИКОГДА не уходят в авто-подтверждение,
# всегда требуют явного "Я понимаю риск и разрешаю".
HIGH_RISK_ACTIONS = {"create_file", "move_file"}

# open_url обратим и низкий риск сам по себе (после строгой проверки ниже).
LOW_RISK_ACTIONS = {"open_url", "save_screenshot", "move_mouse"}

# open_app — низкий риск ТОЛЬКО для явно безопасных, общеизвестных программ;
# остальное требует обычного клика (не "запрещено", а не авто-пропускается).
# Полный allowlist "имя -> точный путь exe" сознательно не делаю: shell=True
# уже убран (см. action_executor.open_app), так что главный риск — не
# инъекция команды, а "тихое согласие не глядя", а его и так закрывает
# понижение до обычного риска для всего, что не в этом маленьком списке.
SAFE_APP_NAMES = {
    "chrome", "chrome.exe", "firefox", "firefox.exe",
    "msedge", "msedge.exe", "edge",
    "notepad", "notepad.exe", "calc", "calc.exe", "calculator",
    "explorer", "explorer.exe", "mspaint", "snippingtool",
}

ALLOWED_KEYS = {
    "enter", "esc", "escape", "tab", "space", "backspace", "delete",
    "up", "down", "left", "right", "home", "end", "pageup", "pagedown",
    "ctrl", "shift", "alt", "win",
    "volumeup", "volumedown", "volumemute", "playpause", "nexttrack", "prevtrack", "printscreen",
    "f1", "f2", "f3", "f4", "f5", "f6", "f7", "f8", "f9", "f10", "f11", "f12",
} | set("abcdefghijklmnopqrstuvwxyz") | set("0123456789")

MAX_TEXT_LENGTH = 5000
MAX_HOTKEY_KEYS = 4
MAX_FILE_CONTENT_BYTES = 1_000_000


def _screen_bounds():
    """Реальные границы экрана для проверки координат клика; если узнать
    не удалось (например, окружение без дисплея) — не блокируем по этой причине."""
    try:
        import pyautogui
        return pyautogui.size()
    except Exception:
        return 100000, 100000


def _validate_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme and parsed.scheme not in ("http", "https"):
        return False, f"open_url: запрещённая схема '{parsed.scheme}'"
    if not parsed.scheme:
        # похоже на голый домен без схемы (например 'youtube.com') - добавляем https://
        parsed = urlparse("https://" + url)
    if parsed.scheme not in ("http", "https"):
        return False, "open_url: разрешены только http/https"
    if not parsed.netloc:
        return False, "open_url: некорректный URL"
    return True, ""


def validate_step(step):
    """Возвращает (валиден?, причина_если_нет)."""
    if not isinstance(step, dict):
        return False, "шаг не является объектом"

    action = step.get("action")
    if action not in ALLOWED_ACTIONS:
        return False, f"неизвестное действие: {action}"

    args = step.get("args", {})
    if not isinstance(args, dict):
        return False, "args не является объектом"

    schema = ALLOWED_ACTIONS[action]
    optional = OPTIONAL_ARGS.get(action, {})

    unknown = set(args) - set(schema) - set(optional)
    if unknown:
        return False, f"{action}: лишние параметры {sorted(unknown)}"

    for key, expected_type in list(schema.items()) + [(k, t) for k, t in optional.items() if k in args]:
        if key not in args:
            return False, f"{action}: отсутствует обязательный параметр '{key}'"
        value = args[key]
        # bool в Python — подкласс int: True прошёл бы как число 1, а это явная ошибка модели
        if isinstance(value, bool) and expected_type is not bool:
            return False, f"{action}: параметр '{key}' неверного типа"
        if not isinstance(value, expected_type):
            return False, f"{action}: параметр '{key}' неверного типа"

    if action in POINTER_ACTIONS:
        x, y = args["x"], args["y"]
        if x != x or y != y:  # NaN != NaN — единственный надёжный способ проверить NaN
            return False, f"{action}: координата NaN"
        w, h = _screen_bounds()
        if not (0 <= x <= w and 0 <= y <= h):
            return False, f"{action}: координаты вне экрана ({x},{y} при экране {w}x{h})"

    elif action == "scroll":
        amount = args["amount"]
        if amount != amount or not (-30 <= amount <= 30):
            return False, "scroll: допустимо от -30 до 30 щелчков"

    elif action == "wait":
        seconds = args["seconds"]
        if seconds != seconds or not (0 <= seconds <= 15):
            return False, "wait: допустимо от 0 до 15 секунд"

    elif action == "save_screenshot":
        if args["where"] not in ("desktop", "pictures"):
            return False, "save_screenshot: where = desktop или pictures"

    elif action == "type_text":
        if len(args["text"]) > MAX_TEXT_LENGTH:
            return False, f"type_text: текст длиннее {MAX_TEXT_LENGTH} символов"

    elif action == "press_key":
        if args["key"].lower() not in ALLOWED_KEYS:
            return False, f"press_key: недопустимая клавиша '{args['key']}'"

    elif action == "hold_key":
        if args["key"].lower() not in ALLOWED_KEYS:
            return False, f"hold_key: недопустимая клавиша '{args['key']}'"
        sec = args["seconds"]
        if sec != sec or not (0.05 <= sec <= 10):
            return False, "hold_key: допустимо от 0.05 до 10 секунд"

    elif action == "look":
        dx, dy = args["dx"], args["dy"]
        if dx != dx or dy != dy or abs(dx) > 1500 or abs(dy) > 600:
            return False, "look: допустимо dx до ±1500, dy до ±600 пикселей"

    elif action == "move_turn":
        dx, sec = args["dx"], args["seconds"]
        if dx != dx or abs(dx) > 1500 or sec != sec or not (0.1 <= sec <= 10):
            return False, "move_turn: dx до ±1500, seconds 0.1–10"
        if str(args.get("key", "w")).lower() not in ALLOWED_KEYS:
            return False, "move_turn: недопустимая клавиша"

    elif action == "hotkey":
        keys = args["keys"]
        if not keys or len(keys) > MAX_HOTKEY_KEYS:
            return False, "hotkey: некорректное количество клавиш"
        if not all(isinstance(k, str) for k in keys):
            return False, "hotkey: каждая клавиша должна быть строкой"
        if not all(k.lower() in ALLOWED_KEYS for k in keys):
            return False, "hotkey: недопустимая клавиша в комбинации"

    elif action == "open_url":
        ok, reason = _validate_url(args["url"])
        if not ok:
            return False, reason

    elif action == "create_file":
        content = args.get("content", "")
        if len(content.encode("utf-8", errors="ignore")) > MAX_FILE_CONTENT_BYTES:
            return False, "create_file: содержимое файла слишком большое"

    return True, ""


def validate_plan(plan):
    """
    Проверяет весь план целиком. Возвращает (valid_steps, rejected) —
    список валидных шагов и список (шаг, причина) для отброшенных.
    """
    if not isinstance(plan, dict):
        return [], [(plan, "план не является объектом")]

    steps = plan.get("steps", [])
    if not isinstance(steps, list):
        log("План: 'steps' не является списком — план отклонён целиком")
        return [], [(plan, "'steps' не является списком")]

    max_steps = config.get("max_plan_steps")
    valid_steps = []
    rejected = []

    for step in steps[:max_steps]:
        ok, reason = validate_step(step)
        if ok:
            valid_steps.append(step)
        else:
            rejected.append((step, reason))
            log(f"План: шаг отклонён валидатором ({reason}): {step}")

    if len(steps) > max_steps:
        log(f"План: обрезан до {max_steps} шагов (было {len(steps)})")

    return valid_steps, rejected


def _is_local_or_odd_url(url: str) -> bool:
    """localhost, адреса вида 192.168.x.x / 127.x / IP, «user@host», нестандартные порты — открывать только с подтверждением."""
    import ipaddress
    from urllib.parse import urlparse
    p = urlparse(url if "://" in url else "https://" + url)
    host = (p.hostname or "").lower()
    if not host or "@" in (p.netloc or "") or host == "localhost" or host.endswith((".local", ".lan", ".internal", ".localhost")):
        return True
    if "." not in host:
        return True                                  # одно имя без точки — внутренняя сеть
    try:
        ipaddress.ip_address(host)
        return True                                  # голый IP
    except ValueError:
        pass
    return p.port not in (None, 80, 443)


def classify_risk(step: dict) -> str:
    """
    Возвращает 'high' | 'low' | 'normal'.
    high   — необратимые/файловые операции ИЛИ опасные слова — ВСЕГДА
             требуют явного подтверждения
    low    — открыть сайт, или открыть заведомо безопасную программу
             (браузер/блокнот/калькулятор/проводник) — кандидат на
             авто-подтверждение
    normal — всё остальное (клик, ввод текста, клавиши, open_app для
             неизвестной программы)
    """
    action = step.get("action")
    args = step.get("args", {})

    if action in HIGH_RISK_ACTIONS:
        return "high"

    text_blob = f"{action} {args}".lower()
    if any(word in text_blob for word in config.get("dangerous_keywords")):
        return "high"

    if action == "open_url" and _is_local_or_odd_url(str(args.get("url", ""))):
        return "normal"          # локальные адреса, IP, адреса с логином — не «заведомо безопасный сайт», нужно подтверждение

    if action in LOW_RISK_ACTIONS:
        return "low"

    if action == "open_app":
        app_name = str(args.get("app_name", "")).strip().lower()
        return "low" if app_name in SAFE_APP_NAMES else "normal"

    return "normal"


def highest_risk(steps: list) -> str:
    """Наивысший уровень риска среди всех шагов плана."""
    levels = [classify_risk(s) for s in steps]
    if "high" in levels:
        return "high"
    if "normal" in levels:
        return "normal"
    return "low"
