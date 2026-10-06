"""
Управление ассистентом из Telegram (необязательно, выключено по умолчанию).

Как устроена безопасность:
  * бот отвечает ТОЛЬКО подтверждённым пользователям; остальным — только код доступа;
  * новому пользователю программа генерирует одноразовый код из 16 символов (живёт 10 минут) и показывает
    его в чате; чтобы получить доступ, владелец должен в ПРОГРАММЕ (Настройки → Telegram) ввести этот код —
    значит, подтверждает человек, сидящий за компьютером;
  * обычные действия из Telegram подтверждаются кнопками в чате; ОПАСНЫЕ (файлы, установка, удаление) —
    двойным подтверждением там же; без ответа 3 минуты действие отменяется; безопасные быстрые команды
    (открыть сайт/приложение, скрин) можно выполнять сразу — это настройка;
  * токен бота хранится в защищённом хранилище Windows (если оно есть);
  * в самом чате пароли и ключи маскируются (privacy.redact), экран бот присылает только по команде /screen.
Работает на обычных HTTPS-запросах (long polling), без дополнительных библиотек.
"""
import io
import json
import os
import re
import secrets
import threading
import time

import requests

import atomic_io
from app_paths import DATA_DIR
from logger import log
from privacy import redact

DATA_PATH = os.path.join(DATA_DIR, "telegram.json")
SERVICE = "ai_assistant_gemini"
CODE_TTL = 600                      # код живёт 10 минут
MAX_PENDING = 5                     # одновременно ожидающих заявок
MAX_MSGS_PER_HOUR = 30              # для неподтверждённых — чтобы не спамили
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"        # без похожих символов (0/O, 1/I)
API = "https://api.telegram.org"

_lock = threading.RLock()
_pending = {}        # user_id -> {"code","name","chat","expires"}
_msg_times = {}      # user_id -> [время сообщений за час] (для неподтверждённых)
_thread = None
_stop = threading.Event()      # у каждого запуска свой Event (см. start)
_state = {"running": False, "bot": "", "error": ""}
_approvals = {}      # токен кнопки -> {"uid","event","result"} (ожидание ответа «Разрешить/Отмена»)
_on_command = None   # callable(text, user_id, user_name) — выполняет команду на компьютере
_hooks = {}          # status/stop/history/notes/macros -> callable() -> str (даёт main.py)


# ---------- хранение ----------

def _load() -> dict:
    d = atomic_io.read_json(DATA_PATH, {})
    return d if isinstance(d, dict) else {}


def _save(d: dict):
    atomic_io.write_json(DATA_PATH, d)


def _keyring():
    try:
        import keyring
        keyring.get_keyring()
        return keyring
    except Exception:
        return None


def get_token() -> str:
    kr = _keyring()
    if kr:
        try:
            t = kr.get_password(SERVICE, "telegram_token")
            if t:
                return t
        except Exception:
            pass
    return str(_load().get("token_fallback") or "")


def set_token(token: str):
    token = (token or "").strip()
    d = _load()
    kr = _keyring()
    stored = False
    if kr:
        try:
            if token:
                kr.set_password(SERVICE, "telegram_token", token)
            else:
                try:
                    kr.delete_password(SERVICE, "telegram_token")
                except Exception:
                    pass
            stored = True
        except Exception as e:
            log(f"Telegram: токен не сохранился в защищённом хранилище: {e}")
    d["token_fallback"] = "" if stored else token
    _save(d)


def users() -> list:
    return list(_load().get("users", []))


def is_allowed(user_id) -> bool:
    return any(u.get("id") == user_id for u in users())


def revoke(user_id):
    d = _load()
    d["users"] = [u for u in d.get("users", []) if u.get("id") != user_id]
    _save(d)
    log(f"Telegram: доступ пользователя {user_id} отозван.")


# ---------- коды доступа ----------

