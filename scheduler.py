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

_lock = threading.Lock()
_last_run_minute = {}  # task_id -> "YYYY-mm-dd HH:MM" последнего запуска (не запускать дважды в ту же минуту)


def _load() -> list:
    return atomic_io.read_json(TASKS_PATH, [])


def _save(tasks: list):
    atomic_io.write_json(TASKS_PATH, tasks)


def get_all() -> list:
    return _load()


def add_task(command: str, days: list, time_str: str) -> dict:
    tasks = _load()
    new_id = max((t["id"] for t in tasks), default=0) + 1
    task = {"id": new_id, "command": command, "days": days, "time": time_str, "enabled": True}
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


def start(on_due_callback):
    """
    Запускает фоновый поток: раз в ~20 секунд проверяет расписание и
    вызывает on_due_callback(command) для каждой подошедшей задачи.
    """
    def _loop():
        while True:
            now = datetime.datetime.now()
            minute_key = now.strftime("%Y-%m-%d %H:%M")
            with _lock:
                due = _due_tasks(now)
                for t in due:
                    _last_run_minute[t["id"]] = minute_key
            for t in due:
                log(f"Планировщик: время задачи #{t['id']} — {t['command']}")
                try:
                    on_due_callback(t["command"])
                except Exception as e:
                    log(f"Ошибка при запуске запланированной задачи: {e}")
            time.sleep(20)

    threading.Thread(target=_loop, daemon=True).start()
    log("Планировщик задач запущен.")
