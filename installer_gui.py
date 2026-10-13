"""
Графический установщик AI-ассистента. Запускается после того, как
AI_Assistant_Setup.bat молча распакует все файлы в эту же папку —
дальше всю "витрину" (логотип, прогресс, кнопки) показывает уже это
окно, а не голый текст в командной строке.

Использует только стандартную библиотеку Python + tkinter (входит в
обычную установку Python с python.org), поэтому не требует, чтобы
зависимости проекта уже стояли — наоборот, именно этот скрипт их и ставит.
"""
import os
import sys
import re
import subprocess
import threading
import queue
import tkinter as tk
from tkinter import ttk

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo_128.png")
ICON_PATH = os.path.join(BASE_DIR, "assets", "app.ico")

PRIMARY = "#4F46E5"
SUCCESS = "#16A34A"
DANGER = "#DC2626"
BG = "#FAFAFA"
TEXT_MUTED = "#6B7280"

REQUIREMENTS = os.path.join(BASE_DIR, "requirements.txt")   # единственный список зависимостей
# Без этого программа не запустится; остальное (озвучка, уведомления, ключи в хранилище, голос) необязательно.
CRITICAL = {"pyautogui", "keyboard", "pystray", "pillow", "pyperclip", "psutil", "requests"}
IMPORT_NAME = {"pillow": "PIL", "speechrecognition": "speech_recognition"}


def read_requirements():
    specs = []
    with open(REQUIREMENTS, encoding="utf-8") as f:
        for line in f:
            line = line.split("#")[0].strip()
            if line:
                specs.append(line)
    return specs


def package_name(spec: str) -> str:
    return re.split(r"[<>=!~\[ ;]", spec)[0]


def _darker(color, f=0.9):
    try:
        c = color.lstrip("#")
        return "#%02X%02X%02X" % tuple(int(int(c[i:i + 2], 16) * f) for i in (0, 2, 4))
    except Exception:
        return color


