"""
Обновления через GitHub Releases: проверка, скачивание установщика и его запуск (только по нажатию кнопки в Настройках).
Релизы с AI_Assistant_Setup.exe собирает GitHub Actions при теге v* (см. .github/workflows/build.yml).
Для приватного репозитория нужен токен GitHub (доступ на чтение), он хранится в защищённом хранилище.
"""
import os
import re
import tempfile

import requests

import config
import secure_store

REPO = "Cawa5160/ai-assistant"
API = f"https://api.github.com/repos/{REPO}"


def _ver(text: str):
    nums = re.findall(r"\d+", text or "")[:3]
    return tuple(int(n) for n in nums) + (0,) * (3 - len(nums))


def _headers(accept="application/vnd.github+json") -> dict:
    h = {"Accept": accept, "X-GitHub-Api-Version": "2022-11-28"}
    token = secure_store.get_secret("github_token") or config.get("github_token_plain") or ""
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def set_token(token: str):
    token = (token or "").strip()
    if secure_store.set_secret("github_token", token):
        config.save({"github_token_plain": ""})
    else:
        config.save({"github_token_plain": token})


def has_token() -> bool:
    return bool(secure_store.get_secret("github_token") or config.get("github_token_plain"))


def latest(timeout: float = 8.0):
    """Последний релиз: {'tag', 'url', 'assets': [{'name', 'size', 'api_url'}]}; None — релизов нет. Бросает исключение при сбое сети."""
    r = requests.get(f"{API}/releases/latest", timeout=timeout, headers=_headers())
    if r.status_code == 404:
        return None
    r.raise_for_status()
    d = r.json()
    return {"tag": d.get("tag_name", ""), "url": d.get("html_url", ""),
            "assets": [{"name": a.get("name", ""), "size": a.get("size", 0), "api_url": a.get("url", "")}
                       for a in d.get("assets", [])]}


def check(timeout: float = 6.0):
    """-> (state, text, url): state = 'new' | 'current' | 'unknown'."""
    try:
        rel = latest(timeout)
        if rel is None:
            return "unknown", "релизов на GitHub пока нет (или репозиторий приватный — нужен токен в Настройках)", ""
        if _ver(rel["tag"]) > _ver(config.APP_VERSION):
            return "new", f"доступна версия {rel['tag']} (у тебя {config.APP_VERSION})", rel["url"]
        return "current", f"установлена последняя версия ({config.APP_VERSION})", rel["url"]
    except Exception as e:
        return "unknown", f"не удалось проверить ({type(e).__name__})", ""


def pick_asset(rel: dict):
    """Установщик из релиза: AI_Assistant_Setup.exe, иначе .bat."""
    assets = rel.get("assets", [])
    for ext in ("_setup.exe", ".bat"):
        for a in assets:
            if a["name"].lower().endswith(ext):
                return a
    return None


def download(rel: dict, on_progress=None) -> str:
    """Скачивает установщик из релиза во временную папку, возвращает путь. Бросает RuntimeError с понятным текстом."""
    asset = pick_asset(rel)
    if not asset:
        raise RuntimeError("В релизе нет установщика (AI_Assistant_Setup.exe).")
    folder = os.path.join(tempfile.gettempdir(), "AI_Assistant_update")
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, re.sub(r"[^\w.\-]", "_", asset["name"]))
    try:
        with requests.get(asset["api_url"], headers=_headers("application/octet-stream"), stream=True, timeout=30) as r:
            r.raise_for_status()
            done = 0
            with open(path, "wb") as f:
                for chunk in r.iter_content(256 * 1024):
                    f.write(chunk)
                    done += len(chunk)
                    if on_progress and asset["size"]:
                        on_progress(min(1.0, done / asset["size"]))
    except requests.RequestException as e:
        raise RuntimeError(f"Не удалось скачать обновление: {type(e).__name__}")
    if asset["size"] and os.path.getsize(path) != asset["size"]:
        os.remove(path)
        raise RuntimeError("Файл скачался не полностью — попробуй ещё раз.")
    return path


def start_installer(path: str):
    """Запускает скачанный установщик (Windows)."""
    os.startfile(path)            # noqa: S606 — путь только что скачан из нашего релиза
