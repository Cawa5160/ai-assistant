# AI Ассистент для Windows 11 — заметки для Claude

Пользователь пишет по-русски (с опечатками), любит короткие ответы. Windows 11, игровой ноутбук (RTX 4050, 6 ГБ видеопамяти, ~16 ГБ RAM), один монитор 1920×1080 @125%, Python 3.14, Ollama нет. Проверяет сборки сам на своём ПК и присылает скриншоты/логи.

## Что это
Tkinter-ассистент: пишешь/говоришь команду → Gemini (бесплатные ключи, несколько) смотрит на экран и выполняет шаги. Трей, Telegram-бот, макросы, шаблоны, расписание, голос. Поставляется как самораспаковывающийся `AI_Assistant_Setup.bat` (`python tools/build_installer.py`, проверка `python tools/verify_installer.py`). Настоящий Setup.exe — только GitHub Actions → Run workflow.

## Архитектура (кратко)
- Порядок в `main.process_command`: `macros.try_memory_command` → `try_quick_action` → view_only → `macros.try_macro_plan` / `templates.try_template_plan` / `try_quick_plan` / `try_local_plan` → `_run_agent` (автопилот: `agent_loop.decide` + `run_agent`).
- GUI в одном потоке (`ui_thread`, `@on_ui`); окна скрыты от захвата экрана.
- Стоп: `_stop_event`, `request_stop()`, `telegram_bot.cancel_approvals()`.
- Данные: `%APPDATA%\AI_Assistant` (`app_paths.DATA_DIR`), зашифрованный бэкап ключей/токена (`backup.py`, DPAPI). Логи: `action_log.txt` там же.
- Ключ Gemini передаётся заголовком `x-goog-api-key` (не в URL).
- Модель по умолчанию `gemini-3.5-flash` (config.py), запасные 2.5; возможный апгрейд до 3.8 Flash — ID не проверен.

## Дизайн (v1.28.x)
- Новое оформление в стиле концепта ChatGPT на чистом Tkinter: `ui_kit.py` (RoundCard, Toggle, Segmented, Swatches, NavItem, Chip, style_ttk), палитра и `FILLS` в `theme.py`.
- Этапы 1–5 сделаны (настройки/меню, главная, история/расписание, проверка/диалоги, установщик). Значки меню — Segoe Fluent Icons (коды не проверены на ПК).
- Картинки интерфейса на сервере: `xvfb-run` + заглушки модулей + `import -window root`.
- Страница «Ассистент» (v1.29.0): `chat.py` + `_build_chat` в app_window; переписка только в памяти, ask через `Controller.chat_ask` (gemini|ollama, запасной ответ локально).
- Локальные модели (v1.30.0): `hardware.py` (железо), `local_llm.py` (Ollama, реестр, подбор), вкладка Настройки → «Локальные модели». Адрес реестра `registry.ollama.ai` и вывод nvidia-smi не проверены на ПК пользователя.

- Язык интерфейса (v1.31.0): `i18n.py` подменяет `text=` в tkinter на лету; словари `lang_en.py`/`lang_uk.py` (ключ — русский текст). Новые надписи в интерфейсе добавлять в оба словаря. С v1.31.1 переведены и трей/уведомления/Telegram/сообщения (через `i18n.t` в `main.notify`, `setup_tray`, `telegram_bot._send`); к промптам Gemini добавляется `i18n.answer_hint()`. Регулярки, промпты и ключевые слова команд НЕ переводить.

## Как выпускать версию
1. Править код; `python -m unittest discover -s tests` (37 тестов, Windows не нужен).
2. Поднять версию в `config.py` (`APP_VERSION`) и `tools/build_installer.py` (`APP_VERSION_LABEL`), записать в `CHANGELOG.md`.
3. `python tools/build_installer.py` → `python tools/verify_installer.py`.
4. Коммит с строками Co-Authored-By/Claude-Session, push, отправить `.bat` пользователю.
Новые модули добавлять в `FILES_TO_EMBED` в `tools/build_installer.py`.

## Решения пользователя (не предлагать заново)
- Kimi не подключать. Откат версии и настройки прав по категориям — «лишняя работа», не делать.
- Помощь с оцениваемыми тестами/экзаменами — нет.
- Токен Telegram-бота пользователь менять не собирается (предупреждён).
- Версию «2.0» не объявлять, пока он не пройдёт чек-листы тестов.

## Открытое
- Подтвердить на ПК: «открой браузер и включи что-то интересное» (зависание/окно «думает»), данные при обновлении, Desktop, /stop, шаблоны, Setup.exe через Actions.
- Идеи без команды: Ollama как запасной бэкенд (Qwen3-VL 4B влезает в 6 ГБ), OpenRouter как запасной провайдер, добавить `del` в опасные слова (`type_text` с `del /f` сейчас «normal», подтверждение всё равно требуется), автообновление через GitHub Releases.
- Бесплатный Gemini API может использовать скриншоты для обучения вне ЕС/ЕЭЗ — пользователь предупреждён.
