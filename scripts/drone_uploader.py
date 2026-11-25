#!/usr/bin/env python3
"""无人机照片自动上传工具。

只依赖 Python 标准库（3.8+），可部署在地面站电脑、机场边缘计算盒或照片同步服务器上：
监控照片目录（如 DJI Pilot 2 / 大疆司空同步下来的航线照片文件夹），发现新照片后自动推送到
果园巡检分析系统的 /api/v1/drone/ingest 接口。已上传的文件记录在状态文件中，断网后会自动重试。

默认每个子文件夹视为一次巡检任务（文件夹名即任务编号），也可以用 --mission 指定固定编号。

示例：
    # 持续监控目录，每 10 秒扫描一次
    python drone_uploader.py --server http://192.168.1.10:8000 --dir "D:/DJI/Mission" --api-key 你的密钥

    # 只上传一次（如上传演示数据）
    python drone_uploader.py --server http://127.0.0.1:8000 --dir demo_images --once
"""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff"}
STATE_FILENAME = ".orchard_upload_state.json"

log = logging.getLogger("drone_uploader")


class AuthError(Exception):
    pass


def build_multipart(fields: dict, files: list) -> tuple:
    """构造 multipart/form-data 请求体。files: [(字段名, Path)]"""
    boundary = uuid.uuid4().hex
    chunks = []
    for key, value in fields.items():
        if value is None or value == "":
            continue
        chunks.append(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n'.encode()
        )
    for field, path in files:
        filename = path.name.replace('"', "%22")
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        chunks.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{field}"; filename="{filename}"\r\n'
                f"Content-Type: {ctype}\r\n\r\n"
            ).encode()
        )
        chunks.append(path.read_bytes())
        chunks.append(b"\r\n")
    chunks.append(f"--{boundary}--\r\n".encode())
    return b"".join(chunks), f"multipart/form-data; boundary={boundary}"


