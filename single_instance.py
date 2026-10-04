"""
Один экземпляр приложения — и "разбудить" его, если запустили повторно.

Программа живёт в трее, поэтому повторный клик по ярлыку не должен молча ничего
не делать (и тем более запускать вторую копию): вторая копия находит первую и
просит показать окно. Как: в файле-блокировке лежит PID и порт локального
сокета первого экземпляра (только 127.0.0.1); второй экземпляр отправляет туда
слово SHOW и тихо завершается. Если PID из файла мёртв (сбой/убитый процесс) —
блокировка считается устаревшей и просто перезаписывается.
"""
import json
import os
import socket
import threading

from app_paths import DATA_DIR
from logger import log

LOCK_PATH = os.path.join(DATA_DIR, ".instance.lock")

_server = None


def _pid_running(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        return False


def _read_lock() -> dict:
    try:
        with open(LOCK_PATH, "r", encoding="utf-8") as f:
            raw = f.read().strip()
    except OSError:
        return {}
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {"pid": int(data)}
    except (ValueError, TypeError):
        return {}


def _write_lock(data: dict):
    try:
        with open(LOCK_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f)
    except OSError as e:
        log(f"Не удалось записать файл-блокировку единственного экземпляра: {e}")


def acquire() -> bool:
    """True — это единственный экземпляр (и он теперь владеет блокировкой). False — уже запущен другой."""
    pid = _read_lock().get("pid")
    if isinstance(pid, int) and _pid_running(pid):
        return False
    _write_lock({"pid": os.getpid()})
    return True


def start_listener(on_show):
    """Слушает локальный порт: когда приходит SHOW — вызывает on_show() (обычно: показать окно)."""
    global _server
    try:
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(5)
    except OSError as e:
        log(f"Не удалось открыть порт для повторного запуска: {e}")
        return
    _server = srv
    data = _read_lock()
    data["port"] = srv.getsockname()[1]
    _write_lock(data)

    def loop():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return                         # сокет закрыт при выходе
            try:
                conn.settimeout(1.0)
                if conn.recv(16).strip() == b"SHOW":
                    on_show()
            except Exception:
                pass
            finally:
                conn.close()

    threading.Thread(target=loop, daemon=True).start()


def signal_existing() -> bool:
    """Просит уже запущенный экземпляр показать окно. True — получилось."""
    port = _read_lock().get("port")
    if not isinstance(port, int):
        return False
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=2) as conn:
            conn.sendall(b"SHOW")
        return True
    except OSError:
        return False


def release():
    global _server
    if _server is not None:
        try:
            _server.close()
        except OSError:
            pass
        _server = None
    try:
        os.remove(LOCK_PATH)
    except OSError:
        pass
