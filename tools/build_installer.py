#!/usr/bin/env python3
"""
Собирает ОДИН самораспаковывающийся файл AI_Assistant_Setup.bat из
файлов проекта (лежит рядом с папкой проекта, в его родительской папке).

Каждый файл кодируется в base64 (в нём нет спецсимволов batch, поэтому
кириллица и бинарники переживают echo без порчи) и на Windows
раскодируется штатной утилитой certutil. Потом запускается
installer_gui.py — графический установщик.

Запуск:  python tools/build_installer.py   (затем python tools/verify_installer.py)
"""
import base64
import os

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
SRC_DIR = os.path.dirname(TOOLS_DIR)                              # папка проекта
OUT_PATH = os.path.join(os.path.dirname(SRC_DIR), "AI_Assistant_Setup.bat")

# Какие файлы кладём в установщик. Порядок важен только для verify_installer.py.
FILES_TO_EMBED = [
    "action_executor.py", "agent_loop.py", "ai_brain.py", "app_paths.py", "app_window.py",
    "atomic_io.py", "autostart.py", "config.py", "diagnostics.py", "gui.py", "history.py",
    "installer_gui.py", "key_manager.py", "local_brain.py", "logger.py", "main.py",
    "notifications.py", "quick_commands.py", "quick_actions.py", "macros.py", "telegram_bot.py", "media_input.py", "clipboard_tools.py", "templates.py", "wakeword.py", "mouse.py", "app_profiles.py", "audit.py", "updater.py", "ui_elements.py", "plan_validator.py", "privacy.py", "scheduler.py",
    "screen_utils.py", "secure_store.py", "shortcuts.py", "single_instance.py", "theme.py",
    "ui_thread.py", "voice_input.py", "voice_output.py",
    "requirements.txt", "README.md", "CHANGELOG.md", "LICENSE",
    "start.bat", "uninstall.bat", "build_exe.bat",
    "assets/app.ico", "assets/logo_128.png", "assets/logo_64.png",
]

APP_VERSION_LABEL = "1.26.0"
SUBDIRS = sorted({os.path.dirname(p) for p in FILES_TO_EMBED if "/" in p})


def build_extract_block(rel_path: str, content_bytes: bytes) -> str:
    b64_text = base64.encodebytes(content_bytes).decode("ascii")
    lines = [ln for ln in b64_text.splitlines() if ln.strip()]

    win_rel_path = rel_path.replace("/", "\\")
    safe_name = rel_path.replace("/", "_").replace(".", "_")
    tmp_var = f"%TEMP%\\aiasst_{safe_name}.b64"

    out = [f'echo Распаковываю: {win_rel_path}', f'> "{tmp_var}" (']
    out += [f"echo {ln}" for ln in lines]
    out.append(")")
    out.append(f'certutil -decode "{tmp_var}" "%INSTALL_DIR%\\{win_rel_path}" >nul || goto :extract_failed')
    out.append(f'del "{tmp_var}" >nul 2>nul')
    out.append("")
    return "\n".join(out)


def main():
    header = r"""@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ==========================================
echo   AI Ассистент - установка (v__VERSION__)
echo ==========================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден в PATH.
    echo Установи Python 3.10+ с python.org - обязательно поставь галочку
    echo "Add python.exe to PATH" при установке - и запусти этот файл ещё раз.
    start "" https://www.python.org/downloads/
    pause
    exit /b 1
)

where certutil >nul 2>nul
if errorlevel 1 (
    echo Не найдена системная утилита certutil - она нужна для распаковки.
    echo Обычно она есть в любой Windows 10/11. Проверь целостность системы.
    pause
    exit /b 1
)

set "INSTALL_DIR=%~dp0AI_Assistant"
if not exist "%INSTALL_DIR%" mkdir "%INSTALL_DIR%"
""".replace("__VERSION__", APP_VERSION_LABEL)

    header += "\n".join(
        f'if not exist "%INSTALL_DIR%\\{s.replace("/", chr(92))}" mkdir "%INSTALL_DIR%\\{s.replace("/", chr(92))}"'
        for s in SUBDIRS
    ) + "\n\n"
    header += ('echo Устанавливаю в папку: %INSTALL_DIR%\n'
               'echo.\n'
               'echo Распаковываю файлы программы (это займёт несколько секунд)...\n'
               'echo.\n')

    blocks = []
    for rel_path in FILES_TO_EMBED:
        with open(os.path.join(SRC_DIR, rel_path), "rb") as f:
            blocks.append(build_extract_block(rel_path, f.read()))

    footer = r"""
goto :extract_ok

:extract_failed
echo.
echo ОШИБКА: не удалось распаковать один из файлов (см. строку "Распаковываю" выше).
echo Запусти установщик ещё раз, лучше с обычного диска, а не из архива/почты.
pause
exit /b 1

:extract_ok
if not exist "%INSTALL_DIR%\main.py" (
    echo.
    echo ОШИБКА: распаковка не удалась - main.py не найден в целевой папке.
    pause
    exit /b 1
)

echo.
echo Все файлы распакованы. Запускаю графический установщик...
echo.

cd /d "%INSTALL_DIR%"
python installer_gui.py

echo.
echo Установщик закрыт. Если что-то пошло не так - смотри сообщения выше,
echo либо поставь зависимости вручную: python -m pip install -r requirements.txt
pause
exit /b 0
"""

    script = header + "\n" + "\n".join(blocks) + footer
    with open(OUT_PATH, "w", encoding="utf-8", newline="\r\n") as f:
        f.write(script)

    print(f"Готово: {OUT_PATH} ({os.path.getsize(OUT_PATH) / 1024:.1f} КБ, "
          f"{len(FILES_TO_EMBED)} файлов, подпапки: {SUBDIRS})")


if __name__ == "__main__":
    main()
