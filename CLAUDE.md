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

- Язык интерфейса (v1.31.0): `i18n.py` подменяет `text=` в tkinter на лету; словари `lang_en.py`/`lang_uk.py` (ключ — русский текст). Новые надписи в интерфейсе добавлять в оба словаря. С v1.31.1 переведены и трей/уведомления/Telegram/сообщения (через `i18n.t` в `main.notify`, `setup_tray`, `telegram_bot._send`); к промптам Gemini добавляется `i18n.answer_hint()`. Регулярки, промпты и ключевые слова команд НЕ переводить; ключевые слова команд — ru/uk/en прямо в регулярках. Фрагменты f-строк лежат в `FRAG` словарей (подставляются за один проход в `i18n.t`); `logger.log` тоже переводит. Для удаления — `cleanup.py`.

## Интерфейс v3 (Qt) — сделан в v2.1–3.0.0 (19.10.2026), на ПК пользователя ещё не проверен
- `qt_ui/`: `runtime.QtShell` (подставляется вместо `AppWindow`, настройка `ui_engine`, `--tk`/`--qt`), `main_window`, страницы (`pages`, `pages_more`, `memory_page`, `settings_page`), бэкенды-миксины (`backend.RealBackend` = `MoreBackend`+`MemoryBackend`+`SettingsBackend`; у каждого есть Stub для макетов), `dialogs_qt` (все диалоги и окна автопилота с теми же именами, что в `gui.py`; `install()` подменяет функции в `gui`), `attachments`/`dialogs` (вложения и согласие), `file_tools`/`file_ops_dialog` + `file_ops.py` (файловые операции, удаление только в Корзину, отмена), `screen_mask.py` (закраска полей паролей на скриншотах), `files_io.py` (чтение файлов, скрытие секретов).
- Tk (`gui.py`, `app_window.py`) — запасной интерфейс; установщик (`installer_gui.py`) остаётся на Tk. Трей — pystray, уведомления — winotify (не менялись).
- Предпросмотр без экрана: `QT_QPA_PLATFORM=offscreen python -m qt_ui.preview /tmp/qshots` (перед созданием окна нужен `theme.set(name, app)`).
- Не проверено на Windows: UI Automation маска паролей, возврат из Корзины, WDA_EXCLUDEFROMCAPTURE для окон Qt, пути pip для PySide6 на Python 3.14.
- Предложено, не сделано: удалить Tk после проверки пользователем; согласие/маска в Telegram-путях.

## Как выпускать версию
1. Править код; `python -m unittest discover -s tests` (44 теста, Windows не нужен).
2. Поднять версию в `config.py` (`APP_VERSION`) и `tools/build_installer.py` (`APP_VERSION_LABEL`), записать в `CHANGELOG.md`.
3. `python tools/build_installer.py` → `python tools/verify_installer.py`.
4. Коммит с строками Co-Authored-By/Claude-Session, push, отправить `.bat` пользователю.
Новые модули добавлять в `FILES_TO_EMBED` в `tools/build_installer.py`.

## Решения пользователя (не предлагать заново)
- Kimi не подключать. Откат версии и настройки прав по категориям — «лишняя работа», не делать.
- Помощь с оцениваемыми тестами/экзаменами — нет.
- Токен Telegram-бота пользователь менять не собирается (предупреждён).
- Локальная Qwen3-VL как запасной автопилот — не нужно (отказался 18.10.2026); у него своя идея про собственную модель.
- Версия 2.0.0 выпущена после прохождения чек-листов (18.10.2026).

## Открытое
- Подтвердить на ПК: «открой браузер и включи что-то интересное» (зависание/окно «думает»), данные при обновлении, Desktop, /stop, шаблоны, Setup.exe через Actions.
- Идеи без команды: OpenRouter как запасной провайдер, добавить `del` в опасные слова (`type_text` с `del /f` сейчас «normal», подтверждение всё равно требуется), автообновление через GitHub Releases.
- Бесплатный Gemini API может использовать скриншоты для обучения вне ЕС/ЕЭЗ — пользователь предупреждён.
