"""OpenRouter как запасной провайдер: если Gemini недоступен (лимиты на всех ключах, сбой), запрос уходит сюда.
Формат запроса — как у Gemini внутри программы; здесь он переводится в формат OpenAI-чата (messages), а ответ
возвращается в «геминевской» форме, чтобы остальной код (ai_brain.extract_text) ничего не заметил.
Включается в Настройках; ключ хранится в защищённом хранилище. Картинки (скриншоты) передаются, аудио — нет.
"""
import requests

import config
import secure_store
from logger import log

API = "https://openrouter.ai/api/v1"
_auto_model = None


def get_key() -> str:
    return secure_store.get_secret("openrouter_key") or config.get("openrouter_key_plain") or ""


def set_key(key: str):
    """Пустая строка — удалить ключ."""
    key = (key or "").strip()
    if secure_store.set_secret("openrouter_key", key):
        config.save({"openrouter_key_plain": ""})
    else:
        config.save({"openrouter_key_plain": key})       # хранилища нет — как с ключами Gemini, в settings.json


def enabled() -> bool:
    return bool(config.get("openrouter_enabled")) and bool(get_key())


def to_messages(payload: dict) -> list:
    """Gemini-payload -> messages OpenAI-формата."""
    msgs = []
    sys_parts = (payload.get("systemInstruction") or {}).get("parts") or []
    sys_text = "\n".join(p.get("text", "") for p in sys_parts if p.get("text"))
    if sys_text:
        msgs.append({"role": "system", "content": sys_text})
    for turn in payload.get("contents", []):
        role = "assistant" if turn.get("role") == "model" else "user"
        content = []
        for part in turn.get("parts", []):
            if part.get("text") is not None:
                content.append({"type": "text", "text": part["text"]})
            elif "inline_data" in part:
                data = part["inline_data"]
                mime = data.get("mime_type", "")
                if not mime.startswith("image/"):
                    raise RuntimeError("OpenRouter не принимает аудио и файлы — только текст и картинки.")
                content.append({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data['data']}"}})
        if role == "assistant":
            msgs.append({"role": "assistant", "content": "\n".join(c["text"] for c in content if c["type"] == "text")})
        elif len(content) == 1 and content[0]["type"] == "text":
            msgs.append({"role": "user", "content": content[0]["text"]})
        else:
            msgs.append({"role": "user", "content": content})
    return msgs


def _headers(key: str) -> dict:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json",
            "X-OpenRouter-Title": "AI Assistant"}


def list_models(key: str):
    """(ok, [(id, бесплатная?, видит картинки?)] или текст ошибки). Сначала бесплатные с поддержкой картинок."""
    try:
        r = requests.get(f"{API}/models", headers=_headers(key), timeout=20)
        r.raise_for_status()
        out = []
        for m in r.json().get("data", []):
            pricing = m.get("pricing") or {}
            free = str(pricing.get("prompt", "1")) in ("0", "0.0") and str(pricing.get("completion", "1")) in ("0", "0.0")
            free = free or str(m.get("id", "")).endswith(":free")
            vision = "image" in ((m.get("architecture") or {}).get("input_modalities") or [])
            out.append((m.get("id", ""), free, vision))
        out.sort(key=lambda x: (not (x[1] and x[2]), not x[1], x[0]))
        return True, out
    except Exception as e:
        return False, f"Не удалось получить список моделей: {type(e).__name__}: {str(e)[:120]}"


def pick_model(need_vision: bool) -> str:
    """Модель из настроек; если пусто — первая бесплатная (с картинками, когда нужны)."""
    global _auto_model
    chosen = (config.get("openrouter_model") or "").strip()
    if chosen:
        return chosen
    if _auto_model is None:
        ok, models = list_models(get_key())
        if ok:
            pool = [m for m in models if m[1] and (m[2] or not need_vision)] or [m for m in models if m[1]]
            _auto_model = pool[0][0] if pool else ""
    if not _auto_model:
        raise RuntimeError("OpenRouter: не выбрана модель — открой Настройки и нажми «Найти бесплатные модели».")
    return _auto_model


def generate(payload: dict, cancel=None) -> dict:
    """Запрос к OpenRouter; ответ в форме Gemini {'candidates': [{'content': {'parts': [{'text': ...}]}}]}."""
    key = get_key()
    if not key:
        raise RuntimeError("OpenRouter: нет ключа.")
    msgs = to_messages(payload)
    has_image = any(isinstance(m["content"], list) for m in msgs)
    model = pick_model(has_image)
    gen = payload.get("generationConfig") or {}
    body = {"model": model, "messages": msgs, "max_tokens": int(gen.get("maxOutputTokens") or 1024),
            "temperature": gen.get("temperature", 0.3)}
    r = requests.post(f"{API}/chat/completions", headers=_headers(key), json=body, timeout=90)
    if r.status_code >= 400:
        raise RuntimeError(f"OpenRouter ответил {r.status_code}: {r.text[:200]}")
    try:
        text = r.json()["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError, ValueError):
        raise RuntimeError(f"OpenRouter: неожиданный ответ: {r.text[:200]}")
    log(f"Ответил запасной провайдер OpenRouter (модель «{model}»).")
    return {"candidates": [{"content": {"parts": [{"text": text}]}}]}
