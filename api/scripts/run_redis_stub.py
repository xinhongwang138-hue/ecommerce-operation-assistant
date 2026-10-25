"""本机开发用的轻量 Redis 兼容服务端（基于 fakeredis.TcpFakeServer）。

用途：在没有 Docker Desktop / 真实 Redis 的机器上，让 Celery broker 与
backend 能够连上 127.0.0.1:6379，从而跑通 api + worker 的完整链路。

与真实 Redis 的差异（仅开发环境可接受）：
- 不持久化：重启即丢数据
- 不支持集群、哨兵、模块、事务多命令优化

生产环境请用 docker-compose.yml 里的 redis:7-alpine。
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path
from urllib.parse import urlparse

from fakeredis import TcpFakeServer
from fakeredis._core._server import FakeServer

API_DIR = Path(__file__).resolve().parents[1]


def _read_redis_url() -> str | None:
    """从 api/.env.local（优先）或 api/.env 读取 REDIS_URL，避免把口令写进命令行。"""
    for name in (".env.local", ".env"):
        path = API_DIR / name
        if not path.exists():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("REDIS_URL="):
                return line.split("=", 1)[1].strip()
    return None


def _resolve_endpoint(argv: list[str]) -> tuple[str, int]:
    if len(argv) >= 2:
        return argv[1], int(argv[2]) if len(argv) > 2 else 6379
    url = _read_redis_url()
    if not url:
        raise SystemExit("未提供密码，且未能从 api/.env.local 或 api/.env 解析 REDIS_URL")
    parsed = urlparse(url)
    if not parsed.password:
        raise SystemExit("REDIS_URL 中未包含密码（格式应为 redis://:<password>@host:port/db）")
    return parsed.password, parsed.port or 6379


def main() -> int:
    password, port = _resolve_endpoint(sys.argv)

    # fakeredis 的 AUTH 比对 server_config["requirepass"]，需要构造带 config 的
    # FakeServer；TcpFakeServer 内部自建 FakeServer，因此这里替换成带密码的实例。
    server = TcpFakeServer(("127.0.0.1", port), server_type="redis")
    server.fake_server = FakeServer(
        server_type="redis",
        version=(8, 0),
        config={b"requirepass": password.encode()},
    )

    print(f"[redis-stub] listening on 127.0.0.1:{port} (requirepass enabled)", flush=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        thread.join()
    except KeyboardInterrupt:
        print("[redis-stub] stopped", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
