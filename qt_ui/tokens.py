"""Дизайн-токены (цвета) двух тем и сборка общего стиля QSS."""
from string import Template

THEMES = {
    "dark": dict(bg="#0b0b10", side="#0e0e15", card="#13131c", line="#22222f", text="#ececf4", mut="#8a8aa3",
                 acc="#6366f1", acc2="#8b5cf6", ok="#34d399", warn="#fbbf24", err="#f87171", hover="#ffffff0d",
                 sel="#6366f124", selb="#6366f160", seg="#0b0b10", segon="#252535", input_glow="#6366f126"),
    "light": dict(bg="#f4f5fa", side="#fbfbfe", card="#ffffff", line="#e3e5ee", text="#16161d", mut="#6b6e80",
                  acc="#5b5ef0", acc2="#7c5cf6", ok="#15803d", warn="#b45309", err="#dc2626", hover="#0000000a",
                  sel="#5b5ef014", selb="#5b5ef050", seg="#eceef5", segon="#ffffff", input_glow="#5b5ef01f"),
}

QSS = Template("""
QWidget { background: transparent; color: $text; font-size: 14px; }
QWidget#root, QStackedWidget#pages { background: $bg; }
QFrame#side { background: $side; border: none; border-right: 1px solid $line; }
QLabel#h1 { font-size: 26px; font-weight: 700; }
QLabel#sub, QLabel#muted { color: $mut; }
QLabel#small { color: $mut; font-size: 12px; }
QLabel#big { font-size: 34px; font-weight: 700; }
QLabel#h3 { font-size: 14px; font-weight: 600; }
QLabel#ok { color: $ok; font-weight: 600; }
QLabel#logo { font-size: 17px; font-weight: 700; }

QFrame#card { background: $card; border: 1px solid $line; border-radius: 16px; }
QFrame#cardflat { background: $card; border: 1px solid $line; border-radius: 14px; }
QFrame#input { background: $card; border: 1px solid $line; border-radius: 18px; }
QFrame#input[focus="true"] { border: 1px solid $acc; }
QFrame#cmd { background: $card; border: 1px solid $line; border-radius: 16px; }
QFrame#cmd[focus="true"] { border: 1px solid $acc; }
QFrame#status { background: $card; border: 1px solid $line; border-radius: 14px; }
QFrame#sep { background: $line; border: none; max-height: 1px; min-height: 1px; }

QPushButton { border: none; outline: none; }
QPushButton#nav { text-align: left; padding: 10px 12px; border-radius: 10px; border: 1px solid transparent; color: $mut; }
QPushButton#nav:hover { background: $hover; color: $text; }
QPushButton#nav:checked { background: $sel; border: 1px solid $selb; color: $text; font-weight: 600; }
QPushButton#primary { color: white; font-weight: 600; padding: 9px 18px; border-radius: 12px;
    background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 $acc, stop:1 $acc2); }
QPushButton#primary:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 $acc2, stop:1 $acc); }
QPushButton#primary:disabled { background: $line; color: $mut; }
QPushButton#ghost { color: $mut; padding: 8px 12px; border-radius: 10px; border: 1px solid transparent; }
QPushButton#ghost:hover { background: $hover; color: $text; }
QPushButton#iconbtn { padding: 7px; border-radius: 10px; }
QPushButton#iconbtn:hover { background: $hover; }
QPushButton#chip { background: $card; border: 1px solid $line; border-radius: 17px; padding: 8px 14px; }
QPushButton#chip:hover { border: 1px solid $acc; }
QPushButton#seg { color: $mut; padding: 6px 13px; border-radius: 8px; font-size: 13px; }
QPushButton#seg:checked { background: $segon; color: $text; font-weight: 600; }
QFrame#segbox { background: $seg; border: 1px solid $line; border-radius: 11px; }

QFrame#bubU { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 $acc, stop:1 $acc2); border-radius: 14px; border-bottom-right-radius: 4px; }
QFrame#bubB { background: $card; border: 1px solid $line; border-radius: 14px; border-bottom-left-radius: 4px; }
QLabel#bubUt { color: white; }
QLabel#avatar { border-radius: 9px; }

QTextEdit#field, QLineEdit#field { background: transparent; border: none; selection-background-color: $acc; }
QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $line; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: $mut; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
QFrame#attach { background: $hover; border: 1px solid $line; border-radius: 10px; }
QFrame#attach[bad="true"] { border: 1px solid $err; }
QDialog#dlg { background: $card; }
QFrame#warnbox { background: $sel; border: 1px solid $selb; border-radius: 12px; }
QCheckBox { color: $text; spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; border-radius: 5px; border: 1px solid $mut; background: transparent; }
QCheckBox::indicator:checked { background: $acc; border: 1px solid $acc; }
QPushButton#danger { color: $err; font-weight: 600; padding: 9px 18px; border-radius: 12px; border: 1px solid $err; }
QPushButton#danger:hover { background: $hover; }
QPushButton#success { color: white; font-weight: 600; padding: 9px 18px; border-radius: 12px; background: $ok; }
QPushButton#neutral { color: $text; font-weight: 600; padding: 9px 18px; border-radius: 12px; background: $seg; border: 1px solid $line; }
QPushButton#neutral:hover { border: 1px solid $acc; }
QPushButton#stop { color: white; font-weight: 700; padding: 0 14px; background: #b91c1c; border-radius: 0; }
QPushButton#barbtn { color: white; font-weight: 600; padding: 0 12px; background: rgba(255,255,255,40); border-radius: 0; }
QPushButton#barbtn:hover { background: rgba(255,255,255,70); }
QFrame#bar { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 $acc, stop:1 $acc2); border-radius: 12px; }
QLabel#bartext { color: white; font-weight: 600; }
QPlainTextEdit#log, QTextEdit#log { background: $side; border: 1px solid $line; border-radius: 10px; padding: 8px; color: $text; font-family: Consolas, "Cascadia Mono", monospace; font-size: 13px; }
QLabel#warn { color: $warn; }
QLabel#err { color: $err; }
QToolTip { background: $card; color: $text; border: 1px solid $line; padding: 5px 8px; border-radius: 6px; }
""")


def _rgba(v: str) -> str:
    """Qt читает #RRGGBBAA как #AARRGGBB, поэтому прозрачные цвета отдаём как rgba()."""
    if isinstance(v, str) and v.startswith("#") and len(v) == 9:
        r, g, b, a = (int(v[i:i + 2], 16) for i in (1, 3, 5, 7))
        return f"rgba({r},{g},{b},{a})"
    return v


def stylesheet(theme: str) -> str:
    return QSS.substitute({k: _rgba(v) for k, v in THEMES[theme].items()})
