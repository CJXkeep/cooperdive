"""煤价代理模块（06 §3 `metrics_daily` 煤价段；D-05 用 ZC 动力煤主连近似煤价）。

- `zc_valid`：成交额 ≥ 近 20 日均值 × `coal.zc_min_volume_ratio` 视为**有效主力**（流动性过滤，D-05）——
  无效时段信号层禁用煤价代理规则；
- `zc_dev_20d`：收盘价对 20 日均线的偏离（%）；
- `asof_industry`：煤价源数据截止日。

对齐：期货与 A 股同为 15:00 收盘 → `lag_days=0`。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from daqin.indicators.align import align_to_trading_days
from daqin.thresholds import load_thresholds

COAL_COLS = ["zc_close", "zc_valid", "zc_dev_20d", "asof_industry"]


def add_coal(conn: sqlite3.Connection, m: pd.DataFrame) -> None:
    """读 `futures_daily` → 写入 `m` 的煤价列（原地）。"""
    fut = pd.read_sql_query("SELECT date, zc_close, zc_amount FROM futures_daily ORDER BY date", conn)
    if fut.empty:
        return
    f = fut.set_index("date")
    zc_close = pd.to_numeric(f["zc_close"], errors="coerce")
    zc_amount = pd.to_numeric(f["zc_amount"], errors="coerce")
    ratio = load_thresholds().coal.zc_min_volume_ratio
    frame = pd.DataFrame({
        "zc_close": zc_close,
        "zc_valid": (zc_amount >= zc_amount.rolling(20).mean() * ratio).fillna(False).astype(int),
        "zc_dev_20d": (zc_close / zc_close.rolling(20).mean() - 1) * 100.0,
    })
    idx = pd.DatetimeIndex(m.index)
    for col in frame.columns:
        m[col] = align_to_trading_days(frame[col], idx, lag_days=0).to_numpy()
    m["asof_industry"] = str(f.index.max())
