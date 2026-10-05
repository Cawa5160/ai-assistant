"""
Диалоговые окна приложения (всё, что всплывает поверх главного окна).

Все окна создаются как Toplevel единственного корня Tk (см. ui_thread.py) и
всегда — в интерфейсном потоке. Публичные функции можно смело вызывать из
любого потока: они сами передадут работу интерфейсному потоку и дождутся
ответа. Сами страницы главного окна (главная, история, расписание, настройки,
проверка системы) — в app_window.py.

Подтверждение плана — три уровня, по риску шагов:
    🟢 низкий  (открыть безопасное приложение/сайт) -> авто-выполнение с таймером и отменой
    🟡 обычный (клик, ввод текста, клавиши)         -> обычное "Разрешить"
    🔴 высокий (файлы, опасные слова)               -> ТОЛЬКО явное "Я понимаю риск и разрешаю"

Окна выполнения (прогресс / автопилот) НЕ закрываются сами: после завершения
остаётся журнал и кнопка "Закрыть" — читай результат сколько нужно.
"""
import base64
import io
import queue
import threading
import webbrowser
import tkinter as tk
from tkinter import scrolledtext

from PIL import Image, ImageDraw, ImageTk

import action_executor
import plan_validator
import theme
import ui_thread
from ui_thread import on_ui

RISK_ICON = {"low": "🟢", "normal": "🟡", "high": "🔴"}


def _format_step_args(step: dict) -> str:
    """Текст args для показа пользователю — с маскировкой чувствительного текста."""
    action = step.get("action")
    args = dict(step.get("args", {}))
    if action == "type_text" and args.get("sensitive"):
        return "{'text': '••••••• (скрыто)'}"
    return str(args)


def _step_line(i: int, step: dict) -> str:
    risk = RISK_ICON.get(plan_validator.classify_risk(step), "")
    try:
        import agent_loop
        text = agent_loop.describe_step(step)
    except Exception:
        text = f"{step.get('action')}: {_format_step_args(step)}"
    return f"{risk} {i + 1}. {text}"


# ---------- общие помощники ----------