def make_code() -> str:
    raw = "".join(secrets.choice(ALPHABET) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def _norm(code: str) -> str:
    return "".join(ch for ch in (code or "").upper() if ch.isalnum())


def pending_requests() -> list:
    """Ожидающие заявки (без устаревших): [{'id','name','expires'}]. Сам код наружу не отдаём — его вводит владелец."""
    now = time.time()
    with _lock:
        for uid in [u for u, p in _pending.items() if p["expires"] < now]:
            _pending.pop(uid, None)
        return [{"id": u, "name": p["name"], "expires": p["expires"]} for u, p in _pending.items()]


def peek(code: str):
    """Кто стоит за этим кодом: (id, имя) или None — чтобы спросить владельца подтверждение окном."""
    want = _norm(code)
    with _lock:
        pending_requests()
        for uid, p in _pending.items():
            if len(want) == 16 and secrets.compare_digest(_norm(p["code"]), want):
                return uid, p["name"]
    return None


def confirm(code: str):
    """Владелец ввёл код в программе. -> (ok, сообщение)."""
    want = _norm(code)
    if len(want) != 16:
        return False, "Код состоит из 16 символов (например, ABCD-EFGH-JKLM-NPQR)."
    with _lock:
        pending_requests()
        for uid, p in list(_pending.items()):
            if secrets.compare_digest(_norm(p["code"]), want):
                d = _load()
                d.setdefault("users", []).append({"id": uid, "name": p["name"], "added": int(time.time())})
                _save(d)
                chat = p["chat"]
                _pending.pop(uid, None)
                log(f"Telegram: пользователь {p['name']} ({uid}) подтверждён.")
                _send(chat, "✅ Доступ подтверждён. Напиши команду, например: «открой блокнот». /help — список команд.")
                return True, f"Доступ выдан: {p['name']}"
    return False, "Такого кода нет или он устарел (код живёт 10 минут)."


def reject_all():
    with _lock:
        _pending.clear()


# ---------- обмен с Telegram ----------

def _call(method: str, token: str = None, http_timeout: int = 35, **params):
    token = token or get_token()
    r = requests.post(f"{API}/bot{token}/{method}", json=params, timeout=http_timeout)
    return r.json()


_MENU_BUTTONS = {
    "📸 Скрин": "/screen", "📊 Статус": "/status", "🛑 Стоп": "/stop", "📜 История": "/history",
    "🗒 Заметки": "/notes", "🔊 Громче": "громче", "🔉 Тише": "тише", "⏯ Пауза": "пауза", "🖥 Рабочий стол": "покажи рабочий стол",
}
_KEYBOARD = {"keyboard": [["📸 Скрин", "📊 Статус", "🛑 Стоп"], ["🔊 Громче", "🔉 Тише", "⏯ Пауза"],
                          ["📜 История", "🗒 Заметки", "🖥 Рабочий стол"]], "resize_keyboard": True, "is_persistent": True}
HELP_TEXT = ("Я управляю компьютером как программа на нём. Просто пиши что сделать, например:\n"
             "• открой блокнот и напиши привет\n• найди на ютубе музыку для работы\n• сделай скрин вкладки ютуб\n"
             "• поставь таймер на 10 минут\n• запомни: пароль от wifi на холодильнике\n\n"
             "Команды:\n/screen — скриншот\n/screenfile — скриншот файлом (без сжатия)\n/status — что делает ассистент\n"
             "/stop — остановить автопилот\n/history — последние команды\n/notes — заметки\n/macros — макросы\n/windows — какие окна открыты\n"
             "/menu — кнопки\n\nВсе вопросы программы приходят сюда кнопками. Опасные действия (файлы, удаление, "
             "установка) спрашиваю дважды.\n\n🎙 Можно слать голосовые — я их распознаю и выполню.\n🖼 Пришли фото (с подписью-вопросом) — отвечу, что на нём.")


def _post_retry(method, tries=3, **params):
    """Запрос к Telegram; при «слишком часто» (429) подождать сколько просят и повторить."""
    last = None
    for _ in range(tries):
        r = _call(method, http_timeout=15, **params)
        if r.get("ok") or r.get("error_code") != 429:
            return r
        last = r
        time.sleep(min(30, int((r.get("parameters") or {}).get("retry_after", 2)) + 1))
    return last


def _chunks(text: str, size: int = 3800):
    text = str(text)
    while len(text) > size:
        cut = text.rfind("\n", 0, size)
        cut = cut if cut > size // 2 else size
        yield text[:cut]
        text = text[cut:].lstrip("\n")
    yield text


def _send(chat_id, text: str, keyboard: bool = False):
    try:
        parts = list(_chunks(text))[:6]
        for i, part in enumerate(parts):
            extra = {"reply_markup": _KEYBOARD} if keyboard and i == len(parts) - 1 else {}
            _post_retry("sendMessage", chat_id=chat_id, text=part or "…", **extra)
    except Exception as e:
        log(f"Telegram: не отправилось сообщение: {e}")


def _typing(chat_id, action="typing"):
    try:
        _call("sendChatAction", http_timeout=8, chat_id=chat_id, action=action)
    except Exception:
        pass


def _send_photo(chat_id, image) -> bool:
    try:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=80)
        buf.seek(0)
        r = requests.post(f"{API}/bot{get_token()}/sendPhoto", data={"chat_id": chat_id},
                          files={"photo": ("screen.jpg", buf, "image/jpeg")}, timeout=60)
        j = r.json()
        if not j.get("ok"):
            raise RuntimeError(str(j.get("description", r.status_code)))
        return True
    except Exception as e:
        log(f"Telegram: не отправился скриншот: {e}")
        _send(chat_id, f"Не получилось отправить скриншот: {str(e)[:200]}")
        return False


