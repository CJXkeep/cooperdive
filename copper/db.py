"""SQLite 存储层（copper）。

设计：
- series 表为通用长表：dataset（数据集名）× date（ISO 日期）× key（字段名）→ value
- 主键 (dataset, date, key)，写入用 INSERT OR REPLACE => 天然幂等，重跑不重不漏
- freshness 表记录每个数据集的最近成功/尝试状态（连接与读写复用仓库级 storage 基座，D-20）
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from copper import config
from storage import FRESHNESS_DDL
from storage import sqlite as base

_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS series (
    dataset TEXT NOT NULL,
    date    TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   REAL,
    PRIMARY KEY (dataset, date, key)
);
CREATE INDEX IF NOT EXISTS idx_series_dataset_date ON series (dataset, date);

{FRESHNESS_DDL}

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    return base.connect(config.DB_PATH, _SCHEMA)


def upsert_series(rows: list[tuple[str, str, str, float]], conn: sqlite3.Connection | None = None) -> int:
    """rows: [(dataset, 'YYYY-MM-DD', key, value)]，返回写入行数。"""
    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            conn.executemany(
                "INSERT OR REPLACE INTO series (dataset, date, key, value) VALUES (?, ?, ?, ?)",
                rows,
            )
        return len(rows)
    finally:
        if own:
            conn.close()


def df_to_rows(df: pd.DataFrame, dataset: str, key_map: dict[str, str]) -> list[tuple[str, str, str, float]]:
    """把宽表转成 series 长表行。

    df 需含 date 列（datetime.date 或 str）；key_map: {df列名 -> 存储key}，仅映射数值列。
    """
    out: list[tuple[str, str, str, float]] = []
    for col, key in key_map.items():
        if col not in df.columns:
            continue
        s = pd.to_numeric(df[col], errors="coerce")
        for d, v in zip(df["date"], s):
            if pd.isna(v) or pd.isna(d):
                continue
            out.append((dataset, pd.Timestamp(d).strftime("%Y-%m-%d"), key, float(v)))
    return out


def read_series(dataset: str, key: str = "close", start: str | None = None,
                conn: sqlite3.Connection | None = None) -> pd.Series:
    """读成以 date 为索引的 Series。"""
    own = conn is None
    conn = conn or connect()
    try:
        sql = "SELECT date, value FROM series WHERE dataset=? AND key=?"
        params: list = [dataset, key]
        if start:
            sql += " AND date>=?"
            params.append(start)
        df = pd.read_sql_query(sql + " ORDER BY date", conn, params=params)
        if df.empty:
            return pd.Series(dtype=float)
        s = df.set_index("date")["value"]
        s.index = pd.to_datetime(s.index)
        s.name = f"{dataset}.{key}"
        return s
    finally:
        if own:
            conn.close()


def last_date(dataset: str, key: str = "close", conn: sqlite3.Connection | None = None) -> str | None:
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute(
            "SELECT MAX(date) AS d FROM series WHERE dataset=? AND key=?", (dataset, key)
        ).fetchone()
        return row["d"] if row else None
    finally:
        if own:
            conn.close()


def set_freshness(dataset: str, ok: bool, rows: int = 0, error: str = "",
                  conn: sqlite3.Connection | None = None) -> None:
    """记录一次采集结果（委托 storage 基座；ok=False 时保留历史 last_success）。"""
    own = conn is None
    conn = conn or connect()
    try:
        base.set_freshness(conn, dataset, ok, rows, error)
    finally:
        if own:
            conn.close()


def get_freshness(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    own = conn is None
    conn = conn or connect()
    try:
        return base.get_freshness(conn)
    finally:
        if own:
            conn.close()


def meta_get(key: str, conn: sqlite3.Connection | None = None) -> str | None:
    own = conn is None
    conn = conn or connect()
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
    finally:
        if own:
            conn.close()


def meta_set(key: str, value: str, conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO meta (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
    finally:
        if own:
            conn.close()
