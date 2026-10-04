"""
Общение с Google Gemini (бесплатный API через AI Studio).

generate() — единственная точка, через которую идут все запросы (автопилот, распознавание речи).
Она сама:
  * держит ТЕМП: не больше `requests_per_minute` запросов в минуту на ключ (иначе бесплатный
    лимит Google ловится уже на второй-третьей минуте работы автопилота);
  * при ответе 429 ждёт ровно столько, сколько просит Google, или берёт другой ключ;
  * при нехватке ожидания показывает причину через on_wait (окно автопилота пишет
    "⏳ жду лимит…" вместо молчаливого зависания);
  * если всё же не вышло — называет НАСТОЯЩУЮ причину, а не общее "лимиты, ключи или интернет":
      – нет бесплатной квоты на эту модель ("limit: 0") -> смени модель;
      – исчерпан дневной лимит; – лимит запросов в минуту; – ключ недействителен; – нет интернета.
"""
import json
import time
import requests

import config
import key_manager
from logger import log

API_ROOT = "https://generativelanguage.googleapis.com/v1beta"


def _gemini_url() -> str:
    return f"{API_ROOT}/models/{config.get('model')}:generateContent"


def _error_info(response) -> tuple:
    """(status, message) из тела ошибки Gemini; пустые строки, если разобрать не удалось."""
    try:
        err = response.json().get("error", {})
        return str(err.get("status", "")), str(err.get("message", ""))
    except Exception:
        return "", ""


def quota_kind(response) -> str:
    """Что именно за лимит в ответе 429: zero | day | minute | unknown."""
    try:
        err = response.json().get("error", {})
    except Exception:
        return "unknown"
    text = (str(err.get("message", "")) + " " + json.dumps(err.get("details", []))).lower()
    if "limit: 0" in text:
        return "zero"
    if "perday" in text or "per day" in text:
        return "day"
    if "perminute" in text or "per minute" in text:
        return "minute"
    return "unknown"


def _call_gemini(api_key: str, payload: dict):
    """
    Один запрос с одним ключом. Возвращает (response_или_None, outcome, kind), outcome:
    'ok' | 'rate_limited' | 'no_quota' | 'invalid' | 'bad_model' | 'server_error' | 'network_error'
    """
    try:
        response = requests.post(_gemini_url(), params={"key": api_key}, json=payload, timeout=60)
    except requests.exceptions.RequestException as e:
        log(f"Сетевая ошибка при обращении к Gemini: {e}")
        return None, "network_error", ""

    code = response.status_code
    if code == 200:
        return response, "ok", ""

    if code == 429:
        kind = quota_kind(response)
        delay = key_manager.parse_retry_delay(response.json()) if response.content else None
        if kind == "zero":
            key_manager.mark_rate_limited(api_key, 600)          # нет квоты на модель — не долбим
            return None, "no_quota", kind
        if kind == "day":
            key_manager.mark_rate_limited(api_key, 3 * 3600)
            return None, "rate_limited", kind
        key_manager.mark_rate_limited(api_key, delay if delay is not None else key_manager.DEFAULT_COOLDOWN)
        return None, "rate_limited", kind

    status, message = _error_info(response)

    if code == 404:
        log(f"Gemini: модель '{config.get('model')}' не найдена: {message[:200]}")
        return None, "bad_model", ""

    # Ключ считаем недействительным ТОЛЬКО если об этом прямо сказано. Обычный 400
    # (кривой запрос) — не повод навсегда выкидывать рабочий ключ.
    if code == 403 or (code == 400 and "api key" in message.lower()):
        key_manager.mark_invalid(api_key)
        return None, "invalid", ""

    log(f"Gemini вернул код {code} ({status}): {message[:200]}")
    if code == 400:
        return None, "bad_params", message[:300]      # кривой запрос повтором не лечится
    return None, "server_error", ""


def _sleep(seconds: float, cancel, on_wait, why: str) -> bool:
    """Ожидание, которое можно прервать. False — прервали."""
    if on_wait is not None and seconds >= 2:
        try:
            on_wait(seconds, why)
        except Exception:
            pass
    end = time.time() + seconds
    while time.time() < end:
        if cancel is not None and cancel.is_set():
            return False
        time.sleep(min(0.2, max(0.0, end - time.time())))
    return not (cancel is not None and cancel.is_set())


def _limit_message(count: int, reasons: dict) -> str:
    kinds = set(reasons.values())
    model = config.get("model")
    if "no_quota" in kinds:
        return (f"У твоего ключа нет бесплатной квоты на модель «{model}» (Google отвечает «limit: 0»). "
                "Открой Настройки → Gemini → «Найти доступные модели», выбери другую (например, flash-lite) "
                "или добавь ключ из другого Google-аккаунта.")
    if "day" in kinds and kinds <= {"day", "invalid"}:
        return ("Дневной бесплатный лимит Gemini исчерпан. Он обновится в полночь по тихоокеанскому времени "
                "(утром по Украине/Москве). Можно добавить ключи с других Google-аккаунтов.")
    if "rate_limited" in kinds or "minute" in kinds:
        return ("Достигнут лимит запросов в минуту. На бесплатном тарифе он небольшой (обычно 5–10 на проект), "
                "а лимиты Google считает на ПРОЕКТ, а не на ключ. Подожди минуту, поставь в Настройках поменьше "
                "«Запросов в минуту» или добавь ключи из других аккаунтов.")
    if kinds == {"invalid"}:
        return "Ключ Gemini недействителен — проверь его в Настройках («Проверить все ключи»)."
    return f"Не удалось получить ответ от Gemini ({count} ключ(а)). Проверь ключи и интернет в Настройках."


