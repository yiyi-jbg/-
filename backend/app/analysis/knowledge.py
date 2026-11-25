"""农学知识库：状态等级、生长阶段、问题类型的中文名称、可能原因与管理建议。

前端和接口返回的中文说明都来自这里，修改此文件即可调整文案或补充本地经验。
"""

from __future__ import annotations

from dataclasses import dataclass

HEALTH_LEVELS: dict[str, str] = {
    "healthy": "健康",
    "mild": "轻度异常",
    "moderate": "中度异常",
    "severe": "严重异常",
    "unknown": "无法评估",
}

# 健康评分阈值：score >= 阈值 即为对应等级
HEALTH_LEVEL_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("healthy", 85.0),
    ("mild", 70.0),
    ("moderate", 50.0),
    ("severe", 0.0),
)

GROWTH_STAGES: dict[str, str] = {
    "dormant": "休眠期",
    "budding": "萌芽展叶期",
    "flowering": "开花期",
    "fruit_development": "果实发育期",
    "ripening": "果实成熟期",
    "leaf_fall": "落叶期",
    "vegetative": "营养生长期",
    "unknown": "无法判断",
}

VIGOR_LEVELS: dict[str, str] = {
    "strong": "旺盛",
    "normal": "正常",
    "weak": "偏弱",
    "poor": "衰弱",
    "unknown": "无法评估",
}

SEVERITIES: dict[str, str] = {"low": "轻微", "medium": "中等", "high": "严重"}

CATEGORIES: dict[str, str] = {
    "disease": "病害",
    "nutrition": "营养",
    "growth": "长势",
    "pest": "虫害",
    "quality": "图像质量",
    "model": "模型识别",
}


@dataclass(frozen=True)
class IssueDefinition:
    code: str
    name: str
    category: str
    description: str
    causes: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = ()


