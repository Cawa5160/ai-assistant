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

PRIMARY = "#4F46E5"      # заливка кнопок (белый текст, контраст ≥ 4.5)
SUCCESS = "#4ADE80"
DANGER = "#F87171"
WARN = "#FBBF24"
BG = "#0E1424"
SIDE = "#0A0F1C"
CARD = "#151C30"
INPUT = "#0B1020"
BORDER = "#27314F"
TEXT = "#EAF0FF"
TEXT_MUTED = "#93A0BE"
STEPS = ["Подготовка", "Компоненты", "Проверка", "Готово"]

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
        try:
            import i18n
            if os.path.exists(os.path.join(BASE_DIR, "lang_en.py")):
                try:
                    import config
                    saved = config.load().get("ui_language") if os.path.exists(config.SETTINGS_PATH) else None
                except Exception:
                    saved = None
                i18n.set_language(saved or i18n.detect_system_language())
                i18n.install()
        except Exception:
            pass
        self.root = tk.Tk()
        self.root.title("AI Ассистент — установка")
        self.root.geometry("760x500")
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
        self._step = 0

        self._build_ui()
        self.root.after(200, self._poll_queue)
        threading.Thread(target=self._run_install, daemon=True).start()

    def _build_ui(self):
        # слева — шаги установки
        side = tk.Frame(self.root, bg=SIDE, width=230)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        head = tk.Frame(side, bg=SIDE)
        head.pack(fill="x", padx=20, pady=(26, 22))
        try:
            from PIL import Image, ImageTk
            img = Image.open(LOGO_PATH).resize((36, 36))
            self._logo = ImageTk.PhotoImage(img)
            tk.Label(head, image=self._logo, bg=SIDE).pack(side="left")
        except Exception:
            tk.Label(head, text="🖱", font=("Segoe UI", 18), bg=SIDE, fg=TEXT).pack(side="left")
        tk.Label(head, text="AI Ассистент", font=("Segoe UI", 13, "bold"), bg=SIDE, fg=TEXT).pack(side="left", padx=10)
        self._step_labels = []
        for text in STEPS:
            lab = tk.Label(side, text="", font=("Segoe UI", 10), bg=SIDE, fg=TEXT_MUTED, anchor="w")
            lab.pack(fill="x", padx=22, pady=7)
            self._step_labels.append((lab, text))
        tk.Label(side, text=f"Папка установки:\n{BASE_DIR}", font=("Segoe UI", 8), fg=TEXT_MUTED, bg=SIDE,
                 wraplength=190, justify="left", anchor="w").pack(side="bottom", fill="x", padx=22, pady=16)

        # справа — прогресс, журнал, кнопки
        main = tk.Frame(self.root, bg=BG)
        main.pack(side="left", fill="both", expand=True, padx=28, pady=(26, 18))
        self.title_label = tk.Label(main, text="Установка", font=("Segoe UI", 18, "bold"), bg=BG, fg=TEXT, anchor="w")
        self.title_label.pack(fill="x")
        self.status_label = tk.Label(main, text="Подготовка…", font=("Segoe UI", 10), fg=TEXT_MUTED, bg=BG,
                                     anchor="w", justify="left", wraplength=440)
        self.status_label.pack(fill="x", pady=(4, 12))

        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("Inst.Horizontal.TProgressbar", troughcolor=INPUT, background=PRIMARY, bordercolor=BORDER,
                        lightcolor=PRIMARY, darkcolor=PRIMARY, thickness=10)
        self.progress = ttk.Progressbar(main, mode="determinate", maximum=100, style="Inst.Horizontal.TProgressbar")
        self.progress.pack(fill="x", pady=(0, 12))

        self.log_box = tk.Text(main, height=9, font=("Consolas", 9), bg=CARD, fg=TEXT_MUTED, relief="flat", bd=0,
                               highlightthickness=1, highlightbackground=BORDER, padx=10, pady=8, wrap="word",
                               state="disabled")
        self.log_box.pack(fill="both", expand=True)
        self.btn_frame = tk.Frame(main, bg=BG)
        self.btn_frame.pack(fill="x", pady=(12, 0))
        # Кнопки и галочки появятся после завершения установки (см. _show_done_buttons)
        self._set_step(0)
        self._log("Начинаю установку.")

    def _set_step(self, n):
        self._step = n
        for i, (lab, text) in enumerate(self._step_labels):
            if i < n:
                lab.config(text=f"✓  {text}", fg=SUCCESS, font=("Segoe UI", 10))
            elif i == n:
                lab.config(text=f"●  {text}", fg=TEXT, font=("Segoe UI", 10, "bold"))
            else:
                lab.config(text=f"○  {text}", fg=TEXT_MUTED, font=("Segoe UI", 10))

    def _log(self, text):
        try:
            self.log_box.config(state="normal")
            self.log_box.insert("end", text + "\n")
            self.log_box.see("end")
            self.log_box.config(state="disabled")
        except tk.TclError:
            pass

    def _button(self, parent, text, command, primary=False, width=None):
        bg = PRIMARY if primary else "#3A4568"
        return tk.Button(parent, text=text, command=command, bg=bg, fg="white", activebackground=bg,
                         activeforeground="white", font=("Segoe UI", 10, "bold" if primary else "normal"),
                         width=width or 0, padx=14, cursor="hand2")

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
        self._queue.put(("step", 1))
        self._queue.put(("status", "Ставлю компоненты программы (1–3 минуты)…"))
        self._queue.put(("log", "Устанавливаю компоненты из requirements.txt…"))
        self._install_all()
        self._queue.put(("progress", 85))
        self._queue.put(("step", 2))
        self._queue.put(("status", "Проверяю, что всё запускается…"))
        self._queue.put(("log", "Проверяю, что все компоненты импортируются…"))
        self._verify_imports()
        self._queue.put(("progress", 100))
        self._queue.put(("step", 3))
        self._queue.put(("done", None))

    def _pip(self, specs, timeout):
        return subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", *specs],
                              capture_output=True, text=True, timeout=timeout)

    def _install_all(self):
        specs = read_requirements()
        try:
            if self._pip(specs, 900).returncode == 0:
                self._queue.put(("log", f"✓ Все компоненты поставились разом ({len(specs)})."))
                return
        except Exception:
            pass
        # Один сбойный пакет не должен срывать установку остальных (общий pip install — "всё или ничего"):
        # ставим по одному и записываем, что именно не встало и почему.
        self._queue.put(("log", "Общая установка не прошла — ставлю по одному."))
        for i, spec in enumerate(specs, 1):
            name = package_name(spec)
            self._queue.put(("status", f"Ставлю {name}… ({i}/{len(specs)})"))
            self._queue.put(("progress", 8 + int(75 * i / len(specs))))
            try:
                proc = self._pip([spec], 400)
                if proc.returncode != 0:
                    tail = " ".join((proc.stderr or proc.stdout or "").strip().splitlines()[-2:])
                    self._failed[name] = tail[:200] or "pip завершился с ошибкой"
                    self._queue.put(("log", f"✕ {name}: {self._failed[name]}"))
                else:
                    self._queue.put(("log", f"✓ {name}"))
            except Exception as e:
                self._failed[name] = str(e)[:200]
                self._queue.put(("log", f"✕ {name}: {self._failed[name]}"))

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
            self._queue.put(("log", ("✕ Не запускаются: " + ", ".join(self._missing)) if self._missing
                             else "✓ Всё запускается."))
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
            elif kind == "step":
                self._set_step(value)
            elif kind == "log":
                self._log(value)
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
                text=f"Не встали необязательные компоненты: {', '.join(optional)}.\n"
                     "Программа работает; для голоса есть кнопка «Починить» в самом приложении.", fg=WARN)
        else:
            self.status_label.config(text="Все компоненты установлены и проверены.", fg=SUCCESS)
        self._set_step(4)
        self.title_label.config(text="Готово")
        self._show_done_buttons()

    def _show_retry_buttons(self):
        self.title_label.config(text="Есть проблема")
        for w in self.btn_frame.winfo_children():
            w.destroy()
        self._button(self.btn_frame, "Повторить установку", self._retry, primary=True).pack(side="left")
        self._button(self.btn_frame, "Продолжить всё равно", self._continue_anyway).pack(side="left", padx=10)

    def _continue_anyway(self):
        self._set_step(4)
        self._show_done_buttons()

    def _retry(self):
        for w in self.btn_frame.winfo_children():
            w.destroy()
        self.progress["value"] = 0
        self.title_label.config(text="Установка")
        self.status_label.config(fg=TEXT_MUTED)
        self._set_step(0)
        self._log("— Повторная установка —")
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
            tk.Checkbutton(self.btn_frame, text=text, variable=var, bg=BG, fg=TEXT, anchor="w",
                           selectcolor=INPUT, activebackground=BG, activeforeground=TEXT, highlightthickness=0, bd=0,
                           font=("Segoe UI", 10)).pack(fill="x")

        row = tk.Frame(self.btn_frame, bg=BG)
        row.pack(fill="x", pady=(10, 0))
        self._button(row, "Запустить AI Ассистент", lambda: self._finish(launch=True), primary=True).pack(side="left")
        self._button(row, "Только закрыть", lambda: self._finish(launch=False)).pack(side="left", padx=10)

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
            self._button(self.btn_frame, "Закрыть", self.root.destroy, primary=True).pack(anchor="w", pady=8)
        else:
            self.root.destroy()

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    InstallerApp().run()
