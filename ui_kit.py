"""
Общие элементы нового оформления: скруглённые карточки, переключатель, сегмент-выбор, выбор цвета,
пункт навигации. Всё на обычном tkinter (Canvas), без сторонних библиотек.
Цвета читаются из theme при создании элемента (как и везде в программе).
"""
import tkinter as tk
import tkinter.font as tkfont

import theme

_ICON_FONT = "unset"


def icon_font():
    """Шрифт значков Windows 11/10 или None (тогда рисуем эмодзи). Определяется один раз."""
    global _ICON_FONT
    if _ICON_FONT == "unset":
        _ICON_FONT = None
        try:
            families = set(tkfont.families())
            for name in ("Segoe Fluent Icons", "Segoe MDL2 Assets"):
                if name in families:
                    _ICON_FONT = name
                    break
        except Exception:
            _ICON_FONT = None
    return _ICON_FONT


def _font(size=10, bold=False):
    return ("Segoe UI", size, "bold") if bold else ("Segoe UI", size)


def round_points(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
            x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1, x1 + r, y1]


def draw_round(canvas, x1, y1, x2, y2, r, fill, outline="", tag="bg"):
    return canvas.create_polygon(round_points(x1, y1, x2, y2, r), smooth=True, fill=fill,
                                 outline=outline, width=1, tags=tag)


class RoundCard(tk.Canvas):
    """Скруглённая карточка. Содержимое складывай в .body (обычный Frame)."""

    def __init__(self, parent, outer_bg=None, fill=None, border=None, radius=12, pad=14):
        self._outer = outer_bg or theme.BG
        self._fill = fill or theme.CARD_BG
        self._border = border or theme.BORDER
        self._r, self._pad = radius, pad
        super().__init__(parent, bg=self._outer, highlightthickness=0, bd=0, height=2 * pad + 4)
        self.body = tk.Frame(self, bg=self._fill)
        self._win = self.create_window(pad, pad, window=self.body, anchor="nw")
        self.bind("<Configure>", self._on_canvas)
        self.body.bind("<Configure>", self._on_body)

    def _on_canvas(self, event):
        self.itemconfigure(self._win, width=max(event.width - 2 * self._pad, 1))
        self._draw(event.width, event.height)

    def _autowrap(self, widget):
        """Подписи с переносом строк подстраиваются под ширину карточки (иначе текст обрезается)."""
        for child in widget.winfo_children():
            if isinstance(child, tk.Label) and not getattr(child, "_aw", False):
                try:
                    if int(child.cget("wraplength")) > 0 and child.pack_info().get("fill") in ("x", "both"):
                        child._aw = True
                        child.bind("<Configure>", self._rewrap_label, add="+")
                except Exception:
                    pass
            self._autowrap(child)

    @staticmethod
    def _rewrap_label(event):
        label = event.widget
        try:
            new = max(120, event.width - 6)
            if abs(int(label.cget("wraplength")) - new) > 2:
                label.config(wraplength=new)
        except Exception:
            pass

    def _on_body(self, _event=None):
        self._autowrap(self.body)
        h = self.body.winfo_reqheight() + 2 * self._pad
        if int(self.cget("height")) != h:
            self.configure(height=h)
        self._draw(self.winfo_width(), h)

    def _draw(self, w, h):
        if w < 10:
            return
        self.delete("bg")
        draw_round(self, 1, 1, w - 1, h - 1, self._r, self._fill, self._border)
        self.tag_lower("bg")


