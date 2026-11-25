"""分析结果的数据结构（同时用于接口文档和数据库存储）。"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class _Model(BaseModel):
    # 读取旧版本结果时忽略多余字段，保证升级兼容
    model_config = ConfigDict(extra="ignore")


class Issue(_Model):
    code: str = Field(description="问题代码，如 chlorosis")
    name: str = Field(description="问题名称")
    category: str = Field(description="类别：disease/nutrition/growth/quality/model")
    category_label: str = ""
    severity: str = Field(description="严重程度：low/medium/high")
    severity_label: str = ""
    value: float | None = Field(default=None, description="判定依据的指标值")
    evidence: str = Field(default="", description="判定依据说明")
    description: str = ""
    possible_causes: list[str] = []
    suggestions: list[str] = []


class GrowthStatus(_Model):
    stage: str = Field(description="生长阶段代码")
    stage_label: str
    stage_evidence: str = ""
    vigor_level: str = Field(description="长势等级代码")
    vigor_label: str
    vigor_score: float | None = Field(default=None, description="长势评分 0~100")
    canopy_coverage: float = Field(description="树冠占画面比例 0~1")
    green_coverage: float = Field(description="绿色植被占画面比例 0~1")
    foliage_density: float | None = Field(default=None, description="树冠内绿叶密度 0~1")
    crown_count: int = Field(default=0, description="识别到的树冠数量（相连的树冠会合并计数）")
    canopy_area_m2: float | None = Field(default=None, description="树冠总面积（平方米，需正射且有高度信息）")
    mean_crown_diameter_m: float | None = Field(default=None, description="平均冠幅（米）")
    flower_ratio: float = 0.0
    fruit_ratio: float = 0.0


class HealthStatus(_Model):
    score: float | None = Field(default=None, description="健康评分 0~100")
    level: str = Field(description="健康等级代码")
    level_label: str
    confidence: float = Field(description="结果可信度 0~1")
    summary: str = ""
    issues: list[Issue] = []
    suggestions: list[str] = []
    penalties: dict[str, float] = Field(default={}, description="各项扣分明细")


class VegetationIndices(_Model):
    exg: float | None = Field(default=None, description="超绿指数 ExG（树冠绿叶均值）")
    exgr: float | None = Field(default=None, description="ExG-ExR（树冠均值）")
    gli: float | None = Field(default=None, description="绿叶指数 GLI（树冠绿叶均值）")
    vari: float | None = Field(default=None, description="可见光大气阻抗植被指数 VARI（树冠中位数）")
    ngrdi: float | None = Field(default=None, description="归一化绿红差值指数 NGRDI（树冠均值）")
    chlorosis_ratio: float = Field(default=0.0, description="黄化叶片占叶片比例")
    necrosis_ratio: float = Field(default=0.0, description="褐变/坏死占叶片比例")
    white_ratio: float = Field(default=0.0, description="白色斑块占树冠比例")
    spot_count: int = Field(default=0, description="零散病斑数量")
    spot_density: float = Field(default=0.0, description="每万叶片像素病斑数")
    canopy_cv: float | None = Field(default=None, description="树冠绿度变异系数（越大越不均匀）")


class ImageQuality(_Model):
    brightness: float
    contrast: float
    sharpness: float
    ok: bool
    issues: list[Issue] = []


class Crown(_Model):
    bbox: list[float] = Field(description="归一化外接框 [x0, y0, x1, y1]")
    area_px: int
    area_ratio: float
    area_m2: float | None = None
    diameter_m: float | None = None
    chlorosis_ratio: float = 0.0
    necrosis_ratio: float = 0.0


class ModelPrediction(_Model):
    name: str
    label: str
    confidence: float
    healthy: bool
    top_k: list[dict[str, float | str]] = []


class AnalysisResult(_Model):
    analyzer: str
    analyzer_version: str
    analyzed_at: datetime
    analysis_size: list[int] = Field(description="分析时使用的图像尺寸 [宽, 高]")
    gsd_cm: float | None = Field(default=None, description="原图地面分辨率（厘米/像素）")
    summary: str = ""
    growth: GrowthStatus
    health: HealthStatus
    indices: VegetationIndices
    quality: ImageQuality
    crowns: list[Crown] = []
    model: ModelPrediction | None = None
