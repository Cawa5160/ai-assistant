"""Методы бэкенда для страницы «Настройки» (qt_ui/settings_page.py).

SettingsBackend — миксин: добавить к RealBackend (`class RealBackend(SettingsBackend, MoreBackend)`) или смешать на лету.
Требует у self: `post(fn)` (выполнить в потоке Qt) и `controller` (main.Controller; нужен `save_settings(values)`).
StubSettings — те же методы с тестовыми данными (для макетов/тестов); `StubSettings.post` вызывает сразу.
Долгие вещи (проверка ключей, микрофон, оборудование, реестр Ollama, Telegram) идут в потоке,
результат приходит в on_done(...) через `self.post(...)`, то есть в потоке Qt.
"""
import re
import threading

KEY_SPLIT = re.compile(r"[\s,;]+")
KEY_LIKE = re.compile(r"[A-Za-z0-9_.\-]{20,}")
SKIP_MODELS = ("image", "tts", "live", "audio", "embedding", "robotics", "computer-use", "native")
LANGS = {"ru": "Русский", "uk": "Українська", "en": "English"}
VOICE_LANGS = [("ru-RU", "Русский"), ("uk-UA", "Українська"), ("en-US", "English")]


def split_keys(text: str) -> list:
    """Ключи из текста: по одному на строку или через пробел/запятую; повторы убираются."""
    return list(dict.fromkeys(p for p in KEY_SPLIT.split(text or "") if p))


def keys_from_clipboard_text(text: str) -> list:
    """То, что в буфере похоже на ключи Gemini (AIza… и новые AQ.…)."""
    return [p for p in KEY_SPLIT.split((text or "").strip()) if KEY_LIKE.fullmatch(p)]


def filter_models(names) -> list:
    """Подходящие для автопилота модели flash (как в Tk-версии), новые первыми, не больше 10."""
    return sorted([n for n in names if "flash" in n and not any(x in n for x in SKIP_MODELS)], reverse=True)[:10]


def _int(text, default, lo, hi):
    try:
        v = int(str(text).strip() or default)
    except ValueError:
        v = default
    return max(lo, min(hi, v))


