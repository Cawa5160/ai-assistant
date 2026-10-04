"""
Офлайн голосовой отклик — озвучивает, что ассистент делает.
Использует pyttsx3 (голоса, уже установленные в Windows), поэтому не
требует интернета и ничего не стоит. Выключено по умолчанию —
включается галочкой в Настройках.
"""
import threading

import config
from logger import log

_lock = threading.Lock()


def speak(text: str):
    """Озвучивает текст в отдельном потоке, если голосовой отклик включён в настройках."""
    if not config.get("voice_feedback_enabled") or not text:
        return

    def _run():
        with _lock:
            try:
                import pyttsx3
                engine = pyttsx3.init()
                engine.say(text)
                engine.runAndWait()
                engine.stop()
            except Exception as e:
                log(f"Голосовой отклик не сработал: {e}")

    threading.Thread(target=_run, daemon=True).start()
