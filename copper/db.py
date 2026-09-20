"""SQLite 存储层。

设计：
- series 表为通用长表：dataset（数据集名）× date（ISO 日期）× key（字段名）→ value
- 主键 (dataset, date, key)，写入用 INSERT OR REPLACE => 天然幂等，重跑不重不漏
- freshness 表记录每个数据集的最近成功/尝试状态，看板据此显示数据新鲜度
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

import pandas as pd

from copper import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS series (
    dataset TEXT NOT NULL,
    date    TEXT NOT NULL,
    key     TEXT NOT NULL,
    value   REAL,
    PRIMARY KEY (dataset, date, key)
);
CREATE INDEX IF NOT EXISTS idx_series_dataset_date ON series (dataset, date);

CREATE TABLE IF NOT EXISTS freshness (
    dataset      TEXT PRIMARY KEY,
    last_success TEXT,
    last_attempt TEXT,
    rows_last    INTEGER,
    last_error   TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);
"""


def connect() -> sqlite3.Connection:
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.executescript(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(config.TZ).strftime(config.DATETIME_FMT)


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
    own = conn is None
    conn = conn or connect()
    now = _now()
    try:
        with conn:
            if ok:
                conn.execute(
                    """INSERT INTO freshness (dataset, last_success, last_attempt, rows_last, last_error)
                       VALUES (?, ?, ?, ?, NULL)
                       ON CONFLICT(dataset) DO UPDATE SET
                         last_success=excluded.last_success, last_attempt=excluded.last_attempt,
                         rows_last=excluded.rows_last, last_error=NULL""",
                    (dataset, now, now, rows),
                )
            else:
                conn.execute(
                    """INSERT INTO freshness (dataset, last_success, last_attempt, rows_last, last_error)
                       VALUES (?, NULL, ?, 0, ?)
                       ON CONFLICT(dataset) DO UPDATE SET
                         last_attempt=excluded.last_attempt, last_error=excluded.last_error""",
                    (dataset, now, error[:500]),
                )
    finally:
        if own:
            conn.close()


def get_freshness(conn: sqlite3.Connection | None = None) -> pd.DataFrame:
    own = conn is None
    conn = conn or connect()
    try:
        return pd.read_sql_query(
            "SELECT dataset, last_success, last_attempt, rows_last, last_error FROM freshness ORDER BY dataset",
            conn,
        )
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
