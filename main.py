"""
AI-ассистент для управления компьютером (Windows 11).

Запуск:  python main.py              — с окном
         python main.py --background — сразу в трее (так стартует автозагрузка)

Как устроено приложение:
  * Главный поток — главное окно (app_window.py). Крестик его СВОРАЧИВАЕТ: программа
    продолжает работать в трее (горячая клавиша, расписание, голосовые команды).
  * Трей (pystray) работает в своём потоке. Двойной клик по значку — открыть окно.
  * Команды выполняются в рабочих потоках; всё, что нужно показать на экране, они
    просят у интерфейсного потока (ui_thread.py).

Как обрабатывается команда:
  1. Есть Ollama и команда простая -> короткий план с подтверждением.
  2. Иначе -> АВТОПИЛОТ (agent_loop.py): ИИ смотрит на экран и делает по одному шагу с
     проверкой результата. Один раз подтверждаешь запуск; опасные шаги (файлы, установка,
     удаление) спрашиваются отдельно каждый раз; остановить можно кнопкой или рывком мыши в угол.

Одновременно выполняется только ОДНА команда, второй экземпляр не запускается
(повторный запуск показывает окно уже работающего).
"""
import sys
import time
import threading

# Масштаб экрана 125–150%: без этого Windows "растягивает" окно, а координаты кликов и скриншота расходятся.
# Нужно выставить ДО создания окон и импорта pyautogui.
try:
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)   # per-monitor
    except Exception:
        ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import keyboard
import pystray
from PIL import Image, ImageDraw

import config
import gui
import theme
import ui_thread
import app_window
import plan_validator
import agent_loop
import voice_input
import voice_output
import action_executor
import autostart
import history
import screen_utils
import notifications
import scheduler
import single_instance
from privacy import redact
from local_brain import try_local_plan
from quick_commands import try_quick_plan
from quick_actions import try_quick_action
import key_manager
from logger import log

_app = None
_registered_hotkeys = []
_tray_icon = None
_command_lock = threading.Lock()

COLOR_IDLE = "#4CAF50"
COLOR_BUSY = "#FF9800"
COLOR_ERROR = "#f44336"


def notify(title: str, message: str):
    """Нативное Windows-уведомление, а если не вышло — небольшое окно."""
    if not notifications.notify(title, message):
        gui.show_notification(message)


def _set_busy(busy: bool, text: str = ""):
    if _app is not None:
        ui_thread.post(_app.set_busy, busy, text)


# ---------- обычный план (локальная модель) ----------

def run_steps_with_progress(steps: list, progress):
    """Выполняется в ОТДЕЛЬНОМ потоке; обновляет окно прогресса по мере выполнения."""
    completed = 0
    for i, step in enumerate(steps):
        if progress.abort_event.is_set():
            progress.mark_step(i, "skipped")
            continue

        progress.mark_step(i, "running")
        log(f"Шаг {i + 1}/{len(steps)}: {step.get('action')}")

        before_img = None
        if step.get("action") in agent_loop.SCREEN_CHANGING:
            try:
                before_img = screen_utils.take_screenshot_image()
            except Exception:
                before_img = None

        try:
            action_executor.run_step(step)
        except PermissionError as e:
            progress.mark_step(i, "error")
            log(f"Заблокировано: {e}")
            progress.finish(f"Остановлено: {e}")
            return
        except Exception as e:
            progress.mark_step(i, "error")
            log(f"Ошибка на шаге {i + 1}: {e}")
            progress.finish(f"Остановлено из-за ошибки на шаге {i + 1}: {e}")
            return

        status = "done"
        if before_img is not None:
            time.sleep(0.6)  # даём интерфейсу время среагировать
            try:
                if not screen_utils.screens_differ(before_img, screen_utils.take_screenshot_image()):
                    status = "warning"
                    log(f"Шаг {i + 1}: экран визуально не изменился — возможно, промах.")
            except Exception:
                pass
        progress.mark_step(i, status)
        completed += 1

    if progress.abort_event.is_set():
        progress.finish(f"Остановлено пользователем. Выполнено шагов: {completed}/{len(steps)}")
    else:
        progress.finish(f"Готово! Выполнено шагов: {completed}/{len(steps)}")


