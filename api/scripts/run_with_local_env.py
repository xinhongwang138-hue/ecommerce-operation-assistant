"""按 api/.env.local 的配置运行本地验收脚本（不把任何口令写进命令行）。

用法: python scripts/run_with_local_env.py scripts/seed_demo_data.py
"""
from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]
LOCAL_ENV = API_DIR / ".env.local"


def load_local_env() -> None:
    if not LOCAL_ENV.exists():
        raise SystemExit(f"未找到 {LOCAL_ENV}，请先运行 scripts/write_local_env.py")
    for line in LOCAL_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ[key.strip()] = value.strip()


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python scripts/run_with_local_env.py <script.py> [args...]")
    load_local_env()
    target = Path(sys.argv[1])
    if not target.is_absolute():
        target = API_DIR / target
    sys.argv = [str(target), *sys.argv[2:]]
    try:
        runpy.run_path(str(target), run_name="__main__")
    except SystemExit as exc:
        return int(exc.code or 0)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
