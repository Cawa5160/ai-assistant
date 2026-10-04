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


def try_quick_plan(command: str):
    """Возвращает план {"summary", "steps"} или None, если команда не простая."""
    m = _PATTERN.match(command or "")
    if not m:
        return None
    app = APPS[m.group("app").lower()]
    steps = [{"action": "open_app", "args": {"app_name": app}}]
    summary = f"Открыть «{m.group('app')}»"
    text = (m.group("text") or "").strip().strip("«»\"'“”").strip()
    if text:
        steps += [{"action": "wait", "args": {"seconds": 2.5}},
                  {"action": "type_text", "args": {"text": text}}]
        summary += f" и напечатать: {text[:60]}"
    return {"summary": summary, "steps": steps, "need_vision": False}
