"""
Шаблоны задач: набор быстрых команд под одним именем — одно подтверждение на всё.
  «шаблон утро» / «запусти шаблон работа»   — выполнить
  «мои шаблоны»                              — список
  «создай шаблон вечер: открой ютуб; громче» — свой шаблон (команды через «;», только быстрые — без ИИ)
  «удали шаблон вечер»
Подходят и для расписания: в задаче можно написать «шаблон утро».
"""
import os
import re

import atomic_io
from app_paths import DATA_DIR

PATH = os.path.join(DATA_DIR, "templates.json")
MAX_USER, MAX_ITEMS = 30, 8

BUILTIN = {
    "утро": ["открой почту", "открой телеграм", "открой гугл"],
    "работа": ["открой хром", "открой блокнот", "открой почту"],
    "перерыв": ["пауза", "покажи рабочий стол"],
    "тишина": ["выключи звук", "покажи рабочий стол"],
    "вечер": ["открой ютуб", "громче"],
}

_RUN = re.compile(r"^\s*(?:запусти\s+|выполни\s+|включи\s+)?шаблон\s+(?P<name>[^\n:;]{1,30}?)\s*[.!]*\s*$", re.I)
_LIST = re.compile(r"^\s*(?:мои\s+шаблоны|список\s+шаблонов|покажи\s+шаблоны|шаблоны)\s*[.!?]*\s*$", re.I)
_CREATE = re.compile(r"^\s*(?:создай|сохрани|добавь)\s+шаблон\s+(?P<name>[^\n:;]{1,30}?)\s*[:\-]\s*(?P<items>.+)$", re.I | re.S)
_DELETE = re.compile(r"^\s*(?:удали|забудь)\s+шаблон\s+(?P<name>[^\n]{1,30}?)\s*[.!]*\s*$", re.I)


def user_templates() -> dict:
    d = atomic_io.read_json(PATH, {})
    return d if isinstance(d, dict) else {}


def all_templates() -> dict:
    return {**BUILTIN, **user_templates()}


def _plan_for(cmd: str):
    from quick_commands import try_quick_plan
    p = try_quick_plan(cmd)
    return p if p and p.get("steps") else None


def build_plan(name: str, items: list):
    """Склеивает быстрые планы команд в один (с паузами). -> (план, список_нераспознанных)."""
    steps, bad, trusted = [], [], True
    for cmd in items:
        p = _plan_for(cmd)
        if p is None:
            bad.append(cmd)
            continue
        if steps:
            steps.append({"action": "wait", "args": {"seconds": 1.5}})
        steps.extend(p["steps"])
        trusted = trusted and bool(p.get("trusted"))
    return {"summary": f"Шаблон «{name}»: " + "; ".join(items)[:120], "steps": steps,
            "need_vision": False, "trusted": trusted}, bad


def try_template_plan(command: str):
    m = _RUN.match(command or "")
    if not m:
        return None
    name = m.group("name").strip().lower()
    items = all_templates().get(name)
    if not items:
        return None
    plan, bad = build_plan(name, items)
    return plan if plan["steps"] else None


def try_template_action(command: str):
    """-> (описание, функция) или None: список / создать / удалить."""
    command = command or ""
    if _LIST.match(command):
        def show():
            lines = [f"• {n}: " + "; ".join(i) for n, i in all_templates().items()]
            return "Шаблоны:\n" + "\n".join(lines) + "\n\nЗапуск: «шаблон <имя>»."
        return ("Показать шаблоны", show)
    m = _CREATE.match(command)
    if m:
        name = m.group("name").strip().lower()
        items = [x.strip() for x in re.split(r"[;\n]+", m.group("items")) if x.strip()][:MAX_ITEMS]

        def create():
            if name in BUILTIN:
                return f"«{name}» — встроенный шаблон, выбери другое имя."
            if len(user_templates()) >= MAX_USER:
                return "Слишком много шаблонов — удали ненужные."
            _plan, bad = build_plan(name, items)
            if bad or not items:
                return "Не понял команды: " + "; ".join(bad or ["пусто"]) + ". В шаблон идут только быстрые команды " \
                       "(открой сайт/программу, звук, пауза, скриншот…)."
            d = user_templates()
            d[name] = items
            atomic_io.write_json(PATH, d)
            return f"Шаблон «{name}» сохранён ({len(items)} команд). Запуск: «шаблон {name}»."
        return (f"Создать шаблон «{name}»", create)
    m = _DELETE.match(command)
    if m:
        name = m.group("name").strip().lower()

        def delete():
            d = user_templates()
            if name in BUILTIN:
                return "Встроенные шаблоны удалить нельзя."
            if name not in d:
                return f"Шаблона «{name}» нет."
            d.pop(name)
            atomic_io.write_json(PATH, d)
            return f"Шаблон «{name}» удалён."
        return (f"Удалить шаблон «{name}»", delete)
    return None


def delete_template(name: str) -> bool:
    d = user_templates()
    if name in d:
        d.pop(name)
        atomic_io.write_json(PATH, d)
        return True
    return False


def add_template(name: str, items_text: str):
    """-> (ok, сообщение). Команды через «;»."""
    action = try_template_action(f"создай шаблон {name.strip()}: {items_text.strip()}")
    if action is None:
        return False, "Введи имя и команды через «;»."
    msg = action[1]()
    return msg.startswith("Шаблон «"), msg
