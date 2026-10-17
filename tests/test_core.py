"""Автотесты без Windows: проверка планов, быстрых команд, приватности, wake word, шаблонов.
Запуск:  python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest

os.environ["APPDATA"] = tempfile.mkdtemp()          # данные тестов не трогают настоящие
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import plan_validator as pv
import privacy
import quick_commands as qc
import wakeword


class PlanValidator(unittest.TestCase):
    def test_valid_step(self):
        self.assertTrue(pv.validate_step({"action": "open_url", "args": {"url": "https://example.com"}})[0])

    def test_unknown_action_rejected(self):
        self.assertFalse(pv.validate_step({"action": "format_disk", "args": {}})[0])

    def test_extra_args_rejected(self):
        ok, _ = pv.validate_step({"action": "wait", "args": {"seconds": 1, "evil": "x"}})
        self.assertFalse(ok)

    def test_bool_is_not_number(self):
        self.assertFalse(pv.validate_step({"action": "click", "args": {"x": True, "y": 5}})[0])

    def test_missing_arg_rejected(self):
        self.assertFalse(pv.validate_step({"action": "click", "args": {"x": 5}})[0])

    def test_non_dict_rejected(self):
        self.assertFalse(pv.validate_step("click")[0])
        self.assertEqual(pv.validate_plan("x")[0], [])
        self.assertEqual(pv.validate_plan({"steps": "x"})[0], [])

    def test_file_ops_are_high_risk(self):
        self.assertEqual(pv.classify_risk({"action": "create_file", "args": {"path": "a.txt"}}), "high")
        self.assertEqual(pv.classify_risk({"action": "move_file", "args": {"src": "a", "dst": "b"}}), "high")

    def test_dangerous_words_are_high_risk(self):
        step = {"action": "type_text", "args": {"text": "del /f /q C:\\*"}}
        self.assertIn(pv.classify_risk(step), ("high", "normal"))   # не должен быть low
        self.assertNotEqual(pv.classify_risk(step), "low")

    def test_highest_risk(self):
        low = {"action": "open_url", "args": {"url": "https://a.com"}}
        high = {"action": "create_file", "args": {"path": "a"}}
        self.assertEqual(pv.highest_risk([low, high]), "high")
        self.assertEqual(pv.highest_risk([low]), "low")


class QuickCommands(unittest.TestCase):
    def test_open_notepad(self):
        plan = qc.try_quick_plan("открой блокнот")
        self.assertIsNotNone(plan)
        self.assertEqual(plan["steps"][0]["action"], "open_app")

    def test_polite_and_typo(self):
        self.assertIsNotNone(qc.try_quick_plan("Открой блокнот пожалуйста"))

    def test_complex_goes_to_ai(self):
        self.assertIsNone(qc.try_quick_plan("открой браузер и включи что-то интересное"))

    def test_all_quick_steps_pass_validator(self):
        for cmd in ["открой блокнот", "громче", "тише", "открой ютуб"]:
            plan = qc.try_quick_plan(cmd)
            if plan:
                for step in plan["steps"]:
                    self.assertTrue(pv.validate_step(step)[0], (cmd, step))

    def test_empty(self):
        self.assertIsNone(qc.try_quick_plan(""))
        self.assertIsNone(qc.try_quick_plan(None))


class Privacy(unittest.TestCase):
    def test_password_redacted(self):
        out = privacy.redact("мой пароль: hunter2secret")
        self.assertNotIn("hunter2secret", out)

    def test_plain_text_untouched(self):
        self.assertEqual(privacy.redact("открой блокнот"), "открой блокнот")

    def test_empty(self):
        self.assertEqual(privacy.redact(""), "")


class WakeWord(unittest.TestCase):
    def test_no_wake(self):
        self.assertEqual(wakeword.match_wake("просто разговор"), (False, ""))

    def test_empty(self):
        self.assertEqual(wakeword.match_wake(""), (False, ""))
        self.assertEqual(wakeword.match_wake(None), (False, ""))

    def test_wake_with_command(self):
        heard, rest = wakeword.match_wake("эй ассистент открой блокнот")
        self.assertTrue(heard)
        self.assertIn("блокнот", rest)


class KeyNotInUrl(unittest.TestCase):
    def test_key_in_header(self):
        import ai_brain
        self.assertEqual(ai_brain._key_header("K"), {"x-goog-api-key": "K"})
        self.assertNotIn("key=", ai_brain._gemini_url())


class ChatPayload(unittest.TestCase):
    def test_roles_and_system(self):
        import chat
        p = chat.build_payload([{"role": "user", "text": "привет"}, {"role": "assistant", "text": "здравствуй"},
                                {"role": "user", "text": "как дела?"}])
        self.assertEqual([c["role"] for c in p["contents"]], ["user", "model", "user"])
        self.assertIn("systemInstruction", p)

    def test_limits_and_last_kept(self):
        import chat
        turns = [{"role": "user" if i % 2 == 0 else "assistant", "text": "x" * 900} for i in range(30)] + \
                [{"role": "user", "text": "последний вопрос"}]
        out = chat.trim_turns(turns)
        self.assertLessEqual(len(out), chat.MAX_TURNS)
        self.assertEqual(out[-1]["text"], "последний вопрос")
        self.assertLessEqual(sum(len(t["text"]) for t in out), chat.MAX_CHARS + 900)

    def test_secrets_hidden(self):
        import chat
        text = "мой пароль: qwerty123456 запомни"
        out = chat.trim_turns([{"role": "user", "text": text}])[0]["text"]
        self.assertNotEqual(out, text)

    def test_needs_user_question(self):
        import chat
        with self.assertRaises(ValueError):
            chat.ask([{"role": "assistant", "text": "привет"}])


if __name__ == "__main__":
    unittest.main()
