"""
Проверка обновлений: спрашивает у GitHub, есть ли версия новее. Ничего не скачивает и не
устанавливает сама — только сообщает, где взять (так безопаснее).
"""
import re
import requests

import config

REPO = "Cawa5160/ai-assistant"


def _ver(text: str):
    nums = re.findall(r"\d+", text or "")[:3]
    return tuple(int(n) for n in nums) + (0,) * (3 - len(nums))


def check(timeout: float = 6.0):
    """-> (state, text, url): state = 'new' | 'current' | 'unknown'."""
    try:
        r = requests.get(f"https://api.github.com/repos/{REPO}/releases/latest", timeout=timeout,
                         headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 404:
            return "unknown", "релизов на GitHub пока нет", ""
        r.raise_for_status()
        data = r.json()
        tag = data.get("tag_name", "")
        url = data.get("html_url", "")
        if _ver(tag) > _ver(config.APP_VERSION):
            return "new", f"доступна версия {tag} (у тебя {config.APP_VERSION})", url
        return "current", f"установлена последняя версия ({config.APP_VERSION})", url
    except Exception as e:
        return "unknown", f"не удалось проверить ({type(e).__name__})", ""
