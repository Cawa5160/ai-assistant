"""
Журнал действий: каждое выполненное приложением действие (клик, ввод, файл…) одной строкой
в audit_log.jsonl рядом с программой. Нужен, чтобы можно было проверить, что именно сделал ИИ.
Введённый текст НЕ сохраняется — только его длина; пути проходят через redact.
"""
import json
import os
import time

import config
from privacy import redact

_MAX_BYTES = 1_000_000


def _path():
    return os.path.join(config.BASE_DIR, "audit_log.jsonl")


def record(step: dict):
    """Никогда не бросает исключений: журнал не должен ломать выполнение."""
    try:
        action = step.get("action")
        args = dict(step.get("args", {}))
        if action == "type_text":
            args = {"символов": len(str(args.get("text", ""))), "sensitive": bool(args.get("sensitive", False))}
        else:
            args.pop("content", None)
            args = {k: redact(v) if isinstance(v, str) else v for k, v in args.items()}
        line = json.dumps({"t": time.strftime("%Y-%m-%d %H:%M:%S"), "action": action, "args": args},
                          ensure_ascii=False)
        p = _path()
        if os.path.exists(p) and os.path.getsize(p) > _MAX_BYTES:
            os.replace(p, p + ".old")
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def tail(n: int = 30) -> list:
    try:
        with open(_path(), encoding="utf-8") as f:
            return f.read().splitlines()[-n:]
    except Exception:
        return []