def _run_local_plan(user_command: str, plan: dict) -> str:
    """План от локальной модели: проверка -> подтверждение -> выполнение. Возвращает 'ok'/'error'."""
    steps = plan.get("steps", [])
    summary = str(plan.get("summary", ""))

    if not steps:
        notify("AI Ассистент", summary or "Не удалось составить план действий.")
        history.add(user_command, summary, "не понял команду")
        return "ok"

    valid_steps, rejected = plan_validator.validate_plan(plan)
    if rejected:
        log(f"Валидатор отклонил шагов: {len(rejected)} из {len(steps)}")
    steps = valid_steps
    if not steps:
        notify("AI Ассистент", "План не прошёл проверку безопасности.")
        history.add(user_command, summary, "план отклонён валидатором")
        return "ok"

    dangerous = action_executor.is_dangerous(summary) or any(
        action_executor.is_dangerous(str(s)) for s in steps
    )
    display_summary = ("⚠️ ВНИМАНИЕ, ПОТЕНЦИАЛЬНО РИСКОВАННОЕ ДЕЙСТВИЕ ⚠️\n\n" + summary) if dangerous else summary

    auto = 0
    if plan.get("trusted") and not dangerous:
        try:
            auto = max(0, min(30, int(config.get("auto_confirm_seconds"))))
        except Exception:
            auto = 0
    confirmed_steps = gui.confirm_plan(display_summary, steps, auto_seconds=auto)
    if confirmed_steps is None:
        log("Пользователь отменил выполнение плана.")
        history.add(user_command, summary, "отменено пользователем")
        return "ok"
    if not confirmed_steps:
        notify("AI Ассистент", "Все шаги были отключены — нечего выполнять.")
        history.add(user_command, summary, "все шаги отключены пользователем")
        return "ok"

    voice_output.speak(f"Выполняю. {summary}")
    progress = gui.open_progress_window(confirmed_steps)
    worker = threading.Thread(target=run_steps_with_progress, args=(confirmed_steps, progress), daemon=True)
    worker.start()
    worker.join()      # окно результата остаётся открытым, пока ты сам его не закроешь

    history.add(user_command, summary, "Готово: план выполнен" if not progress.abort_event.is_set()
                else "Остановлено пользователем")
    voice_output.speak("Готово")
    return "ok"


# ---------- автопилот ----------

_active_agent = None


def _hide_own_windows():
    """Прячет главное окно на время автопилота: иначе ИИ видит на скриншотах само приложение и путается в нём."""
    if _app is not None:
        ui_thread.call(_app.hide_for_agent)
        time.sleep(0.5)      # дать Windows реально убрать окно с экрана до скриншота


def _restore_own_windows():
    if _app is not None:
        ui_thread.post(_app.restore_after_agent)


def _pre_capture():
    if _active_agent is None:            # во время автопилота окна уже спрятаны им самим
        _hide_own_windows()


def _post_capture():
    if _active_agent is None:
        _restore_own_windows()


action_executor.capture_hooks = (_pre_capture, _post_capture)


def stop_agent():
    """Аварийная остановка автопилота из любого места (горячая клавиша / меню трея)."""
    window = _active_agent
    if window is None:
        notify("AI Ассистент", "Сейчас автопилот не работает.")
        return
    window.abort_event.set()
    notify("Автопилот", "Останавливаю…")


def _run_agent(user_command: str) -> str:
    """Замкнутый цикл "экран -> действие -> проверка". Возвращает 'ok'/'error'."""
    waited = {"told": False}

    def on_wait(seconds, why):
        if not waited["told"]:
            waited["told"] = True
            notify("AI Ассистент", f"{why}. Подожду ~{int(seconds)} с и продолжу.")

    _hide_own_windows()
    try:
        return _run_agent_visible(user_command, on_wait)
    finally:
        _restore_own_windows()


