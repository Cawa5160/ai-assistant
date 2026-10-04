"""
Тема оформления — выбирается в Настройках: светлая / тёмная / как в Windows.

Как это устроено: цвета (theme.BG, theme.PRIMARY, ...) отдаются не константами,
а через модульный __getattr__ (PEP 562) из ТЕКУЩЕЙ палитры. Поэтому весь
остальной код (gui.py и др.) менять не пришлось — он и дальше просто пишет
theme.BG. Каждое окно приложения создаётся заново при открытии, так что смена
темы применяется к следующему открытому окну (уже открытые не перекрашиваются —
это ограничение tkinter, окна у нас короткоживущие).

style_window() дополнительно раздаёт цвета через базу опций tk (option_add),
чтобы обычные виджеты без явного fg/bg (Label, Checkbutton, Entry, Text) тоже
корректно выглядели в тёмной теме, а не были чёрным по тёмному.
"""
import os

import config
from app_paths import RESOURCE_DIR

ICON_PATH = os.path.join(RESOURCE_DIR, "assets", "app.ico")
LOGO_128_PATH = os.path.join(RESOURCE_DIR, "assets", "logo_128.png")
LOGO_64_PATH = os.path.join(RESOURCE_DIR, "assets", "logo_64.png")

FONT_TITLE = ("Segoe UI", 13, "bold")
FONT_HEADING = ("Segoe UI", 11, "bold")
FONT_BODY = ("Segoe UI", 10)
FONT_SMALL = ("Segoe UI", 8)
FONT_MONO = ("Consolas", 9)

THEMES = {
    "light": {
        "PRIMARY": "#4F46E5", "PRIMARY_DARK": "#4338CA", "ACCENT": "#9333EA",
        "SUCCESS": "#16A34A", "WARNING": "#D97706", "DANGER": "#DC2626", "NEUTRAL": "#6B7280",
        "BG": "#FAFAFA", "INPUT_BG": "#FFFFFF", "TEXT": "#111827", "TEXT_MUTED": "#6B7280",
        "SIDEBAR_BG": "#F1F2F7", "CARD_BG": "#FFFFFF", "BORDER": "#E2E4EC", "NAV_ACTIVE": "#E3E6FF",
    },
    "dark": {
        "PRIMARY": "#818CF8", "PRIMARY_DARK": "#6366F1", "ACCENT": "#C084FC",
        "SUCCESS": "#4ADE80", "WARNING": "#FBBF24", "DANGER": "#F87171", "NEUTRAL": "#9CA3AF",
        "BG": "#1F2126", "INPUT_BG": "#2B2E35", "TEXT": "#F3F4F6", "TEXT_MUTED": "#9CA3AF",
        "SIDEBAR_BG": "#17191D", "CARD_BG": "#2A2D34", "BORDER": "#3B3F48", "NAV_ACTIVE": "#33376A",
    },
}

THEME_LABELS = {"light": "Светлая", "dark": "Тёмная", "system": "Как в Windows"}


def _detect_system_theme() -> str:
    """Тема Windows (светлая/тёмная) из реестра; если узнать нельзя — светлая."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
        ) as key:
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return "light" if value else "dark"
    except Exception:
        return "light"


def current_theme_name() -> str:
    choice = config.get("theme")
    if choice == "system":
        return _detect_system_theme()
    return choice if choice in THEMES else "light"


def palette() -> dict:
    return THEMES[current_theme_name()]


def __getattr__(name):
    """theme.BG, theme.PRIMARY, ... — из текущей палитры (вызывается, только если имени нет в модуле)."""
    p = palette()
    if name in p:
        return p[name]
    raise AttributeError(f"module 'theme' has no attribute {name!r}")


def apply_icon(root):
    """Ставит иконку приложения на окно. Вне Windows тихо пропускает."""
    try:
        if os.path.exists(ICON_PATH):
            root.iconbitmap(ICON_PATH)
    except Exception:
        pass


def style_window(root):
    """Базовое оформление окна — вызывать сразу после tk.Tk(), ДО создания виджетов."""
    apply_icon(root)
    p = palette()
    try:
        root.configure(bg=p["BG"])
        opts = {
            "*Frame.background": p["BG"], "*Canvas.background": p["BG"],
            "*Label.background": p["BG"], "*Label.foreground": p["TEXT"],
            "*Checkbutton.background": p["BG"], "*Checkbutton.foreground": p["TEXT"],
            "*Checkbutton.activeBackground": p["BG"], "*Checkbutton.activeForeground": p["TEXT"],
            "*Checkbutton.selectColor": p["INPUT_BG"],
            "*Radiobutton.background": p["BG"], "*Radiobutton.foreground": p["TEXT"],
            "*Radiobutton.activeBackground": p["BG"], "*Radiobutton.activeForeground": p["TEXT"],
            "*Radiobutton.selectColor": p["INPUT_BG"],
            "*Entry.background": p["INPUT_BG"], "*Entry.foreground": p["TEXT"],
            "*Entry.insertBackground": p["TEXT"],
            "*Text.background": p["INPUT_BG"], "*Text.foreground": p["TEXT"],
            "*Text.insertBackground": p["TEXT"],
        }
        for key, value in opts.items():
            root.option_add(key, value)
    except Exception:
        pass
