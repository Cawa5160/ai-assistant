import sys
import unittest
from unittest import mock

for _m in ("pyautogui", "keyboard", "pyperclip"):
    sys.modules.setdefault(_m, mock.MagicMock())

import numpy as np


def _ground(h=252, w=448):
    d = np.linspace(0.05, 0.95, h, dtype=np.float32)[:, None].repeat(w, 1)
    return d


class DepthTests(unittest.TestCase):
    def test_ground_is_free(self):
        import game_depth as gd
        s = gd.wall_scores(_ground())
        self.assertTrue(all(v < 0.3 for v in s.values()), s)
        self.assertFalse(gd.center_blocked(s))

    def test_wall_ahead_blocks_center_and_suggests_side(self):
        import game_depth as gd
        d = _ground()
        h, w = d.shape
        d[:int(h * 0.7), int(w * 0.30):int(w * 0.70)] = 0.9          # плоская близкая «стена» по центру
        s = gd.wall_scores(d)
        self.assertTrue(gd.center_blocked(s), s)
        side = gd.free_side(s)
        self.assertIn(side, (-60, -45, -30, 30, 45, 60))
        self.assertLess(s[side], 0.3)

    def test_sector_columns_order(self):
        import game_depth as gd
        a = gd.sector_columns(-30, 448)
        b = gd.sector_columns(30, 448)
        self.assertLess(a[1], b[0] + 1)
        self.assertTrue(0 <= a[0] < a[1] <= 447)

    def test_predict_with_fake_session(self):
        import game_depth as gd

        class Inp:
            name = "pixel_values"
            shape = ["batch", 3, "h", "w"]

        class Sess:
            def get_inputs(self):
                return [Inp()]

            def run(self, _o, feeds):
                x = feeds["pixel_values"]
                assert x.shape == (1, 3, gd.IN_H, gd.IN_W) and x.dtype == np.float32
                return [np.random.rand(1, gd.IN_H, gd.IN_W).astype(np.float32)]
        m = gd.DepthModel(Sess())
        d = m.predict(np.zeros((180, 320, 3), dtype=np.uint8))
        self.assertEqual(d.shape, (gd.IN_H, gd.IN_W))
        self.assertTrue(0.0 <= d.min() and d.max() <= 1.0)

    def test_load_disabled_or_missing(self):
        import game_depth as gd
        with mock.patch.object(gd.config, "get", return_value=False):
            self.assertIsNone(gd.load())
        with mock.patch.object(gd.config, "get", return_value=True), \
                mock.patch.object(gd, "available", return_value=False):
            self.assertIsNone(gd.load())


if __name__ == "__main__":
    unittest.main()
