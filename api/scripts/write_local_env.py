"""本地开发用：把 api/.env 里的 DATABASE_URL 指向本机 MySQL（127.0.0.1:3306）。

背景：本机 Docker Desktop 不可用时，项目的 compose 栈（MySQL 3307 / Redis 6379 /
PG 5433）无法拉起。本脚本生成一份 api/.env.local，供开发机直连本机 MySQL 使用。

安全约束：只在本机开发环境使用；密码从 compose 的 .env 派生，不写死在脚本里，
也不会打印到日志。生产与 Docker 部署路径不使用本文件。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

API_DIR = Path(__file__).resolve().parents[1]
ROOT_DIR = API_DIR.parent
COMPOSE_ENV = ROOT_DIR / ".env"
LOCAL_ENV = API_DIR / ".env.local"
LOCAL_MYSQL_PORT = 3306
LOCAL_MYSQL_PASSWORD = "123456"
LOCAL_MYSQL_USER = "root"


def read_compose_env() -> dict[str, str]:
    values: dict[str, str] = {}
    if not COMPOSE_ENV.exists():
        return values
    for line in COMPOSE_ENV.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def rewrite_database_url(original: str) -> str:
    """把 compose 里的 MySQL 连接改写到本机 3306 端口，密码沿用本机实例的口令。"""
    parts = urlsplit(original)
    netloc = f"{LOCAL_MYSQL_USER}:{LOCAL_MYSQL_PASSWORD}@127.0.0.1:{LOCAL_MYSQL_PORT}"
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def main() -> int:
    compose = read_compose_env()
    if not compose:
        print("未找到根目录 .env，无法派生本地配置", file=sys.stderr)
        return 1

    source = API_DIR / ".env"
    if not source.exists():
        print(f"未找到 {source}", file=sys.stderr)
        return 1

    lines = source.read_text(encoding="utf-8").splitlines()
    out: list[str] = [
        "# 本文件由 scripts/write_local_env.py 生成：Docker 不可用时的本机直连配置。",
        "# 仅供开发机使用，勿提交（.env.* 已被 .gitignore 忽略）。",
        "",
    ]
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("DATABASE_URL="):
            original = stripped.split("=", 1)[1]
            out.append(f"DATABASE_URL={rewrite_database_url(original)}")
        else:
            out.append(line)

    LOCAL_ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    masked = re.sub(r"://[^@]+@", "://***@", rewrite_database_url(
        compose.get("DATABASE_URL", "mysql+pymysql://root@127.0.0.1:3306/ecommerce_operation")
    ))
    print(f"已生成 {LOCAL_ENV}")
    print(f"DATABASE_URL -> {masked}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
