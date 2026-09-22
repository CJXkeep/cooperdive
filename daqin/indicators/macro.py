"""宏观指标模块（06 §3 `metrics_daily` 宏观段）。

- `us10y`：美债 10Y 收益率，按 **t-1 可得性**对齐到 A 股交易日（D-13 防未来函数，实盘/回测共用 `align.py`）；
- `us10y_20d_chg`：20 个交易日变化（**bp**，由百分点差 ×100）；
- `asof_macro`：宏观源数据截止日（标注，便于 freshness 与报告追溯）。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from daqin.indicators.align import align_to_trading_days

MACRO_COLS = ["us10y", "us10y_20d_chg", "asof_macro"]


def add_macro(conn: sqlite3.Connection, m: pd.DataFrame) -> None:
    """读 `macro_daily` → 写入 `m` 的宏观列（原地）。"""
    macro = pd.read_sql_query("SELECT date, us10y FROM macro_daily ORDER BY date", conn)
    if macro.empty:
        return
    series = pd.Series(
        pd.to_numeric(macro["us10y"], errors="coerce").to_numpy(),
        index=macro["date"].to_numpy(),
    )
    m["us10y"] = align_to_trading_days(series, pd.DatetimeIndex(m.index), lag_days=1).to_numpy()
    m["us10y_20d_chg"] = m["us10y"].diff(20) * 100.0
    m["asof_macro"] = str(macro["date"].max())