def generate(payload: dict, on_wait=None, cancel=None, max_wait: float = 150.0) -> dict:
    """
    Отправляет payload в Gemini. Возвращает JSON-ответ (dict).
    ValueError — ключей нет вообще; RuntimeError — не вышло (текст понятен пользователю).
    on_wait(секунды, причина) — вызывается, когда приходится ждать; cancel — threading.Event отмены.
    """
    keys = key_manager.get_keys()
    if not keys:
        raise ValueError(
            "Не задан ни один ключ Gemini. Открой «Настройки» и вставь хотя бы один "
            "бесплатный ключ с https://aistudio.google.com"
        )

    rpm = int(config.get("requests_per_minute") or 0)
    waited, net_fails, server_fails, reasons = 0.0, 0, 0, {}

    for _ in range(len(keys) * 4 + 8):
        key, wait = key_manager.pick_key(rpm)
        if key is None:
            break
        if wait > 0.05:
            if waited + wait > max_wait:
                break
            own = key_manager.rpm_wait(key, rpm) >= key_manager.cooldown_remaining(key)
            why = ("Соблюдаю безопасный темп запросов к Gemini" if own
                   else "Gemini просит подождать (лимит запросов в минуту)")
            if not _sleep(wait, cancel, on_wait, why):
                raise RuntimeError("Остановлено пользователем.")
            waited += wait

        key_manager.record_use(key)
        response, outcome, kind = _call_gemini(key, payload)

        if outcome == "ok":
            key_manager.mark_ok(key)
            return response.json()
        if outcome == "bad_model":
            raise RuntimeError(
                f"Модель Gemini «{config.get('model')}» недоступна (Google мог её отключить или переименовать). "
                "Открой Настройки → Gemini → «Найти доступные модели» и выбери рабочую.")
        if outcome == "bad_params":
            raise RuntimeError(f"Gemini отклонил запрос: {kind}")
        if outcome == "network_error":
            net_fails += 1
            if net_fails >= 2:
                raise RuntimeError("Нет связи с Gemini — проверь интернет.")
            time.sleep(1.0)
            continue
        if outcome == "server_error":
            server_fails += 1
            if server_fails >= 3:
                raise RuntimeError("Сервер Gemini временно не отвечает. Попробуй через минуту.")
            time.sleep(2.0)
            continue
        reasons[key] = kind if outcome == "rate_limited" and kind else outcome

    raise RuntimeError(_limit_message(len(keys), reasons))


def extract_text(data: dict):
    """Текст первого ответа модели или None, если ответа нет."""
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError):
        log(f"Неожиданный ответ от Gemini: {str(data)[:300]}")
        return None


def parse_json(text):
    """Достаёт dict из ответа модели (снимая ```json-обёртку, если она есть). None — если не вышло."""
    if not text:
        return None
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log(f"Не удалось разобрать ответ ИИ как JSON: {cleaned[:300]}")
        return None


def test_api_key(api_key: str):
    """Проверка одного ключа. Возвращает (успех, сообщение) — с настоящей причиной от Google."""
    if not api_key:
        return False, "Ключ пустой."
    try:
        resp = requests.post(
            _gemini_url(), params={"key": api_key},
            json={"contents": [{"role": "user", "parts": [{"text": "ping"}]}],
                  "generationConfig": {"maxOutputTokens": 5}},
            timeout=20,
        )
    except Exception as e:
        return False, f"Не удалось проверить ключ (нет интернета?): {e}"
    code = resp.status_code
    status, message = _error_info(resp)
    short = message.replace("\n", " ")[:160]
    if code == 200:
        return True, f"Ключ работает (модель {config.get('model')})."
    if code == 404:
        return False, f"Модель «{config.get('model')}» не найдена — смени её в Настройках («Найти доступные модели»)."
    if code == 429:
        kind = quota_kind(resp)
        if kind == "zero":
            return False, (f"Ключ рабочий, но на модель «{config.get('model')}» нет бесплатной квоты "
                           "(limit: 0) — выбери другую модель.")
        if kind == "day":
            return True, "Ключ рабочий, но дневной бесплатный лимит исчерпан — обновится в полночь по Тихоокеанскому времени."
        return True, f"Ключ рабочий, но сейчас лимит запросов в минуту. {short}"
    if code in (400, 403):
        return False, f"Google отклонил ключ: {short or 'недействителен или доступ запрещён'}"
    return False, f"Ответ сервера {code}: {short}"


def list_models(api_key: str):
    """Модели, доступные этому ключу для generateContent: (ok, список имён | текст ошибки)."""
    try:
        resp = requests.get(f"{API_ROOT}/models", params={"key": api_key, "pageSize": 200}, timeout=20)
    except Exception as e:
        return False, f"Нет связи с Google: {e}"
    if resp.status_code != 200:
        return False, f"Google ответил {resp.status_code}: {_error_info(resp)[1][:160]}"
    names = []
    for m in resp.json().get("models", []):
        name = str(m.get("name", "")).removeprefix("models/")
        if "generateContent" in m.get("supportedGenerationMethods", []) and name.startswith("gemini"):
            names.append(name)
    return True, sorted(names)
