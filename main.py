"""
AI-ассистент для управления компьютером (Windows 11).

Запуск:  python main.py              — с окном
         python main.py --background — сразу в трее (так стартует автозагрузка)

Как устроено приложение:
  * Главный поток — главное окно (qt_ui/). Крестик его СВОРАЧИВАЕТ: программа
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
import os
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
except Exception as _e:
    log(f"main.py:36: {type(_e).__name__}: {_e}")

import keyboard
from PIL import Image, ImageDraw

import chat
import i18n
import config
try:
    from qt_ui import dialogs_qt as gui      # все диалоги и окна автопилота (Qt)
except ImportError as _qt_err:             # PySide6 не установлен — main() сообщит об этом
    gui = None
    _QT_ERROR = _qt_err
import theme
import ui_thread
import plan_validator
import agent_loop
import app_profiles
import game_bot
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
import macros
import backup
import templates
import wakeword
import telegram_bot
from logger import log

_app = None
_registered_hotkeys = []
_tray_icon = None
_command_lock = threading.Lock()

COLOR_IDLE = "#4CAF50"
COLOR_BUSY = "#FF9800"
COLOR_ERROR = "#f44336"


def notify(title: str, message: str):
    """Нативное Windows-уведомление, а если не вышло — небольшое окно. Для команды из Telegram — ещё и сообщение в чат."""
    if _is_remote():
        try:
            telegram_bot._send(_remote.uid, f"{redact(str(i18n.t(title)))}: {redact(str(i18n.t(message)))}"[:1500])
            _remote.mirrored = True
            return
        except Exception as e:
            log(f"Не отправилось в Telegram: {e}")
    if not notifications.notify(i18n.t(title), i18n.t(message)):
        gui.show_notification(i18n.t(message))


def alarm_notify(title: str, message: str):
    """Сигнал таймера/напоминания: уведомление + заметное окно + голос (тост Windows может быть скрыт режимом «Не беспокоить»)."""
    notify(title, message)
    try:
        gui.show_notification(f"{i18n.t(title)}\n{i18n.t(message)}")
    except Exception as e:
        log(f"Окно сигнала не открылось: {e}")
    try:
        voice_output.speak(i18n.t(message))
    except Exception:
        pass


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
            except Exception as _e:
                log(f"main.py:139: {type(_e).__name__}: {_e}")
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
    if plan.get("trusted") and not dangerous and not _is_remote():
        try:
            auto = max(0, min(30, int(config.get("auto_confirm_seconds"))))
        except Exception:
            auto = 0
    chat_answer = None
    if _is_remote() and config.get("telegram_trusted_auto") and plan.get("trusted") and not dangerous \
            and all(plan_validator.classify_risk(st) == "low" for st in steps):
        chat_answer = True            # безопасная быстрая команда (открыть сайт/приложение, скрин) — без лишних вопросов
    else:
        chat_answer = _approve_in_chat(summary, steps, dangerous)
    if chat_answer is True:
        confirmed_steps = steps
    elif chat_answer is False:
        confirmed_steps = None            # отказ или время вышло в Telegram
    else:
        confirmed_steps = gui.confirm_plan(display_summary, steps, auto_seconds=auto, manual=_is_remote())
    if confirmed_steps is None:
        log("Пользователь отменил выполнение плана.")
        history.add(user_command, summary, "отменено пользователем")
        return "ok"
    if not confirmed_steps:
        notify("AI Ассистент", "Все шаги были отключены — нечего выполнять.")
        history.add(user_command, summary, "все шаги отключены пользователем")
        return "ok"

    voice_output.speak(f"Выполняю. {summary}")
    if _is_remote():
        _remote.screen = True
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


_stop_event = threading.Event()      # «стоп» для ожидания ИИ и вопросов в Telegram (когда окна автопилота ещё нет)


def request_stop() -> str:
    """Остановка из любого места (горячая клавиша, трей, /stop в Telegram). -> что сделано, текстом."""
    _stop_event.set()
    try:
        telegram_bot.cancel_approvals()
    except Exception as _e:
        log(f"main.py:253: {type(_e).__name__}: {_e}")
    window = _active_agent
    if window is not None:
        window.abort_event.set()
        return "🛑 Останавливаю автопилот…"
    if _command_lock.locked():
        return "🛑 Останавливаю текущую команду…"
    return "Сейчас ничего не выполняется."


def stop_agent():
    """Аварийная остановка из любого места (горячая клавиша / меню трея)."""
    notify("Автопилот", request_stop())


def _run_agent(user_command: str) -> str:
    """Замкнутый цикл "экран -> действие -> проверка". Возвращает 'ok'/'error'."""
    waited = {"told": False}

    def on_wait(seconds, why):
        if not waited["told"]:
            waited["told"] = True
            notify("AI Ассистент", f"{why}. Подожду ~{int(seconds)} с и продолжу.")

    try:
        import app_profiles
        prof = app_profiles.current_profile()
        if prof.get("mode") == "hint_only":
            notify("AI Ассистент", app_profiles.hint_only_message(prof))
            return _run_view_only(user_command)
    except Exception as e:
        log(f"Профиль приложения не определён: {e}")
    _hide_own_windows()
    try:
        return _run_agent_visible(user_command, on_wait)
    finally:
        _restore_own_windows()


def _run_agent_visible(user_command: str, on_wait) -> str:
    """Замкнутый цикл "экран -> действие -> проверка". Возвращает 'ok'/'error'."""
    thinking = None
    try:
        thinking = gui.open_thinking_window("Смотрю на экран и придумываю первый шаг…", _stop_event.set)
    except Exception as e:
        log(f"Окно ожидания не открылось: {e}")

    def on_wait_visible(seconds, why):
        if thinking is not None:
            thinking.set_text(f"{why}. Подожду ~{int(seconds)} с и продолжу.")
        on_wait(seconds, why)

    try:
        try:
            decision, _screenshot = agent_loop.decide(user_command, [], on_wait=on_wait_visible, cancel=_stop_event)
        finally:
            if thinking is not None:
                thinking.close()
    except (ValueError, RuntimeError) as e:
        notify("Ошибка", str(e))
        history.add(user_command, "", f"ошибка: {e}")
        return "error"
    except Exception as e:
        notify("Ошибка", f"Ошибка обращения к ИИ: {e}")
        history.add(user_command, "", f"ошибка: {e}")
        return "error"

    intent = decision["intent"]

    if _stop_event.is_set():
        history.add(user_command, decision["intent"], "остановлено пользователем")
        return "ok"

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

    if max_steps < 1000:
        intro = f"Цель: {user_command}\nАвтопилот сам будет смотреть на экран и действовать (до {max_steps} шагов). "
    else:
        intro = f"Цель: {user_command}\nАвтопилот сам будет смотреть на экран и действовать без ограничения числа шагов. "
    chat_ok = _approve_in_chat(intro + "Каждый опасный шаг спрошу отдельно здесь же.\n\nНачнёт так:\n" + "\n".join(steps_text),
                               decision["steps"], False, "Запустить автопилот?")
    if chat_ok is not None:
        start_ok = chat_ok
    else:
        start_ok = gui.confirm_agent_start(user_command, intent, steps_text, max_steps,
                                           auto_seconds=3 if (trivial and not _is_remote()) else 0)
    if not start_ok:
        log("Пользователь отменил запуск автопилота.")
        history.add(user_command, intent, "отменено пользователем")
        return "ok"

    voice_output.speak(f"Начинаю. {intent}")
    global _active_agent
    window = gui.open_agent_window(user_command, max_steps)
    _active_agent = window
    if _is_remote():
        _remote.screen = True
        window = _RemoteAgentUI(window, _remote.uid, user_command)
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


def _run_game(user_command: str) -> str:
    """Игровой режим: бот играет за пользователя в окне, на которое он переключится после отсчёта."""
    if _is_remote():
        notify("AI Ассистент", "Игровой режим запускается только с самого компьютера.")
        return "ok"
    ok = gui.ask_yes_no(
        "Игровой режим",
        f"Цель: {user_command}\n\nБот будет играть за тебя: смотреть на экран, жать клавиши и мышь. "
        "Нужно, чтобы игра была в окне на переднем плане; если переключишься на другое окно — бот подождёт.\n\n"
        "Остановить: кнопка 🛑 или резкий рывок мыши в левый верхний угол. В онлайн-играх автоклики могут нарушать правила — "
        "риск бана на тебе. После «Начать» есть 10 секунд, чтобы переключиться в игру.", "Начать", "Отмена")
    if not ok:
        history.add(user_command, "игровой режим", "отменено пользователем")
        return "ok"
    notify("AI Ассистент", "Переключись в игру — начну через 10 секунд.")
    _hide_own_windows()
    window = None
    try:
        for _ in range(100):
            if _stop_event.is_set():
                history.add(user_command, "игровой режим", "остановлено пользователем")
                return "ok"
            time.sleep(0.1)
        proc = app_profiles.foreground_process()
        if proc in game_bot.NOT_A_GAME and proc != "":
            notify("AI Ассистент", "Не вижу окна игры на переднем плане — не начинаю. Открой игру и повтори команду.")
            history.add(user_command, "игровой режим", "игра не найдена")
            return "ok"
        global _active_agent
        window = gui.open_agent_window(user_command, 99999)
        _active_agent = window
        result = {"ok": False, "message": "Остановлено пользователем."}

        def worker():
            try:
                ok_, msg = game_bot.run_game(user_command, window, proc)
            except Exception as e:
                log(f"Ошибка игрового режима: {e}")
                ok_, msg = False, f"Ошибка игрового режима: {e}"
            result["ok"], result["message"] = ok_, msg
            window.finish(ok_, msg)

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            thread.join()
        finally:
            _active_agent = None
    finally:
        _restore_own_windows()
    history.add(user_command, "игровой режим", result["message"])
    notify("Игровой режим: готово" if result["ok"] else "Игровой режим остановлен", result["message"])
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
        answer = agent_loop.look_and_answer(user_command, cancel=_stop_event)
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
    if _is_remote():
        telegram_bot._send(_remote.uid, "👁 " + redact(answer)[:1500])
        _remote.mirrored = True
    else:
        gui.ask_yes_no("👁 Ответ (режим «только смотреть»)", answer[:1500], yes="Понятно", no="Закрыть")
    history.add(user_command, "только смотреть", answer[:200])
    return "ok"


def _offer_retry_after_limit(user_command: str):
    """Если у всех ключей кончился ДНЕВНОЙ лимит — предлагает повторить команду сама после сброса."""
    try:
        if not key_manager.all_daily_exhausted():
            return
        when = key_manager.next_daily_reset()
        q = (f"Лимит обновится около {when:%H:%M} (по твоему времени, {when:%d.%m}).\n\n"
             f"Повторить команду автоматически?\n«{redact(user_command)[:120]}»\n\n"
             "Компьютер должен быть включён, программа — работать в фоне.")
        if _is_remote():
            agree = telegram_bot.ask_approval(_remote.uid, "Дневной лимит Gemini исчерпан\n\n" + q) is True
        else:
            agree = gui.ask_yes_no("Дневной лимит Gemini исчерпан", q, yes="Да, повторить", no="Нет")
        if agree:
            scheduler.add_task(user_command, [scheduler.WEEKDAYS[when.weekday()]], when.strftime("%H:%M"), once=True)
            notify("AI Ассистент", f"Повторю команду в {when:%H:%M}.")
    except Exception as e:
        log(f"Не удалось предложить повтор после лимита: {e}")


_remote = threading.local()      # команда пришла из Telegram: автозапуск отключён, всегда нужен клик на компьютере


def _is_remote() -> bool:
    return bool(getattr(_remote, "on", False))


def _approve_in_chat(summary: str, steps: list, dangerous: bool, title: str = "Подтверди действия"):
    """Команда пришла из Telegram: подтверждаем кнопками в чате (опасное — двойным подтверждением).
    -> True/False, либо None (в чат не получилось написать — спросим на компьютере)."""
    if not (_is_remote() and config.get("telegram_approve_in_chat")):
        return None
    risky = dangerous or any(plan_validator.classify_risk(st) == "high" for st in steps)
    lines = []
    for i, st in enumerate(steps[:12], 1):
        lines.append(f"{i}. {redact(agent_loop.describe_step(st))}")
    more = f"\n…и ещё {len(steps) - 12}" if len(steps) > 12 else ""
    text = f"{title}\n\n{redact(summary)}\n\n" + "\n".join(lines) + more
    if risky:
        return telegram_bot.ask_danger(_remote.uid, text)
    return telegram_bot.ask_approval(_remote.uid, text)


class _RemoteAgentUI:
    """Окно автопилота для команды из Telegram: всё как на ПК, но шаги и вопросы идут ещё и в чат."""

    def __init__(self, win, uid, goal):
        self._win, self._uid = win, uid
        self._progress = telegram_bot.Progress(uid, f"🤖 Автопилот: {redact(goal)[:100]}")

    def __getattr__(self, name):
        return getattr(self._win, name)

    def add_step(self, n, text):
        self._win.add_step(n, text)
        self._progress.add(f"{n}. {text}")

    def request_confirm(self, text):
        if config.get("telegram_approve_in_chat"):
            ans = telegram_bot.ask_danger(self._uid, redact(str(text)))
            if ans is not None:
                return bool(ans)
        return self._win.request_confirm(text)

    def finish(self, ok, message):
        self._progress.add(("✅ " if ok else "⛔ ") + str(message))
        self._win.finish(ok, message)


def _tg_status() -> str:
    state = "занят" if _command_lock.locked() else "свободен"
    agent = "работает" if _active_agent is not None else "не запущен"
    return (f"📊 Ассистент v{config.APP_VERSION}: {state}\nАвтопилот: {agent}\n"
            f"Ключей Gemini: {len(key_manager.get_keys()) if hasattr(key_manager, 'get_keys') else '—'}\n"
            f"Время на ПК: {time.strftime('%H:%M:%S %d.%m.%Y')}")


def _tg_stop() -> str:
    return request_stop()


def _tg_history() -> str:
    items = history.get_all()[:6]
    if not items:
        return "История пуста."
    return "📜 Последние команды:\n\n" + "\n\n".join(
        f"{i.get('time', '')[5:16]}  {i.get('command', '')[:80]}\n→ {str(i.get('status', ''))[:120]}" for i in items)


def _tg_notes() -> str:
    n = macros.notes()
    if not n:
        return "Заметок нет. Напиши: «запомни: …»."
    return "🗒 Заметки:\n" + "\n".join(f"• {x if isinstance(x, str) else x.get('text', x)}" for x in n[-20:])


def _tg_macros() -> str:
    m = macros.list_macros()
    if not m:
        return "Макросов нет. Выполни действие и скажи «запомни это как <название>»."
    return "⚙️ Макросы:\n" + "\n".join(f"• {k}" for k in m) + "\n\nЗапуск: напиши название."


def _tg_windows() -> str:
    """Список открытых окон (только Windows)."""
    try:
        import ctypes
        user32 = ctypes.windll.user32
        titles = []
        proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    if buf.value.strip() and buf.value not in ("Program Manager", "Settings"):
                        titles.append(buf.value.strip())
            return True
        user32.EnumWindows(proc(cb), 0)
        titles = list(dict.fromkeys(titles))
        return "🪟 Открытые окна:\n" + "\n".join(f"• {t[:90]}" for t in titles[:30]) if titles else "Окон не найдено."
    except Exception as e:
        return f"Не удалось получить список окон: {e}"


def _tg_clip() -> str:
    import clipboard_tools
    t = clipboard_tools.get_text()
    return ("📋 Буфер обмена:\n" + t[:3500]) if t.strip() else "Буфер обмена пуст (или там не текст)."


TELEGRAM_HOOKS = {"clip": _tg_clip, "windows": _tg_windows, "status": _tg_status, "stop": _tg_stop, "history": _tg_history, "notes": _tg_notes, "macros": _tg_macros}


def on_telegram_command(text: str, user_id, user_name) -> str:
    """Выполняет команду из Telegram и возвращает короткий итог для чата ('' — итог уже отправлен в чат)."""
    started = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 1))
    _remote.on = True
    _remote.uid = user_id
    _remote.mirrored = False
    _remote.screen = False
    try:
        process_command(text)
    finally:
        _remote.on = False
    result = "Готово"
    try:
        last = history.get_all()[0]
        if last.get("time", "") >= started:
            result = last.get("status") or last.get("summary") or "Готово"
    except Exception as _e:
        log(f"main.py:581: {type(_e).__name__}: {_e}")
    if _remote.screen and config.get("telegram_screen_after"):
        time.sleep(1.0)
        try:
            telegram_bot.send_screen(user_id)
        except Exception as e:
            log(f"Telegram: скриншот после задачи не отправился: {e}")
    if _remote.mirrored:
        return ""
    return result


def process_command(user_command: str):
    if not user_command.strip():
        return

    if not _command_lock.acquire(blocking=False):
        notify("AI Ассистент", "Уже выполняется другая команда — подожди её завершения.")
        return

    # В лог и историю попадает ТОЛЬКО замаскированный текст (пароли и т.п. -> <скрыто>);
    # настоящая команда идёт дальше в ИИ как есть — иначе он не смог бы её выполнить.
    _stop_event.clear()
    safe_text = redact(user_command)
    log(f"Команда пользователя: {safe_text}")
    set_tray_status(COLOR_BUSY)
    _set_busy(True, f"Выполняется: «{safe_text[:90]}». Смотри окно автопилота.")
    outcome = "ok"

    try:
        action = macros.try_memory_command(user_command, action_executor.RECENT_STEPS)
        if action is None:
            action_executor.RECENT_STEPS.clear()      # новая команда — новая «запись» шагов
            action = try_quick_action(user_command, alarm_notify)
        if action is not None:
            outcome = _run_quick_action(user_command, action)
        elif config.get("view_only"):
            outcome = _run_view_only(user_command)
        elif game_bot.is_play_command(user_command):
            outcome = _run_game(user_command)
        else:
            plan = macros.try_macro_plan(user_command) or templates.try_template_plan(user_command) or try_quick_plan(user_command) or try_local_plan(user_command)
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
        "ui_language": values.get("ui_language", "ru"),
        "view_only": bool(values.get("view_only", False)),
        "use_fallback_models": bool(values.get("use_fallback_models", True)),
        "fallback_models": values.get("fallback_models", ["gemini-2.5-flash", "gemini-2.5-flash-lite"]),
        "auto_confirm_seconds": values.get("auto_confirm_seconds", 5),
        "telegram_enabled": bool(values.get("telegram_enabled", False)),
        "telegram_approve_in_chat": bool(values.get("telegram_approve_in_chat", True)),
        "telegram_trusted_auto": bool(values.get("telegram_trusted_auto", True)),
        "telegram_announce": bool(values.get("telegram_announce", True)),
        "telegram_screen_after": bool(values.get("telegram_screen_after", True)),
        "hotkey_open_window": values["hotkey_open_window"],
        "hotkey_voice": values.get("hotkey_voice", "ctrl+shift+v"),
        "hotkey_stop": values.get("hotkey_stop", "ctrl+shift+s"),
        "requests_per_minute": values.get("requests_per_minute", 6),
        "voice_device": values.get("voice_device", ""),
        "allowed_folders": values["allowed_folders"],
        "voice_feedback_enabled": values["voice_feedback_enabled"],
        "voice_wake_enabled": bool(values.get("voice_wake_enabled", False)),
        "voice_language": values["voice_language"],
        "local_model_enabled": values["local_model_enabled"],
        "local_model_name": values["local_model_name"],
    })
    action_executor.sync_pyautogui_settings()
    try:
        if config.get("voice_wake_enabled"):
            wakeword.restart(_on_wake_word, _command_lock.locked)
        else:
            wakeword.stop()
    except Exception as e:
        log(f"Слово-активатор: {e}")
    try:
        if values.get("telegram_clear_token"):
            telegram_bot.set_token("")
        elif values.get("telegram_token"):
            telegram_bot.set_token(values["telegram_token"])
        if values.get("telegram_enabled"):
            telegram_bot.restart(on_telegram_command, TELEGRAM_HOOKS)
        else:
            telegram_bot.stop()
    except Exception as e:
        log(f"Telegram: не удалось применить настройки: {e}")
    backup.full_snapshot()           # ключи и токен — в зашифрованный резерв, чтобы не вводить заново после обновлений
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
    except Exception as _e:
        log(f"main.py:762: {type(_e).__name__}: {_e}")
    if _app is not None:
        ui_thread.post(_app.quit)


def _on_attached_file(path: str, question: str = ""):
    """Файл из окна программы: картинка -> ответ на вопрос про неё; аудио -> распознать речь и выполнить как команду."""
    import media_input
    kind = media_input.kind_of(path)
    if not kind:
        notify("AI Ассистент", "Поддерживаются картинки (png, jpg, webp…) и аудио (ogg, mp3, wav, m4a…).")
        return
    try:
        data = media_input.read_file(path)
        if kind == "audio":
            notify("AI Ассистент", "Распознаю речь из файла…")
            text = media_input.transcribe(data, media_input.mime_of(path))
            if not text:
                notify("AI Ассистент", "В файле не нашлось речи.")
                return
            notify("AI Ассистент", f"Распознано: «{text[:120]}»")
            process_command(text)
        else:
            notify("AI Ассистент", "Смотрю на картинку…")
            answer = media_input.ask_image(data, media_input.mime_of(path), question)
            gui.ask_yes_no("🖼 Ответ про картинку", answer[:1800], yes="Понятно", no="Закрыть")
            history.add(f"Картинка: {os.path.basename(path)} {question}".strip(), "вопрос про картинку", answer[:200])
    except Exception as e:
        log(f"Файл не обработан: {e}")
        notify("Ошибка", f"Не получилось обработать файл: {e}")


class Controller:
    """То, что главное окно может попросить у приложения (окно ничего не знает про потоки и ИИ)."""

    def submit_command(self, text):
        threading.Thread(target=process_command, args=(text,), daemon=True).start()

    def voice_command(self):
        threading.Thread(target=on_voice_command, daemon=True).start()

    def chat_ask(self, turns, on_done):
        """Вопрос в чат «Ассистент»: ответ приходит в on_done(текст, успех) из рабочего потока.
        Если выбрана локальная модель — отвечает она; если Gemini недоступен, а Ollama запущена — отвечает Ollama."""
        def work():
            backend = config.get("chat_backend") or "gemini"
            try:
                on_done(chat.ask(turns, backend=backend), True)
                return
            except Exception as e:
                log(f"Чат: ошибка ответа ({backend}): {e}")
                err = e
            if backend == "gemini":
                try:
                    import local_llm
                    if local_llm.is_running():
                        answer = chat.ask_local(turns)
                        on_done("(Gemini сейчас недоступен — отвечает локальная модель)\n" + answer, True)
                        return
                except Exception as e2:
                    log(f"Чат: запасная локальная модель тоже не ответила: {e2}")
            on_done(f"Не получилось получить ответ: {err}", False)
        threading.Thread(target=work, daemon=True).start()

    def attach_file(self, path, question=""):
        threading.Thread(target=_on_attached_file, args=(path, question), daemon=True).start()

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
    except Exception as _e:
        log(f"main.py:846: {type(_e).__name__}: {_e}")


def setup_tray():
    """Значок в трее (Qt). Подписи пунктов переводятся; двойной клик — открыть окно."""
    global _tray_icon
    from qt_ui.tray import QtTray
    t = i18n.t
    items = [
        (t("🪟  Открыть окно"), lambda: show_window(), True),
        None,
        (t("💬  Быстрая команда"), _in_thread(on_text_command)),
        (t("🎙️  Команда голосом"), _in_thread(on_voice_command)),
        (t("🔁  Повторить последнюю"), _in_thread(on_repeat_last)),
        (t("🛑  Остановить автопилот"), _in_thread(stop_agent)),
        None,
        (t("Ещё"), [
            (t("📜  История команд"), lambda: show_window("history")),
            (t("📅  Расписание задач"), lambda: show_window("schedule")),
            (t("🩺  Проверка системы"), lambda: show_window("diag")),
        ]),
        (t("⚙️  Настройки"), lambda: show_window("settings")),
        None,
        (t("🚪  Выход"), lambda: quit_app()),
    ]
    _tray_icon = QtTray(items, make_tray_icon_image(COLOR_IDLE), "AI Ассистент", post=_app.post,
                        on_open=lambda: show_window())
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
                except Exception as _e:
                    log(f"main.py:904: {type(_e).__name__}: {_e}")


def _on_wake_word(rest: str):
    """Услышано «эй, ассистент»: с командой — выполняем, без — открываем обычное окно прослушивания."""
    try:
        import winsound
        winsound.Beep(1000, 120)
    except Exception as _e:
        log(f"main.py:913: {type(_e).__name__}: {_e}")
    if _command_lock.locked():
        return
    if rest.strip():
        process_command(rest.strip())
    else:
        on_voice_command()


def _on_schedule_skipped(command: str, cond: str):
    notify("Расписание", f"Задача «{command[:60]}» пропущена: условие «{cond}» не выполнилось за 30 минут.")


def on_scheduled_command(command: str):
    threading.Thread(target=process_command, args=(command,), daemon=True).start()


def _startup_update_check():
    """Через полминуты после запуска тихо смотрит, нет ли новой версии, и один раз за версию сообщает в уведомлении."""
    time.sleep(30)
    try:
        import updater
        state, text, _url = updater.check()
        if state == "new" and config.get("update_notified") != text:
            config.save({"update_notified": text})
            notify("AI Ассистент", f"Есть обновление: {text}. Установить: Настройки → Запуск → Обновления.")
    except Exception as e:
        log(f"Проверка обновлений: {type(e).__name__}: {e}")


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


def _create_ui(controller):
    """Окно на Qt (PySide6). Прежнего интерфейса на Tk больше нет."""
    from qt_ui import runtime
    return runtime.QtShell(controller)


def _fatal_no_qt():
    msg = ("Не найден компонент PySide6 (интерфейс программы).\n\nЗапусти установщик AI_Assistant_Setup.bat заново "
           "или выполни: python -m pip install PySide6-Essentials")
    log("PySide6 не установлен: " + str(globals().get("_QT_ERROR", "")))
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(0, msg, "AI Ассистент", 0x10)
    except Exception:
        print(msg)


def main():
    global _app
    background = "--background" in sys.argv

    if not single_instance.acquire():
        # Уже запущено — просим то приложение показать окно (иначе клик по ярлыку выглядел бы как "ничего не произошло").
        single_instance.signal_existing()
        return

    if gui is None:
        _fatal_no_qt()
        single_instance.release()
        return

    log("Запуск AI-ассистента...")
    backup.restore_secrets()         # ключи/токен пропали (чистка, новая папка) — вернуть из резерва
    backup.full_snapshot()
    try:
        _app = _create_ui(Controller())
        single_instance.start_listener(lambda: ui_thread.post(_app.show))
        register_hotkeys()
        setup_tray()
        scheduler.start(on_scheduled_command, _on_schedule_skipped)
        telegram_bot.start(on_telegram_command, TELEGRAM_HOOKS)
        wakeword.start(_on_wake_word, _command_lock.locked)
        ui_thread.post(_startup, background)
        threading.Thread(target=_startup_update_check, daemon=True).start()
        log("Приложение работает. Крестик окна сворачивает его в трей; выход — кнопка в окне или меню трея.")
        _app.run()
    finally:
        ui_thread.shutdown()
        single_instance.release()
        try:
            if _tray_icon is not None:
                _tray_icon.stop()
        except Exception as _e:
            log(f"main.py:983: {type(_e).__name__}: {_e}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
