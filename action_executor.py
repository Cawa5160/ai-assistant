"""
Выполняет конкретные действия на компьютере: клики, ввод текста,
запуск программ, работа с файлами.
Каждое опасное действие проверяется через is_dangerous() и
требует отдельного подтверждения пользователя (см. gui.py).
"""
import os
import shutil
import time
import webbrowser
import pyautogui
import audit
import psutil

import config
from logger import log


def sync_pyautogui_settings():
    """Перечитывает настройки безопасности из config (вызывать после смены настроек в GUI)."""
    pyautogui.FAILSAFE = config.get("pyautogui_failsafe")
    pyautogui.PAUSE = config.get("pyautogui_pause")


sync_pyautogui_settings()


def is_dangerous(action_description: str) -> bool:
    """Проверяет, содержит ли описание действия опасные ключевые слова."""
    text = action_description.lower()
    return any(word in text for word in config.get("dangerous_keywords"))


def is_path_allowed(path: str) -> bool:
    """
    Проверяет, что путь находится ВНУТРИ одной из разрешённых папок —
    не просто "начинается с похожей строки" (иначе 'C:\\Downloads' пропустил
    бы 'C:\\DownloadsEvil'), а именно внутри границы каталога.
    """
    if not path:
        return False
    abs_path = os.path.abspath(path)
    for folder in config.get("allowed_folders"):
        abs_folder = os.path.abspath(folder)
        if abs_path == abs_folder or abs_path.startswith(abs_folder + os.sep):
            return True
    return False


# ---------- Действия с приложениями и экраном ----------

def open_app(app_name: str):
    """
    Открывает приложение через os.startfile — это нативный вызов Windows
    (ShellExecute), БЕЗ участия командной оболочки (shell=True), поэтому
    спецсимволы в имени не могут быть истолкованы как отдельная команда.
    """
    log(f"Открываю приложение: {app_name}")
    try:
        os.startfile(app_name)
    except OSError as e:
        raise RuntimeError(f"Не удалось открыть '{app_name}': {e}")


def open_url(url: str):
    """Открывает URL через стандартный модуль webbrowser — тоже без участия shell."""
    log(f"Открываю URL: {url}")
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)


def click(x: int, y: int):
    log(f"Клик по координатам ({x}, {y})")
    pyautogui.click(x, y)


def double_click(x: int, y: int):
    log(f"Двойной клик по координатам ({x}, {y})")
    pyautogui.doubleClick(x, y)


def right_click(x: int, y: int):
    log(f"Правый клик по координатам ({x}, {y})")
    pyautogui.rightClick(x, y)


def scroll(amount: float):
    """amount — в "щелчках" колеса: положительное вверх, отрицательное вниз (1 щелчок = 120 единиц Windows)."""
    log(f"Прокрутка: {amount}")
    pyautogui.scroll(int(amount * 120))


def wait(seconds: float):
    log(f"Пауза {seconds} с")
    time.sleep(max(0.0, min(float(seconds), 15.0)))


def _paste_text(text: str):
    """
    Ввод через буфер обмена. pyautogui.write умеет печатать только символы,
    которые есть на клавиатурной раскладке "как клавиши", и молча пропускает
    остальное — русский текст так вообще не набирается. Вставка через Ctrl+V
    работает с любым Unicode. Прежнее содержимое буфера возвращаем на место.
    """
    import pyperclip
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    pyperclip.copy(text)
    time.sleep(0.15)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.8)         # приложение должно успеть прочитать буфер, прежде чем мы вернём старое содержимое
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass


