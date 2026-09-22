"""daqin 存储层：06 §3 全量 DDL + 宽表 upsert。

- 连接与 freshness 复用仓库级 `storage` 基座（D-20 方案 A′）；
- 表为宽表（`date` 为主键），upsert 走 `ON CONFLICT(key) DO UPDATE`；
- 写入职责：采集器只写 raw 表，指标层只写 metrics_daily，信号层只写 signal_log（02 §4.2）。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from daqin import config
from storage import FRESHNESS_DDL
from storage import sqlite as base

# 06 §3 修订版 DDL（唯一权威；改动必须同步 06 与 docs/daqin/iterations 的实现记录）
SCHEMA = f"""
-- 宏观（FRED 原始）
CREATE TABLE IF NOT EXISTS macro_daily (
  date TEXT PRIMARY KEY, us10y REAL, us10y_real REAL, us10y_ie REAL);

-- 行情与估值
CREATE TABLE IF NOT EXISTS stock_daily (
  date TEXT PRIMARY KEY,
  daqin_open REAL, daqin_close REAL, daqin_close_raw REAL,
  daqin_high REAL, daqin_low REAL,
  daqin_volume REAL, daqin_amount REAL,
  daqin_bps REAL,
  daqin_pb REAL, daqin_pe_ttm REAL,
  csi300_close REAL);

-- ZC 动力煤主连（D-05 煤价代理）
CREATE TABLE IF NOT EXISTS futures_daily (
  date TEXT PRIMARY KEY, zc_close REAL, zc_volume REAL, zc_amount REAL, zc_hold REAL);

-- 沿海六大电（D-07），2016-01-01 起
CREATE TABLE IF NOT EXISTS energy_daily (
  date TEXT PRIMARY KEY, pp_stock REAL, pp_daily_consume REAL, pp_available_days REAL);

-- 手工 CSV（D-02，可选）
CREATE TABLE IF NOT EXISTS industry_weekly (
  week_end TEXT PRIMARY KEY, qhd_inventory REAL, source TEXT DEFAULT 'manual');

-- 公告 PDF 解析
CREATE TABLE IF NOT EXISTS company_monthly (
  month TEXT PRIMARY KEY,
  dq_line_volume REAL, dq_line_volume_yoy REAL,
  source TEXT, art_code TEXT, fetched_at TEXT);

-- 分红派息（股息率 TTM 用）
CREATE TABLE IF NOT EXISTS dividend_events (
  ex_date TEXT PRIMARY KEY, dps REAL, source TEXT);

-- 筹码（仅日报展示，不参与转换）
CREATE TABLE IF NOT EXISTS holder_quarterly (
  quarter TEXT PRIMARY KEY, inst_holding_ratio REAL, holder_count INTEGER);

-- 指标层唯一输出、信号层唯一输入
CREATE TABLE IF NOT EXISTS metrics_daily (
  date TEXT PRIMARY KEY,
  -- 宏观（t-1 对齐）
  us10y REAL, us10y_20d_chg REAL,
  dividend_yield_ttm REAL, spread_daqin_us10y REAL,
  -- 行情
  daqin_close REAL, daqin_pb REAL, daily_return_pct REAL,
  ma20 REAL, ma60 REAL, ma120 REAL, volume_ratio_5d REAL,
  ma60_below_streak INTEGER,
  -- 相对强弱
  rel_strength_20d REAL, rel_strength_20d_lag1 REAL, rs_60d_peak REAL,
  csi300_drawdown_20d REAL,
  -- 需求：自动核
  dq_vol_yoy REAL, dq_vol_yoy_lag1 REAL, dq_vol_yoy_lag2 REAL,
  dq_vol_yoy_narrow_streak INTEGER,
  pp_available_days REAL, pp_avail_high_streak INTEGER,
  pp_avail_normal_streak INTEGER,
  -- 需求：可选增强（qhd_enabled）
  qhd_inv_level REAL, qhd_inv_wow REAL, qhd_inv_yoy REAL,
  qhd_inv_wow_streak INTEGER, qhd_inv_down_streak INTEGER,
  -- 煤价代理
  zc_close REAL, zc_valid INTEGER, zc_dev_20d REAL,
  -- 筹码（展示用）
  inst_holding_ratio REAL,
  -- 数据可得性标注（02 §4.3 防未来函数）
  asof_macro TEXT, asof_industry TEXT, asof_company TEXT);

-- D-09：每日一行快照
CREATE TABLE IF NOT EXISTS signal_log (
  date TEXT PRIMARY KEY,
  state TEXT NOT NULL, prev_state TEXT,
  changed INTEGER NOT NULL DEFAULT 0,
  triggered_rules TEXT, position_advice REAL, note TEXT);

{FRESHNESS_DDL}
"""


# 增量迁移：`CREATE TABLE IF NOT EXISTS` 不会给**已存在**的表补列，旧库需显式 ALTER。
# 约定：任何新增列都必须同时出现在 SCHEMA 与本表（否则老库静默缺列）。
_MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "stock_daily": [("daqin_close_raw", "REAL"), ("daqin_bps", "REAL"),                         # D-27
                    ("daqin_open", "REAL")],                                                   # M4 回测
    "metrics_daily": [("daqin_pb", "REAL"), ("ma60_below_streak", "INTEGER"),                   # D-25
                      ("pp_avail_normal_streak", "INTEGER")],                                   # D-22
}


def _migrate(conn: sqlite3.Connection) -> None:
    """给已有表补缺失列（幂等）。"""
    for table, cols in _MIGRATIONS.items():
        if conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is None:
            continue
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                with conn:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """打开 daqin 库（默认 `config.DB_PATH`；测试可传 `":memory:"`），并执行增量迁移。"""
    conn = base.connect(db_path or config.DB_PATH, SCHEMA)
    _migrate(conn)
    return conn


def upsert_rows(conn: sqlite3.Connection, table: str,
                rows: Sequence[Mapping[str, Any]], key: str = "date") -> int:
    """按主键 upsert 一批行；列名取自行字典（各行键集合必须一致）。返回写入行数。

    幂等：同一主键重复写入只会覆盖，不会新增行。
    """
    if not rows:
        return 0
    cols = list(rows[0].keys())
    if key not in cols:
        raise ValueError(f"{table}: 行缺少主键列 {key!r}，实际列 {cols}")
    for i, r in enumerate(rows):
        if list(r.keys()) != cols:
            raise ValueError(f"{table}: 第 {i} 行的列与首行不一致（{list(r.keys())} != {cols}）")
    placeholders = ", ".join("?" for _ in cols)
    updates = ", ".join(f"{c}=excluded.{c}" for c in cols if c != key)
    conflict = f"ON CONFLICT({key}) DO UPDATE SET {updates}" if updates else f"ON CONFLICT({key}) DO NOTHING"
    sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) {conflict}"
    with conn:
        conn.executemany(sql, [tuple(r[c] for c in cols) for r in rows])
    return len(rows)


def upsert_df(conn: sqlite3.Connection, table: str, df: pd.DataFrame, key: str = "date") -> int:
    """DataFrame → 行字典 → `upsert_rows`（NaN 转 NULL）。"""
    if df.empty:
        return 0
    rows = [
        {k: (None if pd.isna(v) else v) for k, v in rec.items()}
        for rec in df.to_dict("records")
    ]
    return upsert_rows(conn, table, rows, key=key)
