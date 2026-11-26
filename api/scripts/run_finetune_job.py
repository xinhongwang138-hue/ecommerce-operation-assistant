"""微调作业执行器：把一条 `registered` 的作业真正跑起来。

分工刻意分开：

    读 → 直接读库。这是本机运维脚本，取自己家的数据没必要绕一圈 HTTP。
    写 → 只走回调（HMAC 签名）。**这一点不能改**：如果执行器也直接改库，
        回调协议就永远不会有真实流量压过，等接第三方训练机时才发现签名算错了。

平台自己不训练。这里执行的是 `FINETUNE_TRAIN_COMMAND` 指向的命令——
llamafactory、torchrun、云厂商 CLI 都行，只要它吃一个 JSONL、吐一个目录。

用法：

    # 后端 .env.local 里要有 FINETUNE_CALLBACK_SECRET，且与实际签名一致
    export FINETUNE_CALLBACK_SECRET=...            # Windows: set 或 .env.local
    export FINETUNE_TRAIN_COMMAND="python -m mytrainer --data {jsonl} --out {output} --base {base_model}"

    cd api
    .venv/Scripts/python.exe scripts/run_finetune_job.py --job 3

退出码：0 训练成功；1 训练失败或回写失败；2 作业状态不对；3 没配训练命令。
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.db.session import SessionLocal
from app.finetune import service as ft_service
from app.models import FinetuneJob

DEFAULT_API = "http://127.0.0.1:8001"
LOG_TAIL_CHARS = 2000
DEFAULT_TIMEOUT_SECONDS = 6 * 3600


def _sign(secret: str, job_id: int, raw: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), f"{job_id}.".encode("utf-8") + raw, hashlib.sha256).hexdigest()


def post_callback(api: str, secret: str, job_id: int, payload: dict) -> tuple[int, str]:
    """把状态回给平台。返回 (HTTP 状态码, 响应文本)。"""
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        f"{api.rstrip('/')}/api/v1/finetune/jobs/{job_id}/callback",
        data=raw,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "X-Finetune-Signature": _sign(secret, job_id, raw),
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


def split_command(command: str) -> list[str]:
    """切分命令。Windows 上不能用 shlex 的 POSIX 模式：它把反斜杠当转义，
    `D:////learning////...` 会被啃成 `D:learning...`，训练器一启动就"数据集不存在"（实测踩到）。
    posix=False 保留反斜杠与引号，再把成对引号剥掉就对了。
    """
    if os.name == "nt":
        return [token.strip('"') for token in shlex.split(command, posix=False)]
    return shlex.split(command)


def build_command(
    template: str, *, jsonl: Path, output: Path, base_model: str, job_id: int, api_root: Path
) -> str:
    """模板替换。用 str.replace 而不是 .format：训练命令里常带 `{}`（JSON 参数、日志格式），
    format 会把它们当占位符直接炸掉。

    `{api_root}` 是给随仓库附带的脚本用的：命令在 job 目录里执行，相对路径会指到别处。
    """
    return (
        template.replace("{api_root}", str(api_root).replace("\\", "/"))
        .replace("{jsonl}", str(jsonl))
        .replace("{output}", str(output))
        .replace("{base_model}", base_model)
        .replace("{job_id}", str(job_id))
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="跑一条已登记的微调作业")
    parser.add_argument("--job", type=int, required=True, help="作业 id")
    parser.add_argument("--api", default=os.getenv("FINETUNE_API_URL", DEFAULT_API))
    parser.add_argument(
        "--workdir",
        default=str(Path(__file__).resolve().parents[1] / "finetune-runs"),
        help="数据集与产物的落地目录",
    )
    parser.add_argument("--timeout", type=int, default=int(os.getenv("FINETUNE_TRAIN_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS)))
    args = parser.parse_args()

    # 环境变量优先，其次读 .env.local（和服务共用一份配置，不用两处维护同一个密钥）
    settings = get_settings()
    secret = (os.getenv("FINETUNE_CALLBACK_SECRET") or settings.finetune_callback_secret or "").strip()
    if not secret:
        print("缺少 FINETUNE_CALLBACK_SECRET：没有它就无法把结果写回台账，先别跑。")
        return 1

    with SessionLocal() as db:
        job = db.get(FinetuneJob, args.job)
        if job is None:
            print(f"作业 #{args.job} 不存在")
            return 2
        current = job.status.value
        if current != "registered":
            print(f"作业 #{args.job} 当前是 {current}，只有 registered 的作业能开跑；要重跑请新登记一条")
            return 2
        base_model = job.base_model
        dataset_id = job.dataset_id
        workspace_id = job.workspace_id
        filename, payload = ft_service.export_jsonl(db, workspace_id, dataset_id)

    run_dir = Path(args.workdir) / f"job-{args.job}"
    run_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = run_dir / filename
    jsonl_path.write_text(payload, encoding="utf-8")
    output_dir = run_dir / "output"
    output_dir.mkdir(exist_ok=True)
    log_path = run_dir / "run.log"

    print(f"数据集已导出：{jsonl_path}（{payload.count(chr(10))} 行）")

    template = (os.getenv("FINETUNE_TRAIN_COMMAND") or settings.finetune_train_command or "").strip()
    if not template:
        note = "未配置 FINETUNE_TRAIN_COMMAND，无法启动训练"
        print(note)
        code, body = post_callback(args.api, secret, args.job, {"status": "failed", "metrics": {}, "note": note})
        print(f"  回写 {code} {body[:200]}")
        return 3

    api_root = Path(__file__).resolve().parents[1]
    command = build_command(
        template,
        jsonl=jsonl_path,
        output=output_dir,
        base_model=base_model,
        job_id=args.job,
        api_root=api_root,
    )
    print(f"训练命令：{command}")

    code, body = post_callback(args.api, secret, args.job, {"status": "running", "metrics": {}, "note": "执行器已启动"})
    if code != 200:
        print(f"回写 running 失败：{code} {body[:300]}")
        return 1

    started = time.monotonic()
    try:
        completed = subprocess.run(  # noqa: S603 - 命令来自运维配置，不是外部输入
            split_command(command),
            cwd=str(run_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=args.timeout,
        )
        exit_code = completed.returncode
        stdout, stderr = completed.stdout or "", completed.stderr or ""
    except subprocess.TimeoutExpired as exc:
        exit_code = -1
        stdout = (exc.stdout or "") if isinstance(exc.stdout, str) else ""
        stderr = f"超过 {args.timeout} 秒未结束，已终止"
    except FileNotFoundError as exc:
        exit_code = -2
        stdout = ""
        stderr = f"命令不存在：{exc}"

    duration = round(time.monotonic() - started, 1)
    log_path.write_text(f"$ {command}\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}\n", encoding="utf-8")
    tail = (stdout + "\n" + stderr).strip()[-LOG_TAIL_CHARS:]

    metrics = {
        "exit_code": exit_code,
        "duration_seconds": duration,
        "log_path": str(log_path),
        "log_tail": tail,
    }
    status = "succeeded" if exit_code == 0 else "failed"
    code, body = post_callback(
        args.api,
        secret,
        args.job,
        {"status": status, "metrics": metrics, "note": f"退出码 {exit_code}，耗时 {duration}s"},
    )
    print(f"结果 {status}（退出码 {exit_code}，{duration}s）→ 回写 {code}")
    if code != 200:
        print(body[:500])
        return 1
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
