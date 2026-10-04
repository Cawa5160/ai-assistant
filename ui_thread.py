"""
Мост между рабочими потоками и единственным окном Tk.

Tkinter нельзя трогать из чужих потоков. Раньше каждое диалоговое окно создавало
собственный tk.Tk() в своём потоке — для одного окна это терпимо, но когда есть
постоянное главное окно, такая схема ломается. Теперь:

  * весь интерфейс живёт в ОДНОМ потоке (главном), у него один корень Tk;
  * рабочие потоки (запрос к ИИ, автопилот, трей, горячие клавиши) не трогают
    виджеты напрямую, а просят интерфейсный поток через call() / post();
  * call() блокирует вызывающий поток, пока окно не вернёт результат — поэтому
    остальной код по-прежнему пишется как "показал диалог -> получил ответ".
"""
import functools
import queue
import threading

from logger import log

_root = None
_ui_ident = None
_alive = False
_queue = queue.Queue()


def init(root):
    """Вызывается один раз из главного потока после создания корневого окна."""
    global _root, _ui_ident, _alive
    _root, _ui_ident, _alive = root, threading.get_ident(), True
    _pump()


def shutdown():
    """Приложение закрывается: будим всех, кто ждёт ответа от интерфейса."""
    global _alive
    _alive = False


def root():
    return _root


def is_ui_thread() -> bool:
    return _root is None or threading.get_ident() == _ui_ident


def _pump():
    if _root is None or not _alive:
        return
    try:
        _root.after(30, _pump)          # сначала планируем следующий проход — вложенные циклы не сломают очередь
    except Exception:
        return
    while True:
        try:
            fn = _queue.get_nowait()
        except queue.Empty:
            break
        try:
            fn()
        except Exception as e:           # ошибка в одной задаче не должна убить всю очередь
            log(f"Ошибка в задаче интерфейса: {e}")


def post(fn, *args, **kwargs):
    """Выполнить fn в интерфейсном потоке, не дожидаясь результата."""
    if _root is None:
        fn(*args, **kwargs)
        return
    _queue.put(lambda: fn(*args, **kwargs))


def call(fn, *args, **kwargs):
    """Выполнить fn в интерфейсном потоке и вернуть результат (исключения пробрасываются)."""
    if _root is None or threading.get_ident() == _ui_ident:
        return fn(*args, **kwargs)

    done = threading.Event()
    box = {}

    def run():
        try:
            box["value"] = fn(*args, **kwargs)
        except BaseException as e:      # noqa: BLE001 — пробрасываем ровно то, что случилось
            box["error"] = e
        finally:
            done.set()

    _queue.put(run)
    while not done.wait(0.3):
        if not _alive:
            raise RuntimeError("Приложение закрывается.")
    if "error" in box:
        raise box["error"]
    return box.get("value")


def on_ui(fn):
    """Декоратор: функция всегда выполняется в интерфейсном потоке."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        return call(fn, *args, **kwargs)
    return wrapper
