"""
Голосовой ввод — запись с микрофона и распознавание речи.

Почему раньше "не работало": запись шла через PyAudio. Для новых версий Python под Windows
у него нет готовой сборки, он требует компилятор (Visual C++ Build Tools) и в большинстве случаев
просто не устанавливается — а программа об этом молчала.

Теперь:
  * запись — через `sounddevice`: у него готовая сборка под Windows (PortAudio внутри), компилятор
    не нужен, работает с любой версией Python 3;
  * распознавание — сначала бесплатный Google (через SpeechRecognition), и если он недоступен —
    запасной путь через Gemini (ключи у тебя уже есть);
  * любая неудача объясняется человеческим языком; если не хватает компонента — программа
    предлагает поставить его сама (install_components);
  * запись останавливается сама, когда договорил (по тишине), уровень звука виден в окне.
"""
import array
import base64
import collections
import importlib
import io
import math
import subprocess
import sys
import time
import wave

import config
from logger import log
from privacy import redact

RATE = 16000                 # частота, на которой просим писать (остальное — подстроимся под устройство)
CHUNK_S = 0.1                # длина одного блока записи
MIN_THRESHOLD = 180.0        # минимальный порог "это речь" (шкала int16)
SILENCE_END_S = 1.0          # столько тишины после речи = договорил
PHRASE_LIMIT_S = 15.0        # максимум одной фразы
START_STREAK = 3             # столько громких блоков подряд = речь началась (0.3 с)
DEAD_PEAK = 30               # пик ниже этого за всё время = микрофон отдаёт "нули"

_sd = None


class VoiceError(Exception):
    """Причина, по которой не удалось получить команду голосом. Текст — для пользователя.
    fixable=True: проблему можно решить автоматической установкой компонента (install_components)."""

    def __init__(self, message: str, fixable: bool = False):
        super().__init__(message)
        self.fixable = fixable


# ---------- sounddevice ----------

def _load_sounddevice():
    """Модуль sounddevice или None. Неудача не кэшируется — после установки заработает без перезапуска."""
    global _sd
    if _sd is not None:
        return _sd
    try:
        import sounddevice as sd      # ImportError — не установлен; OSError — нет библиотеки PortAudio
    except Exception as e:
        log(f"sounddevice недоступен: {e}")
        return None
    _sd = sd
    return sd


def components_missing() -> list:
    """Чего не хватает для записи с микрофона (пусто — всё есть)."""
    return [] if _load_sounddevice() is not None else ["sounddevice"]


def _default_input_index(sd):
    try:
        idx = sd.default.device[0]
        if idx is not None and idx >= 0:
            return idx
        return sd.query_devices(kind="input")["index"]
    except Exception:
        return None


def list_input_devices() -> list:
    """Имена устройств ввода (без дублей по разным звуковым API Windows)."""
    sd = _load_sounddevice()
    if sd is None:
        return []
    try:
        devices = sd.query_devices()
        default_idx = _default_input_index(sd)
        hostapi = devices[default_idx]["hostapi"] if default_idx is not None else None
        names = []
        for d in devices:
            if d["max_input_channels"] > 0 and (hostapi is None or d["hostapi"] == hostapi):
                if d["name"] not in names:
                    names.append(d["name"])
        return names
    except Exception as e:
        log(f"Не удалось получить список микрофонов: {e}")
        return []


def _resolve_device(sd):
    """Индекс микрофона, выбранного в настройках (по имени), либо None = системный по умолчанию."""
    wanted = (config.get("voice_device") or "").strip()
    if not wanted:
        return None
    try:
        for i, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0 and d["name"] == wanted:
                return i
    except Exception:
        pass
    return None


def _open_stream(sd, device):
    rates = [RATE]
    try:
        info = sd.query_devices(device, "input") if device is not None else sd.query_devices(kind="input")
        native = int(info["default_samplerate"])
        if native not in rates:
            rates.append(native)
    except Exception:
        pass
    last_error = None
    for rate in rates:
        try:
            stream = sd.RawInputStream(samplerate=rate, channels=1, dtype="int16", device=device,
                                       blocksize=int(rate * CHUNK_S))
            stream.start()
            return stream, rate
        except Exception as e:
            last_error = e
            log(f"Не удалось открыть микрофон на {rate} Гц: {e}")
    raise VoiceError(
        f"Не удалось открыть микрофон ({last_error}). Возможно, он занят другой программой или доступ закрыт: "
        "Параметры Windows → Конфиденциальность и защита → Микрофон (включи и «Разрешить классическим приложениям»)."
    )