def _run_agent_visible(user_command: str, on_wait) -> str:
    """Замкнутый цикл "экран -> действие -> проверка". Возвращает 'ok'/'error'."""
    try:
        decision, _screenshot = agent_loop.decide(user_command, [], on_wait=on_wait)
    except (ValueError, RuntimeError) as e:
        notify("Ошибка", str(e))
        history.add(user_command, "", f"ошибка: {e}")
        return "error"
    except Exception as e:
        notify("Ошибка", f"Ошибка обращения к ИИ: {e}")
        history.add(user_command, "", f"ошибка: {e}")
        return "error"

    intent = decision["intent"]

    # ИИ сразу ответил без действий (уже сделано / невозможно / не понял)
    if not decision["steps"]:
        if decision["status"] in ("done", "fail") and decision["message"]:
            notify("AI Ассистент", decision["message"])
            history.add(user_command, intent, decision["message"])
        else:
            notify("AI Ассистент", "ИИ не смог составить допустимое действие. Попробуй переформулировать команду.")
            history.add(user_command, intent, "не понял команду")
        return "ok"

    steps_text = [agent_loop.describe_step(s) for s in decision["steps"]]
    # Сами запускаем (с 3-секундным окном на отмену) только задачи вида "просто открыть что-то
    # безопасное" — когда ИИ уверен, что после этих действий всё готово.
    trivial = decision["status"] == "done" and all(
        plan_validator.classify_risk(s) == "low" for s in decision["steps"]
    )
    max_steps = int(config.get("agent_max_steps"))

    if not gui.confirm_agent_start(user_command, intent, steps_text, max_steps, auto_seconds=3 if trivial else 0):
        log("Пользователь отменил запуск автопилота.")
        history.add(user_command, intent, "отменено пользователем")
        return "ok"

    voice_output.speak(f"Начинаю. {intent}")
    global _active_agent
    window = gui.open_agent_window(user_command, max_steps)
    _active_agent = window
    result = {"ok": False, "message": "Остановлено пользователем."}

    def worker():
        try:
            ok, message = agent_loop.run_agent(user_command, window, first_decision=decision)
        except Exception as e:
            log(f"Ошибка автопилота: {e}")
            ok, message = False, f"Ошибка автопилота: {e}"
        result["ok"], result["message"] = ok, message
        window.finish(ok, message)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    try:
        thread.join()      # окно с журналом остаётся открытым, пока ты сам его не закроешь
    finally:
        _active_agent = None

    history.add(user_command, intent, result["message"])
    voice_output.speak(result["message"])
    notify("Автопилот: готово" if result["ok"] else "Автопилот остановлен", result["message"])
    return "ok"


def _run_quick_action(user_command: str, action) -> str:
    """Таймер, калькулятор, открытие папки — выполняет сама программа, без ИИ."""
    summary, run = action
    try:
        result = run()
    except Exception as e:
        log(f"Быстрое действие не удалось: {e}")
        notify("Ошибка", f"Не получилось: {e}")
        history.add(user_command, summary, f"ошибка: {e}")
        return "error"
    notify("AI Ассистент", result or summary)
    voice_output.speak(result or summary)
    history.add(user_command, summary, "Готово: " + (result or summary))
    return "ok"


def _run_view_only(user_command: str) -> str:
    """Режим «только смотреть»: ИИ смотрит на экран и отвечает, ничего не нажимая."""
    _hide_own_windows()
    try:
        answer = agent_loop.look_and_answer(user_command)
    except (ValueError, RuntimeError) as e:
        notify("Ошибка", str(e))
        history.add(user_command, "только смотреть", f"ошибка: {e}")
        return "error"
    except Exception as e:
        notify("Ошибка", f"Ошибка обращения к ИИ: {e}")
        history.add(user_command, "только смотреть", f"ошибка: {e}")
        return "error"
    finally:
        _restore_own_windows()
    gui.ask_yes_no("👁 Ответ (режим «только смотреть»)", answer[:1500], yes="Понятно", no="Закрыть")
    history.add(user_command, "только смотреть", answer[:200])
    return "ok"