ISSUES: dict[str, IssueDefinition] = {
    d.code: d
    for d in (
        IssueDefinition(
            "chlorosis",
            "叶片黄化",
            "nutrition",
            "冠层中出现成片的黄色叶片。",
            causes=(
                "缺氮、缺铁、缺镁等营养元素缺乏",
                "根系受损、积水或干旱导致吸收障碍",
                "黄化类病毒病或生理性病害",
            ),
            suggestions=(
                "采集叶片做营养诊断，结合土壤检测确定缺素类型",
                "对症追施氮肥，或叶面喷施螯合铁、硫酸镁",
                "检查果园排水情况与根颈部是否腐烂",
            ),
        ),
        IssueDefinition(
            "necrosis",
            "叶片褐变/坏死",
            "disease",
            "冠层叶片中夹杂褐色、枯焦区域。",
            causes=(
                "褐斑病、炭疽病、斑点落叶病等真菌性病害",
                "日灼、药害或冻害",
                "干旱或根系病害导致枝叶枯死",
            ),
            suggestions=(
                "现场核查病斑形态，必要时取样送检确诊",
                "剪除病枝病叶，带出园外集中处理",
                "确诊后选用针对性杀菌剂，注意轮换用药",
                "检查灌溉是否到位，避免高温时段喷药",
            ),
        ),
        IssueDefinition(
            "leaf_spots",
            "病斑密集",
            "disease",
            "冠层中分布大量零散的黄褐色斑点。",
            causes=(
                "叶斑类病害（如褐斑病、黑星病、锈病）发病初期",
                "叶螨、蚜虫等刺吸式害虫为害",
            ),
            suggestions=(
                "降低高度近距离复拍，或人工抽查叶片正反面",
                "病害初期及时喷施保护性杀菌剂",
                "调查虫口密度，达到防治指标后再用药",
            ),
        ),
        IssueDefinition(
            "sparse_canopy",
            "冠层稀疏",
            "growth",
            "树冠内部叶片稀疏、空隙较多。",
            causes=(
                "早期落叶病等叶部病害导致落叶",
                "食叶害虫为害",
                "干旱、缺肥导致树势衰弱",
                "修剪过重",
            ),
            suggestions=(
                "检查树下落叶及叶片残缺情况，排查病虫害",
                "加强水肥管理，秋季增施有机肥恢复树势",
                "调整修剪强度，保留适量枝叶",
            ),
        ),
        IssueDefinition(
            "low_greenness",
            "叶色偏淡",
            "nutrition",
            "叶片整体绿色偏浅，叶绿素含量可能偏低。",
            causes=("氮素供应不足", "光照不足或水分胁迫", "根系活力下降"),
            suggestions=(
                "适量追施氮肥，或叶面喷施 0.3%~0.5% 尿素",
                "保持土壤适宜含水量",
                "可用叶绿素仪（SPAD）实地复测",
            ),
        ),
        IssueDefinition(
            "white_patches",
            "疑似白粉病斑",
            "disease",
            "非花期冠层中出现白色斑块。",
            causes=(
                "白粉病（叶面覆盖白色粉层）",
                "也可能是反光膜、药液残留或强烈反光，需现场确认",
            ),
            suggestions=(
                "近距离核查叶片、新梢表面是否有白色粉状物",
                "确诊后喷施三唑类等杀菌剂",
                "剪除病梢，改善通风透光",
            ),
        ),
        IssueDefinition(
            "no_canopy",
            "未识别到果树冠层",
            "quality",
            "画面中几乎没有识别到绿色树冠，无法评估长势与健康。",
            causes=(
                "拍摄区域没有果树，或树冠在画面中占比过小",
                "果树处于休眠/落叶期",
                "拍摄高度、角度或曝光不合适",
            ),
            suggestions=(
                "确认航线是否覆盖果树",
                "建议云台垂直向下（-90°）、相对高度 10~30 米拍摄",
            ),
        ),
        IssueDefinition(
            "image_dark",
            "图像过暗",
            "quality",
            "照片整体亮度偏低，颜色判断可能不准确。",
            suggestions=("适当提高曝光补偿", "避开清晨、傍晚等光线不足时段作业"),
        ),
        IssueDefinition(
            "image_overexposed",
            "图像过曝",
            "quality",
            "照片高光区域过多，叶片颜色信息丢失。",
            suggestions=("降低曝光补偿", "避开正午强光，或开启相机高光保护"),
        ),
        IssueDefinition(
            "image_blurry",
            "图像模糊",
            "quality",
            "照片清晰度不足，细小病斑可能无法识别。",
            suggestions=("降低飞行速度或采用悬停拍照", "检查镜头是否清洁、对焦是否正常"),
        ),
        IssueDefinition(
            "low_contrast",
            "对比度偏低",
            "quality",
            "照片发灰、对比度低，可能存在雾气、逆光或镜头起雾。",
            suggestions=("避开雾天和逆光方向拍摄", "检查镜头是否有水汽"),
        ),
        IssueDefinition(
            "low_resolution",
            "分辨率偏低",
            "quality",
            "照片分辨率过低，难以分辨叶片细节。",
            suggestions=("上传无人机拍摄的原图，不要使用压缩后的图片",),
        ),
    )
}


def issue_definition(code: str) -> IssueDefinition | None:
    return ISSUES.get(code)


def level_for_score(score: float) -> str:
    for level, threshold in HEALTH_LEVEL_THRESHOLDS:
        if score >= threshold:
            return level
    return "severe"


def enum_labels() -> dict[str, dict[str, str]]:
    """提供给前端的全部枚举中文名称。"""
    return {
        "health_levels": HEALTH_LEVELS,
        "growth_stages": GROWTH_STAGES,
        "vigor_levels": VIGOR_LEVELS,
        "severities": SEVERITIES,
        "categories": CATEGORIES,
        "issues": {code: d.name for code, d in ISSUES.items()},
    }
