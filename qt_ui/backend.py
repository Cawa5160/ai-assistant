"""Что нужно интерфейсу от программы. StubBackend — заглушка для макетов и тестов; RealBackend (следующий этап) подключит Controller."""
import time

from PySide6.QtCore import QTimer


class StubBackend:
    version = "2.0.0"

    def status(self) -> dict:
        return {"ready": True, "text": "Готов к работе", "detail": "Gemini · 5 ключей · лимитов нет",
                "gemini": "активен", "local_model": "qwen3:4b", "done_today": 12, "minutes_today": 38}

    def recent(self, n=3) -> list:
        return [("Создать папку и файл", "готово", "14:02"), ("Поставь таймер на 10 минут", "готово", "14:03"),
                ("Открой ютуб и найди музыку", "готово", "13:41")][:n]

    def ask(self, text: str, mode: str, backend: str, on_done):
        """Ответ приходит позже (как у настоящей модели)."""
        QTimer.singleShot(900, lambda: on_done("Готово ✓ Выполнено: " + text if mode == "run" else
                                              "Это пример ответа ассистента на вопрос: «" + text + "»",
                                              time.strftime("%H:%M") + (" · Gemini" if backend == "gemini" else " · локально")))

    def run_command(self, text: str):
        pass
