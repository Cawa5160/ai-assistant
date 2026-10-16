"""
Главное окно приложения — всё в одном месте, как у обычной программы:
слева навигация, справа страницы (Главная, История, Расписание, Настройки,
Проверка системы). Крестик НЕ закрывает программу, а сворачивает её в трей —
она продолжает работать в фоне (горячая клавиша, расписание, голос).
Полностью выйти можно кнопкой «Выйти из приложения» слева внизу или через трей.

Окно живёт в главном (интерфейсном) потоке. Всё долгое (проверка ключей, микрофона,
диагностика) выполняется в отдельных потоках, а результат возвращается через
ui_thread.post — интерфейс не подвисает.
"""
import os
import re
import threading
import time
import tkinter as tk
from tkinter import scrolledtext

from PIL import Image, ImageTk

import config
import history
import scheduler
import theme
import ui_kit
import ui_thread
import voice_input

NAV = [
    ("home", "🏠", "Главная"),
    ("history", "🕘", "История"),
    ("schedule", "📅", "Расписание"),
    ("memory", "🧠", "Макросы и заметки"),
    ("settings", "⚙️", "Настройки"),
    ("diag", "🩺", "Проверка системы"),
]

NAV_GLYPHS = {"home": "\uE80F", "history": "\uE81C", "schedule": "\uE787", "memory": "\uE8F1",
              "settings": "\uE713", "diag": "\uE9D9"}     # значки Segoe Fluent / MDL2 (на Windows 10/11)

EXAMPLES = [
    "Открой браузер",
    "Найди информацию о погоде",
    "Создай файл заметки на рабочем столе",
    "Покажи рабочий стол",
]

DAYS = [("mon", "Пн"), ("tue", "Вт"), ("wed", "Ср"), ("thu", "Чт"), ("fri", "Пт"), ("sat", "Сб"), ("sun", "Вс")]
DAY_NAMES = dict(DAYS)
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


# ---------- маленькие помощники оформления ----------

def _font(size=10, bold=False):
    return ("Segoe UI", size, "bold") if bold else ("Segoe UI", size)


def _button(parent, text, command, kind="primary", small=False, **kw):
    colors = {
        "primary": (theme.FILL, "#FFFFFF"),
        "success": ("#15803D", "#FFFFFF"),
        "danger": ("#B91C1C", "#FFFFFF"),
        "neutral": (theme.CARD_BG, theme.TEXT),
        "ghost": (theme.BG, theme.PRIMARY),
    }
    bg, fg = colors[kind]
    return tk.Button(
        parent, text=text, command=command, bg=bg, fg=fg, activebackground=bg, activeforeground=fg,
        relief="flat", bd=0, cursor="hand2", font=_font(9 if small else 10, kind in ("primary", "success", "danger")),
        padx=10 if small else 14, pady=4 if small else 7,
        highlightthickness=1 if kind in ("neutral", "ghost") else 0,
        highlightbackground=theme.BORDER, **kw)


def _card(parent, **kw):
    return tk.Frame(parent, bg=theme.CARD_BG, highlightthickness=1,
                    highlightbackground=theme.BORDER, highlightcolor=theme.BORDER, **kw)


def _mix(c1: str, c2: str, t: float) -> str:
    a = [int(c1.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    b = [int(c2.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4)]
    return "#%02X%02X%02X" % tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _hero(parent, title, subtitle, height=118):
    """Шапка страницы: градиент акцентного цвета и белый текст."""
    canvas = tk.Canvas(parent, height=height, highlightthickness=0, bd=0, bg=theme.BG)
    accent = theme.ACCENTS["light"].get(config.get("accent") or "indigo", theme.ACCENTS["light"]["indigo"])
    start, end = accent[1], _mix(accent[1], "#9333EA", 0.6)      # тёмный градиент: белый текст читается в обеих темах

    def draw(event=None):
        canvas.delete("all")
        w = max(canvas.winfo_width(), 200)
        strips = 64
        for i in range(strips):
            canvas.create_rectangle(i * w / strips, 0, (i + 1) * w / strips + 1, height, outline="",
                                    fill=_mix(start, end, i / (strips - 1)))
        canvas.create_text(26, 30, text=title, anchor="w", fill="#FFFFFF", font=_font(20, True))
        canvas.create_text(26, 62, text=subtitle, anchor="nw", fill="#E8E9FF", font=_font(10),
                           width=max(300, w - 52))

    canvas.bind("<Configure>", draw)
    return canvas


def _days_text(days) -> str:
    """['mon',...] -> «Каждый день» / «По будням» / «По выходным» / «Пн, Ср»."""
    d = set(days or [])
    if d == {k for k, _ in DAYS}:
        return "Каждый день"
    if d == {"mon", "tue", "wed", "thu", "fri"}:
        return "По будням"
    if d == {"sat", "sun"}:
        return "По выходным"
    return ", ".join(DAY_NAMES.get(k, k) for k, _ in DAYS if k in d) or "—"


def _human_time(stamp: str) -> str:
    """'2026-10-05 14:05:33' -> 'сегодня, 14:05' / 'вчера, 14:05' / '03.10, 14:05'."""
    import datetime
    try:
        t = datetime.datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S")
    except Exception:
        return stamp
    delta = (datetime.date.today() - t.date()).days
    day = "сегодня" if delta == 0 else "вчера" if delta == 1 else t.strftime("%d.%m")
    return f"{day}, {t:%H:%M}"


class _TimeField:
    """Выбор времени: два счётчика «часы : минуты». .get() -> 'ЧЧ:ММ', .set('ЧЧ:ММ')."""

    def __init__(self, parent, value="09:00"):
        self.frame = tk.Frame(parent, bg=theme.CARD_BG)
        self.h = tk.StringVar(master=parent, value=value[:2])
        self.m = tk.StringVar(master=parent, value=value[3:5])
        opts = dict(width=3, font=_font(16, True), justify="center", relief="flat", bg=theme.INPUT_BG,
                    fg=theme.TEXT, insertbackground=theme.TEXT, highlightthickness=1,
                    highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY, wrap=True, format="%02.0f")
        tk.Spinbox(self.frame, from_=0, to=23, textvariable=self.h, **opts).pack(side="left")
        tk.Label(self.frame, text=" : ", font=_font(16, True), bg=theme.CARD_BG, fg=theme.TEXT).pack(side="left")
        tk.Spinbox(self.frame, from_=0, to=59, textvariable=self.m, **opts).pack(side="left")

    def get(self):
        try:
            return f"{int(self.h.get()):02d}:{int(self.m.get()):02d}"
        except ValueError:
            return ""

    def set(self, value):
        self.h.set(value[:2])
        self.m.set(value[3:5])


def _page_hero(parent, title, subtitle):
    """Заголовок страницы: крупное название и пояснение под ним (без цветного баннера)."""
    frame = tk.Frame(parent, bg=theme.BG)
    tk.Label(frame, text=title, font=_font(20, True), bg=theme.BG, fg=theme.TEXT, anchor="w").pack(fill="x")
    sub = tk.Label(frame, text=subtitle, font=_font(10), bg=theme.BG, fg=theme.TEXT_MUTED, anchor="w",
                   justify="left", wraplength=640)
    sub.pack(fill="x", pady=(2, 0))
    sub.bind("<Configure>", lambda e: sub.config(wraplength=max(200, e.width - 6)))
    return frame


def _status_color(status: str) -> str:
    s = (status or "").lower()
    if s.startswith("готово") or "выполнено" in s:
        return theme.SUCCESS
    if "ошибка" in s or "остановлено" in s or "не понял" in s or "отклон" in s or "отмен" in s:
        return theme.WARNING
    return theme.TEXT_MUTED


def _status_mark(status: str) -> str:
    """Значок перед статусом: смысл не только цветом."""
    s = (status or "").lower()
    if s.startswith("готово") or "выполнено" in s:
        return "✓ "
    if "ошибка" in s or "не понял" in s:
        return "! "
    if "остановлено" in s or "отклон" in s or "отмен" in s:
        return "✕ "
    return ""


def _log_tail(lines: int = 45) -> str:
    """Последние строки журнала — по ним видно, что на самом деле случилось (лимит, сеть, ошибка)."""
    try:
        with open(config.LOG_FILE, encoding="utf-8", errors="replace") as f:
            tail = f.read().splitlines()[-lines:]
        return "\n".join(tail) or "(журнал пока пуст)"
    except OSError:
        return "(журнал пока пуст)"


class ScrollFrame(tk.Frame):
    """Прокручиваемая область: складывай виджеты в .inner. Колесо мыши обрабатывает AppWindow."""

    def __init__(self, parent, bg):
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas._is_scroll_canvas = True
        bar = tk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda e: self.canvas.itemconfigure(self._window, width=e.width))
        self.canvas.configure(yscrollcommand=bar.set)
        bar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)


# ---------- главное окно ----------

