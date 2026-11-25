#!/usr/bin/env python3
"""一键启动智慧果园巡检分析系统。

    python run.py                      # 仅本机访问 http://127.0.0.1:8000
    python run.py --host 0.0.0.0       # 局域网内的无人机机场/地面站也可推送照片
    python run.py --reload             # 开发模式，代码修改后自动重启
"""

from __future__ import annotations

import argparse
from pathlib import Path

import uvicorn

BACKEND_DIR = Path(__file__).resolve().parent / "backend"


def main() -> None:
    parser = argparse.ArgumentParser(description="启动智慧果园巡检分析系统")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，局域网访问请用 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="开发模式：代码变化时自动重启")
    args = parser.parse_args()
    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(BACKEND_DIR),
        reload_dirs=[str(BACKEND_DIR)] if args.reload else None,
    )


if __name__ == "__main__":
    main()
