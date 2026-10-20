import base64
import io
import pyautogui
from PIL import Image, ImageChops


def take_screenshot(max_width: int = 1280, mask: bool = True):
    """
    Делает скриншот экрана, уменьшает его для экономии токенов и
    возвращает (base64_jpeg, scale).

    scale нужен, чтобы пересчитать координаты клика: Gemini видит
    УМЕНЬШЕННЫЙ скриншот и возвращает координаты относительно НЕГО,
    а кликать нужно по РЕАЛЬНОМУ экрану — без пересчёта клики были бы
    систематически смещены на любом мониторе шире max_width (то есть
    почти на любом современном).
    """
    screenshot = pyautogui.screenshot()
    try:                                  # перед отправкой в облако закрашиваем поля паролей (в игре не нужно — это медленно)
        if not mask:
            return _finish_screenshot(screenshot, max_width)
        import screen_mask
        screenshot = screen_mask.mask_screenshot(screenshot, get_screen_size())
    except Exception as e:                # не вышло — отправляем как раньше
        try:
            from logger import log
            log(f"Маскирование паролей не сработало: {type(e).__name__}")
        except Exception:
            pass
    return _finish_screenshot(screenshot, max_width)


def _finish_screenshot(screenshot, max_width):
    scale = 1.0
    if screenshot.width > max_width:
        scale = screenshot.width / max_width
        ratio = max_width / screenshot.width
        new_size = (max_width, int(screenshot.height * ratio))
        screenshot = screenshot.resize(new_size, Image.BILINEAR)
    buffer = io.BytesIO()
    screenshot.save(buffer, format="JPEG", quality=70)
    b64 = base64.b64encode(buffer.getvalue()).decode("utf-8")
    return b64, scale


def take_screenshot_image(max_width: int = 400) -> Image.Image:
    """
    Возвращает уменьшенный чёрно-белый скриншот как объект PIL.Image —
    для локального сравнения "до/после" клика (без обращения к ИИ).
    """
    screenshot = pyautogui.screenshot()
    if screenshot.width > max_width:
        ratio = max_width / screenshot.width
        new_size = (max_width, int(screenshot.height * ratio))
        screenshot = screenshot.resize(new_size, Image.LANCZOS)
    return screenshot.convert("L")


def take_screenshot_color(max_width: int = 1600) -> Image.Image:
    """Цветной скриншот для отправки человеку (в Telegram)."""
    shot = pyautogui.screenshot()
    if shot.width > max_width:
        shot = shot.resize((max_width, int(shot.height * max_width / shot.width)), Image.LANCZOS)
    return shot.convert("RGB")


def get_screen_size():
    """(ширина, высота) основного экрана в тех же координатах, в которых кликает pyautogui."""
    size = pyautogui.size()
    return int(size[0]), int(size[1])


def screens_differ(before, after, threshold: float = 0.01) -> bool:
    """
    Грубая локальная проверка "экран изменился?" по двум серым уменьшенным
    скриншотам: считаем долю пикселей, яркость которых заметно (> ~15/255)
    отличается. Без обращения к ИИ — не тратит лимиты.
    """
    try:
        hist = ImageChops.difference(before, after).histogram()
        total = before.size[0] * before.size[1]
        if total == 0:
            return True
        return (sum(hist[15:]) / total) > threshold
    except Exception:
        return True  # не смогли сравнить — не мешаем работе
