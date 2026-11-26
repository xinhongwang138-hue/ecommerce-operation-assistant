"""轮换 AUTH_SECRET_KEY：生成强随机密钥写入 .env 与 .env.local，不打印密钥本身。

历史令牌会因此全部失效，属于预期行为（旧令牌本就应作废）。
"""
from __future__ import annotations

import re
import secrets
from pathlib import Path

API_DIR = Path(__file__).resolve().parents[1]


def main() -> int:
    new_secret = secrets.token_urlsafe(48)
    for name in (".env", ".env.local"):
        path = API_DIR / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if "AUTH_SECRET_KEY=" in text:
            text = re.sub(r"(?m)^AUTH_SECRET_KEY=.*$", f"AUTH_SECRET_KEY={new_secret}", text)
        else:
            text = text.rstrip() + f"\nAUTH_SECRET_KEY={new_secret}\n"
        path.write_text(text, encoding="utf-8")
        print(f"[rotate] updated {name} (length={len(new_secret)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