def _new_window(title: str, width: int, height: int, topmost: bool = True):
    win = tk.Toplevel(ui_thread.root())
    theme.style_window(win)
    win.title(title)
    try:
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{width}x{height}+{max(0, (sw - width) // 2)}+{max(0, (sh - height) // 3)}")
    except Exception:
        win.geometry(f"{width}x{height}")
    if topmost:
        win.attributes("-topmost", True)
    return win


def _header(win, title: str, subtitle: str = "", color: str = None, wrap: int = 520):
    """Цветная шапка окна: крупный заголовок и (необязательно) пояснение белым текстом."""
    color = color or "#4338CA"
    band = tk.Frame(win, bg=color)
    band.pack(fill="x")
    tk.Label(band, text=title, font=("Segoe UI", 15, "bold"), bg=color, fg="white", anchor="w").pack(
        fill="x", padx=18, pady=(14, 0 if subtitle else 14))
    if subtitle:
        tk.Label(band, text=subtitle, font=theme.FONT_BODY, bg=color, fg="#E8E9FF", anchor="w", justify="left",
                 wraplength=wrap).pack(fill="x", padx=18, pady=(2, 14))
    return band


def _present(win):
    """Вытаскивает окно на передний план и даёт ему фокус."""
    try:
        win.lift()
        win.focus_force()
    except Exception:
        pass


def _exists(win) -> bool:
    try:
        return bool(win.winfo_exists())
    except Exception:
        return False


# ---------- быстрый ввод команды (горячая клавиша) ----------

@on_ui
def ask_command() -> str:
    """Маленькое окно "Что сделать?" — открывается по горячей клавише поверх любых окон."""
    result = {"text": ""}
    win = _new_window("AI Ассистент — команда", 560, 190)

    _header(win, "Что сделать?", "Напиши команду и нажми Enter", wrap=520)
    entry = tk.Entry(win, font=("Segoe UI", 12), relief="flat", bg=theme.INPUT_BG, fg=theme.TEXT,
                     insertbackground=theme.TEXT, highlightthickness=1, highlightbackground=theme.BORDER,
                     highlightcolor=theme.PRIMARY)
    entry.pack(fill="x", padx=18, pady=(14, 10), ipady=6)

    def submit(event=None):
        result["text"] = entry.get()
        win.destroy()

    entry.bind("<Return>", submit)
    entry.bind("<Escape>", lambda e: win.destroy())
    tk.Button(win, text="➤ Выполнить", bg=theme.PRIMARY, fg="white", font=("Segoe UI", 10, "bold"),
              relief="flat", padx=16, command=submit).pack()
    _present(win)
    entry.focus_set()
    win.wait_window()
    return result["text"]


# ---------- подтверждение обычного плана ----------

@on_ui
def confirm_plan(summary: str, steps: list, screenshot_b64=None, auto_seconds: int = 0):
    """
    Показывает план (от локальной модели) с галочкой на каждом шаге.
    Возвращает None при отмене, иначе — список шагов, у которых галочка осталась.
    """
    result = {"confirmed": False}
    check_vars = []
    risk = plan_validator.highest_risk(steps)

    win = _new_window("Подтверди план действий", 620, 600)

    band_color = {"low": "#15803D", "normal": "#4338CA", "high": "#B91C1C"}.get(risk, "#4338CA")
    band_title = {"low": "Безопасные действия", "normal": "Подтверди действия", "high": "Внимание: высокий риск"}.get(risk, "Подтверди действия")
    band = tk.Frame(win, bg=band_color)
    band.pack(fill="x")
    tk.Label(band, text=band_title, font=("Segoe UI", 15, "bold"), bg=band_color, fg="white",
             anchor="w").pack(fill="x", padx=18, pady=(14, 0))
    tk.Label(band, text=summary, font=theme.FONT_BODY, bg=band_color, fg="#F1F5F9", anchor="w", justify="left",
             wraplength=580).pack(fill="x", padx=18, pady=(2, 14))

    click_steps = [s for s in steps if s.get("action") in plan_validator.POINTER_ACTIONS
                   and "x" in s.get("args", {})]
    if screenshot_b64 and click_steps:
        try:
            img = Image.open(io.BytesIO(base64.b64decode(screenshot_b64))).convert("RGB")
            draw = ImageDraw.Draw(img)
            for i, s in enumerate(click_steps, 1):
                x, y = s["args"]["x"], s["args"]["y"]
                r = 14
                draw.ellipse((x - r, y - r, x + r, y + r), outline="red", width=4)
                draw.text((x + r + 2, y - 8), str(i), fill="red")
            img.thumbnail((500, 260))
            photo = ImageTk.PhotoImage(img)
            img_label = tk.Label(win, image=photo, bg=theme.BG)
            img_label.image = photo  # держим ссылку, иначе Tk уберёт картинку
            img_label.pack(pady=5)
        except Exception:
            pass

    tk.Label(win, text="Шаги (🟢 безопасно · 🟡 обычное · 🔴 высокий риск). Сними галочку, чтобы пропустить шаг:",
             font=theme.FONT_HEADING, bg=theme.BG, wraplength=560, justify="left").pack(
        pady=(10, 0), padx=10, anchor="w")

    list_box = tk.Frame(win, bg=theme.BG)
    list_box.pack(fill="both", expand=True, padx=(10, 6), pady=5)
    canvas = tk.Canvas(list_box, highlightthickness=0, height=210, bg=theme.BG)
    scrollbar = tk.Scrollbar(list_box, orient="vertical", command=canvas.yview)
    steps_frame = tk.Frame(canvas, bg=theme.BG)
    steps_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=steps_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    canvas.pack(side="left", fill="both", expand=True)

    any_blocked = False
    for i, step in enumerate(steps):
        action = step.get("action")
        args = step.get("args", {})
        warn = ""
        if action == "create_file" and not action_executor.is_path_allowed(args.get("path", "")):
            warn, any_blocked = "  ⚠️ путь вне разрешённых папок", True
        elif action == "move_file" and not (
            action_executor.is_path_allowed(args.get("src", ""))
            and action_executor.is_path_allowed(args.get("dst", ""))
        ):
            warn, any_blocked = "  ⚠️ путь вне разрешённых папок", True

        var = tk.BooleanVar(master=win, value=True)
        check_vars.append(var)
        tk.Checkbutton(steps_frame, text=_step_line(i, step) + warn, variable=var, anchor="w",
                       justify="left", font=theme.FONT_MONO, wraplength=520,
                       bg=theme.BG).pack(anchor="w", fill="x")

    if any_blocked:
        tk.Label(win, text="⚠️ Часть шагов затронет папки вне разрешённого списка — при выполнении "
                            "они всё равно будут заблокированы, независимо от галочки.",
                 font=theme.FONT_SMALL, fg=theme.WARNING, bg=theme.BG,
                 wraplength=560, justify="left").pack(pady=(5, 0), padx=10)

    def on_yes():
        result["confirmed"] = True
        win.destroy()

    def on_no():
        win.destroy()

    status_label = tk.Label(win, text="", font=theme.FONT_BODY, fg=theme.PRIMARY, bg=theme.BG)
    status_label.pack(pady=(8, 0))
    btn_frame = tk.Frame(win, bg=theme.BG)
    btn_frame.pack(pady=10)
    bold = ("Segoe UI", 10, "bold")

    if risk == "high":
        tk.Label(win, text="⚠️ Этот план содержит действия, которые могут быть необратимы "
                            "(файлы, системные изменения, опасные формулировки).",
                 font=("Segoe UI", 9, "bold"), fg=theme.DANGER, bg=theme.BG,
                 wraplength=560, justify="left").pack(pady=(0, 5), padx=10)
        tk.Button(btn_frame, text="⚠️ Я понимаю риск и разрешаю", bg=theme.DANGER, fg="white", font=bold,
                  relief="flat", width=28, command=on_yes).pack(side="left", padx=10)
        tk.Button(btn_frame, text="❌ Отмена", bg=theme.NEUTRAL, fg="white", font=bold,
                  relief="flat", width=15, command=on_no).pack(side="left", padx=10)
    elif risk == "low" or (risk == "normal" and auto_seconds > 0):
        countdown = {"n": auto_seconds if auto_seconds > 0 else 3}
        auto_label = "🟢 Безопасные действия" if risk == "low" else "⏱ Простая команда"

        def tick():
            if not _exists(win):
                return
            if countdown["n"] <= 0:
                on_yes()
                return
            status_label.config(text=f"{auto_label} — выполнится само через {countdown['n']} с… (Отмена — остановить)")
            countdown["n"] -= 1
            win.after(1000, tick)

        tk.Button(btn_frame, text="✅ Выполнить сейчас", bg=theme.SUCCESS, fg="white", font=bold,
                  relief="flat", width=18, command=on_yes).pack(side="left", padx=10)
        tk.Button(btn_frame, text="❌ Отмена", bg=theme.DANGER, fg="white", font=bold,
                  relief="flat", width=15, command=on_no).pack(side="left", padx=10)
        tick()
    else:
        tk.Button(btn_frame, text="✅ Разрешить", bg=theme.SUCCESS, fg="white", font=bold,
                  relief="flat", width=15, command=on_yes).pack(side="left", padx=10)
        tk.Button(btn_frame, text="❌ Отмена", bg=theme.DANGER, fg="white", font=bold,
                  relief="flat", width=15, command=on_no).pack(side="left", padx=10)

    _present(win)
    win.wait_window()
    if not result["confirmed"]:
        return None
    return [s for s, v in zip(steps, check_vars) if v.get()]


# ---------- уведомление-запасной вариант (если нет системных toast) ----------

def show_notification(message: str):
    """Небольшое окно-уведомление. Не блокирует вызывающий поток."""
    ui_thread.post(_show_notification, message)


def _show_notification(message: str):
    win = _new_window("AI Ассистент", 440, 150)
    tk.Label(win, text=message, font=theme.FONT_BODY, bg=theme.BG,
             wraplength=410, justify="left").pack(pady=18, padx=12)
    tk.Button(win, text="OK", bg=theme.PRIMARY, fg="white", relief="flat", width=10,
              command=win.destroy).pack()
    _present(win)
    win.after(9000, lambda: win.destroy() if _exists(win) else None)


# ---------- мастер первого запуска ----------

@on_ui
def first_run_wizard() -> list:
    """Просит вписать бесплатный(е) Gemini-ключ(и) — можно несколько, по одному на строку."""
    result = {"keys": []}
    win = _new_window("Добро пожаловать — настройка", 580, 600)

    _header(win, "Добро пожаловать!", "Для работы нужен бесплатный ключ Google Gemini — это занимает минуту.", wrap=540)
    instructions = (
        "1.  Открой aistudio.google.com\n"
        "2.  Войди через свой Google-аккаунт (Gmail)\n"
        "3.  Нажми «Get API key» → «Create API key»\n"
        "4.  Скопируй ключ и вставь его ниже\n\n"
        "Можно вставить НЕСКОЛЬКО ключей (с разных Google-аккаунтов), по одному на строку — "
        "тогда при лимите на одном программа сама переключится на следующий."
    )
    tk.Label(win, text=instructions, justify="left", font=theme.FONT_BODY, bg=theme.BG,
             wraplength=530).pack(pady=(12, 5), padx=22, anchor="w")
    tk.Button(win, text="🌐 Открыть aistudio.google.com", bg=theme.PRIMARY, fg="white", relief="flat",
              font=("Segoe UI", 10, "bold"),
              command=lambda: webbrowser.open("https://aistudio.google.com/apikey")).pack(pady=5)

    keys_box = scrolledtext.ScrolledText(win, width=58, height=5, font=theme.FONT_MONO, relief="flat",
                                         highlightthickness=1, highlightbackground=theme.BORDER)
    keys_box.pack(pady=10, padx=22, fill="x")

    def submit():
        lines = keys_box.get("1.0", tk.END).splitlines()
        result["keys"] = [line.strip() for line in lines if line.strip()]
        win.destroy()

    btn_frame = tk.Frame(win, bg=theme.BG)
    btn_frame.pack(pady=5)
    tk.Button(btn_frame, text="Сохранить и продолжить", bg=theme.SUCCESS, fg="white", relief="flat",
              command=submit).pack(side="left", padx=5)
    tk.Button(btn_frame, text="Пропустить", relief="flat", command=win.destroy).pack(side="left", padx=5)
    tk.Label(win, text="Ключи можно добавить/изменить позже: Настройки в главном окне",
             font=theme.FONT_SMALL, fg=theme.TEXT_MUTED, bg=theme.BG).pack(pady=(10, 0))

    _present(win)
    keys_box.focus_set()
    win.wait_window()
    return result["keys"]


# ---------- автопилот: подтверждение старта ----------

@on_ui
def confirm_agent_start(goal: str, intent: str, steps_text: list, max_steps: int, auto_seconds: int = 0) -> bool:
    """
    Один раз спрашивает разрешение запустить автопилот на всю задачу (а не на каждый шаг).
    auto_seconds > 0 — запустится само (для безопасных задач "просто открой"), отмена рядом.
    """
    result = {"ok": False}
    win = _new_window("Автопилот — подтверждение", 600, 540)

    _header(win, "🤖 Автопилот", f"Цель: {goal}", wrap=560)
    tk.Label(win, text="ИИ посмотрел на экран и предлагает начать так:", font=theme.FONT_HEADING,
             bg=theme.BG).pack(padx=18, pady=(14, 2), anchor="w")
    if intent:
        tk.Label(win, text=intent, font=theme.FONT_BODY, bg=theme.BG, fg=theme.PRIMARY,
                 wraplength=540, justify="left").pack(padx=18, anchor="w")
    for i, text in enumerate(steps_text, 1):
        tk.Label(win, text=f"   {i}. {text}", font=theme.FONT_MONO, bg=theme.BG,
                 wraplength=540, justify="left").pack(padx=18, anchor="w")

    info = (f"Дальше ИИ сам будет смотреть на экран и делать по одному шагу (не больше {max_steps}).\n"
            "Потенциально опасные действия — файлы, установка, удаление — он спросит отдельно.\n"
            "Остановить в любой момент: кнопка 🛑 в окне автопилота или резкий рывок мыши "
            "в левый верхний угол экрана.")
    tk.Label(win, text=info, font=theme.FONT_SMALL, fg=theme.TEXT_MUTED, bg=theme.BG,
             wraplength=540, justify="left").pack(padx=18, pady=(16, 6), anchor="w")

    status_label = tk.Label(win, text="", font=theme.FONT_BODY, fg=theme.PRIMARY, bg=theme.BG)
    status_label.pack(pady=(4, 0))

    def on_yes():
        result["ok"] = True
        win.destroy()

    btns = tk.Frame(win, bg=theme.BG)
    btns.pack(pady=12)
    bold = ("Segoe UI", 10, "bold")
    tk.Button(btns, text="▶ Запустить автопилот", bg=theme.SUCCESS, fg="white", font=bold, relief="flat",
              width=22, command=on_yes).pack(side="left", padx=10)
    tk.Button(btns, text="❌ Отмена", bg=theme.DANGER, fg="white", font=bold, relief="flat",
              width=14, command=win.destroy).pack(side="left", padx=10)

    if auto_seconds > 0:
        countdown = {"n": auto_seconds}

        def tick():
            if not _exists(win):
                return
            if countdown["n"] <= 0:
                on_yes()
                return
            status_label.config(text=f"Безопасная задача — запустится сама через {countdown['n']}…")
            countdown["n"] -= 1
            win.after(1000, tick)

        tick()

    _present(win)
    win.wait_window()
    return result["ok"]


# ---------- окно выполнения обычного плана ----------

class ExecutionProgressWindow:
    """
    Живой прогресс шагов плана. Создаётся в интерфейсном потоке (см. open_progress_window).
    Рабочий поток дёргает mark_step()/finish(). После завершения окно НЕ закрывается само —
    кнопка "Остановить" превращается в "Закрыть".
    """

    STATUS_ICON = {
        "pending": "⏳", "running": "🔄", "done": "✅",
        "error": "❌", "skipped": "⏭️", "warning": "⚠️",
    }

    def __init__(self, steps: list):
        self.steps = steps
        self.abort_event = threading.Event()
        self.pause_event = threading.Event()
        self._queue = queue.Queue()
        self._labels = []
        self._finished = False

        self.win = _new_window("Выполнение плана", 560, 460, topmost=False)
        exclude_from_capture(self.win)          # окно прогресса не должно попадать на скриншоты
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        _header(self.win, "Выполняю план действий", "Окно можно не закрывать — оно покажет результат", wrap=520)
        list_frame = tk.Frame(self.win, bg=theme.BG)
        list_frame.pack(fill="both", expand=True, padx=15, pady=(10, 0))
        for i, step in enumerate(steps):
            text = f"{self.STATUS_ICON['pending']}  {i + 1}. {self._describe(step)}"
            lbl = tk.Label(list_frame, text=text, anchor="w", justify="left", font=theme.FONT_BODY,
                           wraplength=520, bg=theme.BG)
            lbl.pack(anchor="w", pady=2)
            self._labels.append(lbl)

        self.status_label = tk.Label(self.win, text="Выполняется...", font=theme.FONT_BODY, bg=theme.BG,
                                     wraplength=520)
        self.status_label.pack(pady=(10, 5))
        self.stop_btn = tk.Button(self.win, text="🛑 Остановить", bg=theme.DANGER, fg="white", relief="flat",
                                  font=("Segoe UI", 10, "bold"), command=self._on_stop)
        self.stop_btn.pack(pady=10)

        _present(self.win)
        self.win.after(100, self._poll)

    @staticmethod
    def _describe(step: dict) -> str:
        try:
            import agent_loop
            return agent_loop.describe_step(step)
        except Exception:
            return f"{step.get('action')}: {_format_step_args(step)}"

    # --- вызывается из рабочего потока ---
    def mark_step(self, index: int, status: str):
        self._queue.put(("step", index, status))

    def finish(self, message: str):
        self._queue.put(("finish", message))

    # --- работает в интерфейсном потоке ---
    def _on_stop(self):
        self.abort_event.set()
        self.status_label.config(text="Останавливаю после текущего шага...")

    def _on_close(self):
        if self._finished:
            self.win.destroy()
        else:
            self._on_stop()          # закрытие крестиком посреди работы = остановка, окно остаётся до конца

    def _poll(self):
        if not _exists(self.win):
            return
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "step":
                    _, idx, status = item
                    icon = self.STATUS_ICON.get(status, "•")
                    step = self.steps[idx]
                    self._labels[idx].config(
                        text=f"{icon}  {idx + 1}. {self._describe(step)}")
                else:
                    self._finished = True
                    self.status_label.config(text=item[1])
                    self.stop_btn.config(text="Закрыть", bg=theme.NEUTRAL, command=self.win.destroy)
        except queue.Empty:
            pass
        self.win.after(100, self._poll)


@on_ui
def open_progress_window(steps: list) -> ExecutionProgressWindow:
    return ExecutionProgressWindow(steps)


# ---------- не попадать в скриншоты ----------

def exclude_from_capture(win):
    """
    Windows 10 (версия 2004+): окно видно пользователю, но НЕ попадает в скриншоты и записи экрана.
    Так ИИ не видит ни панель автопилота, ни окно журнала и не пытается на них нажимать.
    На старых Windows просто ничего не делает (в промпте ИИ есть просьба игнорировать эти окна).
    """
    try:
        import ctypes
        win.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id()) or win.winfo_id()
        ctypes.windll.user32.SetWindowDisplayAffinity(hwnd, 0x00000011)   # WDA_EXCLUDEFROMCAPTURE
    except Exception:
        pass


# ---------- да / нет ----------

@on_ui
def ask_yes_no(title: str, text: str, yes: str = "Да", no: str = "Нет") -> bool:
    result = {"ok": False}
    win = _new_window(title, 540, 300)
    _header(win, title, wrap=500)
    tk.Label(win, text=text, font=theme.FONT_BODY, bg=theme.BG, wraplength=500, justify="left").pack(
        padx=22, pady=14, anchor="w")

    def on_yes():
        result["ok"] = True
        win.destroy()

    row = tk.Frame(win, bg=theme.BG)
    row.pack(pady=16)
    bold = ("Segoe UI", 10, "bold")
    tk.Button(row, text=yes, bg=theme.SUCCESS, fg="white", font=bold, relief="flat", width=18,
              command=on_yes).pack(side="left", padx=8)
    tk.Button(row, text=no, bg=theme.NEUTRAL, fg="white", font=bold, relief="flat", width=14,
              command=win.destroy).pack(side="left", padx=8)
    _present(win)
    win.wait_window()
    return result["ok"]


# ---------- окно "Слушаю…" ----------

class VoiceWindow:
    """Маленькое окно записи голоса: что происходит сейчас + живой индикатор уровня звука + отмена."""

    def __init__(self):
        self.cancelled = threading.Event()
        self._queue = queue.Queue()
        self.win = _new_window("Голосовая команда", 440, 330)
        self.win.protocol("WM_DELETE_WINDOW", self._cancel)

        _header(self.win, "Голосовая команда", "Говори — я слушаю", wrap=400)
        tk.Label(self.win, text="🎙", font=("Segoe UI", 38), bg=theme.BG, fg=theme.PRIMARY).pack(pady=(10, 0))
        self.state = tk.Label(self.win, text="Подключаю микрофон…", font=theme.FONT_HEADING, bg=theme.BG,
                              wraplength=380)
        self.state.pack(pady=(2, 6))
        self.level = tk.Label(self.win, text="▯" * 14, font=("Consolas", 16), bg=theme.BG, fg=theme.SUCCESS)
        self.level.pack()
        tk.Label(self.win, text="Скажи, что нужно сделать. Запись остановится сама, когда договоришь.",
                 font=theme.FONT_SMALL, fg=theme.TEXT_MUTED, bg=theme.BG, wraplength=380).pack(pady=(8, 4))
        tk.Button(self.win, text="Отмена", relief="flat", width=14, command=self._cancel).pack(pady=8)

        _present(self.win)
        self.win.after(60, self._poll)

    # --- из рабочего потока ---
    def set_state(self, text: str):
        self._queue.put(("state", text))

    def set_level(self, value: float):
        self._queue.put(("level", value))

    def close(self):
        self._queue.put(("close",))

    # --- в интерфейсном потоке ---
    def _cancel(self):
        self.cancelled.set()
        self.state.config(text="Отменяю…")

    def _poll(self):
        if not _exists(self.win):
            self.cancelled.set()
            return
        level = None
        try:
            while True:
                item = self._queue.get_nowait()
                if item[0] == "state":
                    self.state.config(text=item[1])
                elif item[0] == "level":
                    level = item[1]
                else:
                    self.win.destroy()
                    return
        except queue.Empty:
            pass
        if level is not None:
            n = max(0, min(14, int(level * 14)))
            self.level.config(text="█" * n + "▯" * (14 - n))
        self.win.after(60, self._poll)


@on_ui
def open_voice_window() -> VoiceWindow:
    return VoiceWindow()


# ---------- панель автопилота ----------

class AgentBar:
    """
    Узкая панель у верхнего края экрана: что автопилот делает сейчас, кнопки «журнал» и «Стоп».
    Не попадает в скриншоты (см. exclude_from_capture). Её можно перетащить мышью.
    """

    def __init__(self, owner):
        self.win = tk.Toplevel(ui_thread.root())
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.95)
        except Exception:
            pass
        self.win.configure(bg=theme.PRIMARY)
        width, height = 540, 44
        try:
            x = max(0, (self.win.winfo_screenwidth() - width) // 2)
            self.win.geometry(f"{width}x{height}+{x}+6")
        except Exception:
            self.win.geometry(f"{width}x{height}")

        bold = ("Segoe UI", 10, "bold")
        self.label = tk.Label(self.win, text="🤖 Автопилот запускается…", bg=theme.PRIMARY, fg="white",
                              font=bold, anchor="w", padx=12)
        self.label.pack(side="left", fill="both", expand=True)
        tk.Button(self.win, text="🛑 Стоп", bg=theme.DANGER, fg="white", relief="flat", font=bold,
                  command=owner._on_stop).pack(side="right", fill="y")
        self.pause_btn = tk.Button(self.win, text="⏸ Пауза", bg=theme.PRIMARY_DARK, fg="white", relief="flat",
                                   font=bold, command=owner.toggle_pause)
        self.pause_btn.pack(side="right", fill="y")
        tk.Button(self.win, text="📋 Журнал", bg=theme.PRIMARY_DARK, fg="white", relief="flat", font=bold,
                  command=owner.toggle_log).pack(side="right", fill="y")

        self._drag = (0, 0)
        self.label.bind("<ButtonPress-1>", self._start_drag)
        self.label.bind("<B1-Motion>", self._on_drag)
        exclude_from_capture(self.win)

    def _start_drag(self, event):
        self._drag = (event.x_root - self.win.winfo_x(), event.y_root - self.win.winfo_y())

    def _on_drag(self, event):
        self.win.geometry(f"+{event.x_root - self._drag[0]}+{event.y_root - self._drag[1]}")

    def set_text(self, text: str):
        self.label.config(text="🤖 " + text[:64])

    def set_paused(self, paused: bool):
        self.pause_btn.config(text="▶ Продолжить" if paused else "⏸ Пауза")

    def close(self):
        try:
            self.win.destroy()
        except Exception:
            pass


# ---------- окно автопилота ----------

class AgentWindow:
    """
    Журнал автопилота. Пока ИИ работает, окно СВЁРНУТО (и не попадает в скриншоты), а на экране остаётся
    только узкая панель с «🛑 Стоп» — иначе ИИ видел бы собственное приложение и путался бы в нём.
    Окно само разворачивается, когда нужен твой ответ про опасное действие, и когда задача закончилась;
    после этого остаётся открытым, пока ты его не закроешь.
    Из рабочего потока вызываются add_step / set_result / request_confirm / finish.
    """

    def __init__(self, goal: str, max_steps: int):
        self.abort_event = threading.Event()
        self.pause_event = threading.Event()
        self._queue = queue.Queue()
        self._confirm_event = threading.Event()
        self._confirm_answer = False
        self._finished = False
        self._log_visible = False

        self.win = _new_window("Автопилот", 680, 620, topmost=False)
        self.win.protocol("WM_DELETE_WINDOW", self._on_close)

        _header(self.win, "🤖 Автопилот", f"Цель: {goal}", wrap=640)

        self.preview_label = tk.Label(self.win, text="Здесь появится кадр, который видит ИИ (красный круг — куда он собирается нажать)",
                                      font=theme.FONT_BODY, fg=theme.TEXT_MUTED, bg=theme.BG)
        self.preview_label.pack(padx=12, pady=(6, 0))
        self._preview_photo = None
        self.log_box = scrolledtext.ScrolledText(self.win, width=78, height=12, font=theme.FONT_MONO, wrap="word")
        self.log_box.pack(padx=12, pady=8, fill="both", expand=True)
        self.log_box.config(state="disabled")

        self.confirm_frame = tk.Frame(self.win, bg=theme.BG)
        self.confirm_label = tk.Label(self.confirm_frame, text="", font=theme.FONT_BODY, fg=theme.DANGER,
                                      bg=theme.BG, wraplength=620, justify="left")
        self.confirm_label.pack(pady=(0, 6))
        confirm_btns = tk.Frame(self.confirm_frame, bg=theme.BG)
        confirm_btns.pack()
        bold = ("Segoe UI", 10, "bold")
        tk.Button(confirm_btns, text="⚠️ Да, разрешаю", bg=theme.DANGER, fg="white", font=bold, relief="flat",
                  command=lambda: self._answer(True)).pack(side="left", padx=8)
        tk.Button(confirm_btns, text="Нет, стоп", bg=theme.NEUTRAL, fg="white", font=bold, relief="flat",
                  command=lambda: self._answer(False)).pack(side="left", padx=8)

        self.status_label = tk.Label(self.win, text=f"Работаю… (не больше {max_steps} шагов)",
                                     font=theme.FONT_BODY, bg=theme.BG)
        self.status_label.pack(pady=(4, 2))
        self.stop_btn = tk.Button(self.win, text="🛑 Остановить", bg=theme.DANGER, fg="white", relief="flat",
                                  font=bold, command=self._on_stop)
        self.stop_btn.pack(pady=(4, 6))
        tk.Button(self.win, text="📋 Копировать журнал", bg=theme.NEUTRAL, fg="white", relief="flat",
                  font=("Segoe UI", 9), command=self._copy_log).pack(pady=(0, 12))

        exclude_from_capture(self.win)
        self.bar = AgentBar(self)
        self.win.iconify()                       # не мешаем экрану, пока ИИ смотрит на него
        self.win.after(100, self._poll)

    # --- вызывается из РАБОЧЕГО потока ---
    def add_step(self, n: int, text: str):
        self._queue.put(("step", n, text))

    def set_result(self, text: str):
        self._queue.put(("result", text))

    def set_preview(self, image):
        """image — готовый PIL-кадр (с отметками); показывается в журнале."""
        self._queue.put(("preview", image))

    def request_confirm(self, text: str) -> bool:
        """Показывает вопрос в окне и ждёт ответа. Остановка автопилота = "нет"."""
        self._confirm_event.clear()
        self._confirm_answer = False
        self._queue.put(("confirm", text))
        while not self._confirm_event.wait(0.2):
            if self.abort_event.is_set():
                return False
        return self._confirm_answer

    def finish(self, ok: bool, message: str):
        self._queue.put(("finish", ok, message))

    # --- работает в интерфейсном потоке ---
    def _append(self, text: str):
        self.log_box.config(state="normal")
        self.log_box.insert(tk.END, text)
        self.log_box.see(tk.END)
        self.log_box.config(state="disabled")

    def _show_log(self, topmost: bool = False):
        self._log_visible = True
        self.win.deiconify()
        self.win.lift()
        self.win.attributes("-topmost", topmost)

    def toggle_log(self):
        if self._log_visible:
            self._log_visible = False
            self.win.iconify()
        else:
            self._show_log()

    def _answer(self, value: bool):
        self._confirm_answer = value
        self.confirm_frame.pack_forget()
        self._confirm_event.set()
        if not self._finished:
            self._log_visible = False
            self.win.iconify()                   # ответил — снова освобождаем экран для ИИ

    def _copy_log(self):
        """Копирует весь журнал автопилота в буфер обмена (чтобы прислать ошибку текстом, а не фото экрана)."""
        try:
            text = self.log_box.get("1.0", "end").strip()
            self.win.clipboard_clear()
            self.win.clipboard_append(text)
            self.status_label.config(text="Журнал скопирован в буфер обмена.")
        except Exception:
            pass

    def toggle_pause(self):
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.bar.set_paused(False)
            self.bar.set_text("Продолжаю…")
        else:
            self.pause_event.set()
            self.bar.set_paused(True)
            self.bar.set_text("Пауза — нажми «Продолжить»")

    def _on_stop(self):
        self.abort_event.set()
        self.pause_event.clear()
        self.status_label.config(text="Останавливаю после текущего действия…")
        self.bar.set_text("Останавливаю…")
        if not self._confirm_event.is_set():
            self._answer(False)

    def _on_close(self):
        if self._finished:
            self.bar.close()
            self.win.destroy()
        else:
            self._on_stop()

    def _poll(self):
        if not _exists(self.win):
            self.abort_event.set()               # окно уничтожено — рабочий поток не должен ждать вечно
            self._confirm_event.set()
            self.bar.close()
            return
        try:
            while True:
                item = self._queue.get_nowait()
                kind = item[0]
                if kind == "step":
                    self._append(f"\n{item[1]}. {item[2]}\n")
                    self.bar.set_text(f"Шаг {item[1]}: {item[2]}")
                elif kind == "result":
                    self._append(f"     → {item[1]}\n")
                    if item[1].startswith("⏳"):
                        self.bar.set_text(item[1])
                elif kind == "preview":
                    try:
                        self._preview_photo = ImageTk.PhotoImage(item[1])
                        self.preview_label.config(image=self._preview_photo, text="")
                    except Exception:
                        pass
                elif kind == "confirm":
                    self.confirm_label.config(text=item[1])
                    self.confirm_frame.pack(before=self.status_label, pady=6)
                    self._show_log(topmost=True)  # нужен твой ответ — окно выходит вперёд
                elif kind == "finish":
                    ok, message = item[1], item[2]
                    self._finished = True
                    self._append(f"\n{'✅' if ok else '⛔'} {message}\n")
                    self.status_label.config(text="Готово." if ok else "Остановлено.")
                    self.stop_btn.config(text="Закрыть", bg=theme.NEUTRAL, command=self._on_close)
                    self.bar.close()
                    self._show_log(topmost=True)  # показываем итог и оставляем, пока сам не закроешь
        except queue.Empty:
            pass
        self.win.after(100, self._poll)


@on_ui
def open_agent_window(goal: str, max_steps: int) -> AgentWindow:
    return AgentWindow(goal, max_steps)
