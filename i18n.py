"""
Язык интерфейса: русский (по умолчанию), украинский, английский.
Перевод подставляется на лету при создании/изменении надписей tkinter (text= у кнопок, подписей, пунктов меню,
текст на холсте, заголовки окон), поэтому страницы не нужно переписывать. Ключ перевода — русский текст.
Что не нашлось в словаре, остаётся по-русски (например, тексты ошибок, часть системных уведомлений и меню в трее).
"""
import re

LANGS = {"ru": "Русский", "uk": "Українська", "en": "English"}
_lang = "ru"
_exact = {}
_phrases = []          # (ключ, перевод) — для подстановки внутри составных строк, длинные первыми
_words = []            # (регулярка, перевод) для коротких слов (дни недели, «ГБ»)
_installed = False

CYR = re.compile("[А-Яа-яЁё]")
_PREFIX = re.compile(r"^([^\w]+)(.+)$", re.S)

DAYS = {
    "en": {"Пн": "Mon", "Вт": "Tue", "Ср": "Wed", "Чт": "Thu", "Пт": "Fri", "Сб": "Sat", "Вс": "Sun"},
    "uk": {"Пн": "Пн", "Вт": "Вт", "Ср": "Ср", "Чт": "Чт", "Пт": "Пт", "Сб": "Сб", "Вс": "Нд"},
}


def current() -> str:
    return _lang


def detect_system_language() -> str:
    """Язык интерфейса Windows -> 'ru' / 'uk' / 'en' (по умолчанию 'ru')."""
    try:
        import ctypes
        lid = ctypes.windll.kernel32.GetUserDefaultUILanguage() & 0x3FF
        return {0x19: "ru", 0x22: "uk", 0x09: "en"}.get(lid, "ru")
    except Exception:
        return "ru"


def set_language(code: str):
    global _lang, _exact, _phrases, _words
    code = code if code in LANGS else "ru"
    _lang, _exact, _phrases, _words = code, {}, [], []
    if code == "ru":
        return
    try:
        mod = __import__("lang_" + code)
        _exact = dict(mod.TR)
    except Exception:
        return
    cyr_count = lambda k: len(CYR.findall(k))
    # Подстановка внутри составных строк — только для «обрывков» (с пробелом по краям, с двоеточием на конце, со знаком в начале):
    # целые предложения переводятся только точным совпадением, иначе в данных (история) получалась бы смесь языков.
    is_fragment = lambda k: k != k.strip() or k.endswith(":") or k[:1] in "(.,)—·"
    _phrases = sorted(((k, v) for k, v in _exact.items()
                       if cyr_count(k) >= 5 and is_fragment(k)),
                      key=lambda kv: -len(kv[0]))
    for ru, tr in DAYS.get(code, {}).items():
        _words.append((re.compile(r"(?<![А-Яа-яЁё])" + ru + r"(?![А-Яа-яЁё])"), tr))
    if code == "en":
        _words.append((re.compile(r"(?<![А-Яа-яЁё])ГБ(?![А-Яа-яЁё])"), "GB"))
        _words.append((re.compile(r"(?<![А-Яа-яЁё])шаг\."), "step(s)."))


def answer_hint() -> str:
    """Фраза для промптов Gemini/Ollama: на каком языке писать текст для пользователя."""
    return {"en": "\n\nIMPORTANT: write every text meant for the user (answers, summaries, messages) in English.",
            "uk": "\n\nВАЖЛИВО: весь текст для користувача (відповіді, підсумки, повідомлення) пиши українською."}.get(_lang, "")


def t(text):
    """Перевод строки на выбранный язык (или та же строка, если перевода нет)."""
    if _lang == "ru" or not isinstance(text, str) or not text or not CYR.search(text):
        return text
    hit = _exact.get(text)
    if hit is not None:
        return hit
    m = _PREFIX.match(text)           # «● Нет ключа», «✓ Сохранено», «! Не вышло»: значок отдельно, текст переводим
    if m and m.group(2):
        inner = _exact.get(m.group(2))
        if inner is not None:
            return m.group(1) + inner
    out = text
    for key, value in _phrases:
        if key in out:
            out = out.replace(key, value)
    for rx, value in _words:
        out = rx.sub(value, out)
    return out


def load_language() -> str:
    """Берёт язык из настроек программы (если их нет — русский) и включает его."""
    try:
        import config
        set_language(config.get("ui_language") or "ru")
    except Exception:
        set_language("ru")
    return _lang


def install():
    """Подключает перевод к tkinter (один раз)."""
    global _installed
    if _installed:
        return
    import tkinter as tk
    _installed = True
    original_options = tk.Misc._options

    def options(self, cnf, kw=None):
        if _lang != "ru":
            if kw and isinstance(kw.get("text"), str):
                kw = dict(kw, text=t(kw["text"]))
            if isinstance(cnf, dict) and isinstance(cnf.get("text"), str):
                cnf = dict(cnf, text=t(cnf["text"]))
        return original_options(self, cnf, kw)
    tk.Misc._options = options

    original_title = tk.Wm.wm_title

    def wm_title(self, string=None):
        return original_title(self, t(string) if isinstance(string, str) else string)
    tk.Wm.wm_title = wm_title
    tk.Wm.title = wm_title