def _stats(chunk: bytes):
    """(среднеквадратичная громкость, пик) одного блока int16."""
    samples = array.array("h")
    samples.frombytes(chunk)
    if not samples:
        return 0.0, 0
    peak = max(max(samples), -min(samples))
    return math.sqrt(sum(s * s for s in samples) / len(samples)), peak


# ---------- запись ----------

def _record(timeout, on_state, on_level, cancel):
    """
    Ждёт начала речи (до timeout секунд), пишет, пока говорят, и останавливается по тишине.
    Возвращает (pcm_bytes, rate), None — если отменили. Бросает VoiceError.
    """
    sd = _load_sounddevice()
    if sd is None:
        raise VoiceError(
            "Для работы с микрофоном не хватает компонента (sounddevice). Это не ошибка в приложении — "
            "его просто нужно установить.", fixable=True)

    stream, rate = _open_stream(sd, _resolve_device(sd))
    frames = int(rate * CHUNK_S)

    def read():
        data, _overflow = stream.read(frames)
        chunk = bytes(data)
        rms, peak = _stats(chunk)
        on_level(min(1.0, rms / 3000.0))
        return chunk, rms, peak

    peak_all = 0
    try:
        noise = []
        for _ in range(4):                     # 0,4 с "тишины" — меряем фоновый шум комнаты
            _chunk, rms, peak = read()
            noise.append(rms)
            peak_all = max(peak_all, peak)
        threshold = max(MIN_THRESHOLD, 3.0 * sum(noise) / len(noise))

        on_state("Говори…")
        preroll = collections.deque(maxlen=START_STREAK + 2)   # захватываем и самое начало фразы
        voiced, started, streak, silence = [], False, 0, 0
        deadline = time.time() + timeout
        end_blocks = int(SILENCE_END_S / CHUNK_S)
        limit_blocks = int(PHRASE_LIMIT_S / CHUNK_S)

        while True:
            if cancel is not None and cancel.is_set():
                return None
            chunk, rms, peak = read()
            peak_all = max(peak_all, peak)
            if not started:
                preroll.append(chunk)
                streak = streak + 1 if rms > threshold else 0
                if streak >= START_STREAK:
                    started, voiced = True, list(preroll)
                    on_state("Слушаю…")
                elif time.time() > deadline:
                    break
            else:
                voiced.append(chunk)
                silence = 0 if rms > threshold * 0.7 else silence + 1
                if silence >= end_blocks or len(voiced) >= limit_blocks:
                    break
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass

    if not started:
        if peak_all < DEAD_PEAK:
            raise VoiceError(
                "Микрофон работает, но звука нет совсем — похоже, он выключен или Windows не даёт приложению "
                "доступ. Проверь: Параметры Windows → Конфиденциальность и защита → Микрофон "
                "(включи и «Разрешить классическим приложениям»), а также не выключен ли микрофон кнопкой/в звуке.")
        raise VoiceError("Не расслышал — не услышал речи. Нажми ещё раз и говори сразу, ближе к микрофону.")
    return b"".join(voiced), rate


# ---------- распознавание ----------

def _transcribe_gemini(pcm: bytes, rate: int, language: str) -> str:
    import ai_brain
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    prompt = (f"Дословно запиши речь из аудио (язык: {language}). Верни ТОЛЬКО текст сказанного — без кавычек, "
              "пояснений и пометок о времени. Если в записи нет разборчивой речи — верни пустую строку.")
    payload = {
        "contents": [{"role": "user", "parts": [
            {"text": prompt},
            {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(buf.getvalue()).decode("ascii")}},
        ]}],
        "generationConfig": {"temperature": 0, "maxOutputTokens": 512},
    }
    return (ai_brain.extract_text(ai_brain.generate(payload)) or "").strip().strip('"«»').strip()


def _transcribe(pcm: bytes, rate: int, language: str) -> str:
    problems, network = [], False

    sr = None
    try:
        import speech_recognition as sr
    except Exception as e:
        problems.append(f"SpeechRecognition недоступна ({e})")

    if sr is not None:                                   # 1) бесплатный Google — не тратит лимиты Gemini
        try:
            text = sr.Recognizer().recognize_google(sr.AudioData(pcm, rate, 2), language=language)
            if text.strip():
                return text.strip()
        except sr.UnknownValueError:
            problems.append("Google не разобрал речь")
        except sr.RequestError as e:
            network = True
            problems.append(f"Google недоступен: {e}")
        except Exception as e:
            problems.append(f"ошибка распознавания Google: {e}")

    try:                                                 # 2) запасной путь — Gemini
        text = _transcribe_gemini(pcm, rate, language)
        if text:
            return text
        problems.append("Gemini не разобрал речь")
    except (ValueError, RuntimeError) as e:
        network = network or "интернет" in str(e).lower() or "недоступн" in str(e).lower()
        problems.append(str(e))
    except Exception as e:
        problems.append(f"ошибка распознавания Gemini: {e}")

    log("Распознавание не удалось: " + "; ".join(problems))
    if network:
        raise VoiceError("Не удалось связаться с сервисами распознавания речи — проверь интернет "
                         "и ключи Gemini в Настройках.")
    raise VoiceError("Речь записалась, но разобрать её не удалось. Попробуй сказать чётче и ближе к микрофону.")