class SettingsBackend:
    def _bg(self, work, done):
        """work() -> результат в потоке; done(результат) — в потоке Qt."""
        def run():
            try:
                res = work()
            except Exception as e:                       # поток не должен падать молча
                res = e
            self.post(lambda: done(res))
        threading.Thread(target=run, daemon=True).start()

    # ---------- чтение / запись ----------
    def settings_load(self) -> dict:
        """Текущие значения для формы. Ключи — как в config.py, плюс служебные:
        secure_store (bool: защищённое хранилище Windows доступно), telegram_has_token (bool),
        telegram_status (str), autostart (bool из реестра), mask_password_fields (bool, по умолчанию True)."""
        import autostart
        import config
        import secure_store
        import telegram_bot
        cur = dict(config.load())
        cur["autostart"] = bool(autostart.is_enabled())
        cur["secure_store"] = bool(secure_store.is_available())
        cur["telegram_has_token"] = bool(telegram_bot.get_token())
        cur["telegram_status"] = telegram_bot.status()
        mask = config.get("mask_password_fields")
        cur["mask_password_fields"] = True if mask is None else bool(mask)
        cur["gemini_api_keys"] = list(cur.get("gemini_api_keys") or [])
        import openrouter
        cur["openrouter_has_key"] = bool(openrouter.get_key())
        cur.pop("openrouter_key_plain", None)
        return cur

    def settings_save(self, values: dict) -> str:
        """Сохраняет (через controller.save_settings = main.apply_settings). Возвращает "" или текст ошибки.

        values — плоский словарь с ключами config.py; страница передаёт все поля:
        gemini_api_keys, model, requests_per_minute, use_fallback_models, fallback_models, view_only,
        auto_confirm_seconds, hotkey_open_window, hotkey_voice, hotkey_stop, allowed_folders,
        voice_feedback_enabled, voice_wake_enabled, voice_language, voice_device,
        local_model_enabled, local_model_name, telegram_enabled, telegram_approve_in_chat,
        telegram_trusted_auto, telegram_screen_after, telegram_announce, telegram_token ("" = не менять),
        telegram_clear_token, autostart, ui_theme (dark|light), ui_language,
        cloud_files_consent, mask_password_fields.
        Чего у apply_settings нет (ui_theme, cloud_files_consent, mask_password_fields) —
        пишется напрямую в config.save. Тема Tk («theme») и акцент берутся из ui_theme / текущего конфига."""
        import config
        import i18n
        v = dict(values)
        old_lang = config.get("ui_language")
        theme_name = v.get("ui_theme") if v.get("ui_theme") in ("dark", "light") else (config.get("ui_theme") or "dark")
        full = {
            "gemini_api_keys": list(v.get("gemini_api_keys") or []),
            "model": (v.get("model") or "").strip() or "gemini-3.5-flash",
            "theme": theme_name,                                    # Tk-тема следует за новой (light/dark)
            "accent": config.get("accent") or "indigo",             # акцент в Qt-странице не настраивается
            "ui_language": v.get("ui_language") or "ru",
            "view_only": bool(v.get("view_only")),
            "use_fallback_models": bool(v.get("use_fallback_models", True)),
            "fallback_models": [m for m in re.split(r"[,;\s]+", ",".join(
                [v["fallback_models"]] if isinstance(v.get("fallback_models"), str) else (v.get("fallback_models") or []))
            ) if m][:4],
            "auto_confirm_seconds": _int(v.get("auto_confirm_seconds"), 5, 0, 30),
            "requests_per_minute": _int(v.get("requests_per_minute"), 6, 0, 120),
            "hotkey_open_window": (v.get("hotkey_open_window") or "").strip() or "ctrl+shift+a",
            "hotkey_voice": (v.get("hotkey_voice") or "").strip(),
            "hotkey_stop": (v.get("hotkey_stop") or "").strip(),
            "allowed_folders": [p.strip() for p in (v.get("allowed_folders") or []) if p and p.strip()],
            "voice_feedback_enabled": bool(v.get("voice_feedback_enabled")),
            "voice_wake_enabled": bool(v.get("voice_wake_enabled")),
            "voice_language": v.get("voice_language") or "ru-RU",
            "voice_device": v.get("voice_device") or "",
            "local_model_enabled": bool(v.get("local_model_enabled", True)),
            "local_model_name": (v.get("local_model_name") or "").strip() or "qwen2.5:3b",
            "telegram_enabled": bool(v.get("telegram_enabled")),
            "telegram_approve_in_chat": bool(v.get("telegram_approve_in_chat", True)),
            "telegram_trusted_auto": bool(v.get("telegram_trusted_auto", True)),
            "telegram_screen_after": bool(v.get("telegram_screen_after", True)),
            "telegram_announce": bool(v.get("telegram_announce", True)),
            "telegram_token": (v.get("telegram_token") or "").strip(),
            "telegram_clear_token": bool(v.get("telegram_clear_token")),
            "autostart": bool(v.get("autostart")),
        }
        try:
            self.controller.save_settings(full)
            extra = {"ui_theme": theme_name, 
                     "cloud_files_consent": bool(v.get("cloud_files_consent")),
                     "mask_password_fields": bool(v.get("mask_password_fields", True))}
            config.save(extra)
            import openrouter
            config.save({"openrouter_enabled": bool(v.get("openrouter_enabled")),
                         "openrouter_model": (v.get("openrouter_model") or "").strip()})
            if v.get("openrouter_clear_key"):
                openrouter.set_key("")
            elif (v.get("openrouter_key") or "").strip():
                openrouter.set_key(v["openrouter_key"])
        except Exception as e:
            return f"Не удалось сохранить: {e}"
        if full["ui_language"] != old_lang:
            i18n.set_language(full["ui_language"])      # окно, чтобы перевестись, нужно перестроить (решает MainWindow)
        return ""

    # ---------- Gemini ----------
    def settings_keys_test(self, keys, on_done):
        """on_done([(ok, ...последние 6 символов, сообщение)]) — по ключам по порядку."""
        def work():
            from ai_brain import test_api_key
            out = []
            for k in keys:
                ok, msg = test_api_key(k)
                out.append((ok, "..." + (k[-6:] if len(k) >= 6 else k), msg))
            return out
        self._bg(work, lambda r: on_done([(False, "", str(r))] if isinstance(r, Exception) else r))

    def update_check(self, on_done):
        """on_done(state, text, url): state = new | current | unknown."""
        import updater
        self._bg(updater.check, lambda r: on_done(*r) if isinstance(r, tuple) else on_done("unknown", str(r), ""))

    def update_install(self, on_progress, on_done):
        """Скачивает установщик из последнего релиза и запускает его; программа закрывается. on_done(ok, текст)."""
        import updater

        def work():
            rel = updater.latest()
            if not rel:
                raise RuntimeError("Релизов нет.")
            path = updater.download(rel, lambda p: self.post(lambda: on_progress(p)))
            updater.start_installer(path)
            return "Установщик запущен — программа закроется."

        def done(r):
            if isinstance(r, Exception):
                on_done(False, str(r))
            else:
                on_done(True, r)
                self.controller.quit()
        self._bg(work, done)

    def update_set_token(self, token):
        import updater
        updater.set_token(token)

    def update_has_token(self) -> bool:
        import updater
        return updater.has_token()

    def settings_or_models(self, key, on_done):
        """on_done(ok, [(id, бесплатная, с картинками)] | текст ошибки). Пустой key — взять сохранённый."""
        import openrouter
        self._bg(lambda: openrouter.list_models(key or openrouter.get_key()), lambda r: on_done(*r))

    def settings_keys_rotation(self) -> list:
        """[(замаскированный ключ, статус)] из key_manager."""
        import key_manager
        return [(r["masked"], r["status"]) for r in key_manager.status_report()]

    def settings_find_models(self, key, on_done):
        """on_done(ok, [имена моделей] | текст ошибки)."""
        def work():
            from ai_brain import list_models
            ok, res = list_models(key)
            return ok, (filter_models(res) if ok else res)
        self._bg(work, lambda r: on_done(False, str(r)) if isinstance(r, Exception) else on_done(*r))

    # ---------- голос ----------
    def settings_mic_devices(self, on_done):
        """on_done([имена микрофонов])."""
        def work():
            import voice_input
            return voice_input.list_input_devices()[:8]
        self._bg(work, lambda r: on_done([] if isinstance(r, Exception) else r))

    def settings_mic_test(self, on_done):
        """on_done(ok, текст)."""
        def work():
            import voice_input
            ok, msg = voice_input.check_microphone()
            if ok:
                ok, level = voice_input.test_recording(2.0)
                msg = f"{msg}\n{level}"
            return ok, msg
        self._bg(work, lambda r: on_done(False, str(r)) if isinstance(r, Exception) else on_done(*r))

    def settings_voice_fix(self, on_done):
        """Ставит sounddevice/SpeechRecognition (pip, до ~7 минут). on_done(ok, текст)."""
        def work():
            import voice_input
            return voice_input.install_components()
        self._bg(work, lambda r: on_done(False, str(r)) if isinstance(r, Exception) else on_done(*r))

    # ---------- локальные модели ----------
    def settings_hw_scan(self, on_done):
        """on_done(hw, ollama_running, installed) — hw как в hardware.detect(), installed [{'name','size_gb'}]."""
        def work():
            import hardware
            import local_llm
            hw = hardware.detect()
            running = local_llm.is_running()
            return hw, running, (local_llm.installed_models() if running else [])
        self._bg(work, lambda r: on_done({}, False, []) if isinstance(r, Exception) else on_done(*r))

    def settings_lm_find(self, use_gemini, hw, on_done):
        """Подбор моделей по железу. on_done(rows, offline, extra_error); rows — [{'name','size_gb','fit','note'}],
        fit: gpu|cpu|no|?; offline=True — реестр Ollama недоступен (показан встроенный список без размеров)."""
        def work():
            from concurrent.futures import ThreadPoolExecutor
            import hardware
            import local_llm
            h = hw or hardware.detect()
            notes = dict(local_llm.CANDIDATES)
            names = [n for n, _ in local_llm.CANDIDATES]
            extra = ""
            if use_gemini:
                try:
                    for n in local_llm.suggest_with_gemini(h):
                        if n not in names:
                            names.append(n)
                            notes[n] = "предложено Gemini, проверено в реестре"
                except Exception as e:
                    extra = f" (Gemini не ответил: {e})"
            with ThreadPoolExecutor(max_workers=6) as pool:
                found = list(pool.map(local_llm.registry_size_gb, names))
            sizes = {n: sz for n, sz in zip(names, found) if sz}
            offline = not sizes
            if offline:
                sizes = {n: None for n in names[:len(local_llm.CANDIDATES)]}
            return local_llm.recommend(h, sizes, notes), offline, extra
        self._bg(work, lambda r: on_done([], True, f" ({r})") if isinstance(r, Exception) else on_done(*r))

    # ---------- запуск ----------
    def settings_make_shortcuts(self, on_done):
        """Ярлыки на рабочем столе и в меню Пуск. on_done(ok, текст)."""
        def work():
            import shortcuts
            return "✓ Ярлыки созданы: " + "; ".join(shortcuts.create(desktop=True, start_menu=True))
        self._bg(work, lambda r: on_done(False, f"Не удалось создать ярлыки: {r}") if isinstance(r, Exception)
                 else on_done(True, r))

    # ---------- Telegram ----------
    def tg_status(self) -> str:
        import telegram_bot
        return telegram_bot.status()

    def tg_check_token(self, token, on_done):
        """token "" — проверить сохранённый. on_done(ok, "@имя_бота" | текст ошибки)."""
        def work():
            import telegram_bot
            t = (token or "").strip() or telegram_bot.get_token()
            return telegram_bot.check_token(t) if t else (False, "токен не указан")
        self._bg(work, lambda r: on_done(False, str(r)) if isinstance(r, Exception) else on_done(*r))

    def tg_pending(self) -> list:
        import telegram_bot
        return telegram_bot.pending_requests()           # [{'id','name','expires'}]

    def tg_users(self) -> list:
        import telegram_bot
        return telegram_bot.users()                      # [{'id','name','added'}]

    def tg_peek(self, code):
        """(id, имя) стоящего за кодом или None — чтобы спросить владельца подтверждение."""
        import telegram_bot
        return telegram_bot.peek(code)

    def tg_confirm(self, code):
        """(ok, сообщение)."""
        import telegram_bot
        return telegram_bot.confirm(code)

    def tg_revoke(self, user_id):
        import telegram_bot
        telegram_bot.revoke(user_id)


