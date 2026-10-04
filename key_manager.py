"""
Пул ключей Gemini и "светофор" запросов.

Что важно знать про лимиты Google (по их же документации):
  * лимиты считаются на ПРОЕКТ, а не на ключ — два ключа из одного проекта лимит не удваивают,
    а вот ключи из РАЗНЫХ аккаунтов/проектов складываются;
  * на бесплатном тарифе это порядка 5–10 запросов в МИНУТУ на модель (точные цифры Google
    больше не публикует, они видны в AI Studio), поэтому быстрая серия запросов упирается
    в лимит, даже если за сутки потрачено мало.

Поэтому здесь два механизма:
  1. свой "светофор" — не отправляем больше N запросов в минуту на ключ (N — в Настройках),
     вместо того чтобы биться о лимит и получать 429;
  2. если Google всё же ответил 429 — ключ "остывает" ровно столько, сколько велел Google
     (retryDelay), а запрос уходит на другой ключ или ждёт.

Состояние живёт в памяти: при перезапуске всё считается заново.
"""
import re
import time

import config
from logger import log

_cooldowns = {}          # ключ -> unix-время, до которого его не трогаем (лимит Google)
_invalid = set()         # ключи, которые Google назвал недействительными (на эту сессию)
_stamps = {}             # ключ -> времена наших запросов за последнюю минуту
DEFAULT_COOLDOWN = 30


def get_keys() -> list:
    return [k.strip() for k in config.get("gemini_api_keys") if k.strip()]


def _mask(key: str) -> str:
    return f"...{key[-6:]}" if len(key) >= 6 else "...???"


def cooldown_remaining(key: str) -> float:
    return max(0.0, _cooldowns.get(key, 0) - time.time())


def is_cooling(key: str) -> bool:
    return key in _invalid or cooldown_remaining(key) > 0


def is_invalid(key: str) -> bool:
    return key in _invalid


def parse_retry_delay(response_json: dict):
    """Точное время ожидания из ответа Gemini (RetryInfo.retryDelay вида '34s' или '34.5s')."""
    try:
        for d in response_json.get("error", {}).get("details", []):
            if "retryDelay" in d:
                match = re.match(r"([\d.]+)", str(d["retryDelay"]))
                if match:
                    return float(match.group(1))
    except Exception:
        pass
    return None


def mark_rate_limited(key: str, delay=None):
    wait = delay if delay is not None else DEFAULT_COOLDOWN
    _cooldowns[key] = time.time() + wait
    log(f"Ключ {_mask(key)}: лимит Google, пауза {int(wait)} c.")


def mark_invalid(key: str):
    _invalid.add(key)
    log(f"Ключ {_mask(key)}: недействителен, исключён из ротации на эту сессию.")


def mark_ok(key: str):
    _cooldowns.pop(key, None)
    _invalid.discard(key)


# ---------- свой лимит запросов в минуту ----------

def record_use(key: str):
    _stamps.setdefault(key, []).append(time.time())


def rpm_wait(key: str, rpm: int) -> float:
    """Сколько секунд подождать, чтобы не превысить rpm запросов за минуту (0 — можно сейчас)."""
    if rpm <= 0:
        return 0.0
    now = time.time()
    stamps = _stamps.setdefault(key, [])
    while stamps and now - stamps[0] >= 60:
        stamps.pop(0)
    return 0.0 if len(stamps) < rpm else max(0.0, stamps[0] + 60 - now)


def pick_key(rpm: int, exclude=()):
    """
    Лучший ключ прямо сейчас: (ключ, сколько секунд ждать до его использования).
    (None, 0) — если все ключи недействительны.
    """
    best = None
    for k in get_keys():
        if k in exclude or k in _invalid:
            continue
        wait = max(cooldown_remaining(k), rpm_wait(k, rpm))
        if best is None or wait < best[1]:
            best = (k, wait)
    return best if best else (None, 0.0)


# ---------- совместимость и отчёт для интерфейса ----------

def next_available_key(exclude=None):
    exclude = exclude or set()
    for k in get_keys():
        if k not in exclude and not is_cooling(k):
            return k
    return None


def shortest_wait() -> float:
    keys = [k for k in get_keys() if k not in _invalid]
    return min((cooldown_remaining(k) for k in keys), default=0.0)


def status_report() -> list:
    rpm = int(config.get("requests_per_minute") or 0)
    report = []
    for k in get_keys():
        if k in _invalid:
            status = "недействителен"
        elif cooldown_remaining(k) > 3600:
            status = "дневной лимит исчерпан"
        elif cooldown_remaining(k) > 0:
            status = f"лимит Google, ждёт ~{int(cooldown_remaining(k))} c"
        elif rpm_wait(k, rpm) > 0:
            status = f"пауза по своему лимиту ~{int(rpm_wait(k, rpm))} c"
        else:
            status = "готов"
        report.append({"masked": _mask(k), "status": status})
    return report


def all_daily_exhausted() -> bool:
    """True, если у ВСЕХ действующих ключей исчерпан дневной лимит (ждать придётся до сброса)."""
    live = [x for x in get_keys() if x not in _invalid]
    return bool(live) and all(cooldown_remaining(x) > 3600 for x in live)


def _pacific_offset_hours(utc_dt) -> int:
    """-7 летом (PDT) или -8 зимой (PST) по правилам США; без базы часовых поясов, которой на Windows может не быть."""
    import datetime as dt

    def nth_sunday(year, month, n):
        first = dt.datetime(year, month, 1)
        return first + dt.timedelta(days=(6 - first.weekday()) % 7 + 7 * (n - 1))

    naive = utc_dt.replace(tzinfo=None)
    start = nth_sunday(naive.year, 3, 2).replace(hour=10)     # 2:00 PST
    end = nth_sunday(naive.year, 11, 1).replace(hour=9)       # 2:00 PDT
    return -7 if start <= naive < end else -8


def next_daily_reset():
    """Ближайший сброс дневного лимита Google (полночь по Тихоокеанскому времени + 5 минут) — по ЛОКАЛЬНОМУ времени (naive)."""
    import datetime as dt
    now_utc = dt.datetime.now(dt.timezone.utc)
    off = _pacific_offset_hours(now_utc)
    pacific_now = now_utc + dt.timedelta(hours=off)
    target_pacific = (pacific_now + dt.timedelta(days=1)).replace(hour=0, minute=5, second=0, microsecond=0)
    target_utc = target_pacific - dt.timedelta(hours=off)
    local_offset = dt.datetime.now().astimezone().utcoffset()
    return (target_utc + local_offset).replace(tzinfo=None)
