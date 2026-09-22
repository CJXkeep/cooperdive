"""指标层编排器：raw 表 → `metrics_daily`（M2：按 macro / market / demand / coal 四模块拆分）。

流程：建立交易日骨架 → 行情与相对强弱 → 宏观（t-1 对齐）→ 煤价 → 估值 → 需求 → 落库。
各子模块只读自己需要的 raw 表、原地写列；`ALL_METRIC_COLS` 与 06 §3 DDL 对齐，`uncovered_columns()`
用于自检（DDL 新增列若未接入指标层会立刻暴露）。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from daqin.indicators import coal, demand, macro, market
from daqin.storage import db

WARMUP_DAYS = market.WARMUP_DAYS

ALL_METRIC_COLS: list[str] = (
    macro.MACRO_COLS
    + market.MARKET_COLS
    + market.VALUATION_COLS
    + coal.COAL_COLS
    + demand.DEMAND_COLS
    + demand.QHD_COLS
    + demand.HOLDER_COLS
)


def uncovered_columns(conn: sqlite3.Connection) -> set[str]:
    """DDL 中存在但指标层未产出的列（自检用；应为空集）。"""
    have = {r[1] for r in conn.execute("PRAGMA table_info(metrics_daily)")} - {"date"}
    return have - set(ALL_METRIC_COLS)


def compute_daily(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """计算并 upsert `metrics_daily`；返回写入行数。

    指定 `start` 时会**多读 warm-up 段**（MA120 等指标需要），但只写入 `start` 之后的行。
    """
    stock = market.load_trading_frame(conn, start=start, end=end, warmup_days=WARMUP_DAYS)
    if stock.empty:
        return 0

    m = pd.DataFrame(index=stock.index)
    market.add_market(stock, m)
    macro.add_macro(conn, m)
    coal.add_coal(conn, m)
    market.add_valuation(conn, stock, m)
    demand.add_demand(conn, m)

    if start:
        m = m[m.index >= start]   # 只写目标区间（warm-up 行不落库）
    return _upsert(conn, m)


def _upsert(conn: sqlite3.Connection, m: pd.DataFrame) -> int:
    """落库：只写 DDL 中确实存在且本次产出的列。"""
    have = {r[1] for r in conn.execute("PRAGMA table_info(metrics_daily)")} - {"date"}
    cols = [c for c in m.columns if c in have]
    df = m.reset_index().rename(columns={m.index.name or "index": "date"})[["date", *cols]]
    df = df.astype(object).where(pd.notna(df), None)
    return db.upsert_df(conn, "metrics_daily", df, key="date")
