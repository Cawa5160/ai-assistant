import sys
import threading
import unittest
from unittest import mock

for _m in ("pyautogui", "keyboard", "pyperclip"):
    sys.modules.setdefault(_m, mock.MagicMock())

import numpy as np


def _frame(ex=None, ey=None, w=320, h=180, color=(0, 255, 0)):
    f = np.full((h, w, 3), (90, 80, 70), dtype=np.uint8)
    f[::7, :] = (110, 100, 90)                           # фактура
    if ex is not None:
        yy, xx = np.ogrid[:h, :w]
        m = ((xx - ex) / 14.0) ** 2 + ((yy - ey) / 8.0) ** 2 <= 1
        f[m] = color
    return f


class VisionTests(unittest.TestCase):
    def test_color_tracker_follows_ellipse(self):
        import game_vision as gv
        t = gv.Tracker(_frame(200, 120), 200 / 319 * 1000, 120 / 179 * 1000)
        self.assertEqual(t.mode, "color")
        x, y, c = t.update(_frame(215, 128))
        self.assertAlmostEqual(x / 1000 * 319, 215, delta=3)
        self.assertAlmostEqual(y / 1000 * 179, 128, delta=3)

    def test_tracker_lost(self):
        import game_vision as gv
        t = gv.Tracker(_frame(200, 120), 200 / 319 * 1000, 120 / 179 * 1000)
        self.assertIsNone(t.update(_frame()))              # овал исчез
        self.assertEqual(t.misses, 1)

    def test_patch_tracker(self):
        import game_vision as gv
        rng = np.random.default_rng(1)
        base = rng.integers(40, 200, size=(180, 320, 3), dtype=np.uint8)
        base[...] = base.mean(-1, keepdims=True).astype(np.uint8)       # серый шум: без насыщенного цвета
        a = base.copy()
        b = np.roll(base, (4, 6), axis=(0, 1))
        t = gv.Tracker(a, 160 / 319 * 1000, 90 / 179 * 1000)
        self.assertEqual(t.mode, "patch")
        x, y, c = t.update(b)
        self.assertAlmostEqual(x / 1000 * 319, 166, delta=3)
        self.assertAlmostEqual(y / 1000 * 179, 94, delta=3)

    def test_world_moved_ignores_center(self):
        import game_vision as gv
        a = _frame()
        b = a.copy()
        b[60:140, 110:210] = 255                           # «анимация» вокруг персонажа
        self.assertFalse(gv.world_moved(a, b))
        c = np.roll(a, 20, axis=1)
        c[::5, :] = 0
        self.assertTrue(gv.world_moved(a, c))


class PilotMathTests(unittest.TestCase):
    def test_steer_modes(self):
        import game_pilot as gp
        self.assertEqual(gp.steer(3)[0], "walk")
        mode, dx = gp.steer(40)
        self.assertEqual(mode, "arc")
        self.assertGreater(dx, 0)
        mode, dx = gp.steer(-150)
        self.assertEqual(mode, "turn")
        self.assertLess(dx, 0)

    def test_near(self):
        import game_pilot as gp
        self.assertTrue(gp.near(520, 600))
        self.assertFalse(gp.near(800, 300))

    def test_parse_decision(self):
        import game_pilot as gp
        d = gp.parse_decision({"status": "continue", "target": {"name": "плита", "x": 700, "y": 650, "kind": "STEP_ON"}, "turn": 400})
        self.assertEqual(d["target"]["kind"], "step_on")
        self.assertEqual(d["turn"], 180)
        self.assertIsNone(gp.parse_decision({"target": {"x": 5000, "y": 1}})["target"])
        self.assertIsNone(gp.parse_decision("мусор")["target"])

    def test_follow_reaches_when_target_lost_near(self):
        import game_pilot as gp
        import game_vision as gv
        frames = [_frame(500, 110), _frame(500, 110), _frame(505, 105), _frame()]   # дальше овал пропадает
        # кадры: инициализация, затем ходьба; на финале цель исчезает возле персонажа
        seq = iter([_frame(500, 105)] + [_frame(500, 105)] * 2 + [_frame(512, 105)] * 3)
        ui = mock.Mock(abort_event=threading.Event())

        class FakeTracker:
            def __init__(self, *a):
                self.k = 0
                self.mode = "color"

            def update(self, frame):
                self.k += 1
                return (500, 520, 1.0) if self.k < 3 else (510, 600, 1.0)
        with mock.patch.object(gp.game_vision, "grab", side_effect=lambda *a, **k: next(seq, _frame())), \
                mock.patch.object(gp.game_vision, "Tracker", FakeTracker), \
                mock.patch.object(gp, "_do"), mock.patch.object(gp, "_hk"), \
                mock.patch.object(gp.game_bot, "_foreground", return_value=""):
            out = gp.follow({"name": "плита", "x": 500, "y": 300, "kind": "step_on"}, ui, "")
        self.assertEqual(out, "reached")


if __name__ == "__main__":
    unittest.main()
