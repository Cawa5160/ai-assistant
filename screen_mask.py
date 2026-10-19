"""
Закрашивание полей паролей на скриншоте перед отправкой в облако (Gemini).
Поля ищем через Windows UI Automation (IsPassword=True). Любая ошибка, нет пакета uiautomation, не Windows
или превышение лимита времени — тихо пустой список (в лог пишем), программа не падает.
Координаты прямоугольников — в тех же единицах, что у pyautogui.size() / ui_elements (экранные пиксели).
"""
import threading

import config
from logger import log

TIME_LIMIT_S = 0.5
MAX_DEPTH = 14
PAD = 4                      # отступ вокруг поля, пикселей снимка
_busy = threading.Event()    # предыдущий обход ещё идёт в фоне — новый не запускаем


def enabled() -> bool:
    """Опция mask_password_fields; не задана (None) — считаем включённой."""
    try:
        v = config.get("mask_password_fields")
    except Exception:
        return True
    return True if v is None else bool(v)


def _walk(out: list, stop: threading.Event):
    import uiautomation as auto
    with auto.UIAutomationInitializerInThread():
        root = auto.GetForegroundControl()
        if root is None:
            return
        for ctrl, _depth in auto.WalkControl(root, includeTop=True, maxDepth=MAX_DEPTH):
            if stop.is_set():
                return
            try:
                if ctrl.ControlTypeName != "EditControl" or ctrl.IsOffscreen or not ctrl.IsPassword:
                    continue
                r = ctrl.BoundingRectangle
                if r.width() > 0 and r.height() > 0:
                    out.append((int(r.left), int(r.top), int(r.right), int(r.bottom)))
            except Exception:
                continue


def find_password_rects() -> list:
    """Список (left, top, right, bottom) видимых полей пароля в экранных координатах. Никогда не бросает."""
    out: list = []
    if _busy.is_set():
        return out
    stop = threading.Event()

    def run():
        _busy.set()
        try:
            _walk(out, stop)
        except Exception as e:           # нет модуля, не Windows, COM-ошибка…
            log(f"Маскирование паролей: UI Automation недоступен ({type(e).__name__})")
        finally:
            _busy.clear()

    try:
        t = threading.Thread(target=run, daemon=True)
        t.start()
        t.join(TIME_LIMIT_S)
        if t.is_alive():
            stop.set()
            log("Маскирование паролей: обход не уложился в лимит времени, беру найденное")
    except Exception as e:
        log(f"Маскирование паролей: ошибка поиска ({type(e).__name__})")
        return []
    return list(out)


def mask_image(img, rects, pad: int = PAD, scale_x: float = 1.0, scale_y: float = 1.0):
    """Возвращает НОВУЮ картинку с чёрными прямоугольниками (rects умножаются на scale_x/scale_y)."""
    from PIL import ImageDraw
    res = img.copy()
    if not rects:
        return res
    draw = ImageDraw.Draw(res)
    for l, t, r, b in rects:
        draw.rectangle([int(l * scale_x) - pad, int(t * scale_y) - pad,
                        int(r * scale_x) + pad, int(b * scale_y) + pad], fill=(0, 0, 0))
    return res


def mask_screenshot(img, screen_size=None):
    """find_password_rects + mask_image. screen_size=(w, h) в координатах UIA — для пересчёта, если снимок другого размера."""
    if not enabled():
        return img
    rects = find_password_rects()
    if not rects:
        return img
    sx = sy = 1.0
    if screen_size and screen_size[0] and screen_size[1]:
        sx, sy = img.width / screen_size[0], img.height / screen_size[1]
    log(f"Маскирование паролей: закрашено полей — {len(rects)}")
    return mask_image(img, rects, scale_x=sx, scale_y=sy)
