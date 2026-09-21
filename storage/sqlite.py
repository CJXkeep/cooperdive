"""SQLite 连接与 freshness 读写（copper / daqin 共用）。

连接约定（沿用 copper 既有实践）：
WAL 日志模式 + busy_timeout 30s + Row 工厂，保证 dashboard 与 scheduler 并发读写不互相阻塞。
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

CN_TZ = ZoneInfo("Asia/Shanghai")   # 两模块同为 A 股 / 沪市场景
DATETIME_FMT = "%Y-%m-%d %H:%M:%S"

# freshness：每个数据集最近一次采集的成功/尝试状态（两模块表结构一致）
FRESHNESS_DDL = """
CREATE TABLE IF NOT EXISTS freshness (
    dataset      TEXT PRIMARY KEY,
    last_success TEXT,
    last_attempt TEXT,
    rows_last    INTEGER,
    last_error   TEXT
);
"""


def connect(db_path: Path | str, schema: str = "") -> sqlite3.Connection:
    """打开 SQLite 连接（WAL + busy_timeout + Row 工厂）；schema 非空时执行 DDL。

    `db_path` 传 `":memory:"` 可用于测试。
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    if schema:
        conn.executescript(schema)
    return conn


def now_str(tz: ZoneInfo = CN_TZ) -> str:
    """当前时间字符串（默认北京时间）。"""
    return datetime.now(tz).strftime(DATETIME_FMT)


def set_freshness(conn: sqlite3.Connection, dataset: str, ok: bool, rows: int = 0,
                  error: str = "", now: str | None = None) -> None:
    """记录一次采集结果。

    - `ok=False` 时**保留历史 last_success**（断更判定依赖它），只更新 last_attempt / last_error；
    - `error` 截断到 500 字符，避免异常栈撑爆表。
    """
    ts = now or now_str()
    with conn:
        if ok:
            conn.execute(
                """INSERT INTO freshness (dataset, last_success, last_attempt, rows_last, last_error)
                   VALUES (?, ?, ?, ?, NULL)
                   ON CONFLICT(dataset) DO UPDATE SET
                     last_success=excluded.last_success, last_attempt=excluded.last_attempt,
                     rows_last=excluded.rows_last, last_error=NULL""",
                (dataset, ts, ts, rows),
            )
        else:
            conn.execute(
                """INSERT INTO freshness (dataset, last_success, last_attempt, rows_last, last_error)
                   VALUES (?, NULL, ?, 0, ?)
                   ON CONFLICT(dataset) DO UPDATE SET
                     last_attempt=excluded.last_attempt, last_error=excluded.last_error""",
                (dataset, ts, error[:500]),
            )


def get_freshness(conn: sqlite3.Connection) -> pd.DataFrame:
    """返回 freshness 全表（按 dataset 排序）。"""
    return pd.read_sql_query(
        "SELECT dataset, last_success, last_attempt, rows_last, last_error FROM freshness ORDER BY dataset",
        conn,
    )