_SCREEN_WORDS = re.compile(r"скрин|screenshot|screen\b|снимок экрана|знімок екрана|что на экране|що на екрані", re.I)


_FILLER = re.compile(r"\b(?:скрин\w*|screenshot|screen|снимок|снимки|знімок|экрана|екрана|экран|екран|сделай|зделай|зроби|сними|"
                     r"пришли|отправь|скинь|покажи|мне|и|в|на|телеграм\w*|пожалуйста|please|send|take|of|the|me)\b", re.I)


def _screen_target(text: str) -> str:
    """«скрин вкладки ютуб» -> «вкладки ютуб»; «сделай скрин экрана и пришли» -> ''."""
    t = re.sub(r"[.,!?]+", " ", text)
    return " ".join(_FILLER.sub(" ", t).split())


def _wants_screen(text: str) -> bool:
    t = text.strip()
    return t.startswith("/screen") or (len(t) <= 80 and bool(_SCREEN_WORDS.search(t)))


def ask_approval(uid, text: str, timeout: int = 180, danger: bool = False):
    """Присылает сообщение с кнопками «Разрешить / Отмена» и ждёт ответа.
    -> True (разрешил) / False (отказ или время вышло) / None (не удалось отправить кнопки)."""
    tok = secrets.token_hex(5)
    ev = threading.Event()
    with _lock:
        _approvals[tok] = {"uid": uid, "event": ev, "result": None}
    yes = "⚠️ Понимаю риск, выполнить" if danger else "✅ Разрешить"
    markup = {"inline_keyboard": [[{"text": yes, "callback_data": f"ok:{tok}"}],
                                  [{"text": "❌ Отмена", "callback_data": f"no:{tok}"}]] if danger else
                                 [[{"text": yes, "callback_data": f"ok:{tok}"},
                                   {"text": "❌ Отмена", "callback_data": f"no:{tok}"}]]}
    mid = None
    try:
        r = _call("sendMessage", http_timeout=15, chat_id=uid, text=text[:3500], reply_markup=markup)
        mid = (r.get("result") or {}).get("message_id")
    except Exception as e:
        log(f"Telegram: не отправились кнопки подтверждения: {e}")
        with _lock:
            _approvals.pop(tok, None)
        return None
    ev.wait(timeout)
    with _lock:
        result = (_approvals.pop(tok, None) or {}).get("result")
    if mid is not None:
        try:
            _call("editMessageReplyMarkup", http_timeout=10, chat_id=uid, message_id=mid,
                  reply_markup={"inline_keyboard": []})
        except Exception:
            pass
    if result is None:
        _send(uid, "⌛ Время на подтверждение вышло — действие отменено.")
        return False
    return result


def ask_danger(uid, text: str):
    """Опасное действие: два раздельных подтверждения подряд."""
    first = ask_approval(uid, "⚠️ ОПАСНОЕ ДЕЙСТВИЕ\n\n" + text, danger=True)
    if first is not True:
        return first
    return ask_approval(uid, "Последний вопрос: точно выполнить?\n\n" + text[:600], timeout=120, danger=True)


