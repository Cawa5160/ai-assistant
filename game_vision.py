"""
Быстрое «зрение» игрового бота без ИИ: слежение за целью по цвету или по кусочку картинки,
оценка «мир сдвинулся?». Работает на уменьшенных кадрах (numpy), ~10 раз в секунду.

Координаты везде — сетка 0..1000 по ширине и высоте кадра (как у ИИ).
"""
import numpy as np

SIDE = 1000.0


def to_array(img, width: int = 320):
    """PIL.Image -> uint8 RGB (H, W, 3), уменьшенный до width по ширине."""
    from PIL import Image
    img = img.convert("RGB")
    h = max(1, int(img.height * width / img.width))
    return np.asarray(img.resize((width, h), Image.BILINEAR), dtype=np.uint8)


def grab(width: int = 320):
    import pyautogui
    return to_array(pyautogui.screenshot(), width)


def _gray(a):
    return (a[..., 0] * 0.299 + a[..., 1] * 0.587 + a[..., 2] * 0.114).astype(np.float32)


def _box_sum(mask, k):
    """Суммы по окнам k×k (integral image). Результат размером (H-k+1, W-k+1)."""
    ii = np.pad(mask.astype(np.int32).cumsum(0).cumsum(1), ((1, 0), (1, 0)))
    return ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]


class Tracker:
    """Следит за целью. Режим 'color' — если в зоне цели есть насыщенный цвет (светящиеся плиты, значки),
    иначе 'patch' — сравнение с кусочком кадра. update(frame) -> (x, y, conf) или None, если цель потеряна."""

    def __init__(self, frame, x: float, y: float):
        h, w = frame.shape[:2]
        self.w, self.h = w, h
        self.px, self.py = x / SIDE * (w - 1), y / SIDE * (h - 1)
        self.r = max(5, int(0.04 * w))
        self.mode = "patch"
        self.sig = None
        patch = self._crop(frame, self.px, self.py)
        f = patch.astype(np.float32)
        mx, mn = f.max(-1), f.min(-1)
        sat = (mx - mn) / np.maximum(mx, 1.0)
        good = (sat > 0.45) & (mx > 120)
        if good.mean() >= 0.12:
            self.mode = "color"
            self.sig = f[good].mean(0)
        self.tpl = _gray(patch)
        self.misses = 0

    def shift(self, dx_units: float):
        """Сдвигает ожидаемое положение цели по горизонтали (после поворота камеры мир «уезжает» по экрану)."""
        self.px = min(max(self.px + dx_units / SIDE * (self.w - 1), 0.0), self.w - 1.0)

    def _crop(self, frame, cx, cy):
        r = self.r
        x0, y0 = int(round(cx)) - r, int(round(cy)) - r
        x0 = min(max(x0, 0), self.w - 2 * r - 1)
        y0 = min(max(y0, 0), self.h - 2 * r - 1)
        return frame[y0:y0 + 2 * r + 1, x0:x0 + 2 * r + 1]

    def _window(self, grow=0.28):
        sw, sh = int(self.w * grow), int(self.h * grow)
        x0, x1 = max(0, int(self.px) - sw), min(self.w, int(self.px) + sw)
        y0, y1 = max(0, int(self.py) - sh), min(self.h, int(self.py) + sh)
        return x0, y0, x1, y1

    def update(self, frame, max_jump: float = 260.0):
        """max_jump — на сколько единиц сетки цель может сместиться между кадрами (дальше — считаем, что схватили чужое)."""
        ox, oy = self.px, self.py
        res = self._update_color(frame) if self.mode == "color" else self._update_patch(frame)
        if res is not None:
            jump = max(abs(res[0] - ox) / (self.w - 1), abs(res[1] - oy) / (self.h - 1)) * SIDE
            if jump > max_jump:
                self.px, self.py = ox, oy
                res = None
        if res is None:
            self.misses += 1
            return None
        self.misses = 0
        return res[0] / (self.w - 1) * SIDE, res[1] / (self.h - 1) * SIDE, res[2]

    def _update_color(self, frame):
        x0, y0, x1, y1 = self._window(0.34)
        win = frame[y0:y1, x0:x1].astype(np.float32)
        mask = np.linalg.norm(win - self.sig, axis=-1) < 70
        k = 2 * self.r + 1
        if mask.shape[0] <= k or mask.shape[1] <= k or mask.sum() < 12:
            return None
        sums = _box_sum(mask, k)
        iy, ix = np.unravel_index(int(sums.argmax()), sums.shape)
        if sums[iy, ix] < 10:
            return None
        sub = mask[iy:iy + k, ix:ix + k]
        ys, xs = np.nonzero(sub)
        cx, cy = x0 + ix + xs.mean(), y0 + iy + ys.mean()
        self.px, self.py = float(cx), float(cy)
        return self.px, self.py, float(sums[iy, ix] / (k * k))

    def _update_patch(self, frame):
        x0, y0, x1, y1 = self._window(0.22)
        g = _gray(frame[y0:y1, x0:x1])
        t = self.tpl
        th, tw = t.shape
        if g.shape[0] < th or g.shape[1] < tw:
            return None
        from numpy.lib.stride_tricks import sliding_window_view
        view = sliding_window_view(g, (th, tw))[::1, ::1]
        sad = np.abs(view - t).mean(axis=(-1, -2))
        iy, ix = np.unravel_index(int(sad.argmin()), sad.shape)
        best = float(sad[iy, ix])
        conf = 1.0 - best / 80.0
        if conf < 0.45:
            return None
        cx, cy = x0 + ix + tw / 2, y0 + iy + th / 2
        self.px, self.py = float(cx), float(cy)
        new = _gray(self._crop(frame, cx, cy))
        self.tpl = 0.7 * t + 0.3 * new            # шаблон плавно «подстраивается» под масштаб при приближении
        return self.px, self.py, conf


def world_moved(prev, cur, threshold: float = 0.02) -> bool:
    """Сдвинулся ли мир между кадрами. Область вокруг персонажа игнорируется (анимация ходьбы не в счёт)."""
    if prev is None or cur is None or prev.shape != cur.shape:
        return True
    h, w = prev.shape[:2]
    d = np.abs(_gray(prev) - _gray(cur)) > 18
    d[int(h * 0.30):int(h * 0.80), int(w * 0.30):int(w * 0.70)] = False
    return bool(d.mean() > threshold)
