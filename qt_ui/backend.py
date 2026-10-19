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

    def needs_consent(self, engine, paths):
        return bool(paths) and engine == "gemini" and not getattr(self, "_consent", False)

    def remember_consent(self):
        self._consent = True

    def consent_given(self):
        return getattr(self, "_consent", False)

    def revoke_consent(self):
        self._consent = False

    def ask(self, text: str, mode: str, backend: str, on_done, attachments=()):
        """Ответ приходит позже (как у настоящей модели)."""
        if attachments:
            text = f"{text} (вложений: {len(attachments)})"
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

    # --- вложения ---
    def needs_consent(self, engine, paths) -> bool:
        """Файлы/фото уходят в облако только с согласием. Локальная модель читает текстовые файлы без отправки."""
        import config
        if not paths or engine != "gemini" or config.get("cloud_files_consent"):
            return False
        return True

    def remember_consent(self):
        import config
        config.save({"cloud_files_consent": True})

    def consent_given(self) -> bool:
        import config
        return bool(config.get("cloud_files_consent"))

    def revoke_consent(self):
        import config
        config.save({"cloud_files_consent": False})

    def ask(self, text, mode, engine, on_done, attachments=()):
        meta_engine = " · Gemini" if engine == "gemini" else " · локально"
        if attachments:
            self._ask_with_files(text, engine, list(attachments), on_done, meta_engine)
            return
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

    def _ask_with_files(self, text, engine, paths, on_done, meta_engine):
        import threading
        import files_io
        import media_input
        turns = self.turns.setdefault(engine, [])

        def work():
            notes, blocks, images, hidden = [], [], [], 0
            question = text.strip()
            try:
                for p in paths:
                    info = files_io.describe(p)
                    if not info["ok"]:
                        notes.append(f"«{info['name']}»: {info['reason']}")
                        continue
                    if info["kind"] == "image":
                        if engine != "gemini":
                            notes.append(f"«{info['name']}»: локальная модель не видит картинки — переключи на Gemini.")
                        else:
                            images.append((media_input.read_file(p), media_input.mime_of(p)))
                    elif info["kind"] == "audio":
                        if engine != "gemini":
                            notes.append(f"«{info['name']}»: аудио распознаёт только Gemini.")
                            continue
                        said = media_input.transcribe(media_input.read_file(p), media_input.mime_of(p))
                        blocks.append(f"Расшифровка аудио «{info['name']}»: {said}" if said else "")
                    else:
                        try:
                            block, n, _cut = files_io.file_block(p)
                            blocks.append(block)
                            hidden += n
                        except RuntimeError as e:
                            notes.append(f"«{info['name']}»: {e}")
                if not blocks and not images:
                    self.post(lambda: on_done("\n".join(notes) or "Нечего отправлять.", ""))
                    return
                if images:
                    q = question or "Опиши, что на картинке."
                    if blocks:
                        q = "\n\n".join(b for b in blocks if b) + "\n\n" + q
                    answer = media_input.ask_images(images, q)
                    turns.append({"role": "user", "text": (question or "Картинка") + f" [картинок: {len(images)}]"})
                    turns.append({"role": "assistant", "text": answer})
                else:
                    ask_text = "\n\n".join(b for b in blocks if b) + "\n\n" + (question or "Кратко перескажи, что в файле.")
                    turns.append({"role": "user", "text": ask_text})
                    box = {}
                    done = threading.Event()

                    def got(answer, ok=True):
                        box["a"] = answer
                        done.set()
                    self.controller.chat_ask([dict(t) for t in turns], got)
                    done.wait(180)
                    answer = box.get("a") or "Ответ не получен."
                    turns.append({"role": "assistant", "text": answer})
                tail = ""
                if hidden:
                    tail += f"\n\n🔒 Перед отправкой скрыто секретов: {hidden}."
                if notes:
                    tail += "\n\n" + "\n".join(notes)
                self.post(lambda: on_done(answer + tail, time.strftime("%H:%M") + meta_engine))
            except Exception as e:
                self.post(lambda: on_done(f"Не получилось обработать файл: {e}", ""))
        threading.Thread(target=work, daemon=True).start()

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
