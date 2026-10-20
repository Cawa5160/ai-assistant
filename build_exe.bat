@echo off
chcp 65001 >nul
cd /d "%~dp0"

echo ==========================================
echo   Сборка AI_Assistant.exe (один файл)
echo ==========================================
echo.
echo Собирать .exe можно только на самой Windows - это нормально, ты как раз на ней.
echo Займёт 1-3 минуты. Python нужен ТОЛЬКО для сборки, для запуска готового .exe - нет.
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo Python не найден в PATH. Установи Python 3.10+ с python.org (галочка "Add to PATH").
    pause
    exit /b 1
)

echo [1/3] Ставлю PyInstaller и зависимости программы...
python -m pip install --upgrade pyinstaller
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo Не все зависимости поставились - смотри сообщения выше.
    pause
    exit /b 1
)

echo.
echo [2/3] Собираю .exe...
python -m PyInstaller --noconfirm --clean --onefile --noconsole ^
  --name AI_Assistant ^
  --icon "assets\app.ico" ^
  --add-data "assets;assets" ^
  --hidden-import PySide6.QtSvg ^
  --hidden-import pyttsx3.drivers ^
  --hidden-import pyttsx3.drivers.sapi5 ^
  --hidden-import keyring.backends.Windows ^
  --collect-data speech_recognition ^
  --hidden-import sounddevice ^
  --collect-all uiautomation ^
  --collect-all comtypes ^
  --hidden-import winotify ^
  --collect-all sounddevice ^
  --collect-all _sounddevice_data ^
  main.py

echo.
if exist "dist\AI_Assistant.exe" (
    echo [3/3] Готово!  Файл:  %~dp0dist\AI_Assistant.exe
    echo.
    echo Его можно скопировать в любую папку и запускать двойным кликом - Python не нужен.
    echo Настройки, история и логи будут храниться РЯДОМ с .exe.
    echo Если при запуске что-то не найдётся - PyInstaller подскажет модуль, его добавляют
    echo флагом --hidden-import в команду выше.
) else (
    echo Что-то пошло не так - смотри сообщения PyInstaller выше.
)
pause
