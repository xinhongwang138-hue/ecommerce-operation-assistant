"""CSV 导入的公共行读取。

三个导入服务（商品、SKU 库存、经营数据）原本各持有一份逐字重复的解析逻辑，
只在表头别名归一上有一处差异，因此合并为一个函数、把差异做成可选参数。
"""
from __future__ import annotations

import csv
from io import StringIO


def read_csv_rows(
    csv_text: str,
    header_aliases: dict[str, str] | None = None,
) -> list[tuple[int, dict[str, str]]]:
    """把 CSV 解析成 (行号, 归一化后的表头→取值) 列表。

    行号从 2 起算，对应带表头 CSV 在编辑器里的实际行号，便于把校验错误回指给用户。
    全空行直接跳过，不占用结果位次。
    """
    reader = csv.DictReader(StringIO(csv_text.lstrip("\ufeff")))
    rows: list[tuple[int, dict[str, str]]] = []
    for index, raw_row in enumerate(reader, start=2):
        if not raw_row or not any((value or "").strip() for value in raw_row.values()):
            continue
        normalized: dict[str, str] = {}
        for key, value in raw_row.items():
            name = str(key or "").strip()
            if header_aliases is not None:
                name = header_aliases.get(name, name)
            normalized[name] = (value or "").strip()
        rows.append((index, normalized))
    return rows
