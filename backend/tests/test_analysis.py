"""分析引擎测试：用合成的俯拍果园场景验证生长状态与健康状态判断。"""

from __future__ import annotations

from datetime import datetime

import pytest
from PIL import Image, ImageEnhance, ImageFilter

from app.analysis import AnalysisContext, OrchardAnalyzer, UnsupportedImageError
from app.analysis import knowledge as kb
from app.analysis.engine import PENALTY_TABLES, _grade
from app.demo_data import SCENES, make_scene

JULY = datetime(2026, 7, 15, 10, 0)


@pytest.fixture(scope="module")
def analyzer() -> OrchardAnalyzer:
    return OrchardAnalyzer()


def analyze(analyzer, kind, when=JULY, latitude=34.3, gsd_cm=None, seed=3, image=None):
    img = image if image is not None else make_scene(kind, size=(1000, 750), seed=seed)
    return analyzer.analyze(img, AnalysisContext(captured_at=when, latitude=latitude, gsd_cm=gsd_cm)).result


def codes(result) -> set[str]:
    return {i.code for i in result.health.issues}


def test_healthy_trees(analyzer):
    r = analyze(analyzer, "healthy")
    assert r.health.level == "healthy"
    assert r.health.score >= 95
    assert not r.health.issues
    assert r.growth.stage == "fruit_development"
    assert r.growth.vigor_level in ("strong", "normal")
    assert r.growth.crown_count == 6
    assert 0.25 < r.growth.canopy_coverage < 0.5


def test_chlorosis_detected(analyzer):
    r = analyze(analyzer, "chlorosis")
    assert "chlorosis" in codes(r)
    assert r.indices.chlorosis_ratio > 0.25
    assert r.health.level in ("moderate", "severe")
    assert r.health.score < 70
    assert r.health.suggestions


def test_necrosis_detected(analyzer):
    r = analyze(analyzer, "necrosis")
    assert "necrosis" in codes(r)
    assert r.indices.necrosis_ratio > 0.1
    assert r.health.level in ("moderate", "severe")


def test_leaf_spots_detected(analyzer):
    r = analyze(analyzer, "leaf_spots")
    assert "leaf_spots" in codes(r)
    assert r.indices.spot_count > 50


def test_sparse_canopy_detected(analyzer):
    r = analyze(analyzer, "sparse")
    assert "sparse_canopy" in codes(r)
    assert r.growth.foliage_density < 0.6


def test_flowering_stage_in_spring(analyzer):
    r = analyze(analyzer, "flowering", when=datetime(2026, 4, 10, 10))
    assert r.growth.stage == "flowering"
    assert "white_patches" not in codes(r)
    assert r.health.level == "healthy"


def test_white_patches_outside_flowering_season(analyzer):
    # 同样的白色斑块出现在 8 月，按疑似白粉病提示
    r = analyze(analyzer, "flowering", when=datetime(2026, 8, 10, 10))
    assert r.growth.stage != "flowering"
    assert "white_patches" in codes(r)


def test_ripening_stage_with_coloured_fruit(analyzer):
    r = analyze(analyzer, "fruiting", when=datetime(2026, 9, 20, 10))
    assert r.growth.stage == "ripening"
    assert r.growth.fruit_ratio > 0.015
    assert "necrosis" not in codes(r)  # 果实边缘的混合色不应误判为褐变


def test_dormant_trees_not_flagged(analyzer):
    r = analyze(analyzer, "dormant", when=datetime(2026, 1, 15, 10))
    assert r.growth.stage == "dormant"
    assert r.health.level == "unknown"
    assert r.health.score is None
    assert not r.health.issues
    assert r.health.suggestions


def test_no_canopy_reported_in_growing_season(analyzer):
    r = analyze(analyzer, "dormant", when=JULY)
    assert r.health.level == "unknown"
    assert codes(r) == {"no_canopy"}


def test_southern_hemisphere_season_shift(analyzer):
    # 南半球 7 月是冬季
    r = analyze(analyzer, "dormant", when=datetime(2026, 7, 15, 10), latitude=-35.0)
    assert r.growth.stage == "dormant"


def test_autumn_yellowing_is_leaf_fall(analyzer):
    july = analyze(analyzer, "chlorosis", when=JULY)
    october = analyze(analyzer, "chlorosis", when=datetime(2026, 10, 20, 10))
    assert october.growth.stage == "leaf_fall"
    assert october.health.penalties["chlorosis"] < july.health.penalties["chlorosis"]


def test_canopy_area_from_gsd(analyzer):
    r = analyze(analyzer, "healthy", gsd_cm=2.0)
    assert r.growth.canopy_area_m2 > 0
    assert r.growth.mean_crown_diameter_m > 0
    assert r.gsd_cm == 2.0
    assert len(r.crowns) == 6
    assert all(c.area_m2 for c in r.crowns)
    no_gsd = analyze(analyzer, "healthy")
    assert no_gsd.growth.canopy_area_m2 is None


def test_score_and_level_are_consistent(analyzer):
    for kind in SCENES:
        r = analyze(analyzer, kind)
        if r.health.score is None:
            assert r.health.level == "unknown"
        else:
            assert kb.level_for_score(r.health.score) == r.health.level, kind


def test_quality_checks(analyzer):
    base = make_scene("healthy", size=(1000, 750), seed=5)
    blurry = analyze(analyzer, "healthy", image=base.filter(ImageFilter.GaussianBlur(4)))
    assert "image_blurry" in {q.code for q in blurry.quality.issues}
    assert blurry.health.confidence < 0.8

    dark = analyze(analyzer, "healthy", image=ImageEnhance.Brightness(base).enhance(0.3))
    dark_codes = {q.code for q in dark.quality.issues}
    assert "image_dark" in dark_codes
    assert "image_blurry" not in dark_codes  # 暗图不应被误判为模糊

    small = analyze(analyzer, "healthy", image=base.resize((400, 300)))
    assert "low_resolution" in {q.code for q in small.quality.issues}

    clean = analyze(analyzer, "healthy", image=base)
    assert clean.quality.ok


def test_overlay_matches_analysis_size(analyzer):
    out = analyzer.analyze(make_scene("necrosis", size=(1600, 1200), seed=2))
    assert list(out.overlay.size) == out.result.analysis_size
    assert max(out.overlay.size) == analyzer.config.max_side


def test_grayscale_image_rejected(analyzer):
    with pytest.raises(UnsupportedImageError):
        analyzer.analyze(Image.new("L", (800, 600), 128))


def test_grade_severity_bands():
    for code, table in PENALTY_TABLES.items():
        decreasing = code in ("sparse_canopy", "low_greenness")
        low, medium, high = table[1][0], table[2][0], table[3][0]
        assert _grade(code, low)[0] == "low"
        assert _grade(code, medium)[0] == "medium"
        assert _grade(code, high)[0] == "high"
        healthy_value = table[0][0] + (0.05 if decreasing else -0.01)
        assert _grade(code, healthy_value) == (None, 0.0)
        # 单项“中等/严重”问题分别让评分跌破 85 / 70
        assert 100 - _grade(code, medium)[1] < 85
        assert 100 - _grade(code, high)[1] < 70
