"""果树生长状态与健康状态分析引擎（适用于无人机可见光 RGB 照片）。

处理流程：
1. 读取照片并缩放到分析尺寸（JPEG 使用 DCT 降采样，大疆 2000 万像素照片也能快速处理）；
2. 计算可见光植被指数（ExG、GLI、VARI 等），按颜色把像素分为绿叶、黄叶、褐色、花、果、阴影等；
3. 用形态学闭运算 + 填洞得到树冠区域，统计覆盖度、叶片密度、冠幅、病斑等指标；
4. 综合指标与拍摄时间推断生长阶段与长势，按扣分规则计算健康评分并给出问题与建议；
5. 生成标注图：树冠轮廓（青色）、黄化（黄色）、褐变坏死（红色）、疑似白粉（紫色）。

说明：这是基于颜色与形态规则的可解释分析方法，无需训练数据即可使用；
如需识别具体病害种类，可接入自训练的 ONNX 模型（见 model_adapter.py）。
"""

from __future__ import annotations

import io
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
from PIL import Image, ImageDraw, ImageOps
from scipy import ndimage as ndi
from scipy.spatial import ConvexHull, QhullError

from app.analysis import knowledge as kb
from app.analysis.color import ColorFeatures, ColorThresholds, PixelClasses, classify_pixels, compute_features
from app.analysis.result import (
    AnalysisResult,
    Crown,
    GrowthStatus,
    HealthStatus,
    ImageQuality,
    Issue,
    ModelPrediction,
    VegetationIndices,
)

if TYPE_CHECKING:
    from app.analysis.model_adapter import OnnxClassifier

logger = logging.getLogger(__name__)

ImageSource = str | Path | bytes | Image.Image

# 扣分表：(指标值, 扣分) 断点，按线性插值计算扣分。
# 第 2、3、4 个断点分别对应“轻微 / 中等 / 严重”。扣分经过校准：
# 单项“中等”问题使评分降到 85 以下（轻度异常），单项“严重”问题降到 70 以下（中度异常）。
PENALTY_TABLES: dict[str, list[tuple[float, float]]] = {
    "chlorosis": [(0.03, 0), (0.05, 5), (0.12, 15.1), (0.25, 30.1), (0.50, 45)],
    "necrosis": [(0.015, 0), (0.025, 5), (0.06, 15.1), (0.15, 30.1), (0.35, 50)],
    "leaf_spots": [(1.5, 0), (2.0, 4), (4.0, 15.1), (8.0, 30.1), (20.0, 40)],
    "white_patches": [(0.04, 0), (0.05, 4), (0.12, 15.1), (0.25, 30.1), (0.50, 40)],
    # 以下两项为“越小越差”的指标
    "sparse_canopy": [(0.65, 0), (0.60, 4), (0.50, 15.1), (0.35, 30.1), (0.20, 40)],
    "low_greenness": [(0.22, 0), (0.20, 4), (0.15, 15.1), (0.10, 30.1), (0.05, 40)],
}
LEVEL_SCORE_CAP = {"mild": 84.9, "moderate": 69.9, "severe": 49.9}
DECREASING_INDICATORS = {"sparse_canopy", "low_greenness"}
SEVERITY_ORDER = {"low": 1, "medium": 2, "high": 3}
LEVEL_ORDER = ["healthy", "mild", "moderate", "severe"]
MODEL_PENALTY = {"low": 8.0, "medium": 18.0, "high": 30.0}
# 未检测到叶冠但属于正常物候的情况：(结论, 管理建议)
SEASONAL_NO_CANOPY: dict[str, tuple[str, tuple[str, ...]]] = {
    "dormant": (
        "果树处于休眠期，叶片尚未展开，暂不评估叶片健康状态。",
        ("休眠期做好清园、冬季修剪和树干涂白", "可降低高度巡检枝干，排查腐烂病、干腐病等枝干病害"),
    ),
    "leaf_fall": (
        "果树处于落叶期，叶片大部分已脱落，暂不评估叶片健康状态。",
        ("及时清扫落叶并集中处理，减少越冬病虫源", "落叶后施基肥、灌封冻水，为来年恢复树势"),
    ),
}


class UnsupportedImageError(ValueError):
    """无法分析的图像（如单波段/多光谱影像）。"""


