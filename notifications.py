"""
Показывает нативное всплывающее уведомление Windows (Центр уведомлений)
вместо отдельного окна tkinter — не перехватывает фокус и не требует
клика "OK". Если библиотека недоступна (не Windows, пакет не установлен
и т.п.) — сигнализирует об этом, чтобы вызывающий код показал обычное окно.
"""
from logger import log


def notify(title: str, message: str) -> bool:
    """Пробует показать нативный toast. True при успехе, False — если не вышло."""
    try:
        from winotify import Notification
        toast = Notification(app_id="AI Ассистент", title=title, msg=message, duration="short")
        toast.show()
        return True
    except Exception as e:
        log(f"Нативное уведомление недоступно ({e}), покажу обычное окно.")
        return False
