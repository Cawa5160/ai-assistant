@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo ==========================================
echo  Удаление AI-ассистента
echo ==========================================
echo.
echo Это уберёт:
echo   - автозагрузку с Windows (если была включена)
echo   - ярлыки на рабочем столе и в меню Пуск
echo   - настройки, историю, расписание, логи
echo   - сохранённые ключи из защищённого хранилища Windows
echo.
echo Сама папка с программой и зависимости Python НЕ удаляются —
echo их можно стереть вручную после этого скрипта.
echo.
set /p CONFIRM="Продолжить? (y/n): "
if /i not "%CONFIRM%"=="y" goto :end

python -c "import autostart; autostart.disable()" 2>nul
python -c "import shortcuts; shortcuts.remove()" 2>nul
python -c "import keyring; keyring.delete_password('ai_assistant_gemini', 'keys')" 2>nul

del /q settings.json 2>nul
del /q history.json 2>nul
del /q scheduled_tasks.json 2>nul
del /q action_log.txt* 2>nul

echo.
echo Готово. Папку программы можешь удалить вручную.
:end
pause