class Progress:
    """Одно сообщение в чате, которое обновляется по ходу работы автопилота."""

    def __init__(self, chat_id, title: str):
        self.chat, self.title, self.lines, self.mid, self.last = chat_id, title, [], None, 0.0
        self._push(force=True)

    def add(self, line: str):
        self.lines = (self.lines + [redact(line)[:160]])[-8:]
        self._push()

    def _push(self, force=False):
        if not force and time.time() - self.last < 1.5:
            return
        self.last = time.time()
        text = self.title + ("\n\n" + "\n".join(self.lines) if self.lines else "")
        try:
            if self.mid is None:
                r = _call("sendMessage", http_timeout=15, chat_id=self.chat, text=text[:3500])
                self.mid = (r.get("result") or {}).get("message_id")
            else:
                _call("editMessageText", http_timeout=15, chat_id=self.chat, message_id=self.mid, text=text[:3500])
        except Exception:
            pass


def send_screen(chat_id, as_file: bool = False, caption: str = ""):
    import screen_utils
    img = screen_utils.take_screenshot_color(2400 if as_file else 1600)
    if not as_file:
        return _send_photo(chat_id, img)
    try:
        buf = io.BytesIO()
        img.save(buf, "PNG")
        buf.seek(0)
        r = requests.post(f"{API}/bot{get_token()}/sendDocument", data={"chat_id": chat_id, "caption": caption},
                          files={"document": ("screen.png", buf, "image/png")}, timeout=90)
        if not r.json().get("ok"):
            raise RuntimeError(str(r.json().get("description")))
        return True
    except Exception as e:
        log(f"Telegram: файл со скриншотом не отправился: {e}")
        _send(chat_id, f"Не получилось отправить файл: {str(e)[:200]}")
        return False


def _handle_callback(cb: dict):
    """Нажатие кнопки «Разрешить/Отмена» — принимается только от подтверждённого пользователя, которому адресован запрос."""
    uid = (cb.get("from") or {}).get("id")
    data = str(cb.get("data") or "")
    answer = "Принято"
    action, _, tok = data.partition(":")
    with _lock:
        ap = _approvals.get(tok)
        if uid is None or not is_allowed(uid) or ap is None or ap["uid"] != uid or action not in ("ok", "no"):
            answer = "Запрос устарел или недоступен"
        else:
            ap["result"] = action == "ok"
            ap["event"].set()
    try:
        _call("answerCallbackQuery", http_timeout=10, callback_query_id=cb.get("id"), text=answer)
    except Exception:
        pass


def check_token(token: str):
    """-> (ok, имя бота или текст ошибки)."""
    try:
        r = _call("getMe", token=token.strip(), http_timeout=15)
        if r.get("ok"):
            return True, "@" + str(r["result"].get("username", ""))
        return False, str(r.get("description", "неверный токен"))
    except Exception as e:
        return False, f"нет связи с Telegram: {e}"


def _too_chatty(uid) -> bool:
    now = time.time()
    times = [t for t in _msg_times.get(uid, []) if now - t < 3600]
    times.append(now)
    _msg_times[uid] = times
    return len(times) > MAX_MSGS_PER_HOUR


def _download(file_id: str, limit: int = 14 * 1024 * 1024):
    """Скачивает файл из Telegram -> (bytes, путь на сервере)."""
    r = _call("getFile", http_timeout=20, file_id=file_id)
    if not r.get("ok"):
        raise RuntimeError(str(r.get("description", "не удалось получить файл")))
    info = r["result"]
    if int(info.get("file_size") or 0) > limit:
        raise ValueError("Файл слишком большой (больше 14 МБ).")
    path = info["file_path"]
    resp = requests.get(f"{API}/file/bot{get_token()}/{path}", timeout=60)
    resp.raise_for_status()
    return resp.content, path


def _media_of(msg: dict):
    """-> (вид, file_id, mime) для голосового/аудио/фото/картинки-файла, иначе None."""
    if msg.get("voice"):
        return "audio", msg["voice"]["file_id"], msg["voice"].get("mime_type") or "audio/ogg"
    if msg.get("audio"):
        return "audio", msg["audio"]["file_id"], msg["audio"].get("mime_type") or "audio/mp3"
    if msg.get("video_note"):
        return None
    if msg.get("photo"):
        return "image", msg["photo"][-1]["file_id"], "image/jpeg"
    doc = msg.get("document") or {}
    if str(doc.get("mime_type", "")).startswith("image/"):
        return "image", doc["file_id"], doc["mime_type"]
    return None