class AppWindow:
    def __init__(self, controller):
        self.c = controller
        self.root = tk.Tk()
        self.root.withdraw()                 # не мигаем окном, если запуск в фоне
        # Мост "рабочие потоки -> интерфейс" включаем СРАЗУ: уже при построении страниц запускаются
        # фоновые проверки, и их результат обязан вернуться через очередь, а не тронуть виджеты из чужого потока.
        ui_thread.init(self.root)
        theme.enable_clipboard_shortcuts(self.root)      # Ctrl+C/V/X/A работают и на русской раскладке
        self.root.title("AI Ассистент")
        self._hide_notified = False
        self._visible = False
        self._visible_before_agent = False
        self._busy = False
        self._busy_text = ""
        self._page_name = "home"
        self._pages = {}
        self._nav_items = {}
        self._logo_img = None
        self._shell = None
        self._cards = {}
        self._hint_reset = None

        self.root.protocol("WM_DELETE_WINDOW", self.hide)
        self.root.bind_all("<MouseWheel>", self._on_wheel)
        self._center(1040, 700)
        self.root.minsize(900, 600)
        self._build()

    # ----- окно целиком -----

    def _center(self, w, h):
        try:
            sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 3)}")
        except Exception:
            self.root.geometry(f"{w}x{h}")

    def run(self):
        self.root.mainloop()

    def quit(self):
        try:
            self.root.quit()
        except Exception:
            pass

    def show(self, page=None):
        """Показывает окно поверх остальных (из трея, по повторному запуску, из горячей клавиши)."""
        if page:
            self.show_page(page)
        self._visible = True
        self.root.deiconify()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(250, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def hide(self):
        """Крестик: окно прячется, программа продолжает работать в трее."""
        self._visible = False
        self.root.withdraw()
        if not self._hide_notified:
            self._hide_notified = True
            try:
                self.c.notify_hidden()
            except Exception:
                pass

    def hide_for_agent(self):
        """Прячет окно на время работы автопилота: иначе ИИ видит на скриншоте само приложение."""
        self._visible_before_agent = self._visible
        self._visible = False
        self.root.withdraw()

    def restore_after_agent(self):
        """Возвращает окно, если оно было открыто до автопилота (без отъёма фокуса у окна результата)."""
        if self._visible_before_agent:
            self._visible = True
            self.root.deiconify()
        self._visible_before_agent = False

    def rebuild(self, page=None):
        """Пересобирает интерфейс (нужно после смены темы — цвета читаются при создании виджетов)."""
        if page:
            self._page_name = page
        try:
            if self._shell is not None:
                self._shell.destroy()
        except Exception:
            pass
        self._build()

    def _build(self):
        theme.style_window(self.root)
        self._pages = {}
        self._shell = tk.Frame(self.root, bg=theme.BG)
        self._shell.pack(fill="both", expand=True)
        self._build_sidebar(self._shell)
        self._content = tk.Frame(self._shell, bg=theme.BG)
        self._content.pack(side="left", fill="both", expand=True)
        self.show_page(self._page_name)
        self._apply_busy()

    def _on_wheel(self, event):
        """Прокрутка колесом для ScrollFrame под курсором (Text/ScrolledText прокручиваются сами)."""
        try:
            w = self.root.winfo_containing(event.x_root, event.y_root)
        except Exception:
            return
        while w is not None:
            if isinstance(w, tk.Text):
                return
            if getattr(w, "_is_scroll_canvas", False):
                w.yview_scroll(int(-event.delta / 120), "units")
                return
            w = getattr(w, "master", None)

    # ----- боковая панель -----

    def _build_sidebar(self, parent):
        bg = theme.SIDEBAR_BG
        sb = tk.Frame(parent, bg=bg, width=250)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)
        tk.Frame(parent, bg=theme.BORDER, width=1).pack(side="left", fill="y")

        head = tk.Frame(sb, bg=bg)
        head.pack(fill="x", padx=18, pady=(24, 22))
        try:
            self._logo_img = ImageTk.PhotoImage(Image.open(theme.LOGO_64_PATH).resize((36, 36), Image.LANCZOS))
            tk.Label(head, image=self._logo_img, bg=bg).pack(side="left")
        except Exception:
            pass
        tk.Label(head, text="AI Ассистент", font=_font(13, True), bg=bg, fg=theme.TEXT).pack(side="left", padx=10)

        self._nav_items = {}
        for key, icon, title in NAV:
            item = ui_kit.NavItem(sb, title, lambda k=key: self.show_page(k), glyph=NAV_GLYPHS.get(key),
                                  emoji=icon, bg=bg)
            item.pack(fill="x", padx=12, pady=2)
            self._nav_items[key] = item

        bottom = tk.Frame(sb, bg=bg)
        bottom.pack(side="bottom", fill="x", padx=14, pady=16)
        card = ui_kit.RoundCard(bottom, outer_bg=bg, fill=theme.CARD_BG, radius=12, pad=12)
        card.pack(fill="x")
        self._status_dot = tk.Label(card.body, text="● Готов к работе", font=_font(10, True), bg=theme.CARD_BG,
                                    fg=theme.SUCCESS, anchor="w")
        self._status_dot.pack(fill="x")
        tk.Label(card.body, text=f"v{config.APP_VERSION} · работает в фоне", font=_font(8), fg=theme.TEXT_MUTED,
                 bg=theme.CARD_BG, anchor="w").pack(fill="x", pady=(2, 0))
        _button(bottom, "Выйти из приложения", self.c.quit, "neutral", small=True).pack(fill="x", pady=(10, 0))

    def _highlight_nav(self):
        for key, item in self._nav_items.items():
            item.set_active(key == self._page_name)

    def show_page(self, name):
        if name not in dict((k, 1) for k, _, _ in NAV):
            name = "home"
        current = self._pages.get(self._page_name)
        if current is not None and self._page_name != name:
            current.pack_forget()
        self._page_name = name
        page = self._pages.get(name)
        if page is None:
            page = self._pages[name] = getattr(self, f"_build_{name}")(self._content)
        page.pack(fill="both", expand=True)
        self._highlight_nav()
        self.refresh()

    def refresh(self):
        getattr(self, f"_refresh_{self._page_name}", lambda: None)()

    # ----- состояние "выполняется команда" -----

    def set_busy(self, busy: bool, text: str = ""):
        self._busy, self._busy_text = busy, text
        self._apply_busy()
        if not busy:
            self.refresh()

    def _apply_busy(self):
        try:
            if self._busy:
                self._status_dot.config(text="● Выполняется…", fg=theme.WARNING)
            else:
                self._status_dot.config(text="● Готов к работе", fg=theme.SUCCESS)
            banner = getattr(self, "_busy_banner", None)
            if banner is not None:
                if self._busy:
                    banner.config(text="⏳  " + (self._busy_text or "Выполняется команда… Смотри окно автопилота."))
                    banner.pack(fill="x", pady=(0, 10), before=self._input_card)
                else:
                    banner.pack_forget()
        except tk.TclError:
            pass

    # ===================== ГЛАВНАЯ =====================

    def _build_home(self, parent):
        page = ScrollFrame(parent, theme.BG)
        body = tk.Frame(page.inner, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=32, pady=28)

        tk.Label(body, text="Что нужно сделать?", font=_font(22, True), bg=theme.BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x")
        tk.Label(body, text="Напиши или скажи команду — я посмотрю на экран и выполню её по шагам. "
                            "На опасные действия всегда спрошу разрешение.",
                 font=_font(10), bg=theme.BG, fg=theme.TEXT_MUTED, anchor="w", justify="left",
                 wraplength=700).pack(fill="x", pady=(4, 16))

        self._busy_banner = tk.Label(body, text="", font=_font(10, True), bg=theme.NAV_ACTIVE, fg=theme.TEXT,
                                     anchor="w", padx=12, pady=8, justify="left", wraplength=680)

        # --- карточка ввода ---
        self._input_card = ui_kit.RoundCard(body, outer_bg=theme.BG, pad=14, radius=14)
        self._input_card.pack(fill="x")
        inner = self._input_card.body
        self._input = tk.Text(inner, height=3, wrap="word", font=_font(12), bd=0, relief="flat",
                              bg=theme.CARD_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                              padx=4, pady=4, highlightthickness=0)
        self._input.pack(fill="x")
        self._input.bind("<Return>", self._on_enter)
        bar = tk.Frame(inner, bg=theme.CARD_BG)
        bar.pack(fill="x", pady=(8, 0))
        _button(bar, "🎙 Голосом", self._on_mic, "neutral", small=True).pack(side="left", padx=(0, 8))
        _button(bar, "📎 Файл", self._on_attach, "neutral", small=True).pack(side="left")
        _button(bar, "Выполнить  ➤", self._on_send, "primary").pack(side="right")

        # --- подсказка и «только смотреть» (действует сразу, без «Сохранить») ---
        line = tk.Frame(body, bg=theme.BG)
        line.pack(fill="x", pady=(8, 2))
        self._view_only_var = tk.BooleanVar(master=self.root, value=bool(config.get("view_only")))

        def _toggle_view_only():
            config.save({"view_only": bool(self._view_only_var.get())})

        ui_kit.Toggle(line, self._view_only_var, bg=theme.BG, command=_toggle_view_only,
                      label="Только смотреть").pack(side="right")
        vo = tk.Label(line, text="Только смотреть", font=_font(9), bg=theme.BG, fg=theme.TEXT_MUTED, cursor="hand2")
        vo.pack(side="right", padx=(0, 8))
        vo.bind("<Button-1>", lambda e: (self._view_only_var.set(not self._view_only_var.get()), _toggle_view_only()))
        self._hint = tk.Label(line, text="Enter — выполнить · Shift+Enter — новая строка · Ctrl+Shift+A — из любого места",
                              font=_font(9), fg=theme.TEXT_MUTED, bg=theme.BG, anchor="w")
        self._hint.pack(side="left")

        # --- примеры ---
        tk.Label(body, text="Попробуй, например:", font=_font(9, True), fg=theme.TEXT_MUTED, bg=theme.BG,
                 anchor="w").pack(fill="x", pady=(10, 0))
        for start in range(0, len(EXAMPLES), 2):
            chips = tk.Frame(body, bg=theme.BG)
            chips.pack(fill="x", pady=(5, 0))
            for text in EXAMPLES[start:start + 2]:
                _button(chips, text, lambda t=text: self._fill(t), "neutral", small=True).pack(side="left", padx=(0, 8))

        # --- низ: последние действия + состояние ---
        grid = tk.Frame(body, bg=theme.BG)
        grid.pack(fill="x", pady=(24, 0))
        grid.grid_columnconfigure(0, weight=1)
        grid.grid_columnconfigure(1, minsize=290)

        left = tk.Frame(grid, bg=theme.BG)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 18))
        head = tk.Frame(left, bg=theme.BG)
        head.pack(fill="x", pady=(0, 8))
        tk.Label(head, text="Последние действия", font=_font(12, True), bg=theme.BG, fg=theme.TEXT).pack(side="left")
        _button(head, "Вся история →", lambda: self.show_page("history"), "ghost", small=True).pack(side="right")
        recent = ui_kit.RoundCard(left, outer_bg=theme.BG, pad=6)
        recent.pack(fill="x")
        self._recent_frame = recent.body

        right = tk.Frame(grid, bg=theme.BG)
        right.grid(row=0, column=1, sticky="nsew")
        tk.Label(right, text="Состояние", font=_font(12, True), bg=theme.BG, fg=theme.TEXT, anchor="w").pack(
            fill="x", pady=(5, 8))
        state = ui_kit.RoundCard(right, outer_bg=theme.BG, pad=14)
        state.pack(fill="x")
        self._cards = {}
        for i, (key, title) in enumerate([("gemini", "Gemini API"), ("voice", "Голосовой ввод"),
                                          ("local", "Локальная модель")]):
            holder = tk.Frame(state.body, bg=theme.CARD_BG)
            holder.pack(fill="x", pady=(0 if i == 0 else 10, 0))
            tk.Label(holder, text=title, font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                     anchor="w").pack(fill="x")
            label = tk.Label(holder, text="Проверяю…", font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                             anchor="w", justify="left", wraplength=230)
            label.pack(fill="x", pady=(2, 0))
            self._cards[key] = label
            if key == "voice":
                self._voice_fix_btn = _button(holder, "🔧 Починить голосовой ввод", self._fix_voice, "primary",
                                              small=True)
            if i < 2:
                tk.Frame(state.body, bg=theme.BORDER, height=1).pack(fill="x", pady=(10, 0))
        return page

    def _on_enter(self, event):
        if event.state & 0x0001:            # Shift нажат — обычный перевод строки
            return None
        self._on_send()
        return "break"

    def _on_send(self):
        text = self._input.get("1.0", "end").strip()
        if not text:
            self._input.focus_set()
            return
        if self._busy:
            self._flash("Дождись завершения текущей команды — она ещё выполняется.")
            return
        self._input.delete("1.0", "end")
        self.c.submit_command(text)

    def _on_attach(self):
        """Картинка (спросить про неё — вопрос берётся из поля ввода) или аудиофайл (распознать речь и выполнить)."""
        if self._busy:
            self._flash("Дождись завершения текущей команды — она ещё выполняется.")
            return
        from tkinter import filedialog
        path = filedialog.askopenfilename(parent=self.root, title="Картинка или аудио", filetypes=[
            ("Картинки и аудио", "*.png *.jpg *.jpeg *.webp *.gif *.bmp *.ogg *.oga *.opus *.mp3 *.wav *.m4a *.aac *.flac"),
            ("Все файлы", "*.*")])
        if not path:
            return
        question = self._input.get("1.0", "end").strip()
        self._input.delete("1.0", "end")
        self.c.attach_file(path, question)

    def _on_mic(self):
        if self._busy:
            self._flash("Дождись завершения текущей команды — она ещё выполняется.")
            return
        self.c.voice_command()

    def _fill(self, text):
        self._input.delete("1.0", "end")
        self._input.insert("1.0", text)
        self._input.focus_set()

    def _flash(self, message):
        try:
            self._hint.config(text=message, fg=theme.WARNING)
        except tk.TclError:
            pass

    def _refresh_home(self):
        self._render_recent()
        threading.Thread(target=self._collect_status, daemon=True).start()

    def _render_recent(self):
        frame = self._recent_frame
        for w in frame.winfo_children():
            w.destroy()
        entries = history.get_all()[:5]
        if not entries:
            tk.Label(frame, text="Пока пусто — напиши первую команду выше", font=_font(10),
                     fg=theme.TEXT_MUTED, bg=theme.CARD_BG, anchor="w").pack(fill="x", padx=10, pady=14)
            return
        for n, e in enumerate(entries):
            if n:
                tk.Frame(frame, bg=theme.BORDER, height=1).pack(fill="x", padx=10)
            row = tk.Frame(frame, bg=theme.CARD_BG)
            row.pack(fill="x", padx=10, pady=9)
            tk.Label(row, text=str(e.get("time", ""))[5:16], font=_font(9), fg=theme.TEXT_MUTED,
                     bg=theme.CARD_BG).pack(side="right", padx=(10, 0))
            left = tk.Frame(row, bg=theme.CARD_BG)
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text=e.get("command") or "—", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                     anchor="w", justify="left", wraplength=400).pack(fill="x")
            status = e.get("status", "")
            tk.Label(left, text=_status_mark(status) + status, font=_font(9), bg=theme.CARD_BG,
                     fg=_status_color(status), anchor="w", justify="left", wraplength=400).pack(fill="x")

    def _collect_status(self):
        data = {}
        n = len(config.get("gemini_api_keys") or [])
        waiting = 0
        try:
            import key_manager
            waiting = sum(1 for r in key_manager.status_report() if r["status"] != "готов")
        except Exception:
            pass
        extra = f" (на паузе: {waiting})" if waiting else ""
        data["gemini"] = ("ok", f"Подключено · ключей: {n}{extra}") if n else ("bad", "Нет ключа — добавь его в Настройках")
        ok, message = voice_input.check_microphone()
        data["voice"] = ("ok", "Доступен · " + message) if ok else ("bad", message)
        data["voice_fix"] = bool(voice_input.components_missing())
        try:
            from local_brain import is_ollama_running
            data["local"] = (("ok", f"Готова · {config.get('local_model_name')}") if is_ollama_running()
                             else ("off", "Не запущена (это необязательно)"))
        except Exception:
            data["local"] = ("off", "Недоступна")
        ui_thread.post(self._apply_cards, data)

    def _apply_cards(self, data):
        colors = {"ok": theme.SUCCESS, "bad": theme.DANGER, "off": theme.NEUTRAL}
        fix = getattr(self, "_voice_fix_btn", None)
        if fix is not None:
            try:
                if data.get("voice_fix"):
                    fix.pack(anchor="w", pady=(6, 0))
                else:
                    fix.pack_forget()
            except tk.TclError:
                pass
        for key, value in data.items():
            if key == "voice_fix":
                continue
            state, text = value
            label = self._cards.get(key)
            if label is None:
                continue
            try:
                label.config(text="● " + text, fg=colors[state])
            except tk.TclError:
                pass

    def _fix_voice(self):
        """Ставит недостающее для микрофона (pip, без компилятора) и перепроверяет."""
        label = self._cards.get("voice")
        if label is not None:
            label.config(text="● Устанавливаю компоненты… (около минуты, нужен интернет)", fg=theme.TEXT_MUTED)
        mic = getattr(self, "_s_mic", None)
        if mic is not None:
            try:
                mic.config(text="Устанавливаю компоненты… (около минуты, нужен интернет)", fg=theme.TEXT_MUTED)
            except tk.TclError:
                pass
        threading.Thread(target=self._run_fix_voice, daemon=True).start()

    def _run_fix_voice(self):
        ok, message = voice_input.install_components()
        ui_thread.post(self._after_fix_voice, ok, message)

    def _after_fix_voice(self, ok, message):
        mic = getattr(self, "_s_mic", None)
        if mic is not None:
            self._show_mic(ok, message)
        self.refresh()
        threading.Thread(target=self._collect_status, daemon=True).start()

    # ===================== ИСТОРИЯ =====================

    def _build_history(self, parent):
        page = tk.Frame(parent, bg=theme.BG)
        _page_hero(page, "История команд", "Последние 20 команд. Пароли и похожие данные в истории скрыты.").pack(
            fill="x", padx=32, pady=(26, 10))
        bar = tk.Frame(page, bg=theme.BG)
        bar.pack(fill="x", padx=32, pady=(0, 8))
        self._hist_query = tk.StringVar(master=self.root)
        self._hist_filter = tk.StringVar(master=self.root, value="all")
        search = tk.Entry(bar, textvariable=self._hist_query, font=_font(10), relief="flat", bg=theme.INPUT_BG,
                          fg=theme.TEXT, insertbackground=theme.TEXT, highlightthickness=1,
                          highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY, width=30)
        search.pack(side="left", ipady=5)
        tk.Label(bar, text="Поиск по команде", font=_font(9), bg=theme.BG, fg=theme.TEXT_MUTED).pack(side="left", padx=8)
        _button(bar, "↻ Обновить", self.refresh, "neutral", small=True).pack(side="right")
        ui_kit.Segmented(bar, self._hist_filter, [("all", "Все"), ("ok", "✓ Успешно"), ("bad", "! Не вышло")]).pack(
            side="right", padx=(0, 10))
        self._hist_query.trace_add("write", lambda *a: self._render_history())
        self._hist_filter.trace_add("write", lambda *a: self._render_history())
        self._history_scroll = ScrollFrame(page, theme.BG)
        self._history_scroll.pack(fill="both", expand=True, padx=(32, 16), pady=(2, 10))
        return page

    def _refresh_history(self):
        self._render_history()

    def _render_history(self):
        inner = self._history_scroll.inner
        for w in inner.winfo_children():
            w.destroy()
        query = self._hist_query.get().strip().lower()
        mode = self._hist_filter.get()
        entries = []
        for e in history.get_all():
            mark = _status_mark(e.get("status", "")).strip()
            if mode == "ok" and mark != "✓":
                continue
            if mode == "bad" and mark not in ("!", "✕"):
                continue
            if query and query not in (e.get("command") or "").lower() and query not in (e.get("summary") or "").lower():
                continue
            entries.append(e)
        if not entries:
            msg = "Пока пусто — здесь появятся твои команды." if not history.get_all() else "Ничего не найдено."
            tk.Label(inner, text=msg, font=_font(10), fg=theme.TEXT_MUTED, bg=theme.BG, anchor="w").pack(
                fill="x", pady=12)
            return
        card = ui_kit.RoundCard(inner, outer_bg=theme.BG, pad=6)
        card.pack(fill="x", padx=(0, 8), pady=(0, 8))
        frame = card.body
        for n, e in enumerate(entries):
            if n:
                tk.Frame(frame, bg=theme.BORDER, height=1).pack(fill="x", padx=10)
            row = tk.Frame(frame, bg=theme.CARD_BG)
            row.pack(fill="x", padx=10, pady=10)
            tk.Label(row, text=_human_time(e.get("time", "")), font=_font(9), fg=theme.TEXT_MUTED, bg=theme.CARD_BG,
                     width=14, anchor="w").pack(side="left", anchor="n")
            if e.get("command"):
                _button(row, "↻ Повторить", lambda c=e["command"]: self.c.repeat(c), "ghost",
                        small=True).pack(side="right", anchor="n", padx=(8, 0))
            status = e.get("status", "")
            tk.Label(row, text=_status_mark(status) + status, font=_font(9, True), bg=theme.CARD_BG,
                     fg=_status_color(status), width=22, anchor="nw", justify="left",
                     wraplength=170).pack(side="right", anchor="n")
            mid = tk.Frame(row, bg=theme.CARD_BG)
            mid.pack(side="left", fill="x", expand=True)
            tk.Label(mid, text=e.get("command") or "—", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                     anchor="w", justify="left", wraplength=300).pack(fill="x")
            if e.get("summary"):
                tk.Label(mid, text=e["summary"], font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w",
                         justify="left", wraplength=300).pack(fill="x", pady=(2, 0))

    # ===================== РАСПИСАНИЕ =====================

    def _build_schedule(self, parent):
        page = ScrollFrame(parent, theme.BG)
        body = tk.Frame(page.inner, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=32, pady=26)
        _page_hero(body, "Расписание задач",
                   "Работает, пока приложение запущено (оно живёт в трее). Каждая запланированная задача "
                   "всё равно спросит подтверждение перед выполнением.").pack(fill="x", pady=(0, 14))

        self._schedule_list = tk.Frame(body, bg=theme.BG)
        self._schedule_list.pack(fill="x")

        tk.Label(body, text="Новая задача", font=_font(12, True), bg=theme.BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x", pady=(22, 8))
        rc = ui_kit.RoundCard(body, outer_bg=theme.BG, pad=16)
        rc.pack(fill="x")
        card = rc.body
        form = tk.Frame(card, bg=theme.CARD_BG)
        form.pack(fill="x")

        tk.Label(form, text="Что сделать", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x")
        self._sch_cmd = tk.Entry(form, font=_font(11), relief="flat", bg=theme.INPUT_BG, fg=theme.TEXT,
                                 insertbackground=theme.TEXT, highlightthickness=1,
                                 highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY)
        self._sch_cmd.pack(fill="x", ipady=5, pady=(3, 0))
        tk.Label(form, text="Например: открой почту · сделай скриншот · включи youtube",
                 font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w").pack(fill="x", pady=(2, 10))

        tk.Label(form, text="Во сколько", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x")
        self._sch_time = _TimeField(form, "09:00")
        self._sch_time.frame.pack(anchor="w", pady=(3, 10))

        tk.Label(form, text="Условие запуска", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x")
        self._sch_cond = tk.StringVar(master=self.root, value=scheduler.CONDITIONS[""])
        from tkinter import ttk
        ttk.Combobox(form, textvariable=self._sch_cond, values=list(scheduler.CONDITIONS.values()), state="readonly",
                     width=52).pack(anchor="w", pady=(3, 2))
        tk.Label(form, text=f"Если условие не выполнено в назначенное время, подожду до {scheduler.WAIT_MIN} минут.",
                 font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w").pack(fill="x", pady=(0, 10))

        tk.Label(form, text="В какие дни", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x")
        presets = tk.Frame(form, bg=theme.CARD_BG)
        presets.pack(fill="x", pady=(3, 4))
        self._sch_days = {}

        def pick(keys):
            for k, v in self._sch_days.items():
                v.set(k in keys)

        for text, keys in (("Будни", ("mon", "tue", "wed", "thu", "fri")),
                           ("Каждый день", tuple(k for k, _ in DAYS)), ("Выходные", ("sat", "sun"))):
            _button(presets, text, lambda ks=keys: pick(ks), "ghost", small=True).pack(side="left", padx=(0, 6))

        days_row = tk.Frame(form, bg=theme.CARD_BG)
        days_row.pack(fill="x", pady=(2, 0))
        for key, label in DAYS:
            var = tk.BooleanVar(master=self.root, value=key in ("mon", "tue", "wed", "thu", "fri"))
            ui_kit.Chip(days_row, label, var).pack(side="left", padx=(0, 6))
            self._sch_days[key] = var

        self._sch_error = tk.Label(card, text="", font=_font(9), fg=theme.DANGER, bg=theme.CARD_BG, anchor="w")
        self._sch_error.pack(fill="x", pady=(8, 0))
        _button(card, "Добавить задачу", self._add_task, "primary").pack(anchor="w", pady=(8, 0))
        return page

    def _refresh_schedule(self):
        frame = self._schedule_list
        for w in frame.winfo_children():
            w.destroy()
        tasks = scheduler.get_all()
        if not tasks:
            tk.Label(frame, text="Пока нет задач — добавь первую ниже.", font=_font(10), fg=theme.TEXT_MUTED,
                     bg=theme.BG, anchor="w").pack(fill="x", pady=4)
            return
        for t in tasks:
            enabled = bool(t.get("enabled", True))
            rc = ui_kit.RoundCard(frame, outer_bg=theme.BG, pad=14)
            rc.pack(fill="x", pady=4)
            row = tk.Frame(rc.body, bg=theme.CARD_BG)
            row.pack(fill="x")
            tk.Label(row, text=str(t.get("time", "")), font=_font(20, True), bg=theme.CARD_BG,
                     fg=theme.PRIMARY if enabled else theme.TEXT_MUTED).pack(side="left", padx=(0, 18))
            var = tk.BooleanVar(master=self.root, value=enabled)
            ui_kit.Toggle(row, var, command=lambda tid=t["id"], v=var: self._set_task(tid, v.get()),
                          label="Задача включена").pack(side="right", padx=(10, 0))
            _button(row, "Удалить", lambda tid=t["id"]: self._del_task(tid), "neutral", small=True).pack(side="right")
            info = tk.Frame(row, bg=theme.CARD_BG)
            info.pack(side="left", fill="x", expand=True)
            tk.Label(info, text=t.get("command", ""), font=_font(10, True), bg=theme.CARD_BG,
                     fg=theme.TEXT if enabled else theme.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=380).pack(fill="x")
            sub = ("Один раз · " if t.get("once") else "") + _days_text(t.get("days", [])) + \
                  (f" · {scheduler.CONDITIONS.get(t['condition'], t['condition'])}" if t.get("condition") else "") + \
                  ("" if enabled else " · выключено")
            tk.Label(info, text=sub, font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w",
                     justify="left", wraplength=380).pack(fill="x")

    def _set_task(self, task_id, enabled):
        scheduler.set_enabled(task_id, bool(enabled))
        self.root.after(30, self._refresh_schedule)

    def _toggle_task(self, task_id, current):
        scheduler.set_enabled(task_id, not current)
        self._refresh_schedule()

    def _del_task(self, task_id):
        scheduler.remove_task(task_id)
        self._refresh_schedule()

    def _add_task(self):
        command = self._sch_cmd.get().strip()
        when = self._sch_time.get().strip()
        days = [k for k, v in self._sch_days.items() if v.get()]
        if not command:
            self._sch_error.config(text="Впиши команду.")
        elif not _TIME_RE.match(when):
            self._sch_error.config(text="Время должно быть в формате ЧЧ:ММ, например 09:00.")
        elif not days:
            self._sch_error.config(text="Отметь хотя бы один день недели.")
        else:
            self._sch_error.config(text="")
            label = self._sch_cond.get()
            cond = next((k for k, v in scheduler.CONDITIONS.items() if v == label), "")
            scheduler.add_task(command, days, when, condition=cond)
            self._sch_cmd.delete(0, "end")
            self._refresh_schedule()

    # ===================== МАКРОСЫ, ЗАМЕТКИ, ШАБЛОНЫ =====================

    def _build_memory(self, parent):
        page = ScrollFrame(parent, theme.BG)
        body = tk.Frame(page.inner, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=32, pady=26)
        _page_hero(body, "Макросы, заметки и шаблоны",
                   "Всё, что ассистент запомнил. Макрос — шаги последней команды («запомни как утро»); заметка — факт о тебе; "
                   "шаблон — набор быстрых команд.").pack(fill="x", pady=(0, 6))
        self._mem_box = tk.Frame(body, bg=theme.BG)
        self._mem_box.pack(fill="x")
        return page

    def _mem_title(self, parent, text, hint=""):
        tk.Label(parent, text=text, font=_font(13, True), bg=theme.BG, fg=theme.TEXT, anchor="w").pack(fill="x", pady=(18, 2))
        if hint:
            tk.Label(parent, text=hint, font=_font(9), bg=theme.BG, fg=theme.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=680).pack(fill="x", pady=(0, 6))

    def _mem_row(self, parent, title, sub, buttons):
        card = _card(parent)
        card.pack(fill="x", pady=4)
        row = tk.Frame(card, bg=theme.CARD_BG)
        row.pack(fill="x", padx=14, pady=10)
        info = tk.Frame(row, bg=theme.CARD_BG)
        info.pack(side="left", fill="x", expand=True)
        tk.Label(info, text=title, font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT, anchor="w", justify="left",
                 wraplength=430).pack(fill="x")
        if sub:
            tk.Label(info, text=sub, font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w", justify="left",
                     wraplength=430).pack(fill="x")
        for text, cmd, kind in buttons:
            _button(row, text, cmd, kind, small=True).pack(side="right", padx=(6, 0))

    def _refresh_memory(self):
        import agent_loop
        import macros
        import templates
        box = self._mem_box
        for w in box.winfo_children():
            w.destroy()

        self._mem_title(box, "Макросы", "Выполни команду, потом впиши название ниже (или скажи «запомни как <имя>»). Клики в макросе привязаны к местам на экране.")
        import action_executor
        recent = [x for x in action_executor.RECENT_STEPS]
        card = _card(box)
        card.pack(fill="x", pady=4)
        row = tk.Frame(card, bg=theme.CARD_BG)
        row.pack(fill="x", padx=14, pady=10)
        tk.Label(row, text=(f"Последняя команда: {len(recent)} шаг." if recent else "Сначала выполни любую команду — её шаги можно сохранить."),
                 font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w").pack(fill="x")
        row2 = tk.Frame(card, bg=theme.CARD_BG)
        row2.pack(fill="x", padx=14, pady=(0, 6))
        m_name = tk.Entry(row2, font=_font(11), relief="flat", bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                          highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY)
        m_err = tk.Label(card, text="", font=_font(9), fg=theme.DANGER, bg=theme.CARD_BG, anchor="w", justify="left", wraplength=620)

        def save_last():
            name = m_name.get().strip()
            if not name:
                m_err.config(text="Впиши название макроса.")
                return
            ok, msg = macros.save_macro(name, list(action_executor.RECENT_STEPS))
            if ok:
                self._refresh_memory()
            else:
                m_err.config(text=msg)
        _button(row2, "💾 Сохранить как макрос", save_last, "success", small=True).pack(side="right", padx=(8, 0))
        m_name.pack(side="left", fill="x", expand=True, ipady=4)
        m_err.pack(fill="x", padx=14, pady=(0, 6))
        mac = macros.list_macros()
        if not mac:
            tk.Label(box, text="Макросов пока нет.", font=_font(10), bg=theme.BG, fg=theme.TEXT_MUTED, anchor="w").pack(fill="x")
        for name, m in mac.items():
            steps = m.get("steps", [])
            sub = f"{len(steps)} шаг.: " + "; ".join(agent_loop.describe_step(x) for x in steps if x.get("action") != "wait")[:200]
            self._mem_row(box, name, sub, [
                ("Удалить", lambda n=name: (macros.delete_macro(n), self._refresh_memory()), "neutral"),
                ("▶ Запустить", lambda n=name: self.c.repeat(f"макрос {n}"), "primary")])

        self._mem_title(box, "Заметки", "Факты о тебе — ИИ видит их при выполнении команд (как данные, не как приказы).")
        for i, n in enumerate(macros.notes()):
            self._mem_row(box, n, "", [("Удалить", lambda k=i: (macros.delete_note(k), self._refresh_memory()), "neutral")])
        card = _card(box)
        card.pack(fill="x", pady=4)
        row = tk.Frame(card, bg=theme.CARD_BG)
        row.pack(fill="x", padx=14, pady=10)
        entry = tk.Entry(row, font=_font(11), relief="flat", bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                         highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY)
        err = tk.Label(card, text="", font=_font(9), fg=theme.DANGER, bg=theme.CARD_BG, anchor="w")

        def add():
            ok, msg = macros.add_note(entry.get())
            if ok:
                self._refresh_memory()
            else:
                err.config(text=msg)
        _button(row, "➕ Запомнить", add, "success", small=True).pack(side="right", padx=(8, 0))
        entry.pack(side="left", fill="x", expand=True, ipady=4)
        err.pack(fill="x", padx=14, pady=(0, 6))

        self._mem_title(box, "Шаблоны", "Несколько быстрых команд под одним именем и с одним подтверждением. Запуск: «шаблон <имя>» (и в Telegram, и в расписании).")
        user = templates.user_templates()
        for name, items in templates.all_templates().items():
            builtin = name in templates.BUILTIN and name not in user
            btns = [("▶ Запустить", lambda n=name: self.c.repeat(f"шаблон {n}"), "primary")]
            if not builtin:
                btns.insert(0, ("Удалить", lambda n=name: (templates.delete_template(n), self._refresh_memory()), "neutral"))
            self._mem_row(box, name + ("  (встроенный)" if builtin else ""), "; ".join(items), btns)
        card = _card(box)
        card.pack(fill="x", pady=4)
        form = tk.Frame(card, bg=theme.CARD_BG)
        form.pack(fill="x", padx=14, pady=10)
        tk.Label(form, text="Новый шаблон: имя и команды через «;»", font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                 anchor="w").pack(fill="x")
        e_name = tk.Entry(form, font=_font(11), relief="flat", bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                          highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY, width=16)
        e_name.pack(side="left", ipady=4, pady=(4, 0))
        e_items = tk.Entry(form, font=_font(11), relief="flat", bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                           highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY)
        e_items.pack(side="left", fill="x", expand=True, ipady=4, padx=(8, 0), pady=(4, 0))
        terr = tk.Label(card, text="", font=_font(9), fg=theme.DANGER, bg=theme.CARD_BG, anchor="w", justify="left", wraplength=620)

        def add_tpl():
            ok, msg = templates.add_template(e_name.get(), e_items.get())
            if ok:
                self._refresh_memory()
            else:
                terr.config(text=msg)
        _button(form, "➕ Создать", add_tpl, "success", small=True).pack(side="right", padx=(8, 0), pady=(4, 0))
        terr.pack(fill="x", padx=14, pady=(0, 6))

    # ===================== НАСТРОЙКИ =====================

    def _section(self, parent, title, note=None):
        tk.Label(parent, text=title, font=_font(12, True), bg=theme.BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x", pady=(18, 8))
        card = ui_kit.RoundCard(parent, outer_bg=theme.BG, pad=16)
        card.pack(fill="x")
        inner = card.body
        if note:
            tk.Label(inner, text=note, font=_font(9), fg=theme.TEXT_MUTED, bg=theme.CARD_BG, anchor="w",
                     justify="left", wraplength=640).pack(fill="x", pady=(0, 6))
        return inner

    def _switch(self, parent, text, var, note=None):
        """Строка «текст — переключатель». Клик по тексту тоже переключает."""
        row = tk.Frame(parent, bg=theme.CARD_BG)
        row.pack(fill="x", pady=7)
        box = tk.Frame(row, bg=theme.CARD_BG)
        box.pack(side="left", fill="x", expand=True)
        title = tk.Label(box, text=text, font=_font(10), bg=theme.CARD_BG, fg=theme.TEXT, anchor="w",
                         justify="left", wraplength=560, cursor="hand2")
        title.pack(fill="x")
        title.bind("<Button-1>", lambda e: var.set(not bool(var.get())))
        if note:
            tk.Label(box, text=note, font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w",
                     justify="left", wraplength=560).pack(fill="x", pady=(1, 0))
        ui_kit.Toggle(row, var, label=text).pack(side="right", padx=(14, 0))
        tk.Frame(parent, bg=theme.BORDER, height=1).pack(fill="x")

    def _field_label(self, parent, text):
        tk.Label(parent, text=text, font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                 anchor="w", justify="left", wraplength=640).pack(fill="x", pady=(6, 2))

    def _text_box(self, parent, height, content=""):
        box = tk.Text(parent, height=height, wrap="word", font=theme.FONT_MONO, bd=0, relief="flat",
                      bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT, padx=6, pady=4,
                      highlightthickness=1, highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY)
        box.insert("1.0", content)
        box.pack(fill="x")
        return box

    def _entry(self, parent, var, width=40):
        entry = tk.Entry(parent, textvariable=var, width=width, font=_font(10), relief="flat",
                         bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT, highlightthickness=1,
                         highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY)
        entry.pack(anchor="w", ipady=3)
        return entry

    def _build_settings(self, parent):
        import autostart
        import secure_store

        cur = config.load()
        page = tk.Frame(parent, bg=theme.BG)

        head = tk.Frame(page, bg=theme.BG)
        head.pack(fill="x", padx=32, pady=(26, 8))
        tk.Label(head, text="Настройки", font=_font(20, True), bg=theme.BG, fg=theme.TEXT, anchor="w").pack(fill="x")
        tk.Label(head, text="Ключи Gemini, внешний вид, управление, голос и Telegram. Изменения применяются кнопкой «Сохранить».",
                 font=_font(10), bg=theme.BG, fg=theme.TEXT_MUTED, anchor="w").pack(fill="x", pady=(2, 0))

        # панель сохранения всегда видна внизу, а не в конце длинной страницы
        bar = tk.Frame(page, bg=theme.SIDEBAR_BG)
        bar.pack(side="bottom", fill="x")
        tk.Frame(bar, bg=theme.BORDER, height=1).pack(fill="x")
        barin = tk.Frame(bar, bg=theme.SIDEBAR_BG)
        barin.pack(fill="x", padx=32, pady=12)
        _button(barin, "Сохранить настройки", self._save_settings, "primary").pack(side="left")
        self._s_status = tk.Label(barin, text="", font=_font(10), bg=theme.SIDEBAR_BG, fg=theme.SUCCESS, anchor="w",
                                  justify="left", wraplength=560)
        self._s_status.pack(side="left", padx=14)

        main = tk.Frame(page, bg=theme.BG)
        main.pack(fill="both", expand=True)
        subnav = tk.Frame(main, bg=theme.BG, width=210)
        subnav.pack(side="left", fill="y", padx=(24, 0), pady=(8, 0))
        subnav.pack_propagate(False)
        scroll = ScrollFrame(main, theme.BG)
        scroll.pack(side="left", fill="both", expand=True)
        body = tk.Frame(scroll.inner, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=(24, 32), pady=(0, 20))

        sv = self._sv = {}

        # ---- разделы настроек (слева) ----
        tab_defs = [("ai", "ИИ и ключи"), ("look", "Внешний вид"), ("ctrl", "Управление"),
                    ("voice", "Голос"), ("tg", "Telegram"), ("sys", "Запуск")]
        holder = tk.Frame(body, bg=theme.BG)
        holder.pack(fill="x")
        tabs, tab_buttons = {}, {}

        def show_tab(key):
            for k, f in tabs.items():
                f.pack_forget()
            tabs[key].pack(fill="x")
            for k, btn in tab_buttons.items():
                btn.set_active(k == key)
            self._settings_tab = key
            try:
                scroll.canvas.yview_moveto(0)
            except Exception:
                pass

        for key, label in tab_defs:
            tabs[key] = tk.Frame(holder, bg=theme.BG)
            btn = ui_kit.NavItem(subnav, label, lambda k=key: show_tab(k), bg=theme.BG, height=40, small=True)
            btn.pack(fill="x", pady=1)
            tab_buttons[key] = btn

        secure = ("🔒 Ключи хранятся в защищённом хранилище Windows." if secure_store.is_available()
                  else "⚠️ Защищённое хранилище недоступно — ключи будут лежать в обычном файле settings.json.")
        s = self._section(tabs["ai"], "Gemini (ИИ)", secure)
        self._field_label(s, "Ключи Gemini — по одному на строку (можно с разных аккаунтов, при лимите ключи меняются сами)")
        self._s_keys = self._text_box(s, 4, "\n".join(cur.get("gemini_api_keys", [])))
        row = tk.Frame(s, bg=theme.CARD_BG)
        row.pack(fill="x", pady=(8, 0))
        _button(row, "➕ Вставить ключ из буфера", self._paste_key, "primary", small=True).pack(side="left", padx=(0, 8))
        _button(row, "Проверить все ключи", self._test_keys, "neutral", small=True).pack(side="left", padx=(0, 8))
        _button(row, "Статус ротации", self._show_rotation, "neutral", small=True).pack(side="left")
        self._s_result = tk.Text(s, height=4, wrap="word", font=theme.FONT_MONO, bd=0, relief="flat",
                                 bg=theme.INPUT_BG, fg=theme.TEXT, highlightthickness=1,
                                 highlightbackground=theme.BORDER, state="disabled")
        self._s_result.pack(fill="x", pady=(8, 0))
        self._field_label(s, "Модель (для навигации по экрану нужна модель посильнее lite; имя можно поменять, если Google переименует)")
        sv["model"] = tk.StringVar(master=self.root, value=cur.get("model", "gemini-3.5-flash"))
        self._entry(s, sv["model"], 30)
        _button(s, "🔎 Найти доступные модели", self._find_models, "neutral", small=True).pack(anchor="w", pady=(8, 0))
        self._model_buttons = tk.Frame(s, bg=theme.CARD_BG)
        self._model_buttons.pack(fill="x", pady=(6, 0))
        self._field_label(s, "Запросов в минуту на ключ — свой «светофор», чтобы не упереться в лимит Google "
                             "(бесплатный тариф: обычно 5–10 на проект; 0 = без ограничения)")
        sv["rpm"] = tk.StringVar(master=self.root, value=str(cur.get("requests_per_minute", 6)))
        self._entry(s, sv["rpm"], 6)

        s = self._section(tabs["look"], "Внешний вид")
        sv["theme"] = tk.StringVar(master=self.root, value=cur.get("theme", "light"))
        self._field_label(s, "Тема оформления (применится после «Сохранить настройки»)")
        ui_kit.Segmented(s, sv["theme"], list(theme.THEME_LABELS.items())).pack(anchor="w", pady=(0, 6))
        self._field_label(s, "Акцентный цвет")
        sv["accent"] = tk.StringVar(master=self.root, value=cur.get("accent", "indigo"))
        ui_kit.Swatches(s, sv["accent"], theme.FILLS).pack(anchor="w", pady=(2, 2))

        s = self._section(tabs["ctrl"], "Управление")
        sv["view_only"] = tk.BooleanVar(master=self.root, value=bool(cur.get("view_only", False)))
        self._switch(s, "Режим «только смотреть»: ИИ отвечает про экран, но ничего не нажимает и не печатает", sv["view_only"])
        sv["use_fallback"] = tk.BooleanVar(master=self.root, value=bool(cur.get("use_fallback_models", True)))
        self._switch(s, "При лимите основной модели пробовать запасные (у каждой модели свой бесплатный лимит)", sv["use_fallback"])
        self._field_label(s, "Запасные модели (через запятую; доступные — кнопка «Найти доступные модели» в карточке Gemini)")
        sv["fallback_models"] = tk.StringVar(master=self.root, value=", ".join(cur.get("fallback_models") or []))
        self._entry(s, sv["fallback_models"], 44)
        self._field_label(s, "Секунд до автозапуска простых команд (0 — всегда ждать клика)")
        sv["auto_confirm"] = tk.StringVar(master=self.root, value=str(cur.get("auto_confirm_seconds", 5)))
        self._entry(s, sv["auto_confirm"], 6)
        self._field_label(s, "Горячая клавиша быстрой команды (работает из любого места)")
        sv["hotkey"] = tk.StringVar(master=self.root, value=cur.get("hotkey_open_window", "ctrl+shift+a"))
        self._entry(s, sv["hotkey"], 24)
        self._field_label(s, "Горячая клавиша голосовой команды")
        sv["hotkey_voice"] = tk.StringVar(master=self.root, value=cur.get("hotkey_voice", "ctrl+shift+v"))
        self._entry(s, sv["hotkey_voice"], 24)
        self._field_label(s, "Аварийная остановка автопилота из любого места (ещё: рывок мыши в левый верхний угол)")
        sv["hotkey_stop"] = tk.StringVar(master=self.root, value=cur.get("hotkey_stop", "ctrl+shift+s"))
        self._entry(s, sv["hotkey_stop"], 24)
        self._field_label(s, "Папки, с которыми можно работать (по одной на строку)")
        self._s_folders = self._text_box(s, 3, "\n".join(cur.get("allowed_folders", [])))

        s = self._section(tabs["voice"], "Голос")
        sv["voice_feedback"] = tk.BooleanVar(master=self.root, value=cur.get("voice_feedback_enabled", False))
        self._switch(s, "Озвучивать действия голосом (офлайн)", sv["voice_feedback"])
        sv["voice_wake"] = tk.BooleanVar(master=self.root, value=cur.get("voice_wake_enabled", False))
        self._switch(s, "Слушать «Эй, ассистент» без кнопки (микрофон работает постоянно)", sv["voice_wake"])
        tk.Label(s, text="Звук нигде не хранится. Каждая услышанная фраза (до 5 секунд) отправляется на бесплатное "
                         "распознавание Google, чтобы найти слово «ассистент»; без него фраза выбрасывается. "
                         "Включай, только если тебя это устраивает. Сказанная команда всё равно спросит подтверждение.",
                 font=_font(9), fg=theme.TEXT_MUTED, bg=theme.CARD_BG, anchor="w", justify="left",
                 wraplength=640).pack(fill="x", pady=(2, 6))
        self._field_label(s, "Язык голосовых команд")
        sv["voice_language"] = tk.StringVar(master=self.root, value=cur.get("voice_language", "ru-RU"))
        ui_kit.Segmented(s, sv["voice_language"], [("ru-RU", "Русский"), ("uk-UA", "Українська"), ("en-US", "English")]).pack(anchor="w", pady=(0, 6))
        self._field_label(s, "Микрофон")
        sv["voice_device"] = tk.StringVar(master=self.root, value=cur.get("voice_device", ""))
        choices = [("", "Системный по умолчанию")] + [(n, n[:70]) for n in voice_input.list_input_devices()[:8]]
        for value, label in choices:
            tk.Radiobutton(s, text=label, value=value, variable=sv["voice_device"], bg=theme.CARD_BG, fg=theme.TEXT,
                           activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG, font=_font(10),
                           anchor="w").pack(anchor="w")
        row = tk.Frame(s, bg=theme.CARD_BG)
        row.pack(fill="x", pady=(8, 0))
        _button(row, "🎙 Проверить микрофон", self._test_mic, "neutral", small=True).pack(side="left", padx=(0, 8))
        _button(row, "🔧 Установить / починить голосовой ввод", self._fix_voice, "neutral", small=True).pack(side="left")
        self._s_mic = tk.Label(s, text="", font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w",
                               justify="left", wraplength=640)
        self._s_mic.pack(fill="x", pady=(6, 0))

        s = self._section(tabs["ai"], "Локальная модель (Ollama) — необязательно")
        sv["local_enabled"] = tk.BooleanVar(master=self.root, value=cur.get("local_model_enabled", True))
        self._switch(s, "Использовать для простых команд", sv["local_enabled"])
        self._field_label(s, "Название модели")
        sv["local_name"] = tk.StringVar(master=self.root, value=cur.get("local_model_name", "qwen2.5:3b"))
        self._entry(s, sv["local_name"], 24)

        self._build_telegram_tab(tabs["tg"], sv, cur)

        s = self._section(tabs["sys"], "Запуск")
        sv["autostart"] = tk.BooleanVar(master=self.root, value=autostart.is_enabled())
        self._switch(s, "Запускать вместе с Windows (сразу в трее, без окна)", sv["autostart"])
        _button(s, "🖥 Создать ярлыки (рабочий стол и меню Пуск)", self._make_shortcuts, "neutral",
                small=True).pack(anchor="w", pady=(8, 0))

        show_tab(getattr(self, "_settings_tab", "ai"))
        return page

    # ----- вкладка Telegram -----

    def _build_telegram_tab(self, tab, sv, cur):
        import telegram_bot

        s = self._section(tab, "Управление из Telegram", (
            "Необязательно. Ты пишешь своему боту — ассистент выполняет команду на этом компьютере. "
            "Команды из Telegram никогда не запускаются сами: нужно нажать «Разрешить» — в чате или на компьютере."))
        tk.Label(s, text=("Как создать бота (1 минута):\n"
                          "1. В Telegram открой @BotFather и отправь /newbot\n"
                          "2. Придумай имя и логин бота (логин оканчивается на bot)\n"
                          "3. BotFather пришлёт токен — вставь его ниже и нажми «Сохранить настройки»\n"
                          "4. Напиши своему боту /start — он выдаст код из 16 символов\n"
                          "5. Введи этот код ниже, в «Заявки на доступ»"),
                 font=_font(10), bg=theme.CARD_BG, fg=theme.TEXT, anchor="w", justify="left",
                 wraplength=640).pack(fill="x", pady=(0, 8))
        sv["tg_enabled"] = tk.BooleanVar(master=self.root, value=bool(cur.get("telegram_enabled", False)))
        self._switch(s, "Включить управление из Telegram", sv["tg_enabled"])
        sv["tg_approve"] = tk.BooleanVar(master=self.root, value=bool(cur.get("telegram_approve_in_chat", True)))
        self._switch(s, "Все вопросы и подтверждения — кнопками в Telegram (опасные действия спрашиваю дважды)", sv["tg_approve"])
        for key, cfg, label in (("tg_trusted", "telegram_trusted_auto", "Безопасные быстрые команды (открыть сайт/программу, скрин) выполнять сразу, без вопроса"),
                                ("tg_screen", "telegram_screen_after", "После задачи присылать скриншот экрана"),
                                ("tg_announce", "telegram_announce", "При запуске программы писать в Telegram «ассистент на связи»")):
            sv[key] = tk.BooleanVar(master=self.root, value=bool(cur.get(cfg, True)))
            self._switch(s, label, sv[key])
        has_token = bool(telegram_bot.get_token())
        self._field_label(s, "Токен бота" + (" (сохранён — оставь пустым, чтобы не менять)" if has_token else ""))
        sv["tg_token"] = tk.StringVar(master=self.root, value="")
        entry = self._entry(s, sv["tg_token"], 52)
        entry.config(show="•")
        sv["tg_clear"] = tk.BooleanVar(master=self.root, value=False)
        row = tk.Frame(s, bg=theme.CARD_BG)
        row.pack(fill="x", pady=(8, 0))
        self._tg_status = tk.Label(row, text="Статус: " + telegram_bot.status(), font=_font(10), bg=theme.CARD_BG,
                                   fg=theme.TEXT_MUTED, anchor="w", justify="left", wraplength=420)

        def check():
            token = sv["tg_token"].get().strip() or telegram_bot.get_token()
            self._tg_status.config(text="Проверяю токен…")

            def work():
                ok, info = telegram_bot.check_token(token) if token else (False, "токен не указан")
                ui_thread.post(self._tg_status.config, text=("✓ Токен верный, бот " + info) if ok else ("✗ " + info))

            threading.Thread(target=work, daemon=True).start()

        _button(row, "Проверить токен", check, "neutral", small=True).pack(side="left")
        if has_token:
            tk.Checkbutton(row, text="удалить сохранённый токен", variable=sv["tg_clear"], bg=theme.CARD_BG,
                           fg=theme.TEXT, activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG,
                           font=_font(9)).pack(side="left", padx=10)
        self._tg_status.pack(fill="x", pady=(6, 0))

        s2 = self._section(tab, "Заявки на доступ", "Человек пишет боту /start и получает код из 16 символов. "
                                                    "Введи этот код здесь — только так он получит доступ. Вводи код только от человека, которого знаешь.")
        self._tg_requests = tk.Label(s2, text="", font=_font(10), bg=theme.CARD_BG, fg=theme.TEXT, anchor="w",
                                     justify="left", wraplength=640)
        self._tg_requests.pack(fill="x")
        code_row = tk.Frame(s2, bg=theme.CARD_BG)
        code_row.pack(fill="x", pady=(8, 0))
        code_var = tk.StringVar(master=self.root)
        code_entry = tk.Entry(code_row, textvariable=code_var, width=26, font=("Consolas", 13), relief="flat",
                              bg=theme.INPUT_BG, fg=theme.TEXT, insertbackground=theme.TEXT, highlightthickness=1,
                              highlightbackground=theme.BORDER, highlightcolor=theme.PRIMARY)
        code_entry.pack(side="left", ipady=4)
        self._tg_msg = tk.Label(s2, text="", font=_font(10), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w",
                                justify="left", wraplength=640)

        s3 = self._section(tab, "Подтверждённые пользователи")
        users_frame = tk.Frame(s3, bg=theme.CARD_BG)
        users_frame.pack(fill="x")

        def refresh_tg():
            pend = telegram_bot.pending_requests()
            self._tg_requests.config(text=("Ждут подтверждения: " + ", ".join(p["name"] for p in pend)) if pend
                                     else "Заявок нет. Нажми «Обновить», когда человек напишет боту.")
            for w in users_frame.winfo_children():
                w.destroy()
            us = telegram_bot.users()
            if not us:
                tk.Label(users_frame, text="Пока никого.", font=_font(10), bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                         anchor="w").pack(fill="x")
            for u in us:
                r = tk.Frame(users_frame, bg=theme.CARD_BG)
                r.pack(fill="x", pady=2)
                tk.Label(r, text=f"{u.get('name')}  (id {u.get('id')})", font=_font(10), bg=theme.CARD_BG,
                         fg=theme.TEXT).pack(side="left")
                _button(r, "Отозвать доступ",
                        lambda uid=u.get("id"): (telegram_bot.revoke(uid), refresh_tg()), "neutral",
                        small=True).pack(side="right")
            self._tg_status.config(text="Статус: " + telegram_bot.status())

        def do_confirm():
            who = telegram_bot.peek(code_var.get())
            if who is not None:
                import gui
                if not gui.ask_yes_no("Выдать доступ из Telegram?",
                                      f"Пользователь Telegram «{who[1]}» (id {who[0]}) сможет присылать команды этому "
                                      "компьютеру (каждую — с подтверждением) и запрашивать скриншот экрана.\n\n"
                                      "Выдавай доступ только тому, кого знаешь.", "Да, выдать", "Нет"):
                    self._tg_msg.config(text="Отменено — доступ не выдан.", fg=theme.TEXT_MUTED)
                    return
            ok, msg = telegram_bot.confirm(code_var.get())
            self._tg_msg.config(text=("✓ " if ok else "✗ ") + msg, fg=theme.SUCCESS if ok else theme.DANGER)
            if ok:
                code_var.set("")
            refresh_tg()

        _button(code_row, "Подтвердить код", do_confirm, "success", small=True).pack(side="left", padx=8)
        _button(code_row, "↻ Обновить", refresh_tg, "neutral", small=True).pack(side="left")
        self._tg_msg.pack(fill="x", pady=(6, 0))
        refresh_tg()

    def _set_text(self, widget, text):
        try:
            widget.config(state="normal")
            widget.delete("1.0", "end")
            widget.insert("end", text)
            widget.config(state="disabled")
        except tk.TclError:
            pass

    def _keys_from_box(self):
        """Ключи из поля: по одному на строку или через пробел/запятую; повторы убираются. Подходят и ключи AIza…, и новые AQ.…"""
        parts = re.split(r"[\s,;]+", self._s_keys.get("1.0", "end"))
        return list(dict.fromkeys(p for p in parts if p))

    def _set_result(self, text: str):
        self._set_text(self._s_result, text)

    def _paste_key(self):
        """Кнопка «Вставить ключ из буфера»: добавляет скопированный ключ новой строкой."""
        try:
            clip = self.root.clipboard_get().strip()
        except Exception:
            self._set_result("Буфер обмена пуст — сначала скопируй ключ на странице Google AI Studio.")
            return
        new = [p for p in re.split(r"[\s,;]+", clip) if re.fullmatch(r"[A-Za-z0-9_.\-]{20,}", p)]
        if not new:
            self._set_result("В буфере не похоже на ключ. Скопируй ключ целиком на странице Google AI Studio.")
            return
        have = self._keys_from_box()
        added = [k for k in new if k not in have]
        self._s_keys.delete("1.0", "end")
        self._s_keys.insert("1.0", "\n".join(have + added))
        self._set_result(f"Добавлено ключей: {len(added)}. Всего: {len(have) + len(added)}. Нажми «Сохранить» внизу.")

    def _test_keys(self):
        keys = self._keys_from_box()
        self._set_text(self._s_result, "Проверяю ключи…" if keys else "Сначала вставь хотя бы один ключ выше.")
        if keys:
            threading.Thread(target=self._run_key_tests, args=(keys,), daemon=True).start()

    def _run_key_tests(self, keys):
        from ai_brain import test_api_key
        lines = []
        for k in keys:
            ok, message = test_api_key(k)
            lines.append(f"{'✅' if ok else '❌'} ...{k[-6:] if len(k) >= 6 else k}: {message}")
        ui_thread.post(self._set_text, self._s_result, "\n".join(lines))

    def _show_rotation(self):
        import key_manager
        report = key_manager.status_report()
        self._set_text(self._s_result, "\n".join(f"{r['masked']}: {r['status']}" for r in report)
                       or "Ключей пока нет.")

    def _test_mic(self):
        self._s_mic.config(text="Проверяю… скажи что-нибудь вслух в течение двух секунд", fg=theme.TEXT_MUTED)
        threading.Thread(target=self._run_mic_test, daemon=True).start()

    def _run_mic_test(self):
        ok, message = voice_input.check_microphone()
        if ok:
            ok, level = voice_input.test_recording(2.0)
            message = f"{message}\n{level}"
        ui_thread.post(self._show_mic, ok, message)

    def _find_models(self):
        keys = self._keys_from_box()
        if not keys:
            self._set_text(self._s_result, "Сначала вставь хотя бы один ключ выше.")
            return
        self._set_text(self._s_result, "Спрашиваю у Google список моделей…")
        threading.Thread(target=self._run_find_models, args=(keys[0],), daemon=True).start()

    def _run_find_models(self, key):
        from ai_brain import list_models
        ok, result = list_models(key)
        ui_thread.post(self._show_models, ok, result)

    def _show_models(self, ok, result):
        if not ok:
            self._set_text(self._s_result, result)
            return
        skip = ("image", "tts", "live", "audio", "embedding", "robotics", "computer-use", "native")
        names = sorted([n for n in result if "flash" in n and not any(x in n for x in skip)], reverse=True)[:10]
        self._set_text(self._s_result, "Нажми на модель, чтобы подставить её (потом «Сохранить настройки»):"
                       if names else "Подходящих моделей для этого ключа не нашлось.")
        for w in self._model_buttons.winfo_children():
            w.destroy()
        for name in names:
            _button(self._model_buttons, name, lambda n=name: self._sv["model"].set(n), "neutral",
                    small=True).pack(side="left", padx=(0, 6), pady=2)

    def _show_mic(self, ok, message):
        try:
            self._s_mic.config(text=("✅ " if ok else "❌ ") + message, fg=theme.SUCCESS if ok else theme.DANGER)
        except tk.TclError:
            pass

    def _make_shortcuts(self):
        self._s_status.config(text="Создаю ярлыки…", fg=theme.TEXT_MUTED)
        threading.Thread(target=self._run_shortcuts, daemon=True).start()

    def _run_shortcuts(self):
        try:
            import shortcuts
            paths = shortcuts.create(desktop=True, start_menu=True)
            ui_thread.post(self._show_status, "✓ Ярлыки созданы: " + "; ".join(paths), True)
        except Exception as e:
            ui_thread.post(self._show_status, f"Не удалось создать ярлыки: {e}", False)

    def _show_status(self, text, ok):
        try:
            self._s_status.config(text=text, fg=theme.SUCCESS if ok else theme.DANGER)
        except tk.TclError:
            pass

    def _save_settings(self):
        sv = self._sv
        values = {
            "gemini_api_keys": self._keys_from_box(),
            "model": sv["model"].get().strip() or "gemini-3.5-flash",
            "theme": sv["theme"].get(),
            "hotkey_open_window": sv["hotkey"].get().strip() or "ctrl+shift+a",
            "allowed_folders": [l.strip() for l in self._s_folders.get("1.0", "end").splitlines() if l.strip()],
            "voice_feedback_enabled": sv["voice_feedback"].get(),
            "voice_wake_enabled": sv["voice_wake"].get(),
            "voice_language": sv["voice_language"].get(),
            "local_model_enabled": sv["local_enabled"].get(),
            "local_model_name": sv["local_name"].get().strip() or "qwen2.5:3b",
            "autostart": sv["autostart"].get(),
            "accent": sv["accent"].get(),
            "view_only": sv["view_only"].get(),
            "telegram_enabled": sv["tg_enabled"].get(),
            "telegram_approve_in_chat": sv["tg_approve"].get(),
            "telegram_trusted_auto": sv["tg_trusted"].get(),
            "telegram_screen_after": sv["tg_screen"].get(),
            "telegram_announce": sv["tg_announce"].get(),
            "telegram_token": sv["tg_token"].get().strip(),
            "telegram_clear_token": sv["tg_clear"].get(),
            "use_fallback_models": sv["use_fallback"].get(),
            "fallback_models": [m.strip() for m in re.split(r"[,;\s]+", sv["fallback_models"].get()) if m.strip()][:4],
        }
        try:
            values["auto_confirm_seconds"] = max(0, min(30, int(sv["auto_confirm"].get().strip() or "5")))
        except ValueError:
            values["auto_confirm_seconds"] = 5
        try:
            rpm = int(sv["rpm"].get().strip() or "6")
        except ValueError:
            rpm = 6
        values["requests_per_minute"] = max(0, min(120, rpm))
        values["voice_device"] = sv["voice_device"].get()
        values["hotkey_voice"] = sv["hotkey_voice"].get().strip()
        values["hotkey_stop"] = sv["hotkey_stop"].get().strip()
        old_theme = (config.get("theme"), config.get("accent"))
        try:
            self.c.save_settings(values)
        except Exception as e:
            self._show_status(f"Не удалось сохранить: {e}", False)
            return
        if hasattr(self, "_view_only_var"):
            self._view_only_var.set(bool(values.get("view_only")))
        if (values["theme"], values["accent"]) != old_theme:
            self.rebuild("settings")
        self._show_status("✓ Сохранено", True)

    # ===================== ПРОВЕРКА СИСТЕМЫ =====================

    def _open_audit(self):
        """Открывает audit_log.jsonl (что именно делала программа) в блокноте."""
        import audit
        p = audit._path()
        try:
            if not os.path.exists(p):
                open(p, "a", encoding="utf-8").close()
            os.startfile(p)
        except Exception as e:
            try:
                import gui
                gui.show_notification(f"Не удалось открыть журнал: {e}")
            except Exception:
                pass

    def _build_diag(self, parent):
        page = tk.Frame(parent, bg=theme.BG)
        _page_hero(page, "Проверка системы",
                   "Если что-то не работает (голос, ключи, автозагрузка) — причина обычно видна здесь.").pack(
            fill="x", padx=32, pady=(26, 8))
        head = tk.Frame(page, bg=theme.BG)
        head.pack(fill="x", padx=32)
        _button(head, "🧾 Журнал действий", self._open_audit, "neutral", small=True).pack(side="left")
        _button(head, "↻ Проверить снова", self.refresh, "neutral", small=True).pack(side="left", padx=8)
        _button(head, "📋 Копировать", self._copy_diag, "neutral", small=True).pack(side="left")
        _button(head, "🖱 Тест мыши", self._mouse_test, "primary", small=True).pack(side="left", padx=8)
        self._diag_box = scrolledtext.ScrolledText(page, font=theme.FONT_MONO, wrap="word", bd=0, relief="flat",
                                                   bg=theme.CARD_BG, fg=theme.TEXT, highlightthickness=1,
                                                   highlightbackground=theme.BORDER, padx=10, pady=8)
        self._diag_box.pack(fill="both", expand=True, padx=32, pady=12)
        self._diag_box.config(state="disabled")
        return page

    def _refresh_diag(self):
        self._set_text(self._diag_box, "Проверяю…")
        threading.Thread(target=self._run_diag, daemon=True).start()

    def _run_diag(self):
        import diagnostics
        try:
            results = diagnostics.run_checks()
        except Exception as e:
            results = [("Проверка системы", False, str(e))]
        text = "\n".join(f"{'✅' if ok else '❌'} {label}: {detail}" for label, ok, detail in results)
        text += "\n\n──────── Журнал приложения (последние записи) ────────\n" + _log_tail(45)
        ui_thread.post(self._set_text, self._diag_box, text)

    def _mouse_test(self):
        """Двигает курсор по экрану и кликает по своей кнопке — показывает, где именно ломается управление мышью."""
        self._set_text(self._diag_box, "Тест мыши: не трогай мышь ~10 секунд…")
        threading.Thread(target=self._run_mouse_test, daemon=True).start()

    def _run_mouse_test(self):
        import mouse
        lines = []
        try:
            if not mouse.AVAILABLE:
                ui_thread.post(self._set_text, self._diag_box, "Тест мыши работает только в Windows.")
                return
            info = mouse.screen_info()
            import pyautogui
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
            # клик по собственной кнопке
            box = {}

            def make():
                win = tk.Toplevel(self.root)
                win.title("Тест клика")
                win.attributes("-topmost", True)
                win.geometry("260x120+300+300")
                hit = {"n": 0}

                def pressed():
                    hit["n"] += 1
                btn = tk.Button(win, text="Нажми меня", command=pressed, font=_font(12, True))
                btn.pack(expand=True, fill="both", padx=20, pady=20)
                win.update()
                box.update(win=win, btn=btn, hit=hit)
                return btn.winfo_rootx() + btn.winfo_width() // 2, btn.winfo_rooty() + btn.winfo_height() // 2

            cx, cy = ui_thread.call(make)
            time.sleep(0.5)
            mouse.click(cx, cy)
            time.sleep(0.6)
            clicked = box["hit"]["n"] > 0
            ui_thread.call(box["win"].destroy)
            lines.append(f"{'✅' if clicked else '❌'} Клик по кнопке в своём окне: {'сработал' if clicked else 'НЕ сработал'}")
            if bad == 0 and clicked:
                lines.append("\nМышь управляется нормально. Если в каком-то приложении клики всё равно не работают — оно, "
                             "скорее всего, запущено от имени администратора (Windows не пускает клики от обычной "
                             "программы в такие окна). Для проверки запусти AI Ассистент от имени администратора.")
            elif bad:
                lines.append("\nКурсор не попадает в нужные точки — пришли этот текст, разберусь с масштабом.")
            else:
                lines.append("\nКурсор двигается, но клик не срабатывает — пришли этот текст.")
        except Exception as e:
            lines.append(f"Ошибка теста: {e}")
        ui_thread.post(self._set_text, self._diag_box, "\n".join(lines))

    def _copy_diag(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._diag_box.get("1.0", "end"))
        except tk.TclError:
            pass
