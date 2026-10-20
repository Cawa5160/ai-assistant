"""
Глубина по картинке (Depth Anything V2 Small, ONNX, локально): бот «видит» стены и свободное место перед собой.

Модель (~100 МБ) скачивается один раз в папку данных. Запускается через onnxruntime (DirectML — видеокарта, иначе процессор).
Если библиотеки или модели нет — бот работает как раньше, без глубины.

Глубина относительная (больше = ближе). Стена перед персонажем: в полосе над ним значение высокое и почти не меняется
сверху вниз; земля — наоборот, плавно растёт вниз по кадру. Пороги — первое приближение; картинки отладки (game_debug) нужны для настройки.
"""
import math
import os
import time

import numpy as np

import config
from app_paths import DATA_DIR
from logger import log

MODEL_URL = "https://huggingface.co/onnx-community/depth-anything-v2-small/resolve/main/onnx/model.onnx"
MODEL_PATH = os.path.join(DATA_DIR, "models", "depth_anything_v2_small.onnx")
MODEL_MIN_BYTES = 50_000_000
DEBUG_DIR = os.path.join(DATA_DIR, "game_debug")
HALF_FOV = 52.0                                     # половина горизонтального угла обзора, градусов
SECTOR_ANGLES = (-60, -45, -30, -15, 0, 15, 30, 45, 60)
IN_H, IN_W = 252, 448                               # кратны 14 (требование модели)
MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)


def available() -> bool:
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def ensure_model(progress=None) -> bool:
    """Скачивает модель, если её ещё нет. progress(доля 0..1). -> True, если модель на месте."""
    if os.path.exists(MODEL_PATH) and os.path.getsize(MODEL_PATH) > MODEL_MIN_BYTES:
        return True
    try:
        import requests
        os.makedirs(os.path.dirname(MODEL_PATH), exist_ok=True)
        tmp = MODEL_PATH + ".part"
        with requests.get(MODEL_URL, stream=True, timeout=30) as r:
            r.raise_for_status()
            total = int(r.headers.get("content-length") or 0)
            got = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    got += len(chunk)
                    if progress and total:
                        progress(got / total)
        if os.path.getsize(tmp) < MODEL_MIN_BYTES:
            raise RuntimeError("файл модели слишком маленький")
        os.replace(tmp, MODEL_PATH)
        return True
    except Exception as e:
        log(f"Глубина: модель не скачалась ({type(e).__name__}: {e})")
        return False


class DepthModel:
    def __init__(self, session=None):
        if session is None:
            import onnxruntime as ort
            prov = [p for p in ("DmlExecutionProvider", "CUDAExecutionProvider", "CPUExecutionProvider")
                    if p in ort.get_available_providers()]
            session = ort.InferenceSession(MODEL_PATH, providers=prov or ["CPUExecutionProvider"])
            log(f"Глубина: модель загружена ({prov[0] if prov else 'CPU'})")
        self.sess = session
        inp = session.get_inputs()[0]
        self.name = inp.name
        shape = inp.shape
        self.h = shape[2] if isinstance(shape[2], int) else IN_H
        self.w = shape[3] if isinstance(shape[3], int) else IN_W

    def predict(self, frame):
        """frame — uint8 RGB (H, W, 3). -> float32 (h, w), 0..1, больше = ближе."""
        from PIL import Image
        img = Image.fromarray(frame).resize((self.w, self.h), Image.BILINEAR)
        x = (np.asarray(img, dtype=np.float32) / 255.0 - MEAN) / STD
        x = x.transpose(2, 0, 1)[None].astype(np.float32)
        out = self.sess.run(None, {self.name: x})[0]
        d = np.squeeze(out).astype(np.float32)
        lo, hi = np.percentile(d, 2), np.percentile(d, 98)
        return np.clip((d - lo) / max(hi - lo, 1e-6), 0.0, 1.0)


