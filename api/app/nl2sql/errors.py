"""异常边界。

分成四类而不是一个 `Exception`，因为它们的 HTTP 语义完全不同：
- 仓没跑批 → 409（先做事，重试无意义）
- 模型没配 → 503（管理员动手，用户重试无意义）
- 模型给了不安全的 SQL 且改不对 → 422（用户自己换问法能解决）
- 模型返回的不是合法 JSON → 502（上游故障）
"""

from __future__ import annotations


class WarehouseNotReady(RuntimeError):
    """仓里还没有任何一次跑批结果。"""


class NL2SQLRejected(RuntimeError):
    """模型给的 SQL 过不了护栏或执行失败，且重试后仍未通过。

    `attempts` 里存每次被拒的原因，原样回给前端——用户需要知道"为什么不行"，
    而不是一句"生成失败"。
    """

    def __init__(self, message: str, *, attempts: list[str]) -> None:
        super().__init__(message)
        self.attempts = attempts
