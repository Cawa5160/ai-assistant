"""Методы бэкенда страницы «Макросы, заметки и шаблоны».

MemoryBackend — миксин (`class RealBackend(MemoryBackend, ...)`); требует у self `run_command(text)`.
Файлы маленькие (json), поэтому всё выполняется синхронно и быстро — потоки не нужны; `post` не используется.
Возвращаемые (ok, сообщение) — как у macros.add_note / templates.add_template; сообщения по-русски (страница переводит через T).
StubMemory — те же методы с тестовыми данными в памяти (для макетов/тестов).
"""


class MemoryBackend:
    # ---------- Макросы ----------
    def mem_recent_steps(self) -> int:
        """Сколько шагов в последней выполненной команде (их можно сохранить макросом)."""
        try:
            import action_executor
            return len(action_executor.RECENT_STEPS)
        except Exception:
            return 0

    def mem_macros(self) -> list:
        """[{name, steps (число), desc (кратко, до 200 симв.)}]"""
        import agent_loop
        import macros
        out = []
        for name, m in macros.list_macros().items():
            steps = m.get("steps", []) if isinstance(m, dict) else []
            try:
                desc = "; ".join(agent_loop.describe_step(x) for x in steps if x.get("action") != "wait")[:200]
            except Exception:
                desc = ""
            out.append({"name": name, "steps": len(steps), "desc": desc})
        return out

    def mem_save_macro(self, name: str):
        """Сохраняет шаги последней команды. -> (ok, сообщение)."""
        name = (name or "").strip()
        if not name:
            return False, "Впиши название макроса."
        import action_executor
        import macros
        return macros.save_macro(name, list(action_executor.RECENT_STEPS))

    def mem_delete_macro(self, name: str):
        import macros
        macros.delete_macro(name)

    def mem_run_macro(self, name: str):
        self.run_command(f"макрос {name}")

    # ---------- Заметки ----------
    def mem_notes(self) -> list:
        import macros
        return macros.notes()

    def mem_add_note(self, text: str):
        import macros
        return macros.add_note(text)

    def mem_delete_note(self, index: int):
        import macros
        macros.delete_note(index)

    # ---------- Шаблоны ----------
    def mem_templates(self) -> list:
        """[{name, items (список команд), builtin (встроенный и не переопределён)}]"""
        import templates
        user = templates.user_templates()
        return [{"name": n, "items": list(items), "builtin": n in templates.BUILTIN and n not in user}
                for n, items in templates.all_templates().items()]

    def mem_add_template(self, name: str, items_text: str):
        import templates
        return templates.add_template(name, items_text)

    def mem_delete_template(self, name: str):
        import templates
        templates.delete_template(name)

    def mem_run_template(self, name: str):
        self.run_command(f"шаблон {name}")


class StubMemory:
    """Заглушка с тестовыми данными."""
    post = staticmethod(lambda fn: fn())

    def __init__(self):
        self._recent = 3
        self._macros = {"утро": (4, "Открыть программу «chrome»; Открыть сайт mail.google.com; Нажать в точке экрана (640, 300)"),
                        "отчёт": (7, "Открыть программу «excel»; Нажать в точке экрана (120, 90); Нажать клавиши ctrl+s")}
        self._notes = ["Мой город — Киев", "Почта по умолчанию — Gmail", "Люблю тёмную тему"]
        self._tpl = {"утро": (["открой почту", "открой телеграм", "открой гугл"], True),
                     "работа": (["открой хром", "открой блокнот", "открой почту"], True),
                     "созвон": (["выключи звук", "открой zoom"], False)}
        self.ran = []

    def run_command(self, text):
        self.ran.append(text)

    def mem_recent_steps(self):
        return self._recent

    def mem_macros(self):
        return [{"name": n, "steps": s, "desc": d} for n, (s, d) in self._macros.items()]

    def mem_save_macro(self, name):
        name = (name or "").strip().lower()
        if not name:
            return False, "Впиши название макроса."
        if not self._recent:
            return False, "Нечего сохранять: сначала выполни команду, потом скажи «запомни как …»."
        self._macros[name] = (self._recent, "Открыть программу «notepad»")
        return True, "Сохранено."

    def mem_delete_macro(self, name):
        self._macros.pop(name, None)

    def mem_run_macro(self, name):
        self.run_command(f"макрос {name}")

    def mem_notes(self):
        return list(self._notes)

    def mem_add_note(self, text):
        text = (text or "").strip()
        if len(text) < 3:
            return False, "Слишком коротко."
        self._notes.append(text)
        return True, "Сохранено."

    def mem_delete_note(self, index):
        if 0 <= index < len(self._notes):
            self._notes.pop(index)

    def mem_templates(self):
        return [{"name": n, "items": list(i), "builtin": b} for n, (i, b) in self._tpl.items()]

    def mem_add_template(self, name, items_text):
        items = [x.strip() for x in (items_text or "").split(";") if x.strip()]
        if not name.strip() or not items:
            return False, "Введи имя и команды через «;»."
        self._tpl[name.strip().lower()] = (items, False)
        return True, "Шаблон создан."

    def mem_delete_template(self, name):
        if name in self._tpl and not self._tpl[name][1]:
            del self._tpl[name]

    def mem_run_template(self, name):
        self.run_command(f"шаблон {name}")