@dataclass(frozen=True)
class AnalyzerConfig:
    max_side: int = 1280  # 分析尺寸（长边像素）
    thresholds: ColorThresholds = field(default_factory=ColorThresholds)
    close_radius_frac: float = 0.008  # 闭运算半径占短边的比例
    max_hole_frac: float = 0.004  # 树冠内允许填补的最大空洞（占画面比例）
    min_crown_frac: float = 0.002  # 最小树冠面积（占画面比例）
    min_fragment_frac: float = 0.0002  # 小于此面积的零碎植被视为噪声
    group_radius_frac: float = 0.025  # 距离小于约 2 倍该半径的叶丛碎片归为同一棵树（稀疏树冠会碎成多块）
    min_crown_green_frac: float = 0.2  # 树冠区域内绿叶的最小占比
    foliage_window_frac: float = 0.025  # 判定“褐色像素是否处于叶丛中”的邻域窗口
    min_foliage_neighbors: float = 0.25
    min_canopy_coverage: float = 0.02  # 树冠占画面比例低于此值时不做健康评估
    min_leaf_pixels: int = 400
    min_spot_px: int = 4
    max_spot_frac: float = 0.0003  # 单个病斑的最大面积（占画面比例），更大的按成片褐变/黄化统计
    blur_threshold: float = 50.0  # 归一化拉普拉斯方差低于此值视为模糊
    min_resolution: int = 600


@dataclass
class AnalysisContext:
    """来自无人机元数据和巡检任务的辅助信息（均可为空）。"""

    captured_at: datetime | None = None
    latitude: float | None = None
    gsd_cm: float | None = None  # 原图地面分辨率（厘米/像素），仅正射照片有效
    fruit_type: str | None = None


@dataclass
class AnalysisOutput:
    result: AnalysisResult
    overlay: Image.Image


@dataclass
class _Segmentation:
    region: np.ndarray  # 树冠区域（叶片实际覆盖范围）
    labels: np.ndarray  # 树冠连通域标签
    count: int
    envelope: np.ndarray  # 树冠外轮廓（各树冠凸包），用于计算叶片密度和投影面积
    hull_areas: np.ndarray  # 各树冠凸包面积（下标为标签号）
    green: np.ndarray
    yellow: np.ndarray
    necrosis: np.ndarray
    flowers: np.ndarray
    fruit: np.ndarray
    white: np.ndarray
    dark: np.ndarray


# ---------------------------------------------------------------------------
# 图像读取
# ---------------------------------------------------------------------------