def sector_columns(angle: float, h_width: int, half_step: float = 7.5):
    """Колонки кадра для сектора направления angle (градусы, + вправо)."""
    t = math.tan(math.radians(HALF_FOV))

    def col(a):
        return int(round((0.5 + 0.5 * math.tan(math.radians(max(-HALF_FOV + 1, min(HALF_FOV - 1, a)))) / t) * (h_width - 1)))
    return col(angle - half_step), col(angle + half_step)


def wall_scores(d):
    """Для каждого сектора — «стена» 0..1 (0 — свободно). d — карта глубины (h, w), больше = ближе."""
    h, w = d.shape
    r0, r1 = int(h * 0.22), int(h * 0.50)
    band = d[r0:r1]
    # вертикальный градиент: земля растёт вниз, стена — почти постоянна
    step = max(2, int(h * 0.05))
    grad = (band[step:] - band[:-step])
    out = {}
    for a in SECTOR_ANGLES:
        c0, c1 = sector_columns(a, w)
        c1 = max(c1, c0 + 2)
        near = float(np.median(band[:, c0:c1]))
        g = float(np.median(grad[:, c0:c1])) if grad.size else 0.0
        closeness = min(max((near - 0.35) / 0.4, 0.0), 1.0)
        flat = 1.0 - min(max(g / 0.12, 0.0), 1.0)
        out[a] = round(closeness * flat, 3)
    return out


def center_blocked(scores: dict, thr: float = 0.6) -> bool:
    return max(scores.get(a, 0.0) for a in (-15, 0, 15)) > thr


def free_side(scores: dict):
    """Куда обходить: угол (±) сектора с минимальной «стеной» среди боковых; None, если везде закрыто."""
    cand = [(scores.get(a, 1.0) + 0.002 * abs(a), a) for a in (-60, -45, -30, 30, 45, 60)]
    best = min(cand)
    return best[1] if best[0] < 0.5 else None


def dump_debug(frame, d, scores, keep: int = 40):
    """Сохраняет картинку «кадр | глубина | оценки секторов» для настройки порогов."""
    try:
        from PIL import Image, ImageDraw
        os.makedirs(DEBUG_DIR, exist_ok=True)
        h, w = d.shape
        v = (d * 255).astype(np.uint8)
        rgb = np.stack([np.clip(v * 1.5, 0, 255), np.clip(255 - np.abs(v.astype(np.int16) - 128) * 2, 0, 255), 255 - v], -1).astype(np.uint8)
        left = Image.fromarray(frame).resize((w, h))
        img = Image.new("RGB", (w * 2, h))
        img.paste(left, (0, 0))
        img.paste(Image.fromarray(rgb), (w, 0))
        dr = ImageDraw.Draw(img)
        for a, sc in scores.items():
            c0, c1 = sector_columns(a, w)
            col = (255, 0, 0) if sc > 0.6 else (255, 200, 0) if sc > 0.3 else (0, 255, 0)
            dr.rectangle([w + c0, h - 14, w + c1, h - 2], outline=col)
            dr.text((w + c0 + 2, h - 14), f"{sc:.1f}", fill=col)
        dr.rectangle([w, int(h * 0.22), 2 * w - 1, int(h * 0.50)], outline=(255, 255, 255))
        img.save(os.path.join(DEBUG_DIR, f"{int(time.time() * 1000)}.jpg"), quality=80)
        files = sorted(os.listdir(DEBUG_DIR))
        for f in files[:-keep]:
            os.remove(os.path.join(DEBUG_DIR, f))
    except Exception as e:
        log(f"Глубина: картинка отладки не сохранилась ({type(e).__name__})")


def load(progress=None):
    """-> DepthModel или None (нет библиотеки/модели/отключено настройкой game_depth)."""
    if not config.get("game_depth"):
        return None
    if not available():
        log("Глубина: onnxruntime не установлен — работаю без неё.")
        return None
    if not ensure_model(progress):
        return None
    try:
        return DepthModel()
    except Exception as e:
        log(f"Глубина: модель не запустилась ({type(e).__name__}: {e})")
        return None
