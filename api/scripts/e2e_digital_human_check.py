"""数字人 / WS 聊天的端到端验收脚本（真实 HTTP + WebSocket，不打桩）。

覆盖：
1. 登录取 token
2. GET /api/v1/digital-human/credentials 能否拿到凭证（未配置时应为空串）
3. POST /api/v1/chat/ws-ticket 取一次性票据
4. WS /ws/chat 用票据握手，观察是否收到 user_ack / status 帧
5. 用错误的票据握手，验证被拒绝

口令从环境变量 DEMO_OWNER_PASSWORD 读取，不写在脚本里。
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

import httpx
import websockets

BASE = os.environ.get("E2E_BASE_URL", "http://127.0.0.1:8001")
WS_BASE = BASE.replace("http://", "ws://").replace("https://", "wss://")
# WS 路由与其它 API 一样挂在 /api/v1 前缀下（见 app/main.py 的 include_router）。
WS_PATH = os.environ.get("E2E_WS_PATH", "/api/v1/ws/chat")
EMAIL = os.environ.get("DEMO_OWNER_EMAIL", "demo.owner@example.com")
PASSWORD = os.environ.get("DEMO_OWNER_PASSWORD", "")
WORKSPACE_ID = int(os.environ.get("DEMO_WORKSPACE_ID", "1"))

PASS = "PASS"
FAIL = "FAIL"


def line(tag: str, ok: bool, detail: str = "") -> None:
    print(f"[{PASS if ok else FAIL}] {tag}" + (f" :: {detail}" if detail else ""), flush=True)


async def main() -> int:
    if not PASSWORD:
        print("缺少环境变量 DEMO_OWNER_PASSWORD")
        return 2

    failures = 0
    async with httpx.AsyncClient(base_url=BASE, timeout=20.0) as client:
        # 1. 登录
        resp = await client.post(
            "/api/v1/auth/login",
            json={"email": EMAIL, "password": PASSWORD},
        )
        ok = resp.status_code == 200
        line("登录", ok, f"HTTP {resp.status_code}")
        if not ok:
            print(resp.text[:300])
            return 1
        token = resp.json()["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        # 2. 数字人凭证
        resp = await client.get("/api/v1/digital-human/credentials", headers=headers)
        ok = resp.status_code == 200
        body = resp.json() if ok else {}
        configured = bool(body.get("app_id") and body.get("app_secret"))
        line(
            "数字人凭证接口",
            ok,
            f"HTTP {resp.status_code}, 已配置={configured}, gateway={str(body.get('gateway_server'))[:52]}",
        )
        if not ok:
            failures += 1

        # 3. 无鉴权应被拒
        resp = await client.get("/api/v1/digital-human/credentials")
        line("无鉴权访问凭证被拒", resp.status_code == 401, f"HTTP {resp.status_code}")

        # 4. WS 票据
        resp = await client.post("/api/v1/chat/ws-ticket", headers=headers)
        ok = resp.status_code == 200 and "ticket" in resp.json()
        line("换取 WS 票据", ok, f"HTTP {resp.status_code}")
        if not ok:
            print(resp.text[:300])
            return 1
        ticket = resp.json()["ticket"]

        # 5. 错误票据握手应被拒
        try:
            async with websockets.connect(
                f"{WS_BASE}{WS_PATH}?ticket=forged-ticket&workspace_id={WORKSPACE_ID}"
            ) as ws:
                await asyncio.wait_for(ws.recv(), timeout=8)
                line("伪造票据握手被拒", False, "连接竟然成功了")
                failures += 1
        except Exception:
            line("伪造票据握手被拒", True)

        # 6. 真实票据握手
        frames: list[dict] = []
        try:
            async with websockets.connect(
                f"{WS_BASE}{WS_PATH}?ticket={ticket}&workspace_id={WORKSPACE_ID}"
            ) as ws:
                await ws.send(
                    json.dumps(
                        {
                            "type": "user_text",
                            "text": "帮我看看这个商品的表现",
                            "context": {"product_id": 1, "store_id": 1},
                        }
                    )
                )
                deadline = asyncio.get_event_loop().time() + 25
                while asyncio.get_event_loop().time() < deadline:
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=8)
                    except asyncio.TimeoutError:
                        break
                    frame = json.loads(raw)
                    frames.append(frame)
                    if frame.get("type") == "turn_end":
                        break
        except Exception as exc:
            line("真实票据握手并对话", False, f"{type(exc).__name__}: {str(exc)[:140]}")
            frames = []
        else:
            types = [f.get("type") for f in frames]
            line(
                "真实票据握手并对话",
                "user_ack" in types,
                f"收到帧 {types[:8]}",
            )
            for f in frames:
                if f.get("type") == "status" and f.get("hint"):
                    line("  运行事件", True, str(f["hint"])[:90])
                if f.get("type") == "error":
                    line("  错误帧", False, str(f.get("message"))[:120])
                if f.get("type") == "bot_text":
                    line("  数字人朗读文本", True, str(f.get("text"))[:120])

    print()
    print("=== 验收结论 ===")
    print("全部关键项通过" if failures == 0 else f"存在 {failures} 项失败")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
