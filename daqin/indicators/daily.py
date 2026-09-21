"""指标层骨架：raw 表 → `metrics_daily`（M0 版；M2 将按 macro/market/demand/coal 四模块重构）。

M0 覆盖：行情（均线/量比/破位连续天数）、相对强弱、系统性回撤、宏观 t-1 对齐（D-13）与 `asof_*` 标注；
D 系列 / 估值 / 股息率 / 煤价列为 NULL（分别由 M1 采集、M2 补全）。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from daqin.indicators.align import align_to_trading_days
from daqin.storage import db
from daqin.thresholds import load_thresholds

WARMUP_DAYS = 180   # 自然日；覆盖 MA120 等指标预热（≥120 交易日，06 §4.6）

# 06 §3 DDL 的 metrics_daily 列（除 date）；asof_* 为文本列
METRIC_COLS = [
    "us10y", "us10y_20d_chg", "dividend_yield_ttm", "spread_daqin_us10y",
    "daqin_close", "daqin_pb", "daily_return_pct", "ma20", "ma60", "ma120",
    "volume_ratio_5d", "ma60_below_streak",
    "rel_strength_20d", "rel_strength_20d_lag1", "rs_60d_peak", "csi300_drawdown_20d",
    "dq_vol_yoy", "dq_vol_yoy_lag1", "dq_vol_yoy_lag2", "dq_vol_yoy_narrow_streak",
    "pp_available_days", "pp_avail_high_streak", "pp_avail_normal_streak",
    "qhd_inv_level", "qhd_inv_wow", "qhd_inv_yoy", "qhd_inv_wow_streak", "qhd_inv_down_streak",
    "zc_close", "zc_valid", "zc_dev_20d",
    "inst_holding_ratio",
    "asof_macro", "asof_industry", "asof_company",
]
_ASOF_COLS = {"asof_macro", "asof_industry", "asof_company"}


def _streak(cond: pd.Series) -> pd.Series:
    """连续满足条件的计数（中断归零）。"""
    grp = (~cond).cumsum()
    return cond.groupby(grp).cumsum()


def compute_daily(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """计算并 upsert `metrics_daily`；返回写入行数。

    指定 `start` 时会**多读 warm-up 段**（MA120 等指标需要），但只写入 `start` 之后的行。
    """
    stock = pd.read_sql_query(
        "SELECT date, daqin_close, daqin_amount, csi300_close FROM stock_daily ORDER BY date", conn
    )
    if stock.empty:
        return 0
    stock = stock.set_index("date")
    if end:
        stock = stock[stock.index <= end]
    if start:
        warm_from = (pd.Timestamp(start) - pd.Timedelta(days=WARMUP_DAYS)).strftime("%Y-%m-%d")
        stock = stock[stock.index >= warm_from]
    if stock.empty:
        return 0

    close = pd.to_numeric(stock["daqin_close"], errors="coerce")
    amount = pd.to_numeric(stock["daqin_amount"], errors="coerce")
    csi = pd.to_numeric(stock["csi300_close"], errors="coerce")

    m = pd.DataFrame(index=stock.index)
    # 行情
    m["daqin_close"] = close
    m["daily_return_pct"] = close.pct_change() * 100.0
    m["ma20"] = close.rolling(20).mean()
    m["ma60"] = close.rolling(60).mean()
    m["ma120"] = close.rolling(120).mean()
    m["volume_ratio_5d"] = amount / amount.rolling(5).mean()
    m["ma60_below_streak"] = _streak(close < m["ma60"])
    m["csi300_drawdown_20d"] = (csi / csi.rolling(20).max() - 1) * 100.0
    # 相对强弱（百分点）
    m["rel_strength_20d"] = (close.pct_change(20) - csi.pct_change(20)) * 100.0
    m["rel_strength_20d_lag1"] = m["rel_strength_20d"].shift(1)
    m["rs_60d_peak"] = m["rel_strength_20d"].rolling(60).max()

    # 宏观：t-1 可得性对齐（D-13，实盘/回测共用同一函数）
    macro = pd.read_sql_query("SELECT date, us10y FROM macro_daily ORDER BY date", conn)
    if not macro.empty:
        s = pd.Series(
            pd.to_numeric(macro["us10y"], errors="coerce").to_numpy(),
            index=macro["date"].to_numpy(),
        )
        aligned = align_to_trading_days(s, pd.DatetimeIndex(stock.index), lag_days=1)
        m["us10y"] = aligned.to_numpy()
        m["us10y_20d_chg"] = m["us10y"].diff(20) * 100.0   # 百分点差 → bp
        m["asof_macro"] = str(macro["date"].max())

    # 煤价代理（M1-1，D-05）：有效主力过滤 + 20 日偏离；期货与 A 股同为 15:00 收盘 → lag=0
    fut = pd.read_sql_query("SELECT date, zc_close, zc_amount FROM futures_daily ORDER BY date", conn)
    if not fut.empty:
        f = fut.set_index("date")
        zc_close = pd.to_numeric(f["zc_close"], errors="coerce")
        zc_amount = pd.to_numeric(f["zc_amount"], errors="coerce")
        ratio = load_thresholds().coal.zc_min_volume_ratio
        zc_frame = pd.DataFrame({
            "zc_close": zc_close,
            "zc_valid": (zc_amount >= zc_amount.rolling(20).mean() * ratio).fillna(False).astype(int),
            "zc_dev_20d": (zc_close / zc_close.rolling(20).mean() - 1) * 100.0,
        })
        idx = pd.DatetimeIndex(stock.index)
        for col in zc_frame.columns:
            m[col] = align_to_trading_days(zc_frame[col], idx, lag_days=0).to_numpy()
        m["asof_industry"] = str(f.index.max())

    if start:
        m = m[m.index >= start]   # 只写目标区间（warm-up 行不落库）
    return _upsert(conn, m)


def _upsert(conn: sqlite3.Connection, m: pd.DataFrame) -> int:
    rows = []
    for date, r in m.iterrows():
        rec: dict = {"date": str(date)}
        for col in METRIC_COLS:
            v = r.get(col)
            if col in _ASOF_COLS:
                rec[col] = None if v is None or (not isinstance(v, str) and pd.isna(v)) else str(v)
            else:
                rec[col] = None if v is None or pd.isna(v) else float(v)
        rows.append(rec)
    return db.upsert_rows(conn, "metrics_daily", rows, key="date")
