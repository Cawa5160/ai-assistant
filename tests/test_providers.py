import unittest
from unittest import mock


class OpenRouterTests(unittest.TestCase):
    def test_to_messages_text_and_image(self):
        import openrouter
        payload = {"systemInstruction": {"parts": [{"text": "sys"}]},
                   "contents": [{"role": "user", "parts": [{"text": "привет"}]},
                                {"role": "model", "parts": [{"text": "здравствуй"}]},
                                {"role": "user", "parts": [{"text": "что тут?"}, {"inline_data": {"mime_type": "image/png", "data": "QUJD"}}]}]}
        m = openrouter.to_messages(payload)
        self.assertEqual(m[0], {"role": "system", "content": "sys"})
        self.assertEqual(m[1]["content"], "привет")
        self.assertEqual(m[2]["role"], "assistant")
        self.assertEqual(m[3]["content"][1]["image_url"]["url"], "data:image/png;base64,QUJD")

    def test_audio_rejected(self):
        import openrouter
        with self.assertRaises(RuntimeError):
            openrouter.to_messages({"contents": [{"role": "user", "parts": [{"inline_data": {"mime_type": "audio/ogg", "data": "x"}}]}]})

    def test_generate_returns_gemini_shape(self):
        import openrouter
        resp = mock.Mock(status_code=200, text="")
        resp.json.return_value = {"choices": [{"message": {"content": "ответ"}}]}
        with mock.patch.object(openrouter, "get_key", return_value="k"), \
                mock.patch.object(openrouter, "pick_model", return_value="m:free"), \
                mock.patch("openrouter.requests.post", return_value=resp):
            data = openrouter.generate({"contents": [{"role": "user", "parts": [{"text": "q"}]}]})
        self.assertEqual(data["candidates"][0]["content"]["parts"][0]["text"], "ответ")

    def test_ai_brain_falls_back(self):
        import ai_brain
        import openrouter
        fake = {"candidates": [{"content": {"parts": [{"text": "из OpenRouter"}]}}]}
        with mock.patch.object(ai_brain, "_generate_gemini", side_effect=ai_brain.LimitError("лимит")), \
                mock.patch.object(openrouter, "enabled", return_value=True), \
                mock.patch.object(openrouter, "generate", return_value=fake):
            self.assertEqual(ai_brain.generate({"contents": []}), fake)

    def test_ai_brain_no_fallback_when_disabled(self):
        import ai_brain
        import openrouter
        with mock.patch.object(ai_brain, "_generate_gemini", side_effect=ai_brain.LimitError("лимит")), \
                mock.patch.object(openrouter, "enabled", return_value=False):
            with self.assertRaises(ai_brain.LimitError):
                ai_brain.generate({"contents": []})


class UpdaterTests(unittest.TestCase):
    def test_pick_asset_prefers_setup_exe(self):
        import updater
        rel = {"assets": [{"name": "AI_Assistant.exe"}, {"name": "AI_Assistant_Setup.exe"}, {"name": "x.bat"}]}
        self.assertEqual(updater.pick_asset(rel)["name"], "AI_Assistant_Setup.exe")
        self.assertIsNone(updater.pick_asset({"assets": [{"name": "readme.txt"}]}))

    def test_check_new_and_current(self):
        import updater
        with mock.patch.object(updater, "latest", return_value={"tag": "v99.0.0", "url": "u", "assets": []}):
            self.assertEqual(updater.check()[0], "new")
        with mock.patch.object(updater, "latest", return_value={"tag": "v0.0.1", "url": "u", "assets": []}):
            self.assertEqual(updater.check()[0], "current")
        with mock.patch.object(updater, "latest", return_value=None):
            self.assertEqual(updater.check()[0], "unknown")


if __name__ == "__main__":
    unittest.main()
