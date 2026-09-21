"""storage 基座单测：建表 / freshness 成功与失败语义 / 错误截断 / 排序。

注意：`tests/storage/` **不放** `__init__.py`——否则 pytest 会把该目录注册为顶层 `storage` 包、遮蔽仓库根的真包。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from storage import FRESHNESS_DDL, connect, get_freshness, set_freshness  # noqa: E402


def _conn():
    return connect(":memory:", FRESHNESS_DDL)


def test_connect_creates_freshness_table() -> None:
    tables = {r[0] for r in _conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "freshness" in tables


def test_success_records_and_clears_error() -> None:
    conn = _conn()
    set_freshness(conn, "ds1", ok=False, error="boom")
    set_freshness(conn, "ds1", ok=True, rows=7, now="2026-09-21 21:30:00")
    row = get_freshness(conn).set_index("dataset").loc["ds1"]
    assert row["last_success"] == "2026-09-21 21:30:00"
    assert row["rows_last"] == 7
    assert pd.isna(row["last_error"])


def test_failure_keeps_last_success() -> None:
    conn = _conn()
    set_freshness(conn, "ds1", ok=True, rows=3, now="2026-09-20 21:30:00")
    set_freshness(conn, "ds1", ok=False, error="E: timeout", now="2026-09-21 21:30:00")
    row = get_freshness(conn).set_index("dataset").loc["ds1"]
    assert row["last_success"] == "2026-09-20 21:30:00"      # 历史成功保留
    assert row["last_attempt"] == "2026-09-21 21:30:00"
    assert row["last_error"] == "E: timeout"


def test_error_truncated_to_500() -> None:
    conn = _conn()
    set_freshness(conn, "ds1", ok=False, error="x" * 900)
    row = get_freshness(conn).set_index("dataset").loc["ds1"]
    assert len(row["last_error"]) == 500


def test_get_freshness_sorted() -> None:
    conn = _conn()
    set_freshness(conn, "b", ok=True)
    set_freshness(conn, "a", ok=True)
    assert list(get_freshness(conn)["dataset"]) == ["a", "b"]