def listen_command(timeout: int = 6, language=None, on_state=None, on_level=None, cancel=None) -> str:
    """
    Слушает микрофон и возвращает распознанный текст ("" — если отменили).
    on_state(текст) / on_level(0..1) — для окна «Слушаю…»; cancel — threading.Event отмены.
    Бросает VoiceError с понятным текстом.
    """
    language = language or config.get("voice_language") or "ru-RU"
    on_state = on_state or (lambda s: None)
    on_level = on_level or (lambda v: None)

    on_state("Подключаю микрофон…")
    recorded = _record(timeout, on_state, on_level, cancel)
    if recorded is None:
        return ""
    pcm, rate = recorded
    on_state("Распознаю…")
    text = _transcribe(pcm, rate, language)
    log(f"Распознана голосовая команда: {redact(text)}")      # в лог — только замаскированная
    on_state(f"Распознано: {text}")
    return text


# ---------- проверки для интерфейса ----------

def check_microphone():
    """(ok, сообщение): есть ли компонент и видно ли устройство ввода."""
    if _load_sounddevice() is None:
        return False, "Не хватает компонента для микрофона — нажми «Починить»"
    names = list_input_devices()
    if not names:
        return False, "Микрофон не найден — подключи его и проверь доступ в параметрах Windows"
    default = names[0]
    try:
        sd = _load_sounddevice()
        idx = _default_input_index(sd)
        if idx is not None:
            default = sd.query_devices(idx)["name"]
    except Exception:
        pass
    return True, f"Микрофон: {default}"


def test_recording(seconds: float = 2.0):
    """Живая проверка: пишет пару секунд и говорит, слышно ли звук. Возвращает (ok, сообщение)."""
    sd = _load_sounddevice()
    if sd is None:
        return False, "Не хватает компонента для микрофона — нажми «Починить»"
    try:
        stream, rate = _open_stream(sd, _resolve_device(sd))
    except VoiceError as e:
        return False, str(e)
    frames, loudest, peak_all = int(rate * CHUNK_S), 0.0, 0
    try:
        for _ in range(int(seconds / CHUNK_S)):
            data, _ = stream.read(frames)
            rms, peak = _stats(bytes(data))
            loudest, peak_all = max(loudest, rms), max(peak_all, peak)
    except Exception as e:
        return False, f"Ошибка при записи: {e}"
    finally:
        try:
            stream.stop()
            stream.close()
        except Exception:
            pass
    if peak_all < DEAD_PEAK:
        return False, ("Звука нет совсем — микрофон выключен или Windows не даёт доступ "
                       "(Параметры → Конфиденциальность → Микрофон)")
    if loudest < MIN_THRESHOLD:
        return True, "Микрофон работает, но звук очень тихий — говори ближе или прибавь громкость микрофона в Windows"
    return True, f"Слышу тебя ✓ (уровень {min(100, int(loudest / 30))}%)"


def install_components():
    """
    Ставит недостающее для голоса прямо из приложения (pip, без компилятора). Возвращает (ok, сообщение).
    """
    if getattr(sys, "frozen", False):
        return False, "В собранном .exe компоненты встроены; если голос не работает — пересоберите .exe (build_exe.bat)."
    cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "sounddevice", "SpeechRecognition"]
    flags = 0x08000000 if sys.platform == "win32" else 0        # не показывать чёрное окно
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=420, creationflags=flags)
    except subprocess.TimeoutExpired:
        return False, "Установка заняла слишком долго — проверь интернет и попробуй ещё раз."
    except Exception as e:
        return False, f"Не удалось запустить установку: {e}"
    importlib.invalidate_caches()
    if proc.returncode != 0:
        tail = " ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-2:])
        return False, f"pip не смог установить компоненты: {tail[:240]}"
    if _load_sounddevice() is None:
        return False, "Компонент установился, но не запускается — перезапусти приложение (выйди и открой заново)."
    return True, "Готово! Голосовой ввод установлен — можно говорить."
