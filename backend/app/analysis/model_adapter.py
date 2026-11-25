"""可选：接入自训练的 ONNX 图像分类模型（如病害识别模型）。

使用方法：
1. 用自己的无人机照片训练分类模型（ResNet、EfficientNet、YOLO-cls 等均可），导出为 ONNX；
2. 准备标签文件 labels.json，例如::

    {
      "input_size": [224, 224],
      "mean": [0.485, 0.456, 0.406],
      "std": [0.229, 0.224, 0.225],
      "labels": [
        {"name": "healthy", "label": "健康", "healthy": true},
        {"name": "scab", "label": "苹果黑星病", "severity": "high",
         "suggestions": ["清除落叶病果", "喷施苯醚甲环唑等杀菌剂"]}
      ]
    }

3. 设置环境变量 ORCHARD_MODEL_PATH 和 ORCHARD_MODEL_LABELS 后重启服务。

模型输出为 [1, 类别数] 的 logits 或概率。识别为非健康类别且置信度达到阈值时，
结果会作为一条“模型识别”问题加入健康评估，并参与扣分。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from PIL import Image

from app.analysis.result import ModelPrediction


@dataclass(frozen=True)
class LabelInfo:
    name: str
    label: str
    healthy: bool = False
    severity: str = "medium"
    category: str = "model"
    description: str = ""
    causes: tuple[str, ...] = ()
    suggestions: tuple[str, ...] = field(default_factory=tuple)


def _parse_label(item: str | dict) -> LabelInfo:
    if isinstance(item, str):
        return LabelInfo(name=item, label=item, healthy=item.lower() in ("healthy", "health", "normal", "健康"))
    name = str(item["name"])
    return LabelInfo(
        name=name,
        label=str(item.get("label", name)),
        healthy=bool(item.get("healthy", name.lower() in ("healthy", "normal"))),
        severity=str(item.get("severity", "medium")),
        category=str(item.get("category", "model")),
        description=str(item.get("description", "")),
        causes=tuple(item.get("causes", ())),
        suggestions=tuple(item.get("suggestions", ())),
    )


class OnnxClassifier:
    def __init__(self, model_path: str | Path, labels_path: str | Path, min_confidence: float = 0.5) -> None:
        import onnxruntime as ort  # 可选依赖，仅在启用模型时导入

        self.model_path = Path(model_path)
        self.session = ort.InferenceSession(str(self.model_path), providers=["CPUExecutionProvider"])
        self.input_name = self.session.get_inputs()[0].name
        self.min_confidence = min_confidence

        config = json.loads(Path(labels_path).read_text(encoding="utf-8"))
        if isinstance(config, list):
            config = {"labels": config}
        size = config.get("input_size", 224)
        self.input_hw = (int(size), int(size)) if isinstance(size, int) else (int(size[0]), int(size[1]))
        self.mean = np.array(config.get("mean", [0.485, 0.456, 0.406]), dtype=np.float32)
        self.std = np.array(config.get("std", [0.229, 0.224, 0.225]), dtype=np.float32)
        self.layout = str(config.get("layout", "NCHW")).upper()
        self.labels = [_parse_label(item) for item in config["labels"]]
        if not self.labels:
            raise ValueError("labels.json 中没有类别")
        self._by_name = {info.name: info for info in self.labels}

    @property
    def name(self) -> str:
        return f"onnx:{self.model_path.stem}"

    def label_info(self, name: str) -> LabelInfo:
        return self._by_name.get(name, LabelInfo(name=name, label=name))

    def predict(self, image: np.ndarray, top_k: int = 3) -> ModelPrediction:
        h, w = self.input_hw
        resized = Image.fromarray(image).resize((w, h), Image.Resampling.BILINEAR)
        x = (np.asarray(resized, dtype=np.float32) / 255.0 - self.mean) / self.std
        if self.layout == "NCHW":
            x = x.transpose(2, 0, 1)
        x = np.ascontiguousarray(x[None], dtype=np.float32)

        output = np.asarray(self.session.run(None, {self.input_name: x})[0], dtype=np.float64).reshape(-1)
        if output.size != len(self.labels):
            raise ValueError(f"模型输出 {output.size} 个类别，但标签文件中有 {len(self.labels)} 个")
        is_prob = bool(np.all(output >= 0) and abs(output.sum() - 1.0) < 1e-3)
        if is_prob:
            probs = output
        else:
            exp = np.exp(output - output.max())
            probs = exp / exp.sum()

        order = np.argsort(probs)[::-1]
        best = self.labels[int(order[0])]
        return ModelPrediction(
            name=best.name,
            label=best.label,
            confidence=round(float(probs[order[0]]), 4),
            healthy=best.healthy,
            top_k=[
                {"name": self.labels[int(i)].name, "label": self.labels[int(i)].label,
                 "confidence": round(float(probs[int(i)]), 4)}
                for i in order[:top_k]
            ],
        )
