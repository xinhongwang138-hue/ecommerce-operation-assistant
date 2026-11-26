"""一个**不做训练**的"训练器"，只用来验证链路。

名字里带 standin 是刻意的：它会读 JSONL、会按步数打日志、会在输出目录写产物，
看起来像在训练，但**它不训练任何模型**。存在的理由有两条：

一，`FINETUNE_TRAIN_COMMAND` 需要一个可运行的样板。真正接训练器的人可以照着它写：
     读第一个参数给的 JSONL → 往第二个参数给的目录写产物 → 用退出码表达成败。
     别的都不用管，状态回写由 `run_finetune_job.py` 负责。

二，在没有 GPU 的机器上，仍然能验证"执行器 → 训练命令 → 回调 → 台账"这条缝。
     链路错了在这里就会错，不必等到租了八卡机才发现签名算错。

它做的事：
- 逐行校验 JSONL（必须是 `{"messages": [...]}` 且 user/assistant 轮次齐备），
  格式错就非零退出——这是真实的失败信号，不是演的；
- 按 `--steps` 打若干行日志，最后写 `manifest.json`（样本数、轮次分布、字符数）；
- 退出码：0 全部合法；2 有空行或格式错；3 样本数为 0。

用法：
    python scripts/standin_trainer.py --data train.jsonl --out output --base qwen-plus
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="stand-in 训练器：校验数据、写产物，不训练模型")
    parser.add_argument("--data", required=True, help="JSONL 数据集路径")
    parser.add_argument("--out", required=True, help="产物目录")
    parser.add_argument("--base", default="unknown", help="底座模型名（只记录，不加载）")
    parser.add_argument("--steps", type=int, default=10, help="打日志的步数，纯粹为了让日志像回事")
    args = parser.parse_args()

    data_path = Path(args.data)
    if not data_path.exists():
        print(f"[standin] 数据集不存在：{data_path}", flush=True)
        return 2

    samples = 0
    turns: dict[int, int] = {}
    chars = 0
    for lineno, line in enumerate(data_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            print(f"[standin] 第 {lineno} 行是空行", flush=True)
            return 2
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"[standin] 第 {lineno} 行不是合法 JSON：{exc}", flush=True)
            return 2
        messages = row.get("messages")
        if not isinstance(messages, list) or not messages:
            print(f"[standin] 第 {lineno} 行缺少 messages", flush=True)
            return 2
        roles = [item.get("role") for item in messages]
        if "user" not in roles or "assistant" not in roles:
            print(f"[standin] 第 {lineno} 行缺少 user/assistant 轮次", flush=True)
            return 2
        samples += 1
        turns[len(messages)] = turns.get(len(messages), 0) + 1
        chars += sum(len(item.get("content") or "") for item in messages)

    if samples == 0:
        print("[standin] 数据集里一条样本都没有", flush=True)
        return 3

    print(f"[standin] base={args.base} 载入 {samples} 条样本（不加载任何权重）", flush=True)
    for step in range(1, args.steps + 1):
        # 假的 loss 曲线也不给：这里只报进度，任何数字都会被当成训练指标
        print(f"[standin] step {step}/{args.steps} 数据校验通过", flush=True)
        time.sleep(0.05)

    output_dir = Path(args.out)
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "note": "stand-in：没有训练任何模型，产物只是数据校验结果",
        "base_model": args.base,
        "samples": samples,
        "turns_histogram": {str(key): value for key, value in sorted(turns.items())},
        "chars": chars,
        "steps": args.steps,
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[standin] 产物已写入 {output_dir / 'manifest.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
