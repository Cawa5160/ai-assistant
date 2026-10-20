import sys
import unittest
from unittest import mock

for _m in ("pyautogui", "keyboard", "pyperclip"):
    sys.modules.setdefault(_m, mock.MagicMock())


class GameBotTests(unittest.TestCase):
    def setUp(self):
        import plan_validator
        p = mock.patch.object(plan_validator, "_screen_bounds", return_value=(1920, 1080))
        p.start()
        self.addCleanup(p.stop)

    def test_play_command(self):
        import game_bot as g
        for t in ("играй в роблокс тайкун", "Поиграй в майнкрафт", "грай у roblox", "play roblox tycoon"):
            self.assertTrue(g.is_play_command(t), t)
        for t in ("играй музыку", "включи видео", "открой роблокс", "play some music"):
            self.assertFalse(g.is_play_command(t), t)

    def test_sanitize_filters_and_limits(self):
        import game_bot as g
        raw = {"status": "continue", "intent": "иду", "memory": "m",
               "actions": [{"action": "hold_key", "args": {"key": "w", "seconds": 1.5}},
                           {"action": "click", "args": {"x": 500, "y": 500}},
                           {"action": "wait", "args": {"seconds": 9}},
                           {"action": "type_text", "args": {"text": "hi"}},
                           {"action": "press_key", "args": {"key": "space"}}]}
        d = g.sanitize(raw, 1920, 1080)
        self.assertEqual([s["action"] for s in d["steps"]], ["hold_key", "click", "wait"])   # type_text отсёк остальное
        self.assertEqual(d["steps"][2]["args"]["seconds"], 3)
        self.assertEqual(d["steps"][1]["args"]["x"], 960)

    def test_look_and_press_key(self):
        import game_bot as g
        d = g.sanitize({"actions": [{"action": "look", "args": {"dx": -300, "dy": 0}},
                                    {"action": "press_key", "args": {"key": "space"}},
                                    {"action": "look", "args": {"dx": 5000, "dy": 0}}]}, 1920, 1080)
        self.assertEqual([s["action"] for s in d["steps"]], ["look", "hold_key"])
        self.assertEqual(d["steps"][1]["args"], {"key": "space", "seconds": 0.1})

    def test_go_to_expands(self):
        import game_bot as g
        d = g.sanitize({"actions": [{"action": "go_to", "args": {"x": 900, "y": 500, "seconds": 2}}]}, 1920, 1080)
        self.assertEqual([x["action"] for x in d["steps"]], ["look", "hold_key"])
        self.assertEqual(d["steps"][0]["args"]["dx"], 124)   # atan2(400, 70) ≈ 80° × 0.7 / 0.45°/px
        self.assertEqual(d["steps"][1]["args"], {"key": "w", "seconds": 2.0})
        d = g.sanitize({"actions": [{"action": "go_to", "args": {"x": 500, "y": 100, "seconds": 9}}]}, 1920, 1080)
        self.assertEqual([x["action"] for x in d["steps"]], ["hold_key"])       # почти прямо — без поворота
        self.assertEqual(d["steps"][0]["args"]["seconds"], 4.0)

    def test_look_alone_gets_walk(self):
        import game_bot as g
        d = g.sanitize({"actions": [{"action": "look", "args": {"dx": 200, "dy": 0}}]}, 1920, 1080)
        self.assertEqual([x["action"] for x in d["steps"]], ["look", "hold_key"])

    def test_tycoon_detect(self):
        import game_bot as g
        self.assertTrue(g.is_tycoon("играй в роблокс тайкун"))
        self.assertFalse(g.is_tycoon("играй в шахматы"))

    def test_forbidden_keys_and_click_zone(self):
        import game_bot as g
        d = g.sanitize({"actions": [{"action": "press_key", "args": {"key": "esc"}},
                                    {"action": "press_key", "args": {"key": "e"}},
                                    {"action": "click", "args": {"x": 50, "y": 50}},
                                    {"action": "click", "args": {"x": 500, "y": 500}}]}, 1920, 1080, tycoon=True)
        self.assertEqual([x["action"] for x in d["steps"]], ["hold_key", "click"])
        self.assertEqual(d["steps"][0]["args"]["key"], "e")
        d2 = g.sanitize({"actions": [{"action": "click", "args": {"x": 50, "y": 50}}]}, 1920, 1080, tycoon=False)
        self.assertEqual(len(d2["steps"]), 1)

    def test_go_to_strafe(self):
        import game_bot as g
        d = g.sanitize({"actions": [{"action": "go_to", "args": {"x": 900, "y": 500, "seconds": 1, "dir": "d"}}]}, 1920, 1080)
        self.assertEqual([(x["action"], x["args"].get("key")) for x in d["steps"]], [("hold_key", "d")])

    def test_turn_angle_behind(self):
        import game_bot as g
        self.assertAlmostEqual(g.turn_angle(500, 100), 0, places=3)
        self.assertGreater(g.turn_angle(750, 790), 120)         # цель справа-внизу — за спиной
        self.assertLess(g.turn_angle(200, 300), 0)

    def test_go_to_arc_when_target_ahead(self):
        import game_bot as g
        d = g.sanitize({"actions": [{"action": "go_to", "args": {"x": 600, "y": 300}}]}, 1920, 1080)
        self.assertEqual([x["action"] for x in d["steps"]], ["move_turn"])
        self.assertEqual(d["steps"][0]["args"]["key"], "w")
        self.assertGreater(d["steps"][0]["args"]["seconds"], 1)               # секунды посчитаны сами

    def test_target_parse(self):
        import game_bot as g
        d = g.sanitize({"target": {"name": "плита", "x": 700, "y": 600, "state": "REACHED"}}, 100, 100)
        self.assertEqual(d["target"], {"name": "плита", "x": 700, "y": 600, "state": "reached"})
        self.assertIsNone(g.sanitize({"target": "мусор"}, 100, 100)["target"])

    def test_target_lock_rejects_switch(self):
        import threading
        import game_bot as g
        calls = []

        def fake(goal, memory, history, note, on_wait=None, cancel=None, target=None):
            calls.append(target and target["name"])
            names = ["A", "B", "A", "A"]
            i = len(calls) - 1
            if i >= len(names):
                return {"status": "done", "intent": "", "memory": "", "message": "ок", "steps": [], "target": None}, "x"
            return {"status": "continue", "intent": "i", "memory": "", "message": "", "steps": [],
                    "target": {"name": names[i], "x": 1, "y": 1, "state": "approaching"}}, "x"
        ui = mock.Mock(abort_event=threading.Event())
        orig = g.config.get
        with mock.patch.object(g.config, "get", side_effect=lambda k, *a: "llm" if k == "game_engine" else orig(k, *a)), \
                mock.patch.object(g, "decide", side_effect=fake), \
                mock.patch.object(g.agent_loop, "_wait_if_paused", return_value=True), \
                mock.patch.object(g.agent_loop, "_sleep", return_value=True), \
                mock.patch.object(g.screen_utils, "take_screenshot_image", return_value=None):
            self.assertEqual(g.run_game("играй", ui, "")[0], True)
        self.assertEqual(calls[:3], [None, "A", "A"])         # «B» отклонена, держимся «A»

    def test_move_turn_validation(self):
        import plan_validator as pv
        self.assertTrue(pv.validate_step({"action": "move_turn", "args": {"dx": 100, "seconds": 1.5, "key": "w"}})[0])
        self.assertFalse(pv.validate_step({"action": "move_turn", "args": {"dx": 9999, "seconds": 1, "key": "w"}})[0])

    def test_escape_steps(self):
        import game_bot as g
        import plan_validator as pv
        for level in (1, 2, 3, 4, 7):
            for side in (1, -1):
                steps = g.escape_steps(level, side)
                self.assertTrue(steps)
                for st in steps:
                    self.assertTrue(pv.validate_step(st)[0], st)
        self.assertEqual(g.escape_steps(1, 1)[0]["action"], "hop_forward")
        self.assertEqual(g.escape_steps(2, 1)[1]["args"]["key"], "d")
        self.assertEqual(g.escape_steps(2, -1)[1]["args"]["key"], "a")

    def test_mask_center_hides_character(self):
        import game_bot as g
        from PIL import Image
        a = Image.new("L", (100, 100), 10)
        b = a.copy()
        b.paste(200, (45, 45, 55, 55))                     # «анимация» в центре
        self.assertEqual(list(g.mask_center(a).getdata()), list(g.mask_center(b).getdata()))

    def test_sanitize_bad_input(self):
        import game_bot as g
        self.assertEqual(g.sanitize("мусор", 100, 100)["steps"], [])
        d = g.sanitize({"actions": [{"action": "hold_key", "args": {"key": "w", "seconds": 99}}]}, 100, 100)
        self.assertEqual(d["steps"], [])

    def test_hold_key_validation(self):
        import plan_validator as pv
        self.assertTrue(pv.validate_step({"action": "hold_key", "args": {"key": "w", "seconds": 1}})[0])
        self.assertFalse(pv.validate_step({"action": "hold_key", "args": {"key": "w", "seconds": 0}})[0])
        self.assertFalse(pv.validate_step({"action": "hold_key", "args": {"key": "ё", "seconds": 1}})[0])

    def test_run_game_stops_when_ai_says_done(self):
        import threading
        import game_bot as g
        ui = mock.Mock(abort_event=threading.Event())
        done = {"status": "done", "intent": "", "memory": "", "message": "ок", "steps": []}
        orig = g.config.get
        with mock.patch.object(g.config, "get", side_effect=lambda k, *a: "llm" if k == "game_engine" else orig(k, *a)), \
                mock.patch.object(g, "decide", return_value=(done, "x")), \
                mock.patch.object(g.agent_loop, "_wait_if_paused", return_value=True):
            self.assertEqual(g.run_game("играй", ui, ""), (True, "ок"))


if __name__ == "__main__":
    unittest.main()