def _offer_retry_after_limit(user_command: str):
    """Если у всех ключей кончился ДНЕВНОЙ лимит — предлагает повторить команду сама после сброса."""
    try:
        if not key_manager.all_daily_exhausted():
            return
        when = key_manager.next_daily_reset()
        agree = gui.ask_yes_no(
            "Дневной лимит Gemini исчерпан",
            f"Лимит обновится около {when:%H:%M} (по твоему времени, {when:%d.%m}).\n\n"
            f"Повторить команду автоматически?\n«{redact(user_command)[:120]}»\n\n"
            "Компьютер должен быть включён, программа — работать в фоне.",
            yes="Да, повторить", no="Нет")
        if agree:
            scheduler.add_task(user_command, [scheduler.WEEKDAYS[when.weekday()]], when.strftime("%H:%M"), once=True)
            notify("AI Ассистент", f"Повторю команду в {when:%H:%M}.")
    except Exception as e:
        log(f"Не удалось предложить повтор после лимита: {e}")


def process_command(user_command: str):
    if not user_command.strip():
        return

    if not _command_lock.acquire(blocking=False):
        notify("AI Ассистент", "Уже выполняется другая команда — подожди её завершения.")
        return

    # В лог и историю попадает ТОЛЬКО замаскированный текст (пароли и т.п. -> <скрыто>);
    # настоящая команда идёт дальше в ИИ как есть — иначе он не смог бы её выполнить.
    safe_text = redact(user_command)
    log(f"Команда пользователя: {safe_text}")
    set_tray_status(COLOR_BUSY)
    _set_busy(True, f"Выполняется: «{safe_text[:90]}». Смотри окно автопилота.")
    outcome = "ok"

    try:
        action = try_quick_action(user_command, notify)
        if action is not None:
            outcome = _run_quick_action(user_command, action)
        elif config.get("view_only"):
            outcome = _run_view_only(user_command)
        else:
            plan = try_quick_plan(user_command) or try_local_plan(user_command)
            if plan is not None:
                outcome = _run_local_plan(user_command, plan)
            else:
                outcome = _run_agent(user_command)
                _offer_retry_after_limit(user_command)
    except Exception as e:
        log(f"Необработанная ошибка при выполнении команды: {e}")
        notify("Ошибка", f"Что-то пошло не так: {e}")
        outcome = "error"
    finally:
        set_tray_status(COLOR_ERROR if outcome == "error" else COLOR_IDLE)
        _set_busy(False)
        _command_lock.release()


# ---------- действия из окна, трея и горячих клавиш ----------

def on_text_command():
    cmd = gui.ask_command()
    if cmd:
        process_command(cmd)


def _listen_with_window() -> str:
    window = gui.open_voice_window()
    try:
        text = voice_input.listen_command(on_state=window.set_state, on_level=window.set_level,
                                          cancel=window.cancelled)
        if text:
            time.sleep(0.7)       # успеть показать «Распознано: …»
        return text
    finally:
        window.close()


def on_voice_command():
    if _command_lock.locked():
        notify("AI Ассистент", "Уже выполняется другая команда — подожди её завершения.")
        return
    for attempt in (1, 2):
        try:
            text = _listen_with_window()
        except voice_input.VoiceError as e:
            # Не хватает компонента — не отправляем читать инструкцию, а предлагаем поставить прямо сейчас.
            if e.fixable and attempt == 1 and gui.ask_yes_no(
                    "Голосовой ввод",
                    f"{e}\n\nУстановить недостающее сейчас? Это займёт около минуты, нужен интернет.",
                    "Установить", "Не сейчас"):
                notify("AI Ассистент", "Устанавливаю компоненты для микрофона…")
                ok, message = voice_input.install_components()
                notify("Голосовой ввод", message)
                if ok:
                    continue
                return
            notify("Голосовой ввод", str(e))
            return
        if text:
            process_command(text)
        return


