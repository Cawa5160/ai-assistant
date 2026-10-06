"""
Простой планировщик: хранит задачи вида "в такие-то дни недели, в такое-то
время — выполнить такую-то команду" и в фоне проверяет, не пора ли
запустить одну из них.

ВАЖНО: работает, только пока приложение открыто (не системный планировщик
Windows) — если приложение не запущено, задачи не сработают. Каждая
задача всё равно проходит через обычное окно подтверждения — расписание
не отменяет базовое правило "ничего без подтверждения".
"""
import os
import threading
import time
import datetime

import atomic_io
from logger import log

from app_paths import DATA_DIR as BASE_DIR
TASKS_PATH = os.path.join(BASE_DIR, "scheduled_tasks.json")

WEEKDAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]  # datetime.weekday(): 0=пн ... 6=вс

# Условия запуска: ключ -> подпись для окна. Если условие не выполнено в назначенное время, задача ждёт до WAIT_MIN минут
CONDITIONS = {
    "": "Всегда",
    "online": "Если есть интернет",
    "active:2": "Если я за компьютером (был активен последние 2 мин)",
    "idle:5": "Если компьютер не используется 5 минут",
    "idle:15": "Если компьютер не используется 15 минут",
    "app:chrome.exe": "Если запущен Chrome",
    "noapp:chrome.exe": "Если Chrome не запущен",
}
WAIT_MIN = 30

_lock = threading.Lock()
_waiting = {}          # task_id -> момент (datetime), до которого задача ждёт выполнения условия
_last_run_minute = {}  # task_id -> "YYYY-mm-dd HH:MM" последнего запуска (не запускать дважды в ту же минуту)


def _load() -> list:
    return atomic_io.read_json(TASKS_PATH, [])


def _save(tasks: list):
    atomic_io.write_json(TASKS_PATH, tasks)


def get_all() -> list:
    return _load()


def _idle_seconds() -> float:
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]
    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return (ctypes.windll.kernel32.GetTickCount() - info.dwTime) / 1000.0


def _process_running(name: str) -> bool:
    import subprocess
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {name}", "/NH"], capture_output=True, text=True,
                         creationflags=0x08000000, timeout=10).stdout
    return name.lower() in out.lower()


def check_condition(cond: str) -> bool:
    """Выполнено ли условие запуска задачи. Если проверить не удалось — считаем выполненным (не блокируем задачу)."""
    cond = cond or ""
    if not cond:
        return True
    try:
        kind, _, arg = cond.partition(":")
        if kind == "online":
            import socket
            socket.create_connection(("1.1.1.1", 53), timeout=3).close()
            return True
        if kind == "idle":
            return _idle_seconds() >= float(arg) * 60
        if kind == "active":
            return _idle_seconds() < float(arg) * 60
        if kind == "app":
            return _process_running(arg)
        if kind == "noapp":
            return not _process_running(arg)
    except OSError:
        return kind != "online"
    except Exception as e:
        log(f"Планировщик: условие «{cond}» не проверено ({e}) — выполняю.")
    return True


def add_task(command: str, days: list, time_str: str, once: bool = False, condition: str = "") -> dict:
    tasks = _load()
    new_id = max((t["id"] for t in tasks), default=0) + 1
    task = {"id": new_id, "command": command, "days": days, "time": time_str, "enabled": True}
    if once:
        task["once"] = True                 # выполнить один раз и удалить
    if condition:
        task["condition"] = condition
    tasks.append(task)
    _save(tasks)
    log(f"Добавлена задача по расписанию #{new_id}: {command} ({','.join(days)} в {time_str})")
    return task


def remove_task(task_id: int):
    tasks = [t for t in _load() if t["id"] != task_id]
    _save(tasks)


def set_enabled(task_id: int, enabled: bool):
    tasks = _load()
    for t in tasks:
        if t["id"] == task_id:
            t["enabled"] = enabled
    _save(tasks)


def _due_tasks(now: datetime.datetime) -> list:
    weekday = WEEKDAYS[now.weekday()]
    hhmm = now.strftime("%H:%M")
    minute_key = now.strftime("%Y-%m-%d %H:%M")
    due = []
    for t in _load():
        if not t.get("enabled", True):
            continue
        if weekday not in t.get("days", []):
            continue
        if t.get("time") != hhmm:
            continue
        if _last_run_minute.get(t["id"]) == minute_key:
            continue
        due.append(t)
    return due


def start(on_due_callback, on_skip=None):
    """
    Запускает фоновый поток: раз в ~20 секунд проверяет расписание и
    вызывает on_due_callback(command) для каждой подошедшей задачи.
    Условие не выполнено — задача ждёт до WAIT_MIN минут; не дождалась — on_skip(command, условие).
    """
    def _run(t):
        log(f"Планировщик: время задачи #{t['id']} — {t['command']}")
        try:
            on_due_callback(t["command"])
            if t.get("once"):
                remove_task(t["id"])
        except Exception as e:
            log(f"Ошибка при запуске запланированной задачи: {e}")

    def _loop():
        while True:
            now = datetime.datetime.now()
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            with _lock:
                due = _due_tasks(now)
                for t in due:
                    _last_run_minute[t["id"]] = minute_key
            for t in due:
                if check_condition(t.get("condition", "")):
                    _run(t)
                else:
                    _waiting[t["id"]] = now + datetime.timedelta(minutes=WAIT_MIN)
                    log(f"Планировщик: задача #{t['id']} ждёт условия «{CONDITIONS.get(t.get('condition'), t.get('condition'))}» "
                        f"(до {WAIT_MIN} мин).")
            for tid, until in list(_waiting.items()):
                task = next((x for x in _load() if x["id"] == tid and x.get("enabled", True)), None)
                if task is None:
                    _waiting.pop(tid, None)
                elif check_condition(task.get("condition", "")):
                    _waiting.pop(tid, None)
                    _run(task)
                elif now > until:
                    _waiting.pop(tid, None)
                    log(f"Планировщик: задача #{tid} пропущена — условие не выполнилось за {WAIT_MIN} мин.")
                    if on_skip:
                        try:
                            on_skip(task["command"], CONDITIONS.get(task.get("condition"), task.get("condition")))
                        except Exception:
                            pass
            time.sleep(20)

    threading.Thread(target=_loop, daemon=True).start()
    log("Планировщик задач запущен.")
