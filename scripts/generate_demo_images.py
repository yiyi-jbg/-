#!/usr/bin/env python3
"""生成演示用的模拟无人机巡检照片。

生成 4 次巡检（4 月花期到 8 月挂果）× 8 棵果树的合成俯拍照片，照片中写入与大疆无人机一致的
GPS、相对高度、云台角度等元数据；同时生成果树档案 trees.csv，可在“果树档案”页面导入。

用法：
    python scripts/generate_demo_images.py                 # 输出到 demo_images/
    python scripts/generate_demo_images.py --out D:/demo --size 1600x1200

注意：照片为程序合成，仅用于体验系统流程，不代表真实果园数据。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.demo_data import DEMO_FLIGHTS, generate_demo_set  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description="生成模拟无人机巡检照片")
    parser.add_argument("--out", default=str(ROOT / "demo_images"), help="输出目录（默认 demo_images/）")
    parser.add_argument("--size", default="1600x1200", help="照片尺寸，如 1600x1200")
    parser.add_argument("--flights", type=int, default=len(DEMO_FLIGHTS), help="巡检次数（1~4）")
    parser.add_argument("--lat", type=float, default=34.30215, help="果园起点纬度")
    parser.add_argument("--lon", type=float, default=108.08571, help="果园起点经度")
    args = parser.parse_args()

    width, height = (int(v) for v in args.size.lower().split("x"))
    paths = generate_demo_set(
        args.out,
        origin=(args.lat, args.lon),
        size=(width, height),
        flights=max(1, min(args.flights, len(DEMO_FLIGHTS))),
    )
    out = Path(args.out).resolve()
    print(f"已生成 {len(paths)} 张模拟照片：{out}")
    print(f"果树档案：{out / 'trees.csv'}")
    print("下一步：在网页“上传分析”中上传照片，或运行 scripts/drone_uploader.py 模拟无人机自动推送。")


if __name__ == "__main__":
    main()