def on_repeat_last():
    cmd = history.get_last_command()
    if cmd:
        process_command(cmd)
    else:
        notify("AI Ассистент", "История пуста — пока нечего повторять.")


def _in_thread(fn):
    """Обёртка для обработчиков трея/горячих клавиш: каждое действие — в своём потоке."""
    return lambda *a: threading.Thread(target=fn, daemon=True).start()


def show_window(page=None):
    if _app is not None:
        ui_thread.post(_app.show, page)


def apply_settings(values: dict):
    """Сохраняет настройки из окна и применяет то, что должно подействовать сразу."""
    config.save({
        "gemini_api_keys": values["gemini_api_keys"],
        "model": values["model"],
        "theme": values["theme"],
        "accent": values.get("accent", "indigo"),
        "view_only": bool(values.get("view_only", False)),
        "hotkey_open_window": values["hotkey_open_window"],
        "hotkey_voice": values.get("hotkey_voice", "ctrl+shift+v"),
        "hotkey_stop": values.get("hotkey_stop", "ctrl+shift+s"),
        "requests_per_minute": values.get("requests_per_minute", 6),
        "voice_device": values.get("voice_device", ""),
        "allowed_folders": values["allowed_folders"],
        "voice_feedback_enabled": values["voice_feedback_enabled"],
        "voice_language": values["voice_language"],
        "local_model_enabled": values["local_model_enabled"],
        "local_model_name": values["local_model_name"],
    })
    action_executor.sync_pyautogui_settings()
    if values["autostart"]:
        autostart.enable()
    else:
        autostart.disable()
    register_hotkeys()


def quit_app():
    """Полный выход (не сворачивание): останавливает трей и главный цикл окна."""
    log("Выход из приложения")
    try:
        if _tray_icon is not None:
            _tray_icon.stop()
    except Exception:
        pass
    if _app is not None:
        ui_thread.post(_app.quit)


class Controller:
    """То, что главное окно может попросить у приложения (окно ничего не знает про потоки и ИИ)."""

    def submit_command(self, text):
        threading.Thread(target=process_command, args=(text,), daemon=True).start()

    def voice_command(self):
        threading.Thread(target=on_voice_command, daemon=True).start()

    def repeat(self, command):
        self.submit_command(command)

    def save_settings(self, values):
        apply_settings(values)

    def quit(self):
        quit_app()

    def notify_hidden(self):
        notify("AI Ассистент", "Работаю в фоне — значок остался в трее. Двойной клик по нему открывает окно.")


# ---------- трей ----------

def make_tray_icon_image(color: str) -> Image.Image:
    """Логотип приложения с цветным бейджем статуса в углу (как в Slack/Discord)."""
    try:
        base = Image.open(theme.LOGO_64_PATH).convert("RGBA")
    except Exception:
        base = Image.new("RGBA", (64, 64), (79, 70, 229, 255))

    img = base.copy()
    draw = ImageDraw.Draw(img)
    badge_r = 13
    cx, cy = img.width - badge_r - 2, img.height - badge_r - 2
    draw.ellipse((cx - badge_r, cy - badge_r, cx + badge_r, cy + badge_r), fill="white")
    draw.ellipse((cx - badge_r + 3, cy - badge_r + 3, cx + badge_r - 3, cy + badge_r - 3), fill=color)
    return img


def set_tray_status(color: str):
    """Цвет бейджа в трее: зелёный = свободен, оранжевый = занят, красный (3 сек.) = ошибка."""
    if _tray_icon is None:
        return
    try:
        _tray_icon.icon = make_tray_icon_image(color)
        if color == COLOR_ERROR:
            threading.Timer(3.0, lambda: set_tray_status(COLOR_IDLE)).start()
    except Exception:
        pass


