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
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.2)
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass


def type_text(text: str, sensitive: bool = False):
    if sensitive:
        log("Ввожу скрытый текст (похоже на пароль/личные данные) — содержимое не логируется")
    else:
        log(f"Ввожу текст: {text[:50]}{'...' if len(text) > 50 else ''}")

    if text.isascii() and len(text) <= 200:
        pyautogui.write(text, interval=0.02)
    else:
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
    elif action == "create_file":
        create_file(args["path"], args.get("content", ""))
    elif action == "move_file":
        move_file(args["src"], args["dst"])
    else:
        raise ValueError(f"Неизвестное действие: {action}")
