"""
Надёжная мышь для Windows (ctypes). pyautogui «телепортирует» курсор и сразу кликает — часть окон
(Windows 11, Electron, игры) при этом не получает настоящего движения и клик «не срабатывает».
Здесь курсор плавно ЕДЕТ к точке (с настоящими событиями движения), ненадолго замирает и только потом
нажимает. Вне Windows (или при ошибке) вызывающий код использует pyautogui.
"""
import time

from logger import log

try:
    import ctypes
    from ctypes import wintypes
    _user32 = ctypes.windll.user32
    AVAILABLE = True
except Exception:          # не Windows
    _user32 = None
    AVAILABLE = False

MOVE, LEFTDOWN, LEFTUP, RIGHTDOWN, RIGHTUP = 0x0001, 0x0002, 0x0004, 0x0008, 0x0010


def position():
    pt = wintypes.POINT()
    _user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def move_to(x: int, y: int, duration: float = 0.25):
    """Плавно перемещает курсор в (x, y) и возвращает фактическое положение."""
    x, y = int(x), int(y)
    sx, sy = position()
    dist = max(abs(x - sx), abs(y - sy))
    steps = max(6, min(40, dist // 40))
    for i in range(1, steps + 1):
        t = i / steps
        t = t * t * (3 - 2 * t)                                   # плавный разгон/торможение
        _user32.SetCursorPos(int(sx + (x - sx) * t), int(sy + (y - sy) * t))
        time.sleep(duration / steps)
    _user32.SetCursorPos(x, y)
    # настоящее событие движения на месте: окна обновляют «наведение» и фокус только по нему
    _user32.mouse_event(MOVE, 1, 0, 0, 0)
    _user32.mouse_event(MOVE, -1 & 0xFFFFFFFF, 0, 0, 0)
    time.sleep(0.06)
    return position()


def click(x: int, y: int, button: str = "left", clicks: int = 1):
    ax, ay = move_to(x, y)
    if abs(ax - x) > 3 or abs(ay - y) > 3:
        log(f"Мышь: хотел ({x}, {y}), курсор оказался в ({ax}, {ay}) — возможно, масштаб экрана или чужие права окна.")
    down, up = (LEFTDOWN, LEFTUP) if button == "left" else (RIGHTDOWN, RIGHTUP)
    for i in range(clicks):
        _user32.mouse_event(down, 0, 0, 0, 0)
        time.sleep(0.06)
        _user32.mouse_event(up, 0, 0, 0, 0)
        if i + 1 < clicks:
            time.sleep(0.08)
    return ax, ay


def screen_info() -> dict:
    """Размер экрана и масштаб — чтобы понять, совпадают ли координаты."""
    info = {}
    try:
        info["w"] = _user32.GetSystemMetrics(0)
        info["h"] = _user32.GetSystemMetrics(1)
        try:
            info["dpi"] = _user32.GetDpiForSystem()
        except Exception:
            info["dpi"] = 96
    except Exception:
        pass
    return info
