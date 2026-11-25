"""分析调度：后台线程池执行照片分析，并把结果写回数据库。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor

from PIL import UnidentifiedImageError
from sqlalchemy import select

from app.analysis import AnalysisContext, AnalysisResult, OrchardAnalyzer, UnsupportedImageError
from app.analysis.engine import AnalyzerConfig
from app.config import Settings
from app.database import Database
from app.models import ImageStatus, InspectionImage, now
from app.services.storage import Storage

logger = logging.getLogger(__name__)


def build_analyzer(settings: Settings) -> OrchardAnalyzer:
    """创建分析器；配置了 ONNX 模型时一并加载（加载失败只记录日志，不影响规则分析）。"""
    model = None
    if settings.model_path:
        if not settings.model_labels:
            logger.error("已设置 ORCHARD_MODEL_PATH，但缺少 ORCHARD_MODEL_LABELS，模型未启用")
        else:
            try:
                from app.analysis.model_adapter import OnnxClassifier

                model = OnnxClassifier(settings.model_path, settings.model_labels, settings.model_min_confidence)
                logger.info("已加载 ONNX 模型：%s", settings.model_path)
            except Exception:
                logger.exception("加载 ONNX 模型失败，将仅使用规则分析")
    return OrchardAnalyzer(AnalyzerConfig(max_side=settings.analysis_max_side), model=model)


def apply_result(image: InspectionImage, result: AnalysisResult) -> None:
    """把分析结果写入照片记录（完整 JSON + 便于检索的摘要字段）。"""
    image.result_json = result.model_dump_json()
    image.health_score = result.health.score
    image.health_level = result.health.level
    image.growth_stage = result.growth.stage
    image.vigor_level = result.growth.vigor_level
    issues = result.health.issues
    image.primary_issue = issues[0].name if issues else None
    image.issue_codes = ",".join(i.code for i in issues) or None


class AnalysisWorker:
    def __init__(
        self,
        database: Database,
        storage: Storage,
        analyzer: OrchardAnalyzer,
        workers: int = 2,
        inline: bool = False,
    ) -> None:
        self.database = database
        self.storage = storage
        self.analyzer = analyzer
        self.inline = inline
        self._executor = None if inline else ThreadPoolExecutor(max_workers=workers, thread_name_prefix="analysis")
        self._queued: set[int] = set()
        self._lock = threading.Lock()

    @property
    def queue_size(self) -> int:
        with self._lock:
            return len(self._queued)

    def submit(self, image_ids: Iterable[int]) -> None:
        for image_id in image_ids:
            with self._lock:
                if image_id in self._queued:
                    continue
                self._queued.add(image_id)
            if self._executor is None:
                self._run(image_id)
            else:
                self._executor.submit(self._run, image_id)

    def requeue_unfinished(self) -> int:
        """服务重启后，把未完成分析的照片重新加入队列。"""
        with self.database.session() as session:
            ids = list(
                session.scalars(select(InspectionImage.id).where(InspectionImage.status.in_(ImageStatus.UNFINISHED)))
            )
        self.submit(ids)
        return len(ids)

    def shutdown(self) -> None:
        if self._executor is not None:
            self._executor.shutdown(wait=True, cancel_futures=True)

    def _run(self, image_id: int) -> None:
        try:
            self.process(image_id)
        except Exception:
            logger.exception("处理照片 %s 时发生错误", image_id)
        finally:
            with self._lock:
                self._queued.discard(image_id)

    def process(self, image_id: int) -> None:
        with self.database.session() as session:
            image = session.get(InspectionImage, image_id)
            if image is None:
                return
            image.status = ImageStatus.PROCESSING
            session.commit()

            source = self.storage.path(image.stored_path)
            context = AnalysisContext(
                captured_at=image.captured_at,
                latitude=image.latitude,
                gsd_cm=image.gsd_cm,
                fruit_type=image.task.fruit_type if image.task else None,
            )
            try:
                output = self.analyzer.analyze(source, context)
            except UnsupportedImageError as exc:
                self._fail(image, str(exc))
            except (UnidentifiedImageError, OSError, SyntaxError) as exc:
                self._fail(image, f"图像读取失败：{exc}")
            except Exception as exc:
                logger.exception("分析照片 %s 失败", image_id)
                self._fail(image, f"分析出错：{exc}")
            else:
                if not source.exists():  # 分析期间照片已被删除
                    return
                old_overlay = image.overlay_path
                image.overlay_path = self.storage.save_overlay(output.overlay, image.task_id, image.id)
                if old_overlay and old_overlay != image.overlay_path:
                    self.storage.delete(old_overlay)
                apply_result(image, output.result)
                image.status = ImageStatus.DONE
                image.error = None
                image.analyzed_at = now()
            session.commit()

    @staticmethod
    def _fail(image: InspectionImage, message: str) -> None:
        image.status = ImageStatus.FAILED
        image.error = message[:1000]
        image.analyzed_at = now()
