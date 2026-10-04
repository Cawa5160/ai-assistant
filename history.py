"""
История последних команд — чтобы можно было посмотреть, что и когда
делал ассистент, и одним кликом повторить последнюю команду.
Хранится в history.json рядом со скриптом, максимум MAX_ENTRIES записей.

Команда и summary сохраняются ПОСЛЕ маскировки похожих на пароли/коды
фрагментов (см. privacy.redact) — это значит, что "повторить последнюю
команду" для запроса с паролем повторит уже замаскированный текст, а
не настоящий пароль. Это осознанный компромисс: чувствительные команды
не должны тихо реплеиться из истории.
"""
import os
import datetime

import atomic_io
from privacy import redact

from app_paths import DATA_DIR as BASE_DIR
HISTORY_PATH = os.path.join(BASE_DIR, "history.json")
MAX_ENTRIES = 20


def add(command: str, summary: str, status: str):
    entries = atomic_io.read_json(HISTORY_PATH, [])
    entries.append({
        "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "command": redact(command),
        "summary": redact(summary),
        "status": status,
    })
    entries = entries[-MAX_ENTRIES:]
    atomic_io.write_json(HISTORY_PATH, entries)


def get_all() -> list:
    """Новые записи первыми."""
    return list(reversed(atomic_io.read_json(HISTORY_PATH, [])))


def get_last_command() -> str:
    entries = atomic_io.read_json(HISTORY_PATH, [])
    return entries[-1]["command"] if entries else ""
