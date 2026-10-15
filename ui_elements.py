"""
Список элементов активного окна (кнопки, поля, пункты меню) через Windows UI Automation.
Даётся ИИ рядом со скриншотом — чтобы нажимать точнее. Это ТОЛЬКО добавка: в играх, браузерах и
программах, которые рисуют всё сами, список будет пустым — тогда ИИ работает по картинке, как раньше.
Любая ошибка или зависание (ограничение по времени) тихо дают пустой результат.
"""
import threading

import screen_utils
from logger import log

GRID = 1000
MAX_ELEMENTS = 60
TIME_LIMIT_S = 3.0
USEFUL = {"ButtonControl", "EditControl", "MenuItemControl", "ListItemControl", "TabItemControl",
          "CheckBoxControl", "ComboBoxControl", "HyperlinkControl", "RadioButtonControl",
          "TreeItemControl", "SplitButtonControl", "DataItemControl"}
_NAMES = {"ButtonControl": "кнопка", "EditControl": "поле ввода", "MenuItemControl": "пункт меню",
          "ListItemControl": "элемент списка", "TabItemControl": "вкладка", "CheckBoxControl": "флажок",
          "ComboBoxControl": "выпадающий список", "HyperlinkControl": "ссылка",
          "RadioButtonControl": "переключатель", "TreeItemControl": "элемент дерева",
          "SplitButtonControl": "кнопка", "DataItemControl": "строка"}


def _collect(out: list, screen_w: int, screen_h: int, max_depth: int = 9):
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        root = auto.GetForegroundControl()
        if root is None:
            return
        title = (root.Name or "")[:60]
        out.append(f"Активное окно: «{title}»")
        for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=max_depth):
            if len(out) > MAX_ELEMENTS:
                break
            kind = ctrl.ControlTypeName
            if kind not in USEFUL or ctrl.IsOffscreen:
                continue
            name = (ctrl.Name or "").strip().replace("\n", " ")[:50]
            if not name and kind != "EditControl":
                continue
            r = ctrl.BoundingRectangle
            if r.width() <= 0 or r.height() <= 0:
                continue
            x = int(round(r.xcenter() / max(1, screen_w - 1) * GRID))
            y = int(round(r.ycenter() / max(1, screen_h - 1) * GRID))
            if 0 <= x <= GRID and 0 <= y <= GRID:
                out.append(f"- {_NAMES.get(kind, kind)} «{name}»: x={x}, y={y}")


# Браузеры, Electron-приложения и игры: обход их дерева UI Automation очень медленный, включает у браузера режим
# доступности (он начинает тормозить) и может подвесить компьютер — для них список не собираем, ИИ смотрит на картинку.
BROWSERS = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "vivaldi.exe", "yandex.exe"}
BROWSER_TIME_S = 1.2     # у браузеров обход короткий и неглубокий: вкладки и адресная строка нужны, всё дерево страницы — нет
BROWSER_DEPTH = 6
SKIP_PROCESSES = {"discord.exe", "slack.exe", "teams.exe", "ms-teams.exe", "code.exe", "telegram.exe", "spotify.exe",
                  "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "whatsapp.exe", "viber.exe"}
_busy = threading.Event()        # предыдущий обход ещё идёт в фоне — новый не запускаем


def _foreground_process() -> str:
    try:
        import ctypes
        import psutil
        hwnd = ctypes.windll.user32.GetForegroundWindow()
        pid = ctypes.c_ulong()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        return psutil.Process(pid.value).name().lower()
    except Exception:
        return ""


def describe_foreground() -> str:
    """Текст со списком элементов активного окна или '' (нет данных / не Windows / ошибка)."""
    out: list = []
    proc = _foreground_process()
    if _busy.is_set() or proc in SKIP_PROCESSES:
        return ""
    browser = proc in BROWSERS
    try:
        w, h = screen_utils.get_screen_size()
        def run():
            _busy.set()
            try:
                _safe(out, w, h, BROWSER_DEPTH if browser else 9)
            finally:
                _busy.clear()
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(BROWSER_TIME_S if browser else TIME_LIMIT_S)
        if t.is_alive():
            log(f"UI Automation: обход {proc or 'окна'} не уложился в лимит времени, беру то, что успел")
    except Exception:
        return ""
    return "\n".join(out) if len(out) > 1 else ""


def _safe(out, w, h, max_depth=9):
    try:
        _collect(out, w, h, max_depth)
    except Exception as e:      # нет модуля, не Windows, COM-ошибка…
        log(f"UI Automation недоступен: {type(e).__name__}")
