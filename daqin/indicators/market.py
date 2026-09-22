"""行情、相对强弱与估值模块（06 §3 `metrics_daily` 行情段 / 相对强弱段 / 估值列）。

- 行情：`daqin_close`、`daily_return_pct`、`ma20/60/120`、`volume_ratio_5d`（**成交额**比，03 §2.1）、
  `ma60_below_streak`（K3 用）；
- 相对强弱：`rel_strength_20d`（个股 − 沪深300 的 20 日收益差，百分点）、`rel_strength_20d_lag1`（K1 用）、
  `rs_60d_peak`（近 60 日相对强弱峰值，K1 用）、`csi300_drawdown_20d`（M4 用）；
- 估值（D-27）：`daqin_pb` = 不复权价 ÷ 每股净资产；`dividend_yield_ttm` = 近 365 天每股分红 ÷ 不复权价；
  `spread_daqin_us10y` = 股息率 − 美债 10Y。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from daqin.indicators.common import streak

WARMUP_DAYS = 180   # 自然日；覆盖 MA120 等指标预热（≥120 交易日，06 §4.6）

MARKET_COLS = [
    "daqin_close", "daily_return_pct", "ma20", "ma60", "ma120",
    "volume_ratio_5d", "ma60_below_streak",
    "rel_strength_20d", "rel_strength_20d_lag1", "rs_60d_peak", "csi300_drawdown_20d",
]
VALUATION_COLS = ["daqin_pb", "dividend_yield_ttm", "spread_daqin_us10y"]


def load_trading_frame(conn: sqlite3.Connection, start: str | None = None,
                       end: str | None = None, warmup_days: int = WARMUP_DAYS) -> pd.DataFrame:
    """读 `stock_daily` 建立**交易日骨架**（`date` 为 index）；指定 `start` 时多读 warm-up 段。"""
    stock = pd.read_sql_query(
        "SELECT date, daqin_close, daqin_close_raw, daqin_bps, daqin_amount, csi300_close"
        " FROM stock_daily ORDER BY date", conn
    )
    if stock.empty:
        return stock
    stock = stock.set_index("date")
    if end:
        stock = stock[stock.index <= end]
    if start:
        warm_from = (pd.Timestamp(start) - pd.Timedelta(days=warmup_days)).strftime("%Y-%m-%d")
        stock = stock[stock.index >= warm_from]
    return stock


def add_market(stock: pd.DataFrame, m: pd.DataFrame) -> None:
    """行情与相对强弱（原地写 `m`）。"""
    close = pd.to_numeric(stock["daqin_close"], errors="coerce")
    amount = pd.to_numeric(stock["daqin_amount"], errors="coerce")
    csi = pd.to_numeric(stock["csi300_close"], errors="coerce")

    m["daqin_close"] = close
    m["daily_return_pct"] = close.pct_change(fill_method=None) * 100.0
    m["ma20"] = close.rolling(20).mean()
    m["ma60"] = close.rolling(60).mean()
    m["ma120"] = close.rolling(120).mean()
    m["volume_ratio_5d"] = amount / amount.rolling(5).mean()
    m["ma60_below_streak"] = streak(close < m["ma60"])
    m["csi300_drawdown_20d"] = (csi / csi.rolling(20).max() - 1) * 100.0
    m["rel_strength_20d"] = (
        close.pct_change(20, fill_method=None) - csi.pct_change(20, fill_method=None)
    ) * 100.0
    m["rel_strength_20d_lag1"] = m["rel_strength_20d"].shift(1)
    m["rs_60d_peak"] = m["rel_strength_20d"].rolling(60).max()


def add_valuation(conn: sqlite3.Connection, stock: pd.DataFrame, m: pd.DataFrame) -> None:
    """估值与股息率（D-27；原地写 `m`）。"""
    raw = pd.to_numeric(stock.get("daqin_close_raw"), errors="coerce")
    if not raw.notna().any():
        return
    bps = pd.to_numeric(stock["daqin_bps"], errors="coerce").ffill()
    m["daqin_pb"] = raw / bps

    div = pd.read_sql_query("SELECT ex_date, dps FROM dividend_events ORDER BY ex_date", conn)
    if div.empty:
        return
    idx = pd.DatetimeIndex(stock.index)
    dps = pd.Series(
        pd.to_numeric(div["dps"], errors="coerce").to_numpy(),
        index=pd.DatetimeIndex(pd.to_datetime(div["ex_date"])),
    )
    dps_daily = dps.reindex(idx.union(dps.index)).fillna(0.0).sort_index()
    ttm = dps_daily.rolling("365D").sum().reindex(idx)
    m["dividend_yield_ttm"] = (ttm.to_numpy() / raw.to_numpy()) * 100.0
    if "us10y" in m.columns:
        m["spread_daqin_us10y"] = m["dividend_yield_ttm"] - m["us10y"]
