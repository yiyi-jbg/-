"""可见光（RGB）植被指数计算与像素颜色分类。

参考的常用可见光植被指数：
- ExG  = 2g - r - b（Woebbecke et al., 1995），r/g/b 为归一化色度坐标
- ExR  = 1.4r - g，ExG-ExR > 0 判为植被（Meyer & Neto, 2008）
- GLI  = (2G - R - B) / (2G + R + B)（Louhaichi et al., 2001）
- VARI = (G - R) / (G + R - B)（Gitelson et al., 2002）
- NGRDI = (G - R) / (G + R)
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

EPS = 1e-6


@dataclass
class ColorFeatures:
    rgb: np.ndarray  # H×W×3，float32，0~1
    hue: np.ndarray  # 色相，0~360 度
    sat: np.ndarray  # 饱和度，0~1
    val: np.ndarray  # 明度，0~1
    luma: np.ndarray  # 亮度，0~1
    exg: np.ndarray
    exgr: np.ndarray
    gli: np.ndarray
    vari: np.ndarray
    ngrdi: np.ndarray


@dataclass(frozen=True)
class ColorThresholds:
    """像素分类阈值。色相单位为度，饱和度/明度为 0~1。"""

    dark_val: float = 0.12  # 低于此明度视为阴影，不参与判断
    green_hue: tuple[float, float] = (65.0, 175.0)
    green_min_sat: float = 0.12
    green_min_val: float = 0.10
    yellow_hue: tuple[float, float] = (38.0, 65.0)
    yellow_min_sat: float = 0.40
    yellow_min_val: float = 0.40
    brown_hue: tuple[float, float] = (8.0, 38.0)
    brown_sat: tuple[float, float] = (0.50, 0.95)
    brown_val: tuple[float, float] = (0.15, 0.62)
    white_max_sat: float = 0.16
    white_min_val: float = 0.78
    pink_sat: tuple[float, float] = (0.12, 0.45)
    pink_min_val: float = 0.70
    red_fruit_min_sat: float = 0.50
    red_fruit_min_val: float = 0.30
    orange_fruit_hue: tuple[float, float] = (12.0, 38.0)
    orange_fruit_min_sat: float = 0.70
    orange_fruit_min_val: float = 0.62


@dataclass
class PixelClasses:
    """互斥的像素类别掩膜。"""

    dark: np.ndarray
    white: np.ndarray
    pink: np.ndarray
    fruit: np.ndarray
    green: np.ndarray
    yellow: np.ndarray
    brown: np.ndarray

    @property
    def flowers(self) -> np.ndarray:
        return self.white | self.pink


def rgb_to_hsv(rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    maxc = np.maximum(np.maximum(r, g), b)
    minc = np.minimum(np.minimum(r, g), b)
    delta = maxc - minc
    has_hue = delta > EPS
    safe = np.where(has_hue, delta, 1.0)
    sat = np.where(maxc > EPS, delta / np.where(maxc > EPS, maxc, 1.0), 0.0)

    is_r = has_hue & (maxc == r)
    is_g = has_hue & (maxc == g) & ~is_r
    is_b = has_hue & ~is_r & ~is_g
    hue = np.zeros_like(maxc)
    hue = np.where(is_r, np.mod((g - b) / safe, 6.0), hue)
    hue = np.where(is_g, (b - r) / safe + 2.0, hue)
    hue = np.where(is_b, (r - g) / safe + 4.0, hue)
    return (hue * 60.0).astype(np.float32), sat.astype(np.float32), maxc.astype(np.float32)


def compute_features(image: np.ndarray) -> ColorFeatures:
    """image: H×W×3 uint8 RGB。"""
    rgb = image.astype(np.float32) / 255.0
    red, green, blue = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    total = red + green + blue + EPS
    r, g, b = red / total, green / total, blue / total

    exg = 2 * g - r - b
    exr = 1.4 * r - g
    gli = (2 * green - red - blue) / (2 * green + red + blue + EPS)
    vari_den = green + red - blue
    vari = np.where(np.abs(vari_den) > 0.02, (green - red) / np.where(np.abs(vari_den) > 0.02, vari_den, 1.0), 0.0)
    ngrdi = (green - red) / (green + red + EPS)
    hue, sat, val = rgb_to_hsv(rgb)
    luma = 0.299 * red + 0.587 * green + 0.114 * blue

    return ColorFeatures(
        rgb=rgb,
        hue=hue,
        sat=sat,
        val=val,
        luma=luma.astype(np.float32),
        exg=exg.astype(np.float32),
        exgr=(exg - exr).astype(np.float32),
        gli=gli.astype(np.float32),
        vari=np.clip(vari, -1.0, 1.0).astype(np.float32),
        ngrdi=ngrdi.astype(np.float32),
    )


def _in_range(x: np.ndarray, bounds: tuple[float, float]) -> np.ndarray:
    return (x >= bounds[0]) & (x < bounds[1])


def classify_pixels(f: ColorFeatures, t: ColorThresholds | None = None) -> PixelClasses:
    """按优先级把像素划分为：阴影、白色、粉色、果实、绿叶、黄叶、褐色。"""
    t = t or ColorThresholds()
    h, s, v = f.hue, f.sat, f.val
    assigned = np.zeros(h.shape, dtype=bool)

    def take(mask: np.ndarray) -> np.ndarray:
        nonlocal assigned
        mask = mask & ~assigned
        assigned |= mask
        return mask

    dark = take(v < t.dark_val)
    white = take((s < t.white_max_sat) & (v >= t.white_min_val))
    pink = take(((h >= 280.0) | (h < 20.0)) & _in_range(s, t.pink_sat) & (v >= t.pink_min_val))
    red_fruit = ((h < 10.0) | (h >= 335.0)) & (s >= t.red_fruit_min_sat) & (v >= t.red_fruit_min_val)
    orange_fruit = (
        _in_range(h, t.orange_fruit_hue) & (s >= t.orange_fruit_min_sat) & (v >= t.orange_fruit_min_val)
    )
    fruit = take(red_fruit | orange_fruit)
    green = take(
        _in_range(h, t.green_hue) & (s >= t.green_min_sat) & (v >= t.green_min_val) & (f.exgr > -0.05)
    )
    yellow = take(_in_range(h, t.yellow_hue) & (s >= t.yellow_min_sat) & (v >= t.yellow_min_val))
    brown = take(_in_range(h, t.brown_hue) & _in_range(s, t.brown_sat) & _in_range(v, t.brown_val))
    return PixelClasses(dark=dark, white=white, pink=pink, fruit=fruit, green=green, yellow=yellow, brown=brown)
