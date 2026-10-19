"""Методы бэкенда для страниц «История», «Расписание», «Проверка системы».

MoreBackend — миксин: добавить к RealBackend (`class RealBackend(MoreBackend, ...)`) или смешать на лету.
Требует у self: `post(fn)` (выполнить в потоке Qt) и `run_command(text)`.
StubMore — те же методы с тестовыми данными (для макетов/тестов); `StubMore.post` вызывает сразу.
Долгие вещи (проверка системы, тест мыши) идут в потоке, результат — через `self.post(...)`.
"""
import os
import threading
import time

DAYS = [("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"), ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс")]
CONDITIONS = [("", "Всегда"),
              ("online", "Если есть интернет"),
              ("active:2", "Если я за компьютером (был активен последние 2 мин)"),
              ("idle:5", "Если компьютер не используется 5 минут"),
              ("idle:15", "Если компьютер не используется 15 минут"),
              ("app:chrome.exe", "Если запущен Chrome"),
              ("noapp:chrome.exe", "Если Chrome не запущен")]
WAIT_MIN = 30


def log_tail(lines: int = 45) -> str:
    try:
        import config
        with open(config.LOG_FILE, encoding="utf-8", errors="replace") as f:
            tail = f.read().splitlines()[-lines:]
        return "\n".join(tail) or "(журнал пока пуст)"
    except OSError:
        return "(журнал пока пуст)"


class MoreBackend:
    # ---------- История ----------
    def history_entries(self) -> list:
        """[{time, command, summary, status}], новые первыми (history.get_all)."""
        import history
        return history.get_all()

    def history_clear(self):
        import atomic_io
        import history
        atomic_io.write_json(history.HISTORY_PATH, [])

    def repeat_command(self, command: str):
        self.run_command(command)

    # ---------- Расписание ----------
    def schedule_tasks(self) -> list:
        import scheduler
        return scheduler.get_all()

    def schedule_conditions(self) -> list:
        """[(ключ, подпись)] — берём из scheduler, чтобы список не разошёлся с планировщиком."""
        try:
            import scheduler
            return list(scheduler.CONDITIONS.items())
        except Exception:
            return CONDITIONS

    def schedule_wait_min(self) -> int:
        try:
            import scheduler
            return scheduler.WAIT_MIN
        except Exception:
            return WAIT_MIN

    def schedule_add(self, command: str, days: list, time_str: str, once: bool = False, condition: str = "") -> str:
        """Возвращает "" при успехе или текст ошибки."""
        command = (command or "").strip()
        if not command:
            return "Напиши, что нужно сделать."
        if not days:
            return "Выбери хотя бы один день."
        try:
            h, m = time_str.split(":")
            if not (0 <= int(h) < 24 and 0 <= int(m) < 60):
                raise ValueError
        except ValueError:
            return "Время нужно в формате ЧЧ:ММ."
        import scheduler
        scheduler.add_task(command, list(days), time_str, once=once, condition=condition)
        return ""

    def schedule_remove(self, task_id: int):
        import scheduler
        scheduler.remove_task(task_id)

    def schedule_set_enabled(self, task_id: int, enabled: bool):
        import scheduler
        scheduler.set_enabled(task_id, bool(enabled))

    # ---------- Проверка системы ----------
    def diag_run(self, on_done):
        """Поток: diagnostics.run_checks(); on_done(results, log_text) вызывается в потоке Qt.
        results = [(метка, ок?, подробности)]."""
        def work():
            try:
                import diagnostics
                results = diagnostics.run_checks()
            except Exception as e:
                results = [("Проверка системы", False, str(e))]
            tail = log_tail(45)
            self.post(lambda: on_done(results, tail))
        threading.Thread(target=work, daemon=True).start()

    def diag_log_tail(self, lines: int = 45) -> str:
        return log_tail(lines)

    def diag_open_audit(self) -> str:
        """Открывает audit_log.jsonl во внешней программе. Возвращает "" или текст ошибки."""
        try:
            import audit
            p = audit._path()
            if not os.path.exists(p):
                open(p, "a", encoding="utf-8").close()
            os.startfile(p)
            return ""
        except Exception as e:
            return f"Не удалось открыть журнал: {e}"

    def diag_mouse_test(self, on_done):
        """Поток: проверка попадания курсора в 5 точек экрана (без клика по кнопке — это было в Tk-версии).
        on_done(text) — в потоке Qt."""
        def work():
            lines = []
            try:
                import mouse
                if not mouse.AVAILABLE:
                    lines.append("Тест мыши работает только в Windows.")
                else:
                    import pyautogui
                    info = mouse.screen_info()
                    pw, ph = pyautogui.size()
                    scale = round(info.get("dpi", 96) / 96 * 100)
                    lines.append(f"Экран: {info.get('w')}×{info.get('h')} (pyautogui видит {pw}×{ph}), масштаб Windows ≈ {scale}%")
                    if (info.get("w"), info.get("h")) != (pw, ph):
                        lines.append("⚠️ Размеры не совпадают — координаты будут сдвинуты (проблема масштаба).")
                    w, h = info.get("w", pw), info.get("h", ph)
                    bad = 0
                    for name, (x, y) in {"центр": (w // 2, h // 2), "левый верх": (80, 80), "правый верх": (w - 80, 80),
                                         "правый низ": (w - 80, h - 80), "левый низ": (80, h - 80)}.items():
                        ax, ay = mouse.move_to(x, y)
                        ok = abs(ax - x) <= 2 and abs(ay - y) <= 2
                        bad += 0 if ok else 1
                        lines.append(f"{'✅' if ok else '❌'} Движение: {name} — нужно ({x}, {y}), курсор в ({ax}, {ay})")
                    lines.append("\nМышь управляется нормально." if not bad else
                                 "\nКурсор не попадает в нужные точки — пришли этот текст, разберусь с масштабом.")
            except Exception as e:
                lines.append(f"Ошибка теста: {e}")
            text = "\n".join(lines)
            self.post(lambda: on_done(text))
        threading.Thread(target=work, daemon=True).start()


class StubMore:
    """Заглушка с тестовыми данными (состояние живёт в памяти)."""
    post = staticmethod(lambda fn: fn())

    def __init__(self):
        self._hist = [
            {"time": time.strftime("%Y-%m-%d") + " 14:03:11", "command": "Поставь таймер на 10 минут",
             "summary": "Таймер запущен — сработает в 14:13.", "status": "готово"},
            {"time": time.strftime("%Y-%m-%d") + " 14:02:40", "command": "Создай папку на рабочем столе и файл блокнота",
             "summary": "Папка «Новая» создана, внутри привет.txt. Выполнено за 4 шага.", "status": "готово"},
            {"time": "2000-01-01 13:41:02", "command": "Открой ютуб и найди музыку", "summary": "Не нашёл поле поиска.",
             "status": "ошибка"},
            {"time": "2000-01-01 09:15:55", "command": "Удали все файлы с рабочего стола", "summary": "", "status": "отклонено"},
        ]
        self._tasks = [
            {"id": 1, "command": "открой почту", "days": ["mon", "tue", "wed", "thu", "fri"], "time": "09:00", "enabled": True},
            {"id": 2, "command": "сделай скриншот", "days": ["sat", "sun"], "time": "20:30", "enabled": False,
             "condition": "online"},
            {"id": 3, "command": "включи youtube", "days": ["fri"], "time": "18:00", "enabled": True, "once": True},
        ]

    def history_entries(self):
        return list(self._hist)

    def history_clear(self):
        self._hist = []

    def repeat_command(self, command):
        pass

    def schedule_tasks(self):
        return [dict(t) for t in self._tasks]

    def schedule_conditions(self):
        return CONDITIONS

    def schedule_wait_min(self):
        return WAIT_MIN

    def schedule_add(self, command, days, time_str, once=False, condition=""):
        command = (command or "").strip()
        if not command:
            return "Напиши, что нужно сделать."
        if not days:
            return "Выбери хотя бы один день."
        t = {"id": max((x["id"] for x in self._tasks), default=0) + 1, "command": command, "days": list(days),
             "time": time_str, "enabled": True}
        if once:
            t["once"] = True
        if condition:
            t["condition"] = condition
        self._tasks.append(t)
        return ""

    def schedule_remove(self, task_id):
        self._tasks = [t for t in self._tasks if t["id"] != task_id]

    def schedule_set_enabled(self, task_id, enabled):
        for t in self._tasks:
            if t["id"] == task_id:
                t["enabled"] = bool(enabled)

    def diag_run(self, on_done):
        from PySide6.QtCore import QTimer
        results = [("Python", True, "3.14.0"), ("Модуль pyautogui", True, "управление мышью/клавиатурой"),
                   ("Модуль keyboard", True, "горячие клавиши"),
                   ("Модуль sounddevice (доп.)", False, "запись с микрофона — не установлен, часть функций будет недоступна"),
                   ("Микрофон (доп.)", True, "Микрофон по умолчанию найден"),
                   ("Ключи Gemini", True, "настроено: 5"),
                   ("Локальная модель (Ollama)", False, "не запущена (необязательно — команды пойдут через Gemini)"),
                   ("Автозагрузка с Windows", True, "включена"),
                   ("Доступ на запись в папку приложения", False, "[Errno 13] Permission denied"),
                   ("Обновления", True, "установлена последняя версия 2.0.0")]
        log = "\n".join(f"2026-10-19 14:0{i}:1{i} Планировщик: проверка #{i}" for i in range(6))
        QTimer.singleShot(250, lambda: on_done(results, log))

    def diag_log_tail(self, lines=45):
        return "(журнал пока пуст)"

    def diag_open_audit(self):
        return ""

    def diag_mouse_test(self, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(250, lambda: on_done("Экран: 1920×1080 (pyautogui видит 1920×1080), масштаб Windows ≈ 125%\n"
                                               "✅ Движение: центр — нужно (960, 540), курсор в (960, 540)"))