def _handle_media(msg, chat, uid, name, kind, file_id, mime):
    import media_input
    caption = (msg.get("caption") or "").strip()
    _typing(chat, "record_voice" if kind == "audio" else "typing")
    try:
        data, _path = _download(file_id)
        if kind == "audio":
            _send(chat, "🎙 Слушаю…")
            text = media_input.transcribe(data, mime.split(";")[0])
            if not text:
                _send(chat, "Не расслышал речь в этом сообщении. Попробуй ещё раз или напиши текстом.")
                return
            _send(chat, f"🎙 Распознал: «{redact(text)[:500]}»")
            _handle({**msg, "voice": None, "audio": None, "text": text, "caption": None}, from_voice=True)
        else:
            _send(chat, "🖼 Смотрю…")
            _send(chat, redact(media_input.ask_image(data, mime, caption))[:3800])
    except Exception as e:
        log(f"Telegram: не удалось обработать вложение: {e}")
        _send(chat, f"❌ Не получилось обработать вложение: {redact(str(e))[:300]}")


def _handle(msg: dict, from_voice: bool = False):
    user = msg.get("from") or {}
    uid = user.get("id")
    chat = (msg.get("chat") or {}).get("id")
    if uid is None or chat is None or (msg.get("chat") or {}).get("type") != "private":
        return                                           # только личные чаты
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or user.get("username") or str(uid)
    text = (msg.get("text") or "").strip()
    media = None if text else _media_of(msg)
    if not text and not media:
        return

    if not is_allowed(uid):
        if _too_chatty(uid):
            return
        with _lock:
            pending_requests()
            p = _pending.get(uid)
            if p is None and len(_pending) >= MAX_PENDING:
                _send(chat, "Сейчас слишком много заявок. Попробуй позже.")
                return
            if p is None or text.startswith("/start"):
                p = _pending[uid] = {"code": make_code(), "name": name, "chat": chat, "expires": time.time() + CODE_TTL}
                log(f"Telegram: заявка на доступ от {name} ({uid}).")
        _send(chat, "🔐 Этот бот управляет компьютером, доступ только по подтверждению.\n\n"
                    f"Твой код: {p['code']}\n\nСкажи этот код владельцу компьютера — он введёт его в программе "
                    "(Настройки → Telegram). Код действует 10 минут. Не отправляй его никому другому.")
        return

    if media:
        threading.Thread(target=_handle_media, args=(msg, chat, uid, name) + media, daemon=True).start()
        return
    text = _MENU_BUTTONS.get(text, text)
    low = text.lower().split("@")[0]
    if low.startswith(("/start", "/help", "/menu")) or low in ("помощь", "меню", "help"):
        _send(chat, HELP_TEXT, keyboard=True)
        return
    if low.startswith("/screenfile"):
        threading.Thread(target=send_screen, args=(chat, True, time.strftime("%d.%m.%Y %H:%M:%S")), daemon=True).start()
        return
    for cmd, key, title in (("/status", "status", "Статус"), ("/stop", "stop", "Стоп"), ("/history", "history", "История"),
                            ("/notes", "notes", "Заметки"), ("/macros", "macros", "Макросы")):
        if low.startswith(cmd):
            fn = _hooks.get(key)
            try:
                _send(chat, redact(str(fn())) if fn else f"{title}: недоступно.")
            except Exception as e:
                _send(chat, f"{title}: ошибка — {e}")
            return
    target = _screen_target(text) if _wants_screen(text) and not text.startswith("/screen") else ""
    if target and _on_command is not None:
        log(f"Telegram: скриншот с действием от {name}: {redact(text)[:120]}")
        _send(chat, "⏳ Принято: сначала перейду к нужному окну, потом пришлю скриншот.")
        threading.Thread(target=_run_screen_target, args=(chat, target, uid, name), daemon=True).start()
        return
    if _wants_screen(text):
        try:
            import screen_utils
            _send_photo(chat, screen_utils.take_screenshot_color())
        except Exception as e:
            log(f"Telegram: скриншот не сделан: {e}")
            _send(chat, f"Не удалось сделать скриншот: {e}")
        return
    if low.startswith("/windows"):
        fn = _hooks.get("windows")
        _send(chat, redact(str(fn())) if fn else "Недоступно.")
        return
    if low.startswith("/") and re.match(r"^/[a-z_]+\b", low):
        _send(chat, "Не знаю такую команду. Посмотри список: /help")
        return
    if _on_command is None:
        _send(chat, "Ассистент сейчас не может принять команду.")
        return
    log(f"Telegram: команда от {name}: {redact(text)[:120]}")
    _typing(chat)
    _send(chat, "⏳ Принято, работаю. Если нужно подтверждение — пришлю кнопки.")
    threading.Thread(target=_run_command, args=(chat, text, uid, name), daemon=True).start()