def setup_tray():
    global _tray_icon
    more = pystray.Menu(
        pystray.MenuItem("📜  История команд", lambda: show_window("history")),
        pystray.MenuItem("📅  Расписание задач", lambda: show_window("schedule")),
        pystray.MenuItem("🩺  Проверка системы", lambda: show_window("diag")),
    )
    menu = pystray.Menu(
        pystray.MenuItem("🪟  Открыть окно", lambda: show_window(), default=True),   # двойной клик по значку
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("💬  Быстрая команда", _in_thread(on_text_command)),
        pystray.MenuItem("🎙️  Команда голосом", _in_thread(on_voice_command)),
        pystray.MenuItem("🔁  Повторить последнюю", _in_thread(on_repeat_last)),
        pystray.MenuItem("🛑  Остановить автопилот", _in_thread(stop_agent)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Ещё", more),
        pystray.MenuItem("⚙️  Настройки", lambda: show_window("settings")),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("🚪  Выход", lambda: quit_app()),
    )
    _tray_icon = pystray.Icon("ai_assistant", make_tray_icon_image(COLOR_IDLE), "AI Ассистент", menu)
    return _tray_icon


def register_hotkeys():
    """Вешает горячие клавиши: быстрая команда, голос, остановка. Старые снимает (нужно после смены настроек)."""
    global _registered_hotkeys
    for combo in _registered_hotkeys:
        try:
            keyboard.remove_hotkey(combo)
        except (KeyError, ValueError):
            pass
    _registered_hotkeys = []

    wanted = [
        (config.get("hotkey_open_window"), on_text_command, "быстрая команда"),
        (config.get("hotkey_voice"), on_voice_command, "голосовая команда"),
        (config.get("hotkey_stop"), stop_agent, "остановка автопилота"),
    ]
    for combo, handler, label in wanted:
        combo = (combo or "").strip()
        if not combo or combo in _registered_hotkeys:
            continue
        try:
            keyboard.add_hotkey(combo, _in_thread(handler))
            _registered_hotkeys.append(combo)
            log(f"Горячая клавиша ({label}): {combo}")
        except Exception as e:
            log(f"Не удалось установить горячую клавишу «{combo}» ({label}): {e}")
            if handler is on_text_command and "ctrl+shift+a" not in _registered_hotkeys:
                try:
                    keyboard.add_hotkey("ctrl+shift+a", _in_thread(handler))
                    _registered_hotkeys.append("ctrl+shift+a")
                    log("Для быстрой команды использую клавишу по умолчанию: ctrl+shift+a")
                except Exception:
                    pass


def on_scheduled_command(command: str):
    threading.Thread(target=process_command, args=(command,), daemon=True).start()


def _startup(background: bool):
    """Выполняется в интерфейсном потоке сразу после старта главного цикла."""
    if not config.get("gemini_api_keys"):
        _app.show("home")
        keys = gui.first_run_wizard()
        if keys:
            config.save({"gemini_api_keys": keys})
            log(f"Сохранено ключей Gemini: {len(keys)}")
            _app.refresh()
        else:
            log("Ключ не введён. Его можно добавить в Настройках.")
    elif not background:
        _app.show("home")
    else:
        log("Запуск в фоне (--background): окно скрыто, значок в трее.")


def main():
    global _app
    background = "--background" in sys.argv

    if not single_instance.acquire():
        # Уже запущено — просим то приложение показать окно (иначе клик по ярлыку выглядел бы как "ничего не произошло").
        single_instance.signal_existing()
        return

    log("Запуск AI-ассистента...")
    try:
        _app = app_window.AppWindow(Controller())
        single_instance.start_listener(lambda: ui_thread.post(_app.show))
        register_hotkeys()
        icon = setup_tray()
        threading.Thread(target=icon.run, daemon=True).start()
        scheduler.start(on_scheduled_command)
        ui_thread.post(_startup, background)
        log("Приложение работает. Крестик окна сворачивает его в трей; выход — кнопка в окне или меню трея.")
        _app.run()
    finally:
        ui_thread.shutdown()
        single_instance.release()
        try:
            if _app is not None:
                _app.root.destroy()
        except Exception:
            pass
        try:
            if _tray_icon is not None:
                _tray_icon.stop()
        except Exception:
            pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
