"""仓连接与只读查询护栏。

两条独立的防线，缺一不可：

1. **SQL 护栏**（`guard_select`）：只放行单条 SELECT/WITH，表白名单限本仓分层表，
   禁掉 DuckDB 的文件与外部源函数（read_csv / attach / postgres_scan 等）。
2. **只读连接**（`read_only=True`）：即使护栏被绕过，连接本身也写不进任何字节。

只做第 1 条是"靠正则防注入"，只做第 2 条是"报错信息比攻击面还大"。两条叠加，
才敢把自由 SQL 开放给前台。
"""

from __future__ import annotations

import os
import re
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import duckdb

from app.core.config import get_settings

# 查询结果上限：一个 4 人运营团队不会真的需要一次看 10 万行，
# 但没有上限时一条 SELECT * 就足以把 API 进程的内存打完。
DEFAULT_MAX_ROWS = 500

ALLOWED_PREFIXES = ("ods_", "dwd_", "dws_", "ads_", "meta_")

FORBIDDEN_PATTERN = re.compile(
    r"\b("
    r"attach|detach|install|load|copy|export|import|pragma|call|"
    r"read_csv|read_csv_auto|read_parquet|read_json|read_json_auto|read_text|"
    r"glob|httpfs|sqlite_scan|postgres_scan|mysql_scan|iceberg_scan|delta_scan|"
    r"duckdb_extensions|duckdb_settings|shell"
    r")\b",
    re.IGNORECASE,
)

TABLE_REFERENCE = re.compile(r"\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_.]*)", re.IGNORECASE)
# CTE 名不是仓里的表，但会出现在 FROM 后面；不认出来就会把合法的 WITH 查询判成越权。
CTE_NAME = re.compile(r"(?:\bwith\b|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+as\s*\(", re.IGNORECASE)
FIRST_KEYWORD = re.compile(r"^\s*\(*\s*(select|with)\b", re.IGNORECASE)

_LOCKS: dict[int, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


class QueryRejected(ValueError):
    """查询被护栏拒绝。保留独立异常类型，路由层据此返回 400 而不是 500。"""


def _workspace_lock(workspace_id: int) -> threading.Lock:
    """同一 Workspace 的写入串行化：DuckDB 单文件只允许一个写连接。"""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(workspace_id)
        if lock is None:
            lock = threading.Lock()
            _LOCKS[workspace_id] = lock
        return lock


def warehouse_dir() -> Path:
    """返回仓目录。允许环境变量即时覆盖，测试不必重建 Settings。"""
    configured = os.environ.get("WAREHOUSE_DIR") or get_settings().warehouse_dir
    path = Path(configured)
    path.mkdir(parents=True, exist_ok=True)
    return path


def warehouse_path(workspace_id: int | None) -> Path:
    """一个 Workspace 一个文件。

    不用"单库多租户 + 查询时注入 workspace_id 过滤"：那种做法把隔离正确性
    押在每条 SQL 都记得加 where 上，漏一处就是跨租户泄露。分文件后，
    隔离由文件系统保证，漏写过滤条件最多是查了个空。
    """
    return warehouse_dir() / f"workspace_{int(workspace_id or 0)}.duckdb"


@contextmanager
def connect(workspace_id: int | None, *, read_only: bool = False) -> Iterator[duckdb.DuckDBPyConnection]:
    path = warehouse_path(workspace_id)
    if read_only and not path.exists():
        raise FileNotFoundError(path)
    if read_only:
        con = duckdb.connect(str(path), read_only=True)
    else:
        with _workspace_lock(int(workspace_id or 0)):
            con = duckdb.connect(str(path))
            try:
                yield con
            finally:
                con.close()
        return
    try:
        yield con
    finally:
        con.close()


def _strip_comments(sql: str) -> str:
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.DOTALL)
    return re.sub(r"--[^\n]*", " ", sql)


def guard_select(sql: str, *, max_rows: int | None = None) -> tuple[str, int]:
    """校验并把结果集裁到 max_rows。返回 (可执行 SQL, 行数上限)。"""
    if not sql or not sql.strip():
        raise QueryRejected("SQL 不能为空")

    cleaned = _strip_comments(sql).strip()
    cleaned = cleaned.rstrip(";").strip()

    if ";" in cleaned:
        raise QueryRejected("只允许单条语句：检测到分号分隔的多条 SQL")

    # 先判禁用词再看首关键字：`ATTACH` 这类语句的实质问题是"碰了外部资源"，
    # 只回一句"必须是 SELECT"会让调用方以为换个写法就行。
    forbidden = FORBIDDEN_PATTERN.search(cleaned)
    if forbidden is not None:
        raise QueryRejected(f"包含被禁用的关键字：{forbidden.group(1)}")

    if not FIRST_KEYWORD.match(cleaned):
        raise QueryRejected("只允许 SELECT 或 WITH 查询")

    cte_names = {name.lower() for name in CTE_NAME.findall(cleaned)}
    for reference in TABLE_REFERENCE.findall(cleaned):
        if "." in reference:
            raise QueryRejected(f"不允许跨库引用：{reference}")
        if reference.lower() in cte_names:
            continue
        if not reference.lower().startswith(ALLOWED_PREFIXES):
            raise QueryRejected(f"表不在白名单内：{reference}")

    limit = max_rows or int(get_settings().warehouse_query_max_rows or DEFAULT_MAX_ROWS)
    # 用子查询包一层而不是拼 LIMIT：原 SQL 里若已有 LIMIT，追加会直接语法错误。
    guarded = f"SELECT * FROM (\n{cleaned}\n) AS _guarded LIMIT {limit + 1}"
    return guarded, limit


def execute_guarded(
    workspace_id: int | None,
    sql: str,
    *,
    max_rows: int | None = None,
) -> dict[str, Any]:
    """执行护栏内的查询，返回列名 + 行 + 是否被截断。"""
    guarded, limit = guard_select(sql, max_rows=max_rows)

    with connect(workspace_id, read_only=True) as con:
        cursor = con.execute(guarded)
        columns = [item[0] for item in (cursor.description or [])]
        rows = cursor.fetchall()

    truncated = len(rows) > limit
    if truncated:
        rows = rows[:limit]
    return {
        "columns": columns,
        "rows": [list(row) for row in rows],
        "row_count": len(rows),
        "truncated": truncated,
        "max_rows": limit,
    }