class StubSettings:
    """Заглушка с тестовыми данными (состояние живёт в памяти)."""
    post = staticmethod(lambda fn: fn())

    def __init__(self):
        self._s = {
            "gemini_api_keys": ["AIzaSyA1b2C3d4E5f6G7h8I9j0KlMnOpQrStUv", "AIzaSyZ9y8X7w6V5u4T3s2R1q0PoNmLkJiHgFe",
                                "AQ.Ab8RN6IexampleKeyFortheNewFormat0123"],
            "model": "gemini-3.5-flash", "requests_per_minute": 6, "use_fallback_models": True,
            "fallback_models": ["gemini-2.5-flash", "gemini-2.5-flash-lite"], "view_only": False,
            "auto_confirm_seconds": 5, "hotkey_open_window": "ctrl+shift+a", "hotkey_voice": "ctrl+shift+v",
            "hotkey_stop": "ctrl+shift+s",
            "allowed_folders": ["C:/Users/user/Desktop", "C:/Users/user/Documents", "C:/Users/user/Downloads"],
            "voice_feedback_enabled": False, "voice_wake_enabled": False, "voice_language": "ru-RU", "voice_device": "",
            "local_model_enabled": True, "local_model_name": "qwen2.5:3b", "telegram_enabled": True,
            "telegram_approve_in_chat": True, "telegram_trusted_auto": True, "telegram_screen_after": False,
            "telegram_announce": True, "autostart": True, "ui_theme": "dark", "ui_language": "ru",
            "cloud_files_consent": False, "mask_password_fields": True, "secure_store": True,
            "telegram_has_token": True, "telegram_status": "работает",
        }
        self._users = [{"id": 1001, "name": "Саша", "added": 0}, {"id": 1002, "name": "Мама", "added": 0}]
        self.saved = None

    def settings_load(self):
        return dict(self._s)

    def settings_save(self, values):
        self.saved = dict(values)
        self._s.update({k: v for k, v in values.items() if k not in ("telegram_token", "telegram_clear_token")})
        return ""

    def settings_keys_test(self, keys, on_done):
        from PySide6.QtCore import QTimer
        res = [(i != 1, "..." + k[-6:], "работает" if i != 1 else "Google ответил 429: лимит исчерпан")
               for i, k in enumerate(keys)]
        QTimer.singleShot(300, lambda: on_done(res))

    def update_check(self, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: on_done("new", "доступна версия v3.1.0 (у тебя v3.0.0)", ""))

    def update_install(self, on_progress, on_done):
        on_done(True, "Установщик запущен — программа закроется.")

    def update_set_token(self, token):
        pass

    def update_has_token(self):
        return False

    def settings_or_models(self, key, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, lambda: on_done(True, [("meta-llama/llama-3.2-11b-vision-instruct:free", True, True),
                                                      ("google/gemma-3-27b-it:free", True, True)]))

    def settings_keys_rotation(self):
        return [("AIza…StUv", "готов"), ("AIza…FgFe", "лимит Google, ждёт ~40 c"), ("AQ.A…0123", "готов")]

    def settings_find_models(self, key, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: on_done(True, ["gemini-3.5-flash", "gemini-2.5-flash-lite", "gemini-2.5-flash"]))

    def settings_mic_devices(self, on_done):
        on_done(["Микрофон (Realtek Audio)", "Гарнитура (Bluetooth Hands-Free)"])

    def settings_mic_test(self, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: on_done(True, "Микрофон найден: Realtek Audio\nСлышу тебя ✓ — громкость: хорошая"))

    def settings_voice_fix(self, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(300, lambda: on_done(True, "Готово! Голосовой ввод установлен — можно говорить."))

    def settings_hw_scan(self, on_done):
        from PySide6.QtCore import QTimer
        hw = {"ram_gb": 15.7, "disk_free_gb": 212.4, "cpu": "13th Gen Intel(R) Core(TM) i5-13420H", "cores": 12,
              "gpus": [{"name": "NVIDIA GeForce RTX 4050 Laptop GPU", "vram_gb": 6.0, "source": "nvidia-smi"},
                       {"name": "Intel(R) UHD Graphics", "vram_gb": None, "source": "windows"}], "vram_gb": 6.0}
        QTimer.singleShot(200, lambda: on_done(hw, True, [{"name": "qwen2.5:3b", "size_gb": 1.9}]))

    def settings_lm_find(self, use_gemini, hw, on_done):
        from PySide6.QtCore import QTimer
        rows = [{"name": "qwen3:4b", "size_gb": 2.5, "fit": "gpu", "note": "умнее при том же размере, по-русски неплохо"},
                {"name": "qwen2.5:3b", "size_gb": 1.9, "fit": "gpu", "note": "то, что программа использует по умолчанию"},
                {"name": "qwen3:8b", "size_gb": 5.2, "fit": "cpu", "note": "заметно сильнее, нужна видеокарта от 8 ГБ"},
                {"name": "gemma3:12b", "size_gb": 8.1, "fit": "no", "note": "качественная, нужна видеокарта от 12 ГБ"},
                {"name": "mystery:7b", "size_gb": None, "fit": "?", "note": "предложено Gemini, проверено в реестре"}]
        QTimer.singleShot(300, lambda: on_done(rows, False, ""))

    def settings_make_shortcuts(self, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, lambda: on_done(True, "✓ Ярлыки созданы: Desktop/AI Ассистент.lnk"))

    def tg_status(self):
        return "работает"

    def tg_check_token(self, token, on_done):
        from PySide6.QtCore import QTimer
        QTimer.singleShot(200, lambda: on_done(True, "@my_assistant_bot"))

    def tg_pending(self):
        return [{"id": 77, "name": "Алексей", "expires": 0}]

    def tg_users(self):
        return list(self._users)

    def tg_peek(self, code):
        return (77, "Алексей") if len("".join(c for c in code if c.isalnum())) == 16 else None

    def tg_confirm(self, code):
        if len("".join(c for c in code if c.isalnum())) != 16:
            return False, "Код состоит из 16 символов (например, ABCD-EFGH-JKLM-NPQR)."
        self._users.append({"id": 77, "name": "Алексей", "added": 0})
        return True, "Доступ выдан: Алексей"

    def tg_revoke(self, user_id):
        self._users = [u for u in self._users if u["id"] != user_id]