def _run_screen_target(chat, target, uid, name):
    try:
        _on_command(f"Переключись на: {target}. Только переключись (найди нужное окно или вкладку), "
                    "больше ничего не делай, ничего не закрывай и не вводи.", uid, name)
        time.sleep(1.5)
        import screen_utils
        _send_photo(chat, screen_utils.take_screenshot_color())
    except Exception as e:
        _send(chat, f"❌ Не получилось: {redact(str(e))[:300]}")


def _run_command(chat, text, uid, name):
    try:
        result = _on_command(text, uid, name)
        if result:                    # пустой ответ — программа уже написала итог в чат сама
            _send(chat, "✅ " + redact(str(result))[:1500])
    except Exception as e:
        _send(chat, f"❌ Не получилось: {redact(str(e))[:300]}")


def _register_commands(token):
    try:
        _call("setMyCommands", token=token, http_timeout=15, commands=[
            {"command": "menu", "description": "Кнопки быстрых действий"},
            {"command": "screen", "description": "Скриншот экрана"},
            {"command": "screenfile", "description": "Скриншот файлом (без сжатия)"},
            {"command": "status", "description": "Что делает ассистент"},
            {"command": "stop", "description": "Остановить автопилот"},
            {"command": "history", "description": "Последние команды"},
            {"command": "windows", "description": "Какие окна открыты"},
            {"command": "notes", "description": "Заметки"},
            {"command": "macros", "description": "Макросы"},
            {"command": "help", "description": "Справка"}])
    except Exception as e:
        log(f"Telegram: меню команд не установилось: {e}")


_announced = False


def _announce_online():
    """Один раз за запуск программы сообщает подтверждённым пользователям, что ассистент на связи."""
    global _announced
    import config
    if _announced or not config.get("telegram_announce"):
        return
    _announced = True
    for u in users():
        try:
            _send(u["id"] if isinstance(u, dict) and "id" in u else u, "🟢 Ассистент на связи (компьютер включён). /menu — кнопки.", keyboard=True)
        except Exception:
            pass


def _loop(stop):
    offset = None
    fails = 0
    registered = False
    while not stop.is_set():
        token = get_token()
        if not token:
            _state["error"] = "нет токена"
            stop.wait(5)
            continue
        try:
            params = {"timeout": 25, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset
            r = _call("getUpdates", token=token, http_timeout=40, **params)
            if not r.get("ok"):
                raise RuntimeError(str(r.get("description", "ошибка Telegram")))
            _state["error"] = ""
            fails = 0
            if not registered:
                registered = True
                _register_commands(token)
                _announce_online()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "message" in upd:
                        _handle(upd["message"])
                    elif "callback_query" in upd:
                        _handle_callback(upd["callback_query"])
                except Exception as e:
                    log(f"Telegram: ошибка обработки сообщения: {e}")
        except Exception as e:
            fails += 1
            _state["error"] = str(e)[:120]
            if fails in (1, 5, 20):
                log(f"Telegram: нет связи/ошибка: {e}")
            stop.wait(min(60, 3 * fails))
    _state["running"] = False


def start(on_command, hooks=None):
    """Запускает бота, если он включён в настройках и есть токен."""
    global _thread, _on_command, _stop
    import config
    _on_command = on_command
    if hooks is not None:
        _hooks.clear()
        _hooks.update(hooks)
    if not config.get("telegram_enabled") or not get_token():
        return False
    if _thread and _thread.is_alive():
        return True
    _stop = threading.Event()
    _state["running"] = True
    _thread = threading.Thread(target=_loop, args=(_stop,), daemon=True, name="telegram-bot")
    _thread.start()
    log("Telegram-бот запущен.")
    return True


def stop():
    _stop.set()
    reject_all()


def restart(on_command=None, hooks=None):
    stop()
    global _thread
    _thread = None            # старый поток доработает текущий запрос и сам завершится (у него свой Event)
    return start(on_command or _on_command, hooks)


def status() -> str:
    import config
    if not config.get("telegram_enabled"):
        return "выключен"
    if not get_token():
        return "нет токена"
    if _state["error"]:
        return "ошибка: " + _state["error"]
    return "работает" if _state["running"] else "запускается…"
