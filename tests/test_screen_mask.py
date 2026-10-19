"""Тесты screen_mask без Windows."""
import os
import sys
import tempfile
import unittest
from unittest import mock

os.environ["APPDATA"] = tempfile.mkdtemp()
os.environ["LOCALAPPDATA"] = tempfile.mkdtemp()
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PIL import Image

import screen_mask


class MaskImage(unittest.TestCase):
    def test_paints_rect_with_padding_and_returns_copy(self):
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        out = screen_mask.mask_image(img, [(40, 40, 60, 50)], pad=4)
        self.assertIsNot(out, img)
        self.assertEqual(img.getpixel((50, 45)), (255, 255, 255))   # оригинал не тронут
        self.assertEqual(out.getpixel((50, 45)), (0, 0, 0))
        self.assertEqual(out.getpixel((37, 45)), (0, 0, 0))         # отступ
        self.assertEqual(out.getpixel((20, 20)), (255, 255, 255))

    def test_scale_and_out_of_bounds(self):
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        out = screen_mask.mask_image(img, [(100, 100, 120, 120), (-50, -50, 5, 5)], pad=0, scale_x=0.5, scale_y=0.5)
        self.assertEqual(out.getpixel((55, 55)), (0, 0, 0))
        self.assertEqual(out.getpixel((1, 1)), (0, 0, 0))

    def test_empty_rects(self):
        img = Image.new("RGB", (10, 10), (9, 9, 9))
        self.assertEqual(list(screen_mask.mask_image(img, []).getdata()), list(img.getdata()))


class Robustness(unittest.TestCase):
    def test_find_without_uiautomation(self):
        with mock.patch.dict(sys.modules, {"uiautomation": None}):
            self.assertEqual(screen_mask.find_password_rects(), [])

    def test_mask_screenshot_masks_found(self):
        img = Image.new("RGB", (100, 100), (255, 255, 255))
        with mock.patch.object(screen_mask, "find_password_rects", return_value=[(10, 10, 30, 20)]):
            out = screen_mask.mask_screenshot(img)
            self.assertEqual(out.getpixel((20, 15)), (0, 0, 0))
            out2 = screen_mask.mask_screenshot(img, (200, 200))      # снимок вдвое меньше экрана
            self.assertEqual(out2.getpixel((10, 8)), (0, 0, 0))

    def test_option_off_and_none(self):
        img = Image.new("RGB", (20, 20), (255, 255, 255))
        rects = mock.patch.object(screen_mask, "find_password_rects", return_value=[(0, 0, 20, 20)])
        with rects as f:
            with mock.patch("config.get", return_value=False):
                self.assertIs(screen_mask.mask_screenshot(img), img)
                f.assert_not_called()
            with mock.patch("config.get", return_value=None):          # None = включено
                self.assertEqual(screen_mask.mask_screenshot(img).getpixel((5, 5)), (0, 0, 0))


if __name__ == "__main__":
    unittest.main()