class Uploader:
    def __init__(self, args: argparse.Namespace) -> None:
        self.server = args.server.rstrip("/")
        self.root = Path(args.dir).resolve()
        self.api_key = args.api_key
        self.mission = args.mission
        self.batch_size = max(1, args.batch_size)
        self.settle = args.settle
        self.extra = {
            "orchard": args.orchard,
            "plot": args.plot,
            "fruit_type": args.fruit_type,
            "drone_model": args.drone_model,
            "operator": args.operator,
            "flight_height_m": args.flight_height,
        }
        self.state_path = Path(args.state) if args.state else self.root / STATE_FILENAME
        self.state = self._load_state()

    # —— 状态文件 ——
    def _load_state(self) -> dict:
        if self.state_path.exists():
            try:
                return json.loads(self.state_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                log.warning("状态文件损坏，将重新开始记录：%s", self.state_path)
        return {"files": {}}

    def _save_state(self) -> None:
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        tmp.write_text(json.dumps(self.state, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.state_path)

    @staticmethod
    def _fingerprint(path: Path) -> str:
        st = path.stat()
        return f"{st.st_size}:{int(st.st_mtime)}"

    # —— 扫描与分组 ——
    def mission_for(self, path: Path) -> str:
        if self.mission:
            return self.mission
        parent = path.parent
        return parent.name if parent != self.root else self.root.name

    def pending_files(self) -> list:
        now = time.time()
        pending = []
        for path in sorted(self.root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in IMAGE_EXTS or path.name.startswith("."):
                continue
            try:
                if now - path.stat().st_mtime < self.settle:  # 文件可能仍在写入
                    continue
                key = path.relative_to(self.root).as_posix()
                record = self.state["files"].get(key)
                if record and record.get("fingerprint") == self._fingerprint(path):
                    continue
            except OSError:
                continue
            pending.append(path)
        return pending

    # —— 上传 ——
    def upload_batch(self, mission: str, paths: list) -> dict:
        fields = dict(self.extra, mission_code=mission)
        body, content_type = build_multipart(fields, [("files", p) for p in paths])
        request = urllib.request.Request(f"{self.server}/api/v1/drone/ingest", data=body, method="POST")
        request.add_header("Content-Type", content_type)
        if self.api_key:
            request.add_header("X-API-Key", self.api_key)
        try:
            with urllib.request.urlopen(request, timeout=300) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            if exc.code == 401:
                raise AuthError("接口密钥无效，请检查 --api-key") from None
            raise RuntimeError(f"服务器返回 {exc.code}：{detail[:300]}") from None

    def run_once(self) -> tuple:
        pending = self.pending_files()
        if not pending:
            return 0, 0
        groups: dict = {}
        for path in pending:
            groups.setdefault(self.mission_for(path), []).append(path)

        done = failed = 0
        for mission, paths in groups.items():
            for i in range(0, len(paths), self.batch_size):
                batch = paths[i : i + self.batch_size]
                try:
                    result = self.upload_batch(mission, batch)
                except AuthError:
                    raise
                except (OSError, RuntimeError, ValueError) as exc:
                    failed += len(batch)
                    log.warning("任务 %s：%d 张照片上传失败（稍后重试）：%s", mission, len(batch), exc)
                    continue
                by_name = {}
                for item in result.get("items", []):
                    by_name.setdefault(item["filename"], []).append(item)
                for path in batch:
                    items = by_name.get(path.name) or [{}]
                    item = items.pop(0) if len(items) > 1 else items[0]
                    key = path.relative_to(self.root).as_posix()
                    self.state["files"][key] = {
                        "fingerprint": self._fingerprint(path),
                        "mission": mission,
                        "status": item.get("status", "unknown"),
                        "image_id": item.get("image_id"),
                        "error": item.get("error"),
                        "uploaded_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                    }
                    if item.get("status") == "rejected":
                        log.warning("%s 被拒绝：%s", key, item.get("error"))
                self._save_state()
                done += len(batch)
                log.info(
                    "任务 %s（ID %s）：上传 %d 张，新增 %d，重复 %d，拒绝 %d",
                    mission, result.get("task_id"), len(batch), result.get("accepted", 0),
                    result.get("duplicates", 0), result.get("rejected", 0),
                )
        return done, failed


def main() -> int:
    parser = argparse.ArgumentParser(description="监控照片目录并推送到果园巡检分析系统")
    parser.add_argument("--server", required=True, help="系统地址，如 http://192.168.1.10:8000")
    parser.add_argument("--dir", required=True, help="无人机照片目录")
    parser.add_argument("--api-key", default=None, help="接口密钥（服务端设置了 ORCHARD_API_KEY 时必填）")
    parser.add_argument("--mission", default=None, help="固定任务编号；默认使用照片所在文件夹名")
    parser.add_argument("--orchard", default=None, help="果园名称")
    parser.add_argument("--plot", default=None, help="地块/片区")
    parser.add_argument("--fruit-type", default=None, help="果树种类")
    parser.add_argument("--drone-model", default=None, help="无人机型号")
    parser.add_argument("--operator", default=None, help="飞手")
    parser.add_argument("--flight-height", type=float, default=None, help="飞行相对高度（米），照片缺少高度信息时使用")
    parser.add_argument("--batch-size", type=int, default=5, help="每次请求上传的照片数")
    parser.add_argument("--interval", type=float, default=10.0, help="扫描间隔（秒）")
    parser.add_argument("--settle", type=float, default=5.0, help="文件最后修改后等待多少秒再上传")
    parser.add_argument("--state", default=None, help="状态文件路径（默认在照片目录下）")
    parser.add_argument("--once", action="store_true", help="只扫描上传一次后退出")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    root = Path(args.dir)
    if not root.is_dir():
        log.error("目录不存在：%s", root)
        return 2
    if args.once:
        args.settle = 0
    uploader = Uploader(args)
    log.info("开始监控 %s → %s", uploader.root, uploader.server)

    backoff = args.interval
    while True:
        try:
            done, failed = uploader.run_once()
        except AuthError as exc:
            log.error(str(exc))
            return 3
        except KeyboardInterrupt:
            return 0
        if args.once:
            log.info("完成：成功 %d 张，失败 %d 张", done, failed)
            return 1 if failed else 0
        backoff = min(backoff * 2, 300) if failed else args.interval
        try:
            time.sleep(backoff)
        except KeyboardInterrupt:
            log.info("已停止")
            return 0


if __name__ == "__main__":
    sys.exit(main())