def load_image(source: ImageSource, max_side: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    """读取照片，返回 (RGB uint8 数组, 原图像素/分析像素 的缩放倍数, 原图尺寸)。"""
    if isinstance(source, Image.Image):
        return _prepare_image(source, max_side)
    stream = io.BytesIO(source) if isinstance(source, (bytes, bytearray)) else source
    with Image.open(stream) as img:
        return _prepare_image(img, max_side)


def _prepare_image(img: Image.Image, max_side: int) -> tuple[np.ndarray, float, tuple[int, int]]:
    orig_w, orig_h = img.size
    long_side = max(orig_w, orig_h)
    if img.format == "JPEG" and long_side > max_side:
        ratio = max_side / long_side
        img.draft("RGB", (max(1, int(orig_w * ratio)), max(1, int(orig_h * ratio))))

    if img.mode in ("1", "L", "LA", "I", "F") or img.mode.startswith("I;"):
        raise UnsupportedImageError("暂不支持单波段（灰度/多光谱）影像，请上传可见光 RGB 照片")

    img = ImageOps.exif_transpose(img).convert("RGB")
    if max(img.size) > max_side:
        img.thumbnail((max_side, max_side), Image.Resampling.BILINEAR)
    scale = long_side / max(img.size)
    return np.asarray(img, dtype=np.uint8).copy(), scale, (orig_w, orig_h)


# ---------------------------------------------------------------------------
# 形态学工具（基于距离变换，速度与结构元大小无关）
# ---------------------------------------------------------------------------


def _dilate(mask: np.ndarray, radius: float) -> np.ndarray:
    if not mask.any():
        return mask.copy()
    return ndi.distance_transform_edt(~mask) <= radius


def _erode(mask: np.ndarray, radius: float) -> np.ndarray:
    if mask.all():
        return mask.copy()
    return ndi.distance_transform_edt(mask) > radius


def _closing(mask: np.ndarray, radius: float) -> np.ndarray:
    return _erode(_dilate(mask, radius), radius)


def _closing_coarse(mask: np.ndarray, radius: float, factor: int = 2) -> np.ndarray:
    """在降采样的掩膜上做大半径闭运算（只用于分组，精度要求低、速度快）。"""
    h, w = mask.shape
    ph, pw = -h % factor, -w % factor
    padded = np.pad(mask, ((0, ph), (0, pw)))
    small = padded.reshape(padded.shape[0] // factor, factor, padded.shape[1] // factor, factor).any(axis=(1, 3))
    closed = _closing(small, radius / factor)
    return np.repeat(np.repeat(closed, factor, axis=0), factor, axis=1)[:h, :w] | mask


def _remove_small(mask: np.ndarray, min_px: int) -> np.ndarray:
    labels, n = ndi.label(mask)
    if n == 0:
        return mask
    sizes = np.bincount(labels.ravel(), minlength=n + 1)
    keep = sizes >= min_px
    keep[0] = False
    return keep[labels]


def _fill_small_holes(mask: np.ndarray, max_hole_px: int) -> np.ndarray:
    filled = ndi.binary_fill_holes(mask)
    holes = filled & ~mask
    if not holes.any():
        return filled
    hole_labels, n = ndi.label(holes)
    sizes = np.bincount(hole_labels.ravel(), minlength=n + 1)
    small = sizes <= max_hole_px
    small[0] = False
    return mask | small[hole_labels]


def _ratio(numerator: float, denominator: float) -> float:
    return float(numerator) / float(denominator) if denominator > 0 else 0.0


def _r(value: float | None, digits: int = 4) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return round(float(value), digits)


# ---------------------------------------------------------------------------
# 分析器
# ---------------------------------------------------------------------------


class OrchardAnalyzer:
    name = "rgb-vegetation-index"
    version = "1.0.0"

    def __init__(self, config: AnalyzerConfig | None = None, model: OnnxClassifier | None = None) -> None:
        self.config = config or AnalyzerConfig()
        self.model = model

    @property
    def description(self) -> str:
        base = f"{self.name} v{self.version}"
        return f"{base} + {self.model.name}" if self.model else base

    def analyze(self, source: ImageSource, context: AnalysisContext | None = None) -> AnalysisOutput:
        ctx = context or AnalysisContext()
        cfg = self.config
        image, scale, orig_size = load_image(source, cfg.max_side)
        h, w = image.shape[:2]

        features = compute_features(image)
        pixels = classify_pixels(features, cfg.thresholds)
        quality = self._assess_quality(features, orig_size)
        seg = self._segment(pixels, h, w)

        # —— 基础指标 ——
        total_px = h * w
        region_px = int(seg.region.sum())
        green_n = int(seg.green.sum())
        yellow_n = int(seg.yellow.sum())
        necro_n = int(seg.necrosis.sum())
        flower_n = int(seg.flowers.sum())
        fruit_n = int(seg.fruit.sum())
        leaf_n = green_n + yellow_n + necro_n
        plant_n = leaf_n + flower_n + fruit_n
        lit_px = int(seg.envelope.sum()) - int(seg.dark.sum())

        canopy_cov = _ratio(region_px, total_px)
        green_cov = _ratio(int(pixels.green.sum()), total_px)
        has_canopy = canopy_cov >= cfg.min_canopy_coverage and leaf_n >= cfg.min_leaf_pixels

        chlorosis = _ratio(yellow_n, leaf_n)
        necrosis = _ratio(necro_n, leaf_n)
        flower_ratio = _ratio(flower_n, leaf_n + flower_n)
        fruit_ratio = _ratio(fruit_n, leaf_n + fruit_n)
        white_ratio = _ratio(int(seg.white.sum()), region_px)
        density = _ratio(plant_n, lit_px) if lit_px > 0 else None

        spot_count, spot_density = self._count_spots(seg, leaf_n, total_px)
        indices = self._indices(features, seg, chlorosis, necrosis, white_ratio, spot_count, spot_density, h, w)

        # —— 生长状态 ——
        month = _season_month(ctx.captured_at, ctx.latitude)
        stage, stage_evidence = _estimate_stage(month, has_canopy, chlorosis, flower_ratio, fruit_ratio)
        gsd_analysis_m = (ctx.gsd_cm * scale / 100.0) if ctx.gsd_cm else None
        crowns = self._crowns(seg, w, h, gsd_analysis_m)
        vigor_level, vigor_score = _estimate_vigor(stage, has_canopy, indices.gli, density, indices.canopy_cv,
                                                   chlorosis, necrosis)

        canopy_area_m2 = float(seg.hull_areas.sum()) * gsd_analysis_m**2 if gsd_analysis_m else None
        crown_diameters = [c.diameter_m for c in crowns if c.diameter_m]
        growth = GrowthStatus(
            stage=stage,
            stage_label=kb.GROWTH_STAGES[stage],
            stage_evidence=stage_evidence,
            vigor_level=vigor_level,
            vigor_label=kb.VIGOR_LEVELS[vigor_level],
            vigor_score=_r(vigor_score, 1),
            canopy_coverage=_r(canopy_cov) or 0.0,
            green_coverage=_r(green_cov) or 0.0,
            foliage_density=_r(density),
            crown_count=seg.count if has_canopy else 0,
            canopy_area_m2=_r(canopy_area_m2, 2) if has_canopy else None,
            mean_crown_diameter_m=_r(float(np.mean(crown_diameters)), 2) if crown_diameters and has_canopy else None,
            flower_ratio=_r(flower_ratio) or 0.0,
            fruit_ratio=_r(fruit_ratio) or 0.0,
        )

        # —— 可选：AI 模型识别 ——
        prediction: ModelPrediction | None = None
        if self.model is not None and has_canopy:
            try:
                prediction = self.model.predict(image)
            except Exception:  # 模型异常不影响规则分析
                logger.exception("ONNX 模型推理失败，已跳过")

        # —— 健康状态 ——
        health = self._health(
            has_canopy=has_canopy,
            stage=stage,
            month=month,
            chlorosis=chlorosis,
            necrosis=necrosis,
            spot_density=spot_density,
            spot_count=spot_count,
            white_ratio=white_ratio,
            density=density,
            gli=indices.gli,
            quality=quality,
            canopy_cov=canopy_cov,
            prediction=prediction,
        )

        result = AnalysisResult(
            analyzer=self.name if self.model is None else f"{self.name}+{self.model.name}",
            analyzer_version=self.version,
            analyzed_at=datetime.now().replace(microsecond=0),
            analysis_size=[w, h],
            gsd_cm=_r(ctx.gsd_cm, 3),
            summary=_summary(health, growth),
            growth=growth,
            health=health,
            indices=indices,
            quality=quality,
            crowns=crowns if has_canopy else [],
            model=prediction,
        )
        show_white = any(i.code == "white_patches" for i in health.issues)
        overlay = _render_overlay(image, seg, show_white)
        return AnalysisOutput(result=result, overlay=overlay)

    # ------------------------------------------------------------------

    def _segment(self, px: PixelClasses, h: int, w: int) -> _Segmentation:
        cfg = self.config
        short = min(h, w)
        radius = max(2.0, short * cfg.close_radius_frac)

        # 开运算去掉叶片与土壤交界处、果实边缘的细碎混合色像素
        struct = np.ones((3, 3), dtype=bool)
        yellow = ndi.binary_opening(px.yellow, structure=struct)
        brown = ndi.binary_opening(px.brown, structure=struct)
        if px.fruit.any():
            fruit_ring = _dilate(px.fruit, 2.5) & ~px.fruit
            yellow &= ~fruit_ring
            brown &= ~fruit_ring

        # 褐色像素只有紧邻叶丛时才参与构建树冠，避免把树间裸土并入树冠
        leafy = px.green | yellow
        window = max(3, int(round(short * cfg.foliage_window_frac)) | 1)
        neighbor = ndi.uniform_filter(leafy.astype(np.float32), size=window, mode="nearest")
        brown_near = brown & (neighbor >= cfg.min_foliage_neighbors)
        # 花和果实只在叶丛附近才计入树冠
        near_foliage = _dilate(leafy, radius * 2)
        seed = leafy | brown_near | ((px.flowers | px.fruit) & near_foliage)

        closed = _closing(seed, radius)
        region = _fill_small_holes(closed, int(h * w * cfg.max_hole_frac))
        region = _remove_small(region, int(h * w * cfg.min_fragment_frac))

        # 把相邻的叶丛碎片归并为同一树冠，再按面积和绿叶占比筛选
        groups, n = ndi.label(_closing_coarse(region, short * cfg.group_radius_frac))
        labels = np.where(region, groups, 0)
        if n:
            flat = labels.ravel()
            area = np.bincount(flat, minlength=n + 1)
            green_cnt = np.bincount(flat, weights=px.green.ravel(), minlength=n + 1)
            keep = (area >= h * w * cfg.min_crown_frac) & (green_cnt >= area * cfg.min_crown_green_frac)
            keep[0] = False
            region = keep[labels]
            remap = np.zeros(n + 1, dtype=np.int32)
            remap[keep] = np.arange(1, int(keep.sum()) + 1)
            labels = remap[labels]
            n = int(keep.sum())
        envelope, hull_areas = _convex_envelope(labels, n)

        return _Segmentation(
            region=region,
            labels=labels,
            count=n,
            envelope=envelope,
            hull_areas=hull_areas,
            green=px.green & region,
            yellow=yellow & region,
            necrosis=brown & region,
            flowers=px.flowers & region,
            fruit=px.fruit & region,
            white=px.white & region,
            dark=px.dark & envelope,
        )

    def _count_spots(self, seg: _Segmentation, leaf_n: int, total_px: int) -> tuple[int, float]:
        spots = seg.yellow | seg.necrosis
        if not spots.any() or leaf_n == 0:
            return 0, 0.0
        labels, n = ndi.label(spots)
        sizes = np.bincount(labels.ravel(), minlength=n + 1)[1:]
        max_px = max(self.config.min_spot_px + 1, int(total_px * self.config.max_spot_frac))
        count = int(((sizes >= self.config.min_spot_px) & (sizes <= max_px)).sum())
        return count, count / (leaf_n / 10000.0)

    def _indices(
        self,
        f: ColorFeatures,
        seg: _Segmentation,
        chlorosis: float,
        necrosis: float,
        white_ratio: float,
        spot_count: int,
        spot_density: float,
        h: int,
        w: int,
    ) -> VegetationIndices:
        green = seg.green
        leaf = seg.green | seg.yellow | seg.necrosis

        def mean_of(arr: np.ndarray, mask: np.ndarray) -> float | None:
            return _r(float(arr[mask].mean())) if mask.any() else None

        return VegetationIndices(
            exg=mean_of(f.exg, green),
            exgr=mean_of(f.exgr, leaf),
            gli=mean_of(f.gli, green),
            vari=_r(float(np.median(f.vari[leaf]))) if leaf.any() else None,
            ngrdi=mean_of(f.ngrdi, leaf),
            chlorosis_ratio=_r(chlorosis) or 0.0,
            necrosis_ratio=_r(necrosis) or 0.0,
            white_ratio=_r(white_ratio) or 0.0,
            spot_count=spot_count,
            spot_density=_r(spot_density, 2) or 0.0,
            canopy_cv=_r(_canopy_cv(f.exg, seg.region, h, w)),
        )

    def _crowns(self, seg: _Segmentation, w: int, h: int, gsd_m: float | None) -> list[Crown]:
        if seg.count == 0:
            return []
        flat = seg.labels.ravel()
        n = seg.count
        area = np.bincount(flat, minlength=n + 1)
        leaf = np.bincount(flat, weights=(seg.green | seg.yellow | seg.necrosis).ravel(), minlength=n + 1)
        yellow = np.bincount(flat, weights=seg.yellow.ravel(), minlength=n + 1)
        necro = np.bincount(flat, weights=seg.necrosis.ravel(), minlength=n + 1)
        slices = ndi.find_objects(seg.labels)
        order = sorted(range(1, n + 1), key=lambda i: area[i], reverse=True)[:50]

        crowns = []
        for idx in order:
            sl = slices[idx - 1]
            if sl is None:
                continue
            ys, xs = sl
            area_m2 = float(seg.hull_areas[idx]) * gsd_m**2 if gsd_m else None
            crowns.append(
                Crown(
                    bbox=[round(xs.start / w, 4), round(ys.start / h, 4), round(xs.stop / w, 4), round(ys.stop / h, 4)],
                    area_px=int(area[idx]),
                    area_ratio=round(float(area[idx]) / (w * h), 4),
                    area_m2=_r(area_m2, 2),
                    diameter_m=_r(2 * math.sqrt(area_m2 / math.pi), 2) if area_m2 else None,
                    chlorosis_ratio=round(_ratio(yellow[idx], leaf[idx]), 4),
                    necrosis_ratio=round(_ratio(necro[idx], leaf[idx]), 4),
                )
            )
        return crowns

    def _assess_quality(self, f: ColorFeatures, orig_size: tuple[int, int]) -> ImageQuality:
        cfg = self.config
        brightness = float(f.luma.mean())
        contrast = float(f.luma.std())
        # 按亮度动态范围归一化后再计算拉普拉斯方差，避免暗图被误判为模糊
        lo, hi = np.percentile(f.luma, [1, 99])
        stretched = (f.luma - lo) / max(float(hi - lo), 0.05)
        sharpness = float(ndi.laplace(stretched * 255.0).var())
        clipped = float((f.val > 0.98).mean())

        issues: list[Issue] = []
        if brightness < 0.16:
            issues.append(_make_issue("image_dark", "high" if brightness < 0.08 else "medium", brightness,
                                      f"平均亮度 {brightness:.2f}（建议 0.25~0.75）"))
        elif brightness > 0.82 or clipped > 0.25:
            issues.append(_make_issue("image_overexposed", "medium", brightness,
                                      f"平均亮度 {brightness:.2f}，过曝像素占 {clipped:.0%}"))
        if sharpness < cfg.blur_threshold:
            severity = "high" if sharpness < cfg.blur_threshold / 3 else "medium"
            issues.append(_make_issue("image_blurry", severity, sharpness,
                                      f"清晰度指标 {sharpness:.1f}（低于 {cfg.blur_threshold:.0f}）"))
        if contrast < 0.045:
            issues.append(_make_issue("low_contrast", "low", contrast, f"亮度标准差 {contrast:.3f}"))
        if min(orig_size) < cfg.min_resolution:
            issues.append(_make_issue("low_resolution", "low", float(min(orig_size)),
                                      f"原图尺寸 {orig_size[0]}×{orig_size[1]}"))
        return ImageQuality(
            brightness=round(brightness, 4),
            contrast=round(contrast, 4),
            sharpness=round(sharpness, 2),
            ok=not issues,
            issues=issues,
        )

    def _health(
        self,
        *,
        has_canopy: bool,
        stage: str,
        month: int | None,
        chlorosis: float,
        necrosis: float,
        spot_density: float,
        spot_count: int,
        white_ratio: float,
        density: float | None,
        gli: float | None,
        quality: ImageQuality,
        canopy_cov: float,
        prediction: ModelPrediction | None,
    ) -> HealthStatus:
        confidence = _confidence(quality, canopy_cov, has_canopy)
        if not has_canopy:
            unknown = {"score": None, "level": "unknown", "level_label": kb.HEALTH_LEVELS["unknown"],
                       "confidence": round(confidence, 2)}
            if stage in SEASONAL_NO_CANOPY:  # 休眠/落叶属于正常物候，不作为异常
                summary, suggestions = SEASONAL_NO_CANOPY[stage]
                return HealthStatus(**unknown, summary=summary, suggestions=list(suggestions))
            issue = _make_issue("no_canopy", "medium", canopy_cov, f"树冠仅占画面 {canopy_cov:.1%}")
            return HealthStatus(
                **unknown,
                summary="画面中未识别到足够的果树冠层，无法评估健康状态。",
                issues=[issue],
                suggestions=list(issue.suggestions),
            )

        issues: list[Issue] = []
        penalties: dict[str, float] = {}

        def evaluate(code: str, value: float, evidence: str, weight: float = 1.0) -> None:
            severity, penalty = _grade(code, value)
            if severity is None:
                return
            penalties[code] = round(penalty * weight, 1)
            issues.append(_make_issue(code, severity, value, evidence))

        senescent = stage == "leaf_fall"
        evaluate("chlorosis", chlorosis, f"黄化叶片占叶片面积 {chlorosis:.1%}" + ("（秋季自然转黄，已降低权重）" if senescent else ""),
                 weight=0.3 if senescent else 1.0)
        evaluate("necrosis", necrosis, f"褐变/坏死占叶片面积 {necrosis:.1%}")
        # 病斑密集是早期预警指标：已出现中等以上的成片黄化/褐变时不再重复扣分
        widespread = any(i.code in ("chlorosis", "necrosis") and SEVERITY_ORDER[i.severity] >= 2 for i in issues)
        if spot_count >= 5 and not widespread:
            evaluate("leaf_spots", spot_density, f"检测到 {spot_count} 处零散病斑（每万叶片像素 {spot_density:.1f} 处）")
        if density is not None and stage not in ("flowering", "leaf_fall", "dormant", "budding"):
            evaluate("sparse_canopy", density, f"树冠内叶片覆盖率 {density:.0%}")
        if gli is not None and stage not in ("leaf_fall", "budding"):
            evaluate("low_greenness", gli, f"绿叶指数 GLI 均值 {gli:.3f}（正常约 0.25 以上）")
        in_flower_season = month is None or 2 <= month <= 6
        if not (stage == "flowering" or (in_flower_season and white_ratio < 0.12)):
            evaluate("white_patches", white_ratio, f"树冠内白色斑块占 {white_ratio:.1%}")

        if prediction is not None and not prediction.healthy and self.model is not None:
            if prediction.confidence >= self.model.min_confidence:
                info = self.model.label_info(prediction.name)
                severity = info.severity if info.severity in SEVERITY_ORDER else "medium"
                penalties[f"model:{prediction.name}"] = round(MODEL_PENALTY[severity] * prediction.confidence, 1)
                issues.append(
                    Issue(
                        code=f"model:{prediction.name}",
                        name=prediction.label,
                        category=info.category,
                        category_label=kb.CATEGORIES.get(info.category, info.category),
                        severity=severity,
                        severity_label=kb.SEVERITIES[severity],
                        value=round(prediction.confidence, 4),
                        evidence=f"AI 模型识别置信度 {prediction.confidence:.0%}",
                        description=info.description,
                        possible_causes=list(info.causes),
                        suggestions=list(info.suggestions),
                    )
                )

        score = max(0.0, min(100.0, 100.0 - sum(penalties.values())))
        level = kb.level_for_score(score)
        # 存在中等/严重问题时，等级至少为轻度/中度异常
        worst = max((SEVERITY_ORDER[i.severity] for i in issues), default=0)
        floor = {3: "moderate", 2: "mild"}.get(worst)
        if floor and LEVEL_ORDER.index(floor) > LEVEL_ORDER.index(level):
            level = floor
            score = min(score, LEVEL_SCORE_CAP[level])  # 保持评分与等级一致

        issues.sort(key=lambda i: (SEVERITY_ORDER[i.severity], penalties.get(i.code, 0)), reverse=True)
        suggestions: list[str] = []
        for issue in issues:
            for s in issue.suggestions:
                if s not in suggestions:
                    suggestions.append(s)
        if not suggestions:
            suggestions = ["树体状态良好，保持现有水肥管理，按计划开展下一次巡检"]

        if issues:
            names = "、".join(f"{i.name}（{i.severity_label}）" for i in issues[:3])
            summary = f"发现 {len(issues)} 项异常：{names}。"
        else:
            summary = "未发现明显异常，树体健康。"
        return HealthStatus(
            score=round(score, 1),
            level=level,
            level_label=kb.HEALTH_LEVELS[level],
            confidence=round(confidence, 2),
            summary=summary,
            issues=issues,
            suggestions=suggestions[:6],
            penalties=penalties,
        )


# ---------------------------------------------------------------------------
# 规则函数
# ---------------------------------------------------------------------------


def _grade(code: str, value: float) -> tuple[str | None, float]:
    """根据扣分表返回 (严重程度, 扣分)。未达到“轻微”阈值时严重程度为 None。"""
    table = PENALTY_TABLES[code]
    xs = np.array([p[0] for p in table], dtype=float)
    ys = np.array([p[1] for p in table], dtype=float)
    if code in DECREASING_INDICATORS:
        xs, ys, value = -xs, ys, -value
    penalty = float(np.interp(value, xs, ys))
    if value >= xs[3]:
        severity = "high"
    elif value >= xs[2]:
        severity = "medium"
    elif value >= xs[1]:
        severity = "low"
    else:
        severity = None
    return severity, penalty


def _make_issue(code: str, severity: str, value: float | None, evidence: str) -> Issue:
    d = kb.ISSUES[code]
    return Issue(
        code=code,
        name=d.name,
        category=d.category,
        category_label=kb.CATEGORIES.get(d.category, d.category),
        severity=severity,
        severity_label=kb.SEVERITIES[severity],
        value=_r(value),
        evidence=evidence,
        description=d.description,
        possible_causes=list(d.causes),
        suggestions=list(d.suggestions),
    )


def _season_month(captured_at: datetime | None, latitude: float | None) -> int | None:
    """返回按北半球物候对齐的月份（南半球照片平移 6 个月）。"""
    if captured_at is None:
        return None
    month = captured_at.month
    if latitude is not None and latitude < 0:
        month = (month + 5) % 12 + 1
    return month


def _estimate_stage(
    month: int | None, has_canopy: bool, chlorosis: float, flower_ratio: float, fruit_ratio: float
) -> tuple[str, str]:
    if not has_canopy:
        if month in (12, 1, 2, 3):
            return "dormant", "画面中几乎没有绿色叶片，且拍摄于冬春季，判断为休眠期"
        if month in (10, 11):
            return "leaf_fall", "画面中绿色叶片很少，且拍摄于深秋，判断为落叶期"
        return "unknown", "画面中未识别到足够的果树冠层"
    if flower_ratio >= 0.08 and (month is None or 2 <= month <= 6):
        return "flowering", f"树冠中白色/粉色花朵约占 {flower_ratio:.0%}"
    if fruit_ratio >= 0.015 and (month is None or 6 <= month <= 12):
        return "ripening", f"树冠中检测到着色果实（约占 {fruit_ratio:.1%}）"
    if month is None:
        return "vegetative", "树冠以绿色叶片为主（照片缺少拍摄时间，仅依据图像特征判断）"
    if month in (9, 10, 11) and chlorosis >= 0.3:
        return "leaf_fall", f"秋季树冠大面积转黄（黄叶约占 {chlorosis:.0%}），进入落叶期"
    if month in (3, 4):
        return "budding", "春季拍摄，树冠以新生绿叶为主"
    if 5 <= month <= 9:
        return "fruit_development", "生长季拍摄，树冠叶片繁茂，处于新梢生长与果实发育阶段"
    if month == 10:
        return "ripening", "秋季拍摄，处于果实成熟采收阶段（未检测到明显着色果实）"
    return "vegetative", "冬季仍保持绿色树冠（常绿果树），处于营养生长阶段"


def _estimate_vigor(
    stage: str,
    has_canopy: bool,
    gli: float | None,
    density: float | None,
    cv: float | None,
    chlorosis: float,
    necrosis: float,
) -> tuple[str, float | None]:
    if not has_canopy or stage in ("dormant", "leaf_fall"):
        return "unknown", None
    s_green = float(np.interp(gli if gli is not None else 0.0, [0.08, 0.32], [0.0, 1.0]))
    s_density = float(np.interp(density if density is not None else 0.6, [0.4, 0.92], [0.0, 1.0]))
    s_uniform = 0.7 if cv is None else float(np.interp(cv, [0.15, 0.6], [1.0, 0.0]))
    s_clean = 1.0 - min(1.0, (chlorosis + necrosis) * 2.5)
    score = 100.0 * (0.40 * s_green + 0.35 * s_density + 0.15 * s_uniform + 0.10 * s_clean)
    if score >= 75:
        level = "strong"
    elif score >= 55:
        level = "normal"
    elif score >= 35:
        level = "weak"
    else:
        level = "poor"
    return level, score


def _convex_envelope(labels: np.ndarray, n: int) -> tuple[np.ndarray, np.ndarray]:
    """计算每个树冠连通域的凸包（树冠投影轮廓），返回 (凸包掩膜, 各凸包面积)。"""
    envelope = np.zeros(labels.shape, dtype=bool)
    areas = np.zeros(n + 1, dtype=np.float64)
    if n == 0:
        return envelope, areas
    for idx, sl in enumerate(ndi.find_objects(labels), start=1):
        if sl is None:
            continue
        comp = labels[sl] == idx
        boundary = comp & ~ndi.binary_erosion(comp)
        ys, xs = np.nonzero(boundary)
        hull_mask = comp
        if len(xs) >= 3:
            try:
                hull = ConvexHull(np.column_stack([xs, ys]).astype(np.float64))
                canvas = Image.new("1", (comp.shape[1], comp.shape[0]), 0)
                ImageDraw.Draw(canvas).polygon([(float(xs[v]), float(ys[v])) for v in hull.vertices], fill=1, outline=1)
                hull_mask = np.asarray(canvas, dtype=bool) | comp
            except QhullError:  # 共线等退化情况
                pass
        envelope[sl] |= hull_mask
        areas[idx] = float(hull_mask.sum())
    return envelope, areas


def _canopy_cv(exg: np.ndarray, region: np.ndarray, h: int, w: int, grid: int = 8) -> float | None:
    """把画面划分为 grid×grid 网格，计算树冠覆盖较多的网格中 ExG 均值的变异系数。"""
    if not region.any():
        return None
    rows = (np.arange(h) * grid // h)[:, None]
    cols = (np.arange(w) * grid // w)[None, :]
    cell = (rows * grid + cols).ravel()
    reg = region.ravel()
    count = np.bincount(cell, weights=reg, minlength=grid * grid)
    total = np.bincount(cell, minlength=grid * grid)
    sums = np.bincount(cell, weights=exg.ravel() * reg, minlength=grid * grid)
    valid = (count >= total * 0.3) & (count > 0)
    if valid.sum() < 4:
        return None
    means = sums[valid] / count[valid]
    mean = float(means.mean())
    if mean <= 0.02:
        return None
    return float(means.std() / mean)


def _confidence(quality: ImageQuality, canopy_cov: float, has_canopy: bool) -> float:
    conf = 0.8
    deductions = {"image_blurry": 0.2, "image_dark": 0.15, "image_overexposed": 0.15,
                  "low_contrast": 0.1, "low_resolution": 0.1}
    for issue in quality.issues:
        conf -= deductions.get(issue.code, 0.0)
    if has_canopy and canopy_cov < 0.05:
        conf -= 0.15
    if not has_canopy:
        conf = min(conf, 0.5)
    return float(min(0.9, max(0.2, conf)))


def _summary(health: HealthStatus, growth: GrowthStatus) -> str:
    if health.score is None:
        return health.summary
    return (
        f"健康评分 {health.score:.0f}（{health.level_label}），长势{growth.vigor_label}，"
        f"处于{growth.stage_label}。{health.summary}"
    )


def _render_overlay(image: np.ndarray, seg: _Segmentation, show_white: bool) -> Image.Image:
    """生成标注图：非树冠区域压暗，黄化标黄、褐变标红、疑似白粉标紫，树冠轮廓为青色。"""
    out = image.astype(np.float32)
    out[~seg.region] *= 0.35

    def paint(mask: np.ndarray, color: tuple[int, int, int], alpha: float) -> None:
        if mask.any():
            out[mask] = out[mask] * (1 - alpha) + np.array(color, dtype=np.float32) * alpha

    paint(seg.yellow, (255, 214, 0), 0.75)
    paint(seg.necrosis, (235, 45, 45), 0.8)
    if show_white:
        paint(seg.white, (190, 70, 255), 0.75)
    edge = seg.region & ~_erode(seg.region, 1.5)
    paint(edge, (0, 229, 255), 1.0)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
