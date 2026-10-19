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

    has_classic = False

    def set_engine(self, name):
        pass

    def history_of(self, engine):
        return []

    def open_classic(self, page):
        pass

    def voice(self):
        pass


class RealBackend:
    """Связь с настоящей программой: Controller из main.py (команды, чат), history, config."""

    def __init__(self, controller, post, open_classic):
        import config
        self.controller = controller
        self.post = post                      # выполнить функцию в потоке Qt
        self.open_classic = open_classic
        self.has_classic = True
        self.version = config.APP_VERSION
        self.turns = {"gemini": [], "ollama": []}
        self.engine = config.get("chat_backend") or "gemini"

    # --- главная ---
    def status(self) -> dict:
        import config
        keys = len(config.get("gemini_api_keys") or [])
        today = time.strftime("%Y-%m-%d")
        done = sum(1 for e in self._history() if str(e.get("time", "")).startswith(today))
        if not keys:
            return {"ready": False, "text": "Нужен ключ Gemini", "detail": "Добавь его в Настройках",
                    "done_today": done, "minutes_today": 0}
        return {"ready": True, "text": "Готов к работе", "detail": f"Gemini · ключей: {keys}",
                "done_today": done, "minutes_today": 0}

    @staticmethod
    def _history():
        import history
        try:
            return history.get_all()
        except Exception:
            return []

    def recent(self, n=3) -> list:
        out = []
        for e in self._history()[:n]:
            out.append((e.get("command") or "—", e.get("status") or "", str(e.get("time", ""))[11:16]))
        return out

    # --- чат ---
    def set_engine(self, name):
        import config
        self.engine = name
        config.save({"chat_backend": name})

    def history_of(self, engine):
        return list(self.turns.get(engine, []))

    def ask(self, text, mode, engine, on_done):
        meta_engine = " · Gemini" if engine == "gemini" else " · локально"
        if mode == "run":
            self._run(text, lambda answer: self.post(lambda: on_done(answer, time.strftime("%H:%M"))))
            return
        turns = self.turns.setdefault(engine, [])
        turns.append({"role": "user", "text": text})
        asked = [dict(t) for t in turns]

        def got(answer, ok=True):
            turns.append({"role": "assistant", "text": answer})
            self.post(lambda: on_done(answer, time.strftime("%H:%M") + meta_engine))
        self.controller.chat_ask(asked, got)

    def _run(self, text, finish):
        import threading
        baseline = self._key()
        self.controller.submit_command(text)

        def poll():
            for _ in range(150):
                time.sleep(2)
                items = self._history()
                if items and self._key() != baseline:
                    e = items[0]
                    msg = e.get("status", "") or "готово"
                    if e.get("summary"):
                        msg += "\n" + e["summary"]
                    finish(msg)
                    return
            finish("Результат не появился в истории. Посмотри вкладку «История».")
        threading.Thread(target=poll, daemon=True).start()

    def _key(self):
        items = self._history()
        return (items[0].get("time"), items[0].get("command")) if items else None

    def run_command(self, text):
        self.controller.submit_command(text)

    def voice(self):
        self.controller.voice_command()
