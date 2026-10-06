"""
«Эй, ассистент» — запуск голосом без кнопки (выключено по умолчанию, включается в Настройки → Голос).

ВАЖНО про приватность: пока функция включена, микрофон слушает постоянно. Звук НЕ записывается и не хранится,
пока ты не заговоришь; каждая услышанная фраза (до 5 секунд) уходит на бесплатное распознавание Google, чтобы понять,
есть ли в ней слово «ассистент». Если слова нет — фраза выбрасывается. Gemini для этого не используется (не тратит лимиты).
Фразы вида «эй ассистент, открой блокнот» выполняются сразу; просто «эй ассистент» — включает обычное окно прослушивания.
"""
import re
import threading
import time

import config
import voice_input as vi
from logger import log

WAKE_RE = re.compile(r"(?:^|\W)(?:(?:эй|ей|хей|привет|слушай|окей|ok|hey|hi)\s+)?"
                     r"(?P<w>а?сс?истент|ассистэнт|assistant)\b[\s,.:!\-]*(?P<rest>.*)$", re.I | re.S)
PHRASE_MAX_S = 5.0
END_SILENCE_S = 0.7

_state = {"running": False, "error": ""}
_stop = threading.Event()
_thread = None


def match_wake(text: str):
    """-> (услышал ли ключевое слово, команда после него или '')."""
    m = WAKE_RE.search(text or "")
    if not m:
        return False, ""
    return True, (m.group("rest") or "").strip()


def _google(pcm: bytes, rate: int, language: str) -> str:
    import speech_recognition as sr
    try:
        return sr.Recognizer().recognize_google(sr.AudioData(pcm, rate, 2), language=language).strip()
    except sr.UnknownValueError:
        return ""


def _listen(stop, busy, on_wake):
    try:
        import speech_recognition  # noqa: F401
    except Exception:
        _state["error"] = "нет компонента SpeechRecognition (Настройки → Голос → Установить)"
        log("Слово-активатор: " + _state["error"])
        _state["running"] = False
        return
    sd = vi._load_sounddevice()
    if sd is None:
        _state["error"] = "нет компонента sounddevice"
        _state["running"] = False
        return
    while not stop.is_set():
        if busy():
            stop.wait(1.5)
            continue
        try:
            stream, rate = vi._open_stream(sd, vi._resolve_device(sd))
        except Exception as e:
            _state["error"] = str(e)[:150]
            stop.wait(30)
            continue
        _state["error"] = ""
        frames = int(rate * vi.CHUNK_S)
        ambient, voiced, started, streak, silence = 150.0, [], False, 0, 0
        pre = []
        woke = None
        try:
            while not stop.is_set() and not busy():
                data, _ = stream.read(frames)
                chunk = bytes(data)
                rms, _peak = vi._stats(chunk)
                thr = max(vi.MIN_THRESHOLD * 1.3, 3.0 * ambient)
                if not started:
                    ambient = 0.98 * ambient + 0.02 * rms
                    pre = (pre + [chunk])[-(vi.START_STREAK + 2):]
                    streak = streak + 1 if rms > thr else 0
                    if streak >= vi.START_STREAK:
                        started, voiced, silence = True, list(pre), 0
                else:
                    voiced.append(chunk)
                    silence = 0 if rms > thr * 0.7 else silence + 1
                    if silence >= int(END_SILENCE_S / vi.CHUNK_S) or len(voiced) >= int(PHRASE_MAX_S / vi.CHUNK_S):
                        pcm = b"".join(voiced)
                        started, voiced, streak = False, [], 0
                        try:
                            text = _google(pcm, rate, config.get("voice_language") or "ru-RU")
                        except Exception as e:
                            _state["error"] = f"распознавание недоступно: {str(e)[:80]}"
                            continue
                        ok, rest = match_wake(text)
                        if ok:
                            woke = rest
                            break
        except Exception as e:
            _state["error"] = f"микрофон: {str(e)[:100]}"
            stop.wait(5)
        finally:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        if woke is not None and not stop.is_set():
            log("Слово-активатор услышано.")
            try:
                on_wake(woke)
            except Exception as e:
                log(f"Слово-активатор: ошибка обработки: {e}")
            stop.wait(1.0)
    _state["running"] = False


def start(on_wake, busy=lambda: False):
    """Запускает прослушивание, если включено в настройках."""
    global _thread, _stop
    if not config.get("voice_wake_enabled"):
        return False
    if _thread and _thread.is_alive() and not _stop.is_set():
        return True
    _stop = threading.Event()
    _state["running"] = True
    _thread = threading.Thread(target=_listen, args=(_stop, busy, on_wake), daemon=True, name="wakeword")
    _thread.start()
    log("Слово-активатор «Эй, ассистент» включено.")
    return True


def stop():
    _stop.set()


def restart(on_wake, busy=lambda: False):
    stop()
    return start(on_wake, busy)
