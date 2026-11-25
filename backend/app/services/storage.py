"""文件存储：原图、缩略图、分析标注图，统一存放在 data 目录下。"""

from __future__ import annotations

import hashlib
import logging
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from PIL import Image, ImageOps

logger = logging.getLogger(__name__)

_UNSAFE_CHARS = re.compile(r"[^\w.\-]+", re.UNICODE)
CHUNK_SIZE = 1024 * 1024


class UploadTooLargeError(Exception):
    pass


@dataclass
class SavedFile:
    rel_path: str
    size: int
    sha256: str


def safe_filename(filename: str, max_length: int = 80) -> str:
    """去掉路径与特殊字符，保留中文、字母、数字、点和横线。"""
    name = filename.replace("\\", "/").split("/")[-1].strip() or "image"
    name = _UNSAFE_CHARS.sub("_", name).strip("._") or "image"
    if len(name) > max_length:
        stem, dot, ext = name.rpartition(".")
        name = (stem[: max_length - len(ext) - 1] + "." + ext) if dot and len(ext) <= 8 else name[:max_length]
    return name


class Storage:
    def __init__(self, root: Path, max_bytes: int) -> None:
        self.root = Path(root).resolve()
        self.max_bytes = max_bytes

    def path(self, rel_path: str) -> Path:
        full = (self.root / rel_path).resolve()
        if not full.is_relative_to(self.root):
            raise ValueError(f"非法路径：{rel_path}")
        return full

    def save_stream(self, task_id: int, stream: BinaryIO, filename: str) -> SavedFile:
        """流式写入上传文件，同时计算 SHA-256 并检查大小上限。"""
        rel_path = f"uploads/{task_id}/{uuid.uuid4().hex[:12]}_{safe_filename(filename)}"
        dest = self.path(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".part")
        digest = hashlib.sha256()
        size = 0
        try:
            with open(tmp, "wb") as out:
                while chunk := stream.read(CHUNK_SIZE):
                    size += len(chunk)
                    if size > self.max_bytes:
                        raise UploadTooLargeError
                    digest.update(chunk)
                    out.write(chunk)
            tmp.replace(dest)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        return SavedFile(rel_path=rel_path, size=size, sha256=digest.hexdigest())

    def make_thumbnail(self, src_rel: str, task_id: int, image_id: int, max_side: int = 480) -> str:
        rel_path = f"thumbnails/{task_id}/{image_id}.jpg"
        dest = self.path(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with Image.open(self.path(src_rel)) as img:
            if img.format == "JPEG":
                img.draft("RGB", (max_side, max_side))
            thumb = ImageOps.exif_transpose(img).convert("RGB")
            thumb.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            thumb.save(dest, "JPEG", quality=82, optimize=True)
        return rel_path

    def save_overlay(self, image: Image.Image, task_id: int, image_id: int) -> str:
        rel_path = f"overlays/{task_id}/{image_id}.jpg"
        dest = self.path(rel_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        image.convert("RGB").save(dest, "JPEG", quality=85)
        return rel_path

    def delete(self, *rel_paths: str | None) -> None:
        for rel in rel_paths:
            if not rel:
                continue
            try:
                self.path(rel).unlink(missing_ok=True)
            except OSError:
                logger.warning("删除文件失败：%s", rel, exc_info=True)

    def delete_task_files(self, task_id: int) -> None:
        for sub in ("uploads", "thumbnails", "overlays"):
            shutil.rmtree(self.root / sub / str(task_id), ignore_errors=True)
