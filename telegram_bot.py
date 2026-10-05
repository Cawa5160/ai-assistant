"""
Управление ассистентом из Telegram (необязательно, выключено по умолчанию).

Как устроена безопасность:
  * бот отвечает ТОЛЬКО подтверждённым пользователям; остальным — только код доступа;
  * новому пользователю программа генерирует одноразовый код из 16 символов (живёт 10 минут) и показывает
    его в чате; чтобы получить доступ, владелец должен в ПРОГРАММЕ (Настройки → Telegram) ввести этот код —
    значит, подтверждает человек, сидящий за компьютером;
  * команды из Telegram НИКОГДА не запускаются сами: на компьютере всегда нужен клик «Разрешить»
    (таймеры автозапуска для них отключены), опасные действия — как обычно с усиленным подтверждением;
  * токен бота хранится в защищённом хранилище Windows (если оно есть);
  * в самом чате пароли и ключи маскируются (privacy.redact), экран бот присылает только по команде /screen.
Работает на обычных HTTPS-запросах (long polling), без дополнительных библиотек.
"""
import io
import json
import os
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
_on_command = None   # callable(text, user_id, user_name) — выполняет команду на компьютере


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

def _call(method: str, token: str = None, timeout: int = 35, **params):
    token = token or get_token()
    r = requests.post(f"{API}/bot{token}/{method}", json=params, timeout=timeout)
    return r.json()


def _send(chat_id, text: str):
    try:
        _call("sendMessage", timeout=15, chat_id=chat_id, text=text[:3900])
    except Exception as e:
        log(f"Telegram: не отправилось сообщение: {e}")


def _send_photo(chat_id, image):
    try:
        buf = io.BytesIO()
        image.convert("RGB").save(buf, "JPEG", quality=70)
        buf.seek(0)
        requests.post(f"{API}/bot{get_token()}/sendPhoto", data={"chat_id": chat_id},
                      files={"photo": ("screen.jpg", buf, "image/jpeg")}, timeout=40)
    except Exception as e:
        log(f"Telegram: не отправился скриншот: {e}")
        _send(chat_id, "Не получилось отправить скриншот.")


def check_token(token: str):
    """-> (ok, имя бота или текст ошибки)."""
    try:
        r = _call("getMe", token=token.strip(), timeout=15)
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


def _handle(msg: dict):
    user = msg.get("from") or {}
    uid = user.get("id")
    chat = (msg.get("chat") or {}).get("id")
    if uid is None or chat is None or (msg.get("chat") or {}).get("type") != "private":
        return                                           # только личные чаты
    name = " ".join(x for x in (user.get("first_name"), user.get("last_name")) if x) or user.get("username") or str(uid)
    text = (msg.get("text") or "").strip()
    if not text:
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

    if text.startswith("/start") or text.startswith("/help"):
        _send(chat, "Команды:\n/screen — скриншот экрана\n/help — эта справка\n\nЛюбой другой текст — команда "
                    "ассистенту (например: «открой блокнот и напиши привет»). На компьютере нужно будет нажать "
                    "«Разрешить» — сам по себе он ничего не запускает.")
        return
    if text.startswith("/screen"):
        try:
            import screen_utils
            _send_photo(chat, screen_utils.take_screenshot_image())
        except Exception as e:
            _send(chat, f"Не удалось сделать скриншот: {e}")
        return
    if _on_command is None:
        _send(chat, "Ассистент сейчас не может принять команду.")
        return
    log(f"Telegram: команда от {name}: {redact(text)[:120]}")
    _send(chat, "⏳ Принято. Подтверди выполнение на компьютере.")
    threading.Thread(target=_run_command, args=(chat, text, uid, name), daemon=True).start()


def _run_command(chat, text, uid, name):
    try:
        result = _on_command(text, uid, name)
        _send(chat, "✅ " + redact(str(result or "Готово"))[:1500])
    except Exception as e:
        _send(chat, f"❌ Не получилось: {redact(str(e))[:300]}")


def _loop(stop):
    offset = None
    fails = 0
    while not stop.is_set():
        token = get_token()
        if not token:
            _state["error"] = "нет токена"
            stop.wait(5)
            continue
        try:
            params = {"timeout": 25, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset
            r = _call("getUpdates", token=token, timeout=40, **params)
            if not r.get("ok"):
                raise RuntimeError(str(r.get("description", "ошибка Telegram")))
            _state["error"] = ""
            fails = 0
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                if "message" in upd:
                    try:
                        _handle(upd["message"])
                    except Exception as e:
                        log(f"Telegram: ошибка обработки сообщения: {e}")
        except Exception as e:
            fails += 1
            _state["error"] = str(e)[:120]
            if fails in (1, 5, 20):
                log(f"Telegram: нет связи/ошибка: {e}")
            stop.wait(min(60, 3 * fails))
    _state["running"] = False


def start(on_command):
    """Запускает бота, если он включён в настройках и есть токен."""
    global _thread, _on_command, _stop
    import config
    _on_command = on_command
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


def restart(on_command=None):
    stop()
    global _thread
    _thread = None            # старый поток доработает текущий запрос и сам завершится (у него свой Event)
    return start(on_command or _on_command)


def status() -> str:
    import config
    if not config.get("telegram_enabled"):
        return "выключен"
    if not get_token():
        return "нет токена"
    if _state["error"]:
        return "ошибка: " + _state["error"]
    return "работает" if _state["running"] else "запускается…"
