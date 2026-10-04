"""
Создание и удаление ярлыков (рабочий стол, меню Пуск).

Почему ярлык "не создавался" раньше — сразу два классических подвоха Windows:
  1. VBS-файл записывался в UTF-8, а Windows Script Host ожидает UTF-16;
     с кириллицей в тексте скрипт ломался.
  2. Ярлык клали в ~\\Desktop, но при включённом OneDrive рабочий стол лежит в
     OneDrive\\Desktop (а иногда папки ~\\Desktop нет вообще). Теперь настоящий
     путь спрашивается у самой Windows (WScript.Shell.SpecialFolders), а после
     создания проверяется, что файл .lnk реально появился.

Только стандартная библиотека — модуль работает и из установщика, до установки зависимостей.
"""
import os
import shutil
import subprocess
import sys
import tempfile

APP_NAME = "AI Ассистент"
_FROZEN = getattr(sys, "frozen", False)
BASE_DIR = os.path.dirname(os.path.abspath(sys.executable if _FROZEN else __file__))


def _target():
    """(программа, аргументы, рабочая папка, файл значка) для ярлыка."""
    if _FROZEN:
        return sys.executable, "", BASE_DIR, sys.executable
    pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    if not os.path.exists(pythonw):
        pythonw = sys.executable
    return pythonw, f'"{os.path.join(BASE_DIR, "main.py")}"', BASE_DIR, os.path.join(BASE_DIR, "assets", "app.ico")


def _q(text: str) -> str:
    """Строка для VBScript: кавычки удваиваются."""
    return str(text).replace('"', '""')


def build_script(result_path: str, desktop: bool, start_menu: bool) -> str:
    """Текст VBS: создаёт нужные ярлыки и записывает в result_path пути реально созданных."""
    program, args, workdir, icon = _target()
    lines = [
        'Set oWS = WScript.CreateObject("WScript.Shell")',
        'Set fso = CreateObject("Scripting.FileSystemObject")',
        f'Set outFile = fso.CreateTextFile("{_q(result_path)}", True, True)',
    ]
    places = []
    if desktop:
        places.append("Desktop")
    if start_menu:
        places.append("Programs")
    for place in places:
        lines += [
            f'sLink = oWS.SpecialFolders("{place}") & "\\{_q(APP_NAME)}.lnk"',
            "Set oLink = oWS.CreateShortcut(sLink)",
            f'oLink.TargetPath = "{_q(program)}"',
            f'oLink.Arguments = "{_q(args)}"',
            f'oLink.WorkingDirectory = "{_q(workdir)}"',
            f'oLink.IconLocation = "{_q(icon)},0"',
            f'oLink.Description = "{_q(APP_NAME)}"',
            "oLink.Save",
            "outFile.WriteLine sLink",
        ]
    lines.append("outFile.Close")
    return "\r\n".join(lines) + "\r\n"


def _run_vbs(script_text: str, result_path: str):
    tmp_dir = os.path.dirname(result_path)
    vbs_path = os.path.join(tmp_dir, "make_shortcuts.vbs")
    # ВАЖНО: Windows Script Host читает .vbs как UTF-16 (с BOM), а не UTF-8.
    with open(vbs_path, "w", encoding="utf-16") as f:
        f.write(script_text)
    proc = subprocess.run(["wscript.exe", "//nologo", vbs_path], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or "").strip() or f"wscript завершился с кодом {proc.returncode}")
    if not os.path.exists(result_path):
        raise RuntimeError("скрипт отработал, но не вернул результат")
    with open(result_path, encoding="utf-16") as f:
        return [line.strip() for line in f.read().splitlines() if line.strip()]


def create(desktop: bool = True, start_menu: bool = True):
    """Создаёт ярлыки и возвращает список путей. Бросает RuntimeError с понятным текстом, если не вышло."""
    tmp_dir = tempfile.mkdtemp(prefix="aiasst_")
    try:
        result_path = os.path.join(tmp_dir, "result.txt")
        paths = _run_vbs(build_script(result_path, desktop, start_menu), result_path)
        missing = [p for p in paths if not os.path.exists(p)]
        if not paths or missing:
            raise RuntimeError("Windows не создала файл ярлыка: " + (", ".join(missing) or "путь не определён"))
        return paths
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def remove():
    """Удаляет ярлыки с рабочего стола и из меню Пуск (если они есть). Возвращает список удалённых."""
    tmp_dir = tempfile.mkdtemp(prefix="aiasst_")
    removed = []
    try:
        result_path = os.path.join(tmp_dir, "result.txt")
        script = "\r\n".join([
            'Set oWS = WScript.CreateObject("WScript.Shell")',
            'Set fso = CreateObject("Scripting.FileSystemObject")',
            f'Set outFile = fso.CreateTextFile("{_q(result_path)}", True, True)',
            f'outFile.WriteLine oWS.SpecialFolders("Desktop") & "\\{_q(APP_NAME)}.lnk"',
            f'outFile.WriteLine oWS.SpecialFolders("Programs") & "\\{_q(APP_NAME)}.lnk"',
            "outFile.Close",
        ]) + "\r\n"
        for path in _run_vbs(script, result_path):
            if os.path.exists(path):
                os.remove(path)
                removed.append(path)
    except Exception:
        pass
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    return removed
