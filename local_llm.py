"""
Локальные модели через Ollama: статус, установленные модели, чат, проверка моделей в реестре и подбор по железу.
Скачивать и ставить модели программа сама не будет — только показывает команду `ollama pull ...`.
"""
import json
import re

import requests

from logger import log

OLLAMA = "http://localhost:11434"
REGISTRY = "https://registry.ollama.ai/v2/library/{name}/manifests/{tag}"

# Встроенный короткий список — запасной на случай, когда реестр недоступен. Каждое имя проверяется в реестре.
CANDIDATES = [
    ("qwen3:1.7b", "самая лёгкая, для слабых ПК"),
    ("llama3.2:3b", "компактная, быстрая"),
    ("qwen2.5:3b", "то, что программа использует по умолчанию для простых команд"),
    ("gemma3:4b", "хороша для текста, понимает и русский"),
    ("qwen3:4b", "умнее при том же размере, по-русски неплохо"),
    ("qwen3:8b", "заметно сильнее, нужна видеокарта от 8 ГБ"),
    ("gemma3:12b", "качественная, нужна видеокарта от 12 ГБ"),
]

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,40}(:[a-z0-9._-]{1,30})?$")
OVERHEAD_GB = 1.2        # запас видеопамяти на контекст и служебное


def is_running(timeout: float = 1.0) -> bool:
    try:
        requests.get(OLLAMA, timeout=timeout)
        return True
    except requests.exceptions.RequestException:
        return False


def installed_models(timeout: float = 3.0) -> list:
    """[{'name', 'size_gb'}] — то, что уже скачано. Пустой список, если Ollama не запущена."""
    try:
        data = requests.get(OLLAMA + "/api/tags", timeout=timeout).json()
        return [{"name": m.get("name", ""), "size_gb": round(m.get("size", 0) / 1024 ** 3, 1)}
                for m in data.get("models", []) if m.get("name")]
    except Exception:
        return []


def fit(size_gb, vram_gb, ram_gb) -> str:
    """'gpu' — целиком в видеопамяти (быстро); 'cpu' — только в оперативной (медленно); 'no' — не потянет; '?' — размер неизвестен."""
    if not size_gb:
        return "?"
    if vram_gb and size_gb + OVERHEAD_GB <= vram_gb:
        return "gpu"
    if ram_gb and size_gb + 2.0 <= ram_gb * 0.6:
        return "cpu"
    return "no"


def registry_size_gb(name: str, timeout: float = 8.0):
    """Реальный размер модели по манифесту в реестре Ollama; None, если модели нет или реестр недоступен."""
    if not NAME_RE.match(name or ""):
        return None
    model, _, tag = name.partition(":")
    try:
        r = requests.get(REGISTRY.format(name=model, tag=tag or "latest"), timeout=timeout,
                         headers={"Accept": "application/vnd.docker.distribution.manifest.v2+json"})
        if r.status_code != 200:
            return None
        total = sum(int(layer.get("size", 0)) for layer in r.json().get("layers", []))
        return round(total / 1024 ** 3, 1) if total else None
    except Exception as e:
        log(f"Реестр Ollama: {name}: {e}")
        return None


def parse_names(text: str) -> list:
    """Достаёт имена моделей из ответа ИИ (JSON-массив строк); мусор и выдумки с неверным форматом отбрасываются."""
    try:
        start, end = text.index("["), text.rindex("]") + 1
        items = json.loads(text[start:end])
    except Exception:
        return []
    out = []
    for it in items:
        name = str(it).strip().lower()
        if NAME_RE.match(name) and name not in out:
            out.append(name)
    return out[:8]


def suggest_with_gemini(hw: dict) -> list:
    """Просит Gemini предложить модели Ollama под железо. В запрос идут только характеристики ПК."""
    import ai_brain
    prompt = (f"Компьютер: видеопамять {hw.get('vram_gb') or 'неизвестно'} ГБ, оперативная память {hw.get('ram_gb')} ГБ. "
              "Назови до 6 моделей из каталога Ollama (формат имя:тег, например qwen3:4b), которые подойдут для "
              "чата на русском и поместятся в эту память. Ответь ТОЛЬКО JSON-массивом строк.")
    data = ai_brain.generate({"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                              "generationConfig": {"temperature": 0.2, "maxOutputTokens": 300}})
    return parse_names(ai_brain.extract_text(data) or "")


def recommend(hw: dict, sizes: dict, notes: dict = None) -> list:
    """Строки для вкладки: [{'name','size_gb','fit','note'}], сначала то, что целиком в видеопамяти, крупные выше."""
    order = {"gpu": 0, "cpu": 1, "?": 2, "no": 3}
    rows = [{"name": n, "size_gb": s, "fit": fit(s, hw.get("vram_gb"), hw.get("ram_gb")), "note": (notes or {}).get(n, "")}
            for n, s in sizes.items()]
    rows.sort(key=lambda r: (order[r["fit"]], -(r["size_gb"] or 0)))
    return rows


def chat(model: str, turns: list, system: str = "", timeout: float = 180.0) -> str:
    """turns: [{'role': 'user'|'assistant', 'text'}]. Ответ локальной модели или RuntimeError."""
    messages = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": t["role"], "content": t["text"]} for t in turns]
    try:
        r = requests.post(OLLAMA + "/api/chat", json={"model": model, "messages": messages, "stream": False},
                          timeout=timeout)
    except requests.exceptions.RequestException as e:
        raise RuntimeError("Ollama не отвечает — запусти её (значок в трее) и повтори.") from e
    if r.status_code == 404:
        raise RuntimeError(f"Модель «{model}» не скачана. В консоли: ollama pull {model}")
    r.raise_for_status()
    return (r.json().get("message", {}).get("content") or "").strip() or "Модель ничего не ответила."
