"""
Атомарная запись JSON-файлов: сначала во временный файл, потом
os.replace() — так файл не может оказаться повреждён, если запись
прервётся на середине или два потока запишут почти одновременно.
"""
import json
import os
import threading

_lock = threading.Lock()


def write_json(path: str, data):
    with _lock:
        tmp_path = path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)


def read_json(path: str, default):
    with _lock:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return default
