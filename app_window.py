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
import tkinter as tk
from tkinter import scrolledtext

from PIL import Image, ImageTk

import config
import history
import scheduler
import theme
import ui_thread
import voice_input

NAV = [
    ("home", "🏠", "Главная"),
    ("history", "🕘", "История"),
    ("schedule", "📅", "Расписание"),
    ("settings", "⚙️", "Настройки"),
    ("diag", "🩺", "Проверка системы"),
]

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
        "primary": (theme.PRIMARY, "#FFFFFF"),
        "success": (theme.SUCCESS, "#FFFFFF"),
        "danger": (theme.DANGER, "#FFFFFF"),
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


def _status_color(status: str) -> str:
    s = (status or "").lower()
    if s.startswith("готово") or "выполнено" in s:
        return theme.SUCCESS
    if "ошибка" in s or "остановлено" in s or "не понял" in s or "отклон" in s or "отмен" in s:
        return theme.WARNING
    return theme.TEXT_MUTED


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
        sb = tk.Frame(parent, bg=bg, width=268)
        sb.pack(side="left", fill="y")
        sb.pack_propagate(False)

        head = tk.Frame(sb, bg=bg)
        head.pack(fill="x", padx=16, pady=(22, 20))
        try:
            self._logo_img = ImageTk.PhotoImage(Image.open(theme.LOGO_64_PATH).resize((40, 40), Image.LANCZOS))
            tk.Label(head, image=self._logo_img, bg=bg).pack(side="left")
        except Exception:
            pass
        tk.Label(head, text="AI Ассистент", font=_font(13, True), bg=bg, fg=theme.TEXT).pack(side="left", padx=10)

        self._nav_items = {}
        for key, icon, title in NAV:
            item = tk.Label(sb, text=f"  {icon}   {title}", anchor="w", font=_font(11), bg=bg, fg=theme.TEXT,
                            padx=10, pady=11, cursor="hand2")
            item.pack(fill="x", padx=10, pady=1)
            item.bind("<Button-1>", lambda e, k=key: self.show_page(k))
            item.bind("<Enter>", lambda e, k=key: self._nav_hover(k, True))
            item.bind("<Leave>", lambda e, k=key: self._nav_hover(k, False))
            self._nav_items[key] = item

        bottom = tk.Frame(sb, bg=bg)
        bottom.pack(side="bottom", fill="x", padx=16, pady=16)
        self._status_dot = tk.Label(bottom, text="● Готов", font=_font(9), bg=bg, fg=theme.SUCCESS, anchor="w")
        self._status_dot.pack(fill="x")
        tk.Label(bottom, text=f"v{config.APP_VERSION} · работает в фоне", font=_font(8), fg=theme.TEXT_MUTED,
                 bg=bg, anchor="w").pack(fill="x", pady=(2, 10))
        _button(bottom, "🚪  Выйти из приложения", self.c.quit, "neutral", small=True).pack(fill="x")

    def _nav_hover(self, key, inside):
        if key == self._page_name:
            return
        self._nav_items[key].config(bg=theme.BORDER if inside else theme.SIDEBAR_BG)

    def _highlight_nav(self):
        for key, item in self._nav_items.items():
            if key == self._page_name:
                item.config(bg=theme.NAV_ACTIVE, fg=theme.PRIMARY, font=_font(11, True))
            else:
                item.config(bg=theme.SIDEBAR_BG, fg=theme.TEXT, font=_font(11))

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
                self._status_dot.config(text="● Готов", fg=theme.SUCCESS)
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
        body.pack(fill="both", expand=True, padx=32, pady=26)

        tk.Label(body, text="Привет! Я AI Ассистент 👋", font=_font(20, True), bg=theme.BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x")
        tk.Label(body, text="Напиши, что нужно сделать, или нажми на микрофон. Я посмотрю на экран и выполню это "
                            "по шагам, а на опасные действия всегда спрошу разрешения.",
                 font=_font(11), fg=theme.TEXT_MUTED, bg=theme.BG, anchor="w", justify="left",
                 wraplength=700).pack(fill="x", pady=(4, 16))

        self._busy_banner = tk.Label(body, text="", font=_font(10, True), bg=theme.NAV_ACTIVE, fg=theme.PRIMARY,
                                     anchor="w", padx=12, pady=8, justify="left", wraplength=680)

        self._input_card = _card(body)
        self._input_card.pack(fill="x")
        inner = tk.Frame(self._input_card, bg=theme.CARD_BG)
        inner.pack(fill="x", padx=10, pady=10)
        self._input = tk.Text(inner, height=3, wrap="word", font=_font(11), bd=0, relief="flat",
                              bg=theme.CARD_BG, fg=theme.TEXT, insertbackground=theme.TEXT,
                              padx=6, pady=4, highlightthickness=0)
        self._input.pack(side="left", fill="both", expand=True)
        buttons = tk.Frame(inner, bg=theme.CARD_BG)
        buttons.pack(side="right", padx=(8, 0))
        _button(buttons, "🎙 Голосом", self._on_mic, "neutral", small=True).pack(side="top", pady=(0, 6), fill="x")
        _button(buttons, "➤ Выполнить", self._on_send, "primary", small=True).pack(side="top", fill="x")
        self._input.bind("<Return>", self._on_enter)

        self._hint = tk.Label(body, text="Enter — выполнить · Shift+Enter — новая строка · Ctrl+Shift+A — быстрая "
                                         "команда из любого места",
                              font=_font(9), fg=theme.TEXT_MUTED, bg=theme.BG, anchor="w")
        self._hint.pack(fill="x", pady=(6, 4))

        # Переключатель «только смотреть» прямо на главной: действует сразу, без кнопки «Сохранить»
        self._view_only_var = tk.BooleanVar(master=self.root, value=bool(config.get("view_only")))

        def _toggle_view_only():
            config.save({"view_only": bool(self._view_only_var.get())})

        tk.Checkbutton(body, text="👁 Только смотреть — ИИ отвечает про экран, но ничего не нажимает и не печатает",
                       variable=self._view_only_var, command=_toggle_view_only, bg=theme.BG, fg=theme.TEXT,
                       activebackground=theme.BG, selectcolor=theme.INPUT_BG, font=_font(9),
                       anchor="w").pack(fill="x", pady=(0, 10))

        tk.Label(body, text="Попробуй, например:", font=_font(9, True), fg=theme.TEXT_MUTED, bg=theme.BG,
                 anchor="w").pack(fill="x")
        for start in range(0, len(EXAMPLES), 2):          # по две подсказки в ряд — иначе не помещаются
            chips = tk.Frame(body, bg=theme.BG)
            chips.pack(fill="x", pady=(4, 0))
            for text in EXAMPLES[start:start + 2]:
                _button(chips, text, lambda t=text: self._fill(t), "neutral", small=True).pack(side="left", padx=(0, 8))

        row = tk.Frame(body, bg=theme.BG)
        row.pack(fill="x", pady=(22, 0))
        self._cards = {}
        for i, (key, title) in enumerate([("gemini", "✨  Gemini API"), ("voice", "🎙  Голосовой ввод"),
                                          ("local", "🧠  Локальная модель")]):
            row.grid_columnconfigure(i, weight=1, uniform="cards")
            card = _card(row)
            card.grid(row=0, column=i, sticky="nsew", padx=(0 if i == 0 else 8, 0))
            tk.Label(card, text=title, font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                     anchor="w").pack(fill="x", padx=14, pady=(12, 2))
            label = tk.Label(card, text="Проверяю…", font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                             anchor="w", justify="left", wraplength=200)
            label.pack(fill="x", padx=14, pady=(0, 12))
            self._cards[key] = label
            if key == "voice":
                self._voice_fix_btn = _button(card, "🔧 Починить голосовой ввод", self._fix_voice, "primary", small=True)

        head = tk.Frame(body, bg=theme.BG)
        head.pack(fill="x", pady=(24, 6))
        tk.Label(head, text="Последние действия", font=_font(13, True), bg=theme.BG, fg=theme.TEXT).pack(side="left")
        _button(head, "Вся история →", lambda: self.show_page("history"), "ghost", small=True).pack(side="right")
        self._recent_frame = _card(body)
        self._recent_frame.pack(fill="x")
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
            tk.Label(frame, text="Пока пусто — напиши первую команду выше 👆", font=_font(10),
                     fg=theme.TEXT_MUTED, bg=theme.CARD_BG, anchor="w").pack(fill="x", padx=14, pady=16)
            return
        for e in entries:
            row = tk.Frame(frame, bg=theme.CARD_BG)
            row.pack(fill="x", padx=14, pady=(10, 0))
            left = tk.Frame(row, bg=theme.CARD_BG)
            left.pack(side="left", fill="x", expand=True)
            tk.Label(left, text=e.get("command") or "—", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                     anchor="w", justify="left", wraplength=560).pack(fill="x")
            tk.Label(left, text=e.get("status", ""), font=_font(9), bg=theme.CARD_BG,
                     fg=_status_color(e.get("status", "")), anchor="w", justify="left",
                     wraplength=560).pack(fill="x")
            tk.Label(row, text=str(e.get("time", ""))[5:16], font=_font(9), fg=theme.TEXT_MUTED,
                     bg=theme.CARD_BG).pack(side="right")
        tk.Frame(frame, bg=theme.CARD_BG, height=10).pack()

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
                    fix.pack(anchor="w", padx=14, pady=(0, 12))
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
        head = tk.Frame(page, bg=theme.BG)
        head.pack(fill="x", padx=32, pady=(26, 4))
        tk.Label(head, text="История команд", font=_font(18, True), bg=theme.BG, fg=theme.TEXT).pack(side="left")
        _button(head, "↻ Обновить", self.refresh, "neutral", small=True).pack(side="right")
        tk.Label(page, text="Последние 20 команд. Пароли и похожие данные в истории скрыты.", font=_font(9),
                 fg=theme.TEXT_MUTED, bg=theme.BG, anchor="w").pack(fill="x", padx=32)
        self._history_scroll = ScrollFrame(page, theme.BG)
        self._history_scroll.pack(fill="both", expand=True, padx=(32, 16), pady=10)
        return page

    def _refresh_history(self):
        inner = self._history_scroll.inner
        for w in inner.winfo_children():
            w.destroy()
        entries = history.get_all()
        if not entries:
            tk.Label(inner, text="Пока пусто — здесь появятся твои команды.", font=_font(10), fg=theme.TEXT_MUTED,
                     bg=theme.BG, anchor="w").pack(fill="x", pady=12)
            return
        for e in entries:
            card = _card(inner)
            card.pack(fill="x", pady=5, padx=(0, 8))
            top = tk.Frame(card, bg=theme.CARD_BG)
            top.pack(fill="x", padx=14, pady=(10, 0))
            tk.Label(top, text=e.get("command") or "—", font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                     anchor="w", justify="left", wraplength=520).pack(side="left", fill="x", expand=True)
            tk.Label(top, text=e.get("time", ""), font=_font(9), fg=theme.TEXT_MUTED, bg=theme.CARD_BG).pack(side="right")
            if e.get("summary"):
                tk.Label(card, text=e["summary"], font=_font(9), bg=theme.CARD_BG, fg=theme.TEXT_MUTED, anchor="w",
                         justify="left", wraplength=620).pack(fill="x", padx=14, pady=(2, 0))
            tk.Label(card, text=e.get("status", ""), font=_font(9), bg=theme.CARD_BG,
                     fg=_status_color(e.get("status", "")), anchor="w", justify="left",
                     wraplength=620).pack(fill="x", padx=14, pady=(2, 0))
            if e.get("command"):
                _button(card, "↻ Повторить", lambda c=e["command"]: self.c.repeat(c), "ghost",
                        small=True).pack(anchor="e", padx=14, pady=(0, 10))
            else:
                tk.Frame(card, bg=theme.CARD_BG, height=10).pack()

    # ===================== РАСПИСАНИЕ =====================

    def _build_schedule(self, parent):
        page = ScrollFrame(parent, theme.BG)
        body = tk.Frame(page.inner, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=32, pady=26)
        tk.Label(body, text="Расписание задач", font=_font(18, True), bg=theme.BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x")
        tk.Label(body, text="Работает, пока приложение запущено (оно живёт в трее). Каждая запланированная "
                            "задача всё равно спросит подтверждение перед выполнением.",
                 font=_font(10), fg=theme.TEXT_MUTED, bg=theme.BG, anchor="w", justify="left",
                 wraplength=700).pack(fill="x", pady=(4, 14))

        self._schedule_list = tk.Frame(body, bg=theme.BG)
        self._schedule_list.pack(fill="x")

        tk.Label(body, text="Новая задача", font=_font(13, True), bg=theme.BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x", pady=(20, 6))
        card = _card(body)
        card.pack(fill="x")
        form = tk.Frame(card, bg=theme.CARD_BG)
        form.pack(fill="x", padx=14, pady=14)
        tk.Label(form, text="Команда:", bg=theme.CARD_BG, fg=theme.TEXT, font=_font(10)).grid(row=0, column=0, sticky="w")
        self._sch_cmd = tk.Entry(form, width=52, font=_font(10))
        self._sch_cmd.grid(row=0, column=1, sticky="w", padx=8, pady=3)
        tk.Label(form, text="Время (ЧЧ:ММ):", bg=theme.CARD_BG, fg=theme.TEXT, font=_font(10)).grid(row=1, column=0, sticky="w")
        self._sch_time = tk.Entry(form, width=8, font=_font(10))
        self._sch_time.insert(0, "09:00")
        self._sch_time.grid(row=1, column=1, sticky="w", padx=8, pady=3)

        days_row = tk.Frame(card, bg=theme.CARD_BG)
        days_row.pack(fill="x", padx=14)
        self._sch_days = {}
        for key, label in DAYS:
            var = tk.BooleanVar(master=self.root, value=key in ("mon", "tue", "wed", "thu", "fri"))
            tk.Checkbutton(days_row, text=label, variable=var, bg=theme.CARD_BG, fg=theme.TEXT,
                           activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG).pack(side="left")
            self._sch_days[key] = var

        self._sch_error = tk.Label(card, text="", font=_font(9), fg=theme.DANGER, bg=theme.CARD_BG, anchor="w")
        self._sch_error.pack(fill="x", padx=14, pady=(6, 0))
        _button(card, "➕ Добавить задачу", self._add_task, "success").pack(anchor="w", padx=14, pady=12)
        return page

    def _refresh_schedule(self):
        frame = self._schedule_list
        for w in frame.winfo_children():
            w.destroy()
        tasks = scheduler.get_all()
        if not tasks:
            tk.Label(frame, text="Пока нет задач.", font=_font(10), fg=theme.TEXT_MUTED, bg=theme.BG,
                     anchor="w").pack(fill="x", pady=4)
            return
        for t in tasks:
            enabled = t.get("enabled", True)
            card = _card(frame)
            card.pack(fill="x", pady=4)
            row = tk.Frame(card, bg=theme.CARD_BG)
            row.pack(fill="x", padx=14, pady=10)
            days = ", ".join(DAY_NAMES.get(d, d) for d in t.get("days", []))
            tk.Label(row, text=f"{'✅' if enabled else '⏸️'}  {t.get('time')}  ·  {days}", font=_font(10, True),
                     bg=theme.CARD_BG, fg=theme.TEXT if enabled else theme.TEXT_MUTED).pack(anchor="w")
            tk.Label(row, text=t.get("command", ""), font=_font(10), bg=theme.CARD_BG, fg=theme.TEXT_MUTED,
                     anchor="w", justify="left", wraplength=560).pack(side="left", fill="x", expand=True)
            _button(row, "Удалить", lambda tid=t["id"]: self._del_task(tid), "neutral", small=True).pack(side="right", padx=(6, 0))
            _button(row, "Вкл/Выкл", lambda tid=t["id"], cur=enabled: self._toggle_task(tid, cur), "neutral",
                    small=True).pack(side="right")

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
            scheduler.add_task(command, days, when)
            self._sch_cmd.delete(0, "end")
            self._refresh_schedule()

    # ===================== НАСТРОЙКИ =====================

    def _section(self, parent, title, note=None):
        tk.Label(parent, text=title, font=_font(13, True), bg=theme.BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x", pady=(20, 6))
        card = _card(parent)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=theme.CARD_BG)
        inner.pack(fill="x", padx=14, pady=12)
        if note:
            tk.Label(inner, text=note, font=_font(9), fg=theme.TEXT_MUTED, bg=theme.CARD_BG, anchor="w",
                     justify="left", wraplength=660).pack(fill="x", pady=(0, 6))
        return inner

    def _field_label(self, parent, text):
        tk.Label(parent, text=text, font=_font(10, True), bg=theme.CARD_BG, fg=theme.TEXT,
                 anchor="w").pack(fill="x", pady=(6, 2))

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
        page = ScrollFrame(parent, theme.BG)
        body = tk.Frame(page.inner, bg=theme.BG)
        body.pack(fill="both", expand=True, padx=32, pady=26)
        tk.Label(body, text="Настройки", font=_font(18, True), bg=theme.BG, fg=theme.TEXT, anchor="w").pack(fill="x")

        sv = self._sv = {}

        secure = ("🔒 Ключи хранятся в защищённом хранилище Windows." if secure_store.is_available()
                  else "⚠️ Защищённое хранилище недоступно — ключи будут лежать в обычном файле settings.json.")
        s = self._section(body, "Gemini (ИИ)", secure)
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

        s = self._section(body, "Внешний вид")
        sv["theme"] = tk.StringVar(master=self.root, value=cur.get("theme", "light"))
        row = tk.Frame(s, bg=theme.CARD_BG)
        row.pack(anchor="w")
        for value, label in theme.THEME_LABELS.items():
            tk.Radiobutton(row, text=label, value=value, variable=sv["theme"], bg=theme.CARD_BG, fg=theme.TEXT,
                           activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG,
                           font=_font(10)).pack(side="left", padx=(0, 16))

        self._field_label(s, "Акцентный цвет")
        sv["accent"] = tk.StringVar(master=self.root, value=cur.get("accent", "indigo"))
        arow = tk.Frame(s, bg=theme.CARD_BG)
        arow.pack(anchor="w")
        for value, label in theme.ACCENT_LABELS.items():
            tk.Radiobutton(arow, text=label, value=value, variable=sv["accent"], bg=theme.CARD_BG, fg=theme.TEXT,
                           activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG,
                           font=_font(10)).pack(side="left", padx=(0, 14))

        s = self._section(body, "Управление")
        sv["view_only"] = tk.BooleanVar(master=self.root, value=bool(cur.get("view_only", False)))
        tk.Checkbutton(s, text="👁 Режим «только смотреть»: ИИ отвечает про экран, но ничего не нажимает и не печатает",
                       variable=sv["view_only"], bg=theme.CARD_BG, fg=theme.TEXT, activebackground=theme.CARD_BG,
                       selectcolor=theme.INPUT_BG, font=_font(10)).pack(anchor="w", pady=(0, 8))
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

        s = self._section(body, "Голос")
        sv["voice_feedback"] = tk.BooleanVar(master=self.root, value=cur.get("voice_feedback_enabled", False))
        tk.Checkbutton(s, text="Озвучивать действия голосом (офлайн)", variable=sv["voice_feedback"], bg=theme.CARD_BG,
                       fg=theme.TEXT, activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG,
                       font=_font(10)).pack(anchor="w")
        self._field_label(s, "Язык голосовых команд")
        sv["voice_language"] = tk.StringVar(master=self.root, value=cur.get("voice_language", "ru-RU"))
        row = tk.Frame(s, bg=theme.CARD_BG)
        row.pack(anchor="w")
        for value, label in (("ru-RU", "Русский"), ("uk-UA", "Українська"), ("en-US", "English")):
            tk.Radiobutton(row, text=label, value=value, variable=sv["voice_language"], bg=theme.CARD_BG,
                           fg=theme.TEXT, activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG,
                           font=_font(10)).pack(side="left", padx=(0, 16))
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

        s = self._section(body, "Локальная модель (Ollama) — необязательно")
        sv["local_enabled"] = tk.BooleanVar(master=self.root, value=cur.get("local_model_enabled", True))
        tk.Checkbutton(s, text="Использовать для простых команд", variable=sv["local_enabled"], bg=theme.CARD_BG,
                       fg=theme.TEXT, activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG,
                       font=_font(10)).pack(anchor="w")
        self._field_label(s, "Название модели")
        sv["local_name"] = tk.StringVar(master=self.root, value=cur.get("local_model_name", "qwen2.5:3b"))
        self._entry(s, sv["local_name"], 24)

        s = self._section(body, "Запуск")
        sv["autostart"] = tk.BooleanVar(master=self.root, value=autostart.is_enabled())
        tk.Checkbutton(s, text="Запускать вместе с Windows (сразу в трее, без окна)", variable=sv["autostart"],
                       bg=theme.CARD_BG, fg=theme.TEXT, activebackground=theme.CARD_BG, selectcolor=theme.INPUT_BG,
                       font=_font(10)).pack(anchor="w")
        _button(s, "🖥 Создать ярлыки (рабочий стол и меню Пуск)", self._make_shortcuts, "neutral",
                small=True).pack(anchor="w", pady=(8, 0))

        bar = tk.Frame(body, bg=theme.BG)
        bar.pack(fill="x", pady=(22, 10))
        _button(bar, "💾  Сохранить настройки", self._save_settings, "success").pack(side="left")
        self._s_status = tk.Label(bar, text="", font=_font(10), bg=theme.BG, fg=theme.SUCCESS, anchor="w",
                                  justify="left", wraplength=520)
        self._s_status.pack(side="left", padx=14)
        return page

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
            "voice_language": sv["voice_language"].get(),
            "local_model_enabled": sv["local_enabled"].get(),
            "local_model_name": sv["local_name"].get().strip() or "qwen2.5:3b",
            "autostart": sv["autostart"].get(),
            "accent": sv["accent"].get(),
            "view_only": sv["view_only"].get(),
        }
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
        head = tk.Frame(page, bg=theme.BG)
        head.pack(fill="x", padx=32, pady=(26, 4))
        tk.Label(head, text="Проверка системы", font=_font(18, True), bg=theme.BG, fg=theme.TEXT).pack(side="left")
        _button(head, "📋 Копировать", self._copy_diag, "neutral", small=True).pack(side="right", padx=(8, 0))
        _button(head, "↻ Проверить снова", self.refresh, "neutral", small=True).pack(side="right")
        _button(head, "🧾 Журнал действий", self._open_audit, "neutral", small=True).pack(side="right", padx=(0, 8))
        tk.Label(page, text="Если что-то не работает (голос, ключи, автозагрузка) — причина обычно видна здесь.",
                 font=_font(9), fg=theme.TEXT_MUTED, bg=theme.BG, anchor="w").pack(fill="x", padx=32)
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

    def _copy_diag(self):
        try:
            self.root.clipboard_clear()
            self.root.clipboard_append(self._diag_box.get("1.0", "end"))
        except tk.TclError:
            pass