class InstallerApp:
    @staticmethod
    def _hover_on(e):
        try:
            w = e.widget
            if str(w.cget("state")) != "disabled":
                w._base_bg = getattr(w, "_base_bg", w.cget("bg"))
                w.configure(bg=_darker(w._base_bg), cursor="hand2")
        except Exception:
            pass

    @staticmethod
    def _hover_off(e):
        try:
            if hasattr(e.widget, "_base_bg"):
                e.widget.configure(bg=e.widget._base_bg)
        except Exception:
            pass

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("AI Ассистент — установка")
        self.root.geometry("480x520")
        self.root.resizable(False, False)
        self.root.configure(bg=BG)
        self.root.option_add("*Button.relief", "flat")
        self.root.option_add("*Button.borderWidth", 0)
        self.root.option_add("*Button.padY", 6)
        self.root.bind_class("Button", "<Enter>", self._hover_on, add="+")
        self.root.bind_class("Button", "<Leave>", self._hover_off, add="+")
        try:
            self.root.iconbitmap(ICON_PATH)
        except Exception:
            pass

        self._queue = queue.Queue()
        self._done = False
        self._failed = {}      # пакет -> почему pip не смог
        self._missing = []     # пакеты, которые не импортируются даже после установки

        self._build_ui()
        self.root.after(200, self._poll_queue)
        threading.Thread(target=self._run_install, daemon=True).start()

    def _build_ui(self):
        try:
            from PIL import Image, ImageTk
            img = Image.open(LOGO_PATH)
            self._logo = ImageTk.PhotoImage(img)
            tk.Label(self.root, image=self._logo, bg=BG).pack(pady=(24, 8))
        except Exception:
            tk.Label(self.root, text="🖱️", font=("Segoe UI", 40), bg=BG).pack(pady=(24, 8))

        tk.Label(self.root, text="AI Ассистент", font=("Segoe UI", 16, "bold"), bg=BG).pack()
        tk.Label(self.root, text="Установка зависимостей…", font=("Segoe UI", 9),
                 fg=TEXT_MUTED, bg=BG).pack(pady=(2, 16))

        self.progress = ttk.Progressbar(self.root, length=380, mode="determinate", maximum=100)
        self.progress.pack(pady=(0, 10))

        self.status_label = tk.Label(self.root, text="Подготовка…", font=("Segoe UI", 9),
                                      fg=TEXT_MUTED, bg=BG, wraplength=420)
        self.status_label.pack(pady=(0, 20))

        self.btn_frame = tk.Frame(self.root, bg=BG)
        self.btn_frame.pack(pady=10)
        # Кнопки появятся после завершения установки (см. _show_done_buttons)

        self.footer = tk.Label(self.root, text=f"Папка установки: {BASE_DIR}",
                                font=("Segoe UI", 7), fg=TEXT_MUTED, bg=BG, wraplength=440)
        self.footer.pack(side="bottom", pady=8)

    # ---------- фоновая установка ----------

    def _close_running_app(self):
        """Старая копия программы живёт в трее и держит бота Telegram: закрываем её, чтобы не было двух копий."""
        try:
            import json
            import app_paths
            with open(os.path.join(app_paths.DATA_DIR, ".instance.lock"), encoding="utf-8") as f:
                pid = json.loads(f.read()).get("pid")
            if isinstance(pid, int) and pid != os.getpid():
                subprocess.run(["taskkill", "/PID", str(pid), "/F"], capture_output=True, timeout=15,
                               creationflags=0x08000000 if os.name == "nt" else 0)
        except Exception:
            pass

    def _run_install(self):
        self._close_running_app()
        self._failed, self._missing = {}, []
        self._queue.put(("progress", 8))
        self._queue.put(("status", "Ставлю компоненты программы (1–3 минуты)…"))
        self._install_all()
        self._queue.put(("progress", 85))
        self._queue.put(("status", "Проверяю, что всё запускается…"))
        self._verify_imports()
        self._queue.put(("progress", 100))
        self._queue.put(("done", None))

    def _pip(self, specs, timeout):
        return subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *specs],
                              capture_output=True, text=True, timeout=timeout)

    def _install_all(self):
        specs = read_requirements()
        try:
            if self._pip(specs, 900).returncode == 0:
                return
        except Exception:
            pass
        # Один сбойный пакет не должен срывать установку остальных (общий pip install — "всё или ничего"):
        # ставим по одному и записываем, что именно не встало и почему.
        for i, spec in enumerate(specs, 1):
            name = package_name(spec)
            self._queue.put(("status", f"Ставлю {name}… ({i}/{len(specs)})"))
            self._queue.put(("progress", 8 + int(75 * i / len(specs))))
            try:
                proc = self._pip([spec], 400)
                if proc.returncode != 0:
                    tail = " ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-2:])
                    self._failed[name] = tail[:200] or "pip завершился с ошибкой"
            except Exception as e:
                self._failed[name] = str(e)[:200]

    def _verify_imports(self):
        names = [IMPORT_NAME.get(package_name(sp).lower(), package_name(sp)) for sp in read_requirements()]
        code = ("import importlib, sys\nbad = []\n"
                f"for n in {names!r}:\n"
                "    try: importlib.import_module(n)\n"
                "    except Exception as e: bad.append(n)\n"
                "print(','.join(bad))\n")
        try:
            out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120).stdout
            self._missing = [n for n in out.strip().split(",") if n]
        except Exception:
            pass

    # ---------- обновление UI из очереди (вызывается в главном потоке) ----------

    def _poll_queue(self):
        while not self._queue.empty():
            kind, value = self._queue.get()
            if kind == "progress":
                self.progress["value"] = value
            elif kind == "status":
                self.status_label.config(text=value)
            elif kind == "done":
                self._on_install_done()

        if not self._done:
            self.root.after(150, self._poll_queue)

    def _on_install_done(self):
        self._done = True
        reverse = {IMPORT_NAME.get(package_name(sp).lower(), package_name(sp)): package_name(sp)
                   for sp in read_requirements()}
        bad = set(self._failed) | {reverse.get(m, m) for m in self._missing}
        critical = sorted(n for n in bad if n.lower() in CRITICAL)
        optional = sorted(n for n in bad if n.lower() not in CRITICAL)

        if critical:
            details = "; ".join(f"{n}: {self._failed[n]}" for n in critical if n in self._failed)
            self.status_label.config(
                text=f"Не встали обязательные компоненты: {', '.join(critical)}.\n{details}\n"
                     "Без них программа может не запуститься.", fg=DANGER)
            self._show_retry_buttons()
            return
        if optional:
            self.status_label.config(
                text=f"Готово. Не встали необязательные компоненты: {', '.join(optional)}.\n"
                     "Программа работает; для голоса есть кнопка «Починить» в самом приложении.", fg="#B45309")
        else:
            self.status_label.config(text="Готово! Все компоненты установлены и проверены.", fg=SUCCESS)
        self._show_done_buttons()

    def _show_retry_buttons(self):
        for w in self.btn_frame.winfo_children():
            w.destroy()
        tk.Button(self.btn_frame, text="↻ Повторить установку", bg=PRIMARY, fg="white",
                  font=("Segoe UI", 10, "bold"), width=34, command=self._retry).pack(pady=(14, 4))
        tk.Button(self.btn_frame, text="Продолжить всё равно", width=34,
                  command=self._show_done_buttons).pack(pady=4)

    def _retry(self):
        for w in self.btn_frame.winfo_children():
            w.destroy()
        self.progress["value"] = 0
        self._done = False
        self.root.after(150, self._poll_queue)
        threading.Thread(target=self._run_install, daemon=True).start()

    # ---------- кнопки после установки ----------

    def _show_done_buttons(self):
        for w in self.btn_frame.winfo_children():
            w.destroy()

        self.var_desktop = tk.BooleanVar(master=self.root, value=True)
        self.var_start = tk.BooleanVar(master=self.root, value=True)
        self.var_autostart = tk.BooleanVar(master=self.root, value=False)
        for var, text in ((self.var_desktop, "Ярлык на рабочем столе"),
                          (self.var_start, "Ярлык в меню Пуск"),
                          (self.var_autostart, "Запускать вместе с Windows (сразу в трее)")):
            tk.Checkbutton(self.btn_frame, text=text, variable=var, bg=BG, anchor="w",
                           font=("Segoe UI", 10)).pack(fill="x", padx=40)

        tk.Button(self.btn_frame, text="🚀 Готово — запустить AI Ассистент", bg=PRIMARY, fg="white",
                  font=("Segoe UI", 10, "bold"), width=34,
                  command=lambda: self._finish(launch=True)).pack(pady=(14, 4))
        tk.Button(self.btn_frame, text="Готово — только закрыть", width=34,
                  command=lambda: self._finish(launch=False)).pack(pady=4)

    def _finish(self, launch: bool):
        """Применяет выбранное (ярлыки, автозапуск), затем запускает приложение или закрывается."""
        problems = []

        if self.var_desktop.get() or self.var_start.get():
            try:
                import shortcuts
                shortcuts.create(desktop=self.var_desktop.get(), start_menu=self.var_start.get())
            except Exception as e:
                problems.append(f"Ярлыки не созданы: {e}")

        if self.var_autostart.get():
            try:
                import autostart
                if not autostart.enable():
                    problems.append("Автозапуск не включился (подробности — в action_log.txt).")
            except Exception as e:
                problems.append(f"Автозапуск не включился: {e}")

        if launch:
            pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
            if not os.path.exists(pythonw):
                pythonw = sys.executable
            try:
                subprocess.Popen([pythonw, os.path.join(BASE_DIR, "main.py")], cwd=BASE_DIR)
            except Exception as e:
                problems.append(f"Не удалось запустить приложение: {e}")

        if problems:
            # Не закрываем окно молча: пользователь должен увидеть, что пошло не так.
            self.status_label.config(text="\n".join(problems) + "\n(Ярлыки можно создать позже: "
                                          "Настройки → Запуск в главном окне.)", fg=DANGER)
            for w in self.btn_frame.winfo_children():
                w.destroy()
            tk.Button(self.btn_frame, text="Закрыть", width=20, command=self.root.destroy).pack(pady=8)
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    InstallerApp().run()
