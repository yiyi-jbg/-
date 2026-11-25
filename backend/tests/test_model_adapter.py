"""ONNX 模型接入测试：构造一个极小的分类模型，验证推理与结果融合。"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

onnx = pytest.importorskip("onnx")
pytest.importorskip("onnxruntime")

from onnx import TensorProto, helper  # noqa: E402

from app.analysis import OrchardAnalyzer  # noqa: E402
from app.analysis.model_adapter import OnnxClassifier  # noqa: E402
from app.config import Settings  # noqa: E402
from app.demo_data import make_scene  # noqa: E402
from app.services.processing import build_analyzer  # noqa: E402


def build_model(path: Path, weights: np.ndarray, bias: tuple[float, float] = (0.0, 0.0)) -> None:
    """输入 [1,3,64,64] → 各通道均值 [1,3] → 线性层（含偏置）→ 2 类 logits。"""
    x = helper.make_tensor_value_info("input", TensorProto.FLOAT, [1, 3, 64, 64])
    y = helper.make_tensor_value_info("logits", TensorProto.FLOAT, [1, 2])
    w = helper.make_tensor("w", TensorProto.FLOAT, list(weights.shape), weights.flatten().tolist())
    b = helper.make_tensor("b", TensorProto.FLOAT, [2], list(bias))
    axes = helper.make_tensor("axes", TensorProto.INT64, [2], [2, 3])
    nodes = [
        helper.make_node("ReduceMean", ["input", "axes"], ["pooled"], keepdims=0),
        helper.make_node("MatMul", ["pooled", "w"], ["scores"]),
        helper.make_node("Add", ["scores", "b"], ["logits"]),
    ]
    graph = helper.make_graph(nodes, "tiny", [x], [y], initializer=[w, b, axes])
    model = helper.make_model(graph, opset_imports=[helper.make_opsetid("", 18)])
    model.ir_version = 9
    onnx.save(model, str(path))


@pytest.fixture
def model_files(tmp_path: Path) -> tuple[Path, Path]:
    model_path = tmp_path / "tiny.onnx"
    # “健康”得分 = 绿色通道 - 红色通道；“锈病”得分相反（各乘以 10 放大差异）
    build_model(model_path, np.array([[-10.0, 10.0], [10.0, -10.0], [0.0, 0.0]], dtype=np.float32))
    labels = tmp_path / "labels.json"
    labels.write_text(json.dumps({
        "input_size": 64,
        "mean": [0, 0, 0],
        "std": [1, 1, 1],
        "labels": [
            {"name": "healthy", "label": "健康", "healthy": True},
            {"name": "rust", "label": "锈病", "severity": "high", "suggestions": ["喷施三唑类杀菌剂"]},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return model_path, labels


def test_classifier_predicts(model_files):
    clf = OnnxClassifier(*model_files)
    green = np.zeros((100, 100, 3), dtype=np.uint8)
    green[..., 1] = 200
    red = np.zeros((100, 100, 3), dtype=np.uint8)
    red[..., 0] = 200
    assert clf.predict(green).name == "healthy"
    pred = clf.predict(red)
    assert pred.name == "rust" and pred.label == "锈病" and pred.confidence > 0.9
    assert len(pred.top_k) == 2


def test_model_issue_merged_into_health(model_files, tmp_path):
    _, labels = model_files
    always_rust = tmp_path / "always_rust.onnx"
    build_model(always_rust, np.zeros((3, 2), dtype=np.float32), bias=(0.0, 5.0))
    clf = OnnxClassifier(always_rust, labels, min_confidence=0.5)
    scene = make_scene("healthy", size=(800, 600), seed=1)

    result = OrchardAnalyzer(model=clf).analyze(scene).result
    assert result.model is not None and result.model.name == "rust"
    model_issue = next(i for i in result.health.issues if i.code == "model:rust")
    assert model_issue.name == "锈病" and model_issue.suggestions == ["喷施三唑类杀菌剂"]
    assert result.health.penalties["model:rust"] > 0
    assert result.analyzer.endswith("+onnx:always_rust")
    assert result.health.level != "healthy"  # 严重类病害扣分后不再判为健康

    # 画面中没有树冠时不调用模型
    dormant = OrchardAnalyzer(model=clf).analyze(make_scene("dormant", size=(800, 600))).result
    assert dormant.model is None


def test_build_analyzer_loads_model(model_files, tmp_path):
    model_path, labels = model_files
    analyzer = build_analyzer(Settings(_env_file=None, data_dir=tmp_path, model_path=model_path, model_labels=labels))
    assert analyzer.model is not None
    # 模型文件不存在时仅记录日志，退回规则分析
    broken = build_analyzer(Settings(_env_file=None, data_dir=tmp_path, model_path=tmp_path / "missing.onnx", model_labels=labels))
    assert broken.model is None