class Toggle(tk.Canvas):
    """Переключатель вкл/выкл, привязанный к BooleanVar. Работает и с клавиатуры (пробел / Enter)."""

    W, H = 46, 26

    def __init__(self, parent, variable, bg=None, command=None, label=""):
        self._bg = bg or theme.CARD_BG
        super().__init__(parent, width=self.W, height=self.H, bg=self._bg, highlightthickness=2,
                         highlightbackground=self._bg, highlightcolor=theme.PRIMARY, bd=0, cursor="hand2",
                         takefocus=1)
        self.var, self._command, self.label = variable, command, label
        self.bind("<Button-1>", self._flip)
        self.bind("<space>", self._flip)
        self.bind("<Return>", self._flip)
        self._trace = self.var.trace_add("write", lambda *a: self._draw())
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, _e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass

    def _flip(self, _e=None):
        self.var.set(not bool(self.var.get()))
        if self._command:
            self._command()
        return "break"

    def _draw(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        on = bool(self.var.get())
        track = theme.FILL if on else theme.TOGGLE_OFF
        x1, y1, x2, y2 = 2, 3, self.W - 2, self.H - 3
        draw_round(self, x1, y1, x2, y2, (y2 - y1) // 2, track, track, tag="t")
        d = y2 - y1 - 6
        kx = x2 - 3 - d if on else x1 + 3
        self.create_oval(kx, y1 + 3, kx + d, y1 + 3 + d, fill="#FFFFFF", outline="", tags="k")


class Segmented(tk.Frame):
    """Выбор одного варианта из нескольких (светлая / тёмная / как в Windows). Привязан к StringVar."""

    def __init__(self, parent, variable, options, bg=None):
        super().__init__(parent, bg=theme.BORDER, highlightthickness=0, bd=0)
        self.var = variable
        self._buttons = {}
        for i, (value, label) in enumerate(options):
            b = tk.Button(self, text=label, font=_font(10), relief="flat", bd=0, padx=14, pady=6, highlightthickness=0,
                          cursor="hand2", command=lambda v=value: self.var.set(v))
            b.pack(side="left", padx=(1, 0) if i == 0 else (0, 0), pady=1)
            self._buttons[value] = b
        self._trace = self.var.trace_add("write", lambda *a: self._paint())
        self.bind("<Destroy>", self._on_destroy)
        self._paint()

    def _on_destroy(self, _e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass

    def _paint(self):
        try:
            current = self.var.get()
            for value, b in self._buttons.items():
                active = value == current
                bg = theme.FILL if active else theme.INPUT_BG
                b._base_bg = bg
                b.config(bg=bg, fg="#FFFFFF" if active else theme.TEXT, activebackground=bg,
                         activeforeground="#FFFFFF" if active else theme.TEXT,
                         font=_font(10, active))
        except tk.TclError:
            pass


class Swatches(tk.Canvas):
    """Выбор акцентного цвета кружками. Привязан к StringVar со значением 'indigo' / 'blue' / ..."""

    D, GAP = 26, 12

    def __init__(self, parent, variable, colors, labels=None, bg=None):
        self._bg = bg or theme.CARD_BG
        self.colors = list(colors.items())
        w = len(self.colors) * (self.D + self.GAP) + 4
        super().__init__(parent, width=w, height=self.D + 10, bg=self._bg, highlightthickness=0, bd=0,
                         cursor="hand2")
        self.var = variable
        self.bind("<Button-1>", self._click)
        self._trace = self.var.trace_add("write", lambda *a: self._draw())
        self.bind("<Destroy>", self._on_destroy)
        self._draw()

    def _on_destroy(self, _e=None):
        try:
            self.var.trace_remove("write", self._trace)
        except Exception:
            pass

    def _click(self, event):
        i = int((event.x - 2) // (self.D + self.GAP))
        if 0 <= i < len(self.colors):
            self.var.set(self.colors[i][0])

    def _draw(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        current = self.var.get()
        for i, (key, color) in enumerate(self.colors):
            x = 4 + i * (self.D + self.GAP)
            y = 5
            if key == current:
                self.create_oval(x - 4, y - 4, x + self.D + 4, y + self.D + 4, outline=theme.TEXT, width=2)
            self.create_oval(x, y, x + self.D, y + self.D, fill=color, outline="")
            if key == current:
                self.create_text(x + self.D / 2, y + self.D / 2, text="✓", fill="#FFFFFF",
                                 font=_font(11, True))


class NavItem(tk.Canvas):
    """Пункт меню слева: значок + текст, подсветка выбранного и при наведении."""

    def __init__(self, parent, text, on_click, glyph=None, emoji="", bg=None, height=44, small=False):
        self._bg = bg or theme.SIDEBAR_BG
        super().__init__(parent, height=height, bg=self._bg, highlightthickness=0, bd=0, cursor="hand2")
        self.text, self.glyph, self.emoji, self.small = text, glyph, emoji, small
        self.active = False
        self._hover = False
        self._on_click = on_click
        self.bind("<Configure>", lambda e: self._draw())
        self.bind("<Button-1>", lambda e: self._on_click())
        self.bind("<Enter>", lambda e: self._set_hover(True))
        self.bind("<Leave>", lambda e: self._set_hover(False))

    def set_active(self, value: bool):
        self.active = value
        self._draw()

    def _set_hover(self, value):
        self._hover = value
        self._draw()

    def _draw(self):
        try:
            self.delete("all")
        except tk.TclError:
            return
        w, h = self.winfo_width(), self.winfo_height()
        if w < 10:
            return
        if self.active:
            draw_round(self, 2, 2, w - 2, h - 2, 10, theme.FILL, theme.FILL)
            fg = "#FFFFFF"
        elif self._hover:
            draw_round(self, 2, 2, w - 2, h - 2, 10, theme.HOVER, theme.HOVER)
            fg = theme.TEXT
        else:
            fg = theme.TEXT_MUTED if self.small else theme.TEXT
        x = 16
        if not self.small:
            font = icon_font()
            if font and self.glyph:
                self.create_text(x + 8, h / 2, text=self.glyph, fill=fg, font=(font, 14), anchor="center")
            elif self.emoji:
                self.create_text(x + 8, h / 2, text=self.emoji, fill=fg, font=_font(12), anchor="center")
            x = 46
        self.create_text(x, h / 2, text=self.text, fill=fg, font=_font(11 if not self.small else 10, self.active),
                         anchor="w")