def _type_unicode(text: str):
    """
    Печатает текст "как клавиатура" через SendInput + KEYEVENTF_UNICODE: работает с любым языком и
    раскладкой и не зависит от буфера обмена (вставка через Ctrl+V в новом Блокноте Windows 11
    иногда не срабатывала). Бросает исключение, если это не Windows — тогда вызывающий код
    переключается на вставку через буфер.
    """
    import ctypes
    ulong_ptr = ctypes.c_size_t

    class MOUSEINPUT(ctypes.Structure):
        _fields_ = [("dx", ctypes.c_int32), ("dy", ctypes.c_int32), ("mouseData", ctypes.c_uint32),
                    ("dwFlags", ctypes.c_uint32), ("time", ctypes.c_uint32), ("dwExtraInfo", ulong_ptr)]

    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", ctypes.c_uint16), ("wScan", ctypes.c_uint16), ("dwFlags", ctypes.c_uint32),
                    ("time", ctypes.c_uint32), ("dwExtraInfo", ulong_ptr)]

    class HARDWAREINPUT(ctypes.Structure):
        _fields_ = [("uMsg", ctypes.c_uint32), ("wParamL", ctypes.c_uint16), ("wParamH", ctypes.c_uint16)]

    class _Union(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("mi", MOUSEINPUT), ("hi", HARDWAREINPUT)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", ctypes.c_uint32), ("u", _Union)]

    send = ctypes.windll.user32.SendInput          # AttributeError вне Windows — это нормально
    KEYUP, UNICODE, VK_RETURN, VK_TAB = 0x0002, 0x0004, 0x0D, 0x09

    def key(vk=0, scan=0, flags=0):
        inp = INPUT(type=1)
        inp.u.ki = KEYBDINPUT(vk, scan, flags, 0, 0)
        if send(1, ctypes.byref(inp), ctypes.sizeof(INPUT)) != 1:
            raise OSError("SendInput не принял событие")

    for ch in text.replace("\r\n", "\n"):
        if ch == "\n":
            key(vk=VK_RETURN); key(vk=VK_RETURN, flags=KEYUP)
        elif ch == "\t":
            key(vk=VK_TAB); key(vk=VK_TAB, flags=KEYUP)
        else:
            raw = ch.encode("utf-16-le")
            for i in range(0, len(raw), 2):          # символы вне BMP идут парой суррогатов
                unit = int.from_bytes(raw[i:i + 2], "little")
                key(scan=unit, flags=UNICODE); key(scan=unit, flags=UNICODE | KEYUP)
        time.sleep(0.02)


def _user_folder(kind: str) -> str:
    """Настоящая папка «Рабочий стол» / «Изображения» (учитывает OneDrive и перенос папок)."""
    names = {"desktop": ("Desktop", "Desktop"), "pictures": ("My Pictures", "Pictures")}
    reg_name, plain = names[kind]
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as key:
            value, _ = winreg.QueryValueEx(key, reg_name)
        path = os.path.expandvars(value)
        if os.path.isdir(path):
            return path
    except Exception:
        pass
    return os.path.join(os.path.expanduser("~"), plain)


# main.py ставит сюда пару (спрятать_окна, вернуть_окна), чтобы приложение не попало на свой же скриншот
capture_hooks = None


def save_screenshot(where: str = "desktop") -> str:
    """Снимок экрана в PNG на рабочий стол или в «Изображения → Снимки экрана». Возвращает путь к файлу."""
    folder = _user_folder(where)
    if where == "pictures":
        folder = os.path.join(folder, "Screenshots")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, time.strftime("Скриншот %Y-%m-%d %H-%M-%S.png"))
    hooks = capture_hooks
    try:
        if hooks:
            hooks[0]()
        time.sleep(0.4)
        image = pyautogui.screenshot()
    finally:
        if hooks:
            hooks[1]()
    image.save(path)
    log(f"Скриншот сохранён: {path}")
    return path


def type_text(text: str, sensitive: bool = False):
    if sensitive:
        log("Ввожу скрытый текст (похоже на пароль/личные данные) — содержимое не логируется")
    else:
        log(f"Ввожу текст: {text[:50]}{'...' if len(text) > 50 else ''}")

    if text.isascii() and len(text) <= 200:
        pyautogui.write(text, interval=0.02)
        return
    if len(text) <= 300:
        try:
            _type_unicode(text)
            return
        except Exception as e:
            log(f"Прямой ввод не удался ({type(e).__name__}), вставляю через буфер обмена")
    _paste_text(text)


def press_key(key: str):
    log(f"Нажимаю клавишу: {key}")
    pyautogui.press(key)


def hotkey(*keys):
    log(f"Комбинация клавиш: {'+'.join(keys)}")
    pyautogui.hotkey(*keys)


# ---------- Работа с файлами (только в разрешённых папках) ----------

def create_file(path: str, content: str = ""):
    if not is_path_allowed(path):
        raise PermissionError(f"Путь вне разрешённых папок: {path}")
    log(f"Создаю файл: {path}")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)


def move_file(src: str, dst: str):
    if not (is_path_allowed(src) and is_path_allowed(dst)):
        raise PermissionError("Путь вне разрешённых папок")
    log(f"Перемещаю файл: {src} -> {dst}")
    shutil.move(src, dst)


def list_running_apps():
    return [p.name() for p in psutil.process_iter(['name'])]


# ---------- Единая точка выполнения шага плана ----------

def run_step(step: dict):
    """
    Выполняет один (уже проверенный plan_validator) шаг. Один диспетчер на
    всех: им пользуются и обычный план (main.py), и автопилот (agent_loop.py).
    """
    action = step.get("action")
    args = dict(step.get("args", {}))
    audit.record(step)

    if action == "open_app":
        open_app(args["app_name"])
    elif action == "open_url":
        open_url(args["url"])
    elif action == "click":
        click(int(args["x"]), int(args["y"]))
    elif action == "double_click":
        double_click(int(args["x"]), int(args["y"]))
    elif action == "right_click":
        right_click(int(args["x"]), int(args["y"]))
    elif action == "type_text":
        type_text(args["text"], bool(args.get("sensitive", False)))
    elif action == "press_key":
        press_key(args["key"])
    elif action == "hotkey":
        hotkey(*args["keys"])
    elif action == "scroll":
        scroll(args["amount"])
    elif action == "wait":
        wait(args["seconds"])
    elif action == "save_screenshot":
        save_screenshot(args["where"])
    elif action == "create_file":
        create_file(args["path"], args.get("content", ""))
    elif action == "move_file":
        move_file(args["src"], args["dst"])
    else:
        raise ValueError(f"Неизвестное действие: {action}")
