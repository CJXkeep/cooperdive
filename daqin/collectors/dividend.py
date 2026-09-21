"""分红派息采集（巨潮）→ `dividend_events`（股息率 TTM 用）。

- 数据源：`ak.stock_dividend_cninfo(symbol="601006")`（巨潮，M1 探活可用：22 条，2007 起）；
- 主键取**除权日**（`ex_date`），`dps` = 每股派息（元/股）= 派息比例 ÷ 10；
- 巨潮接口返回全历史，增量与幂等由 `upsert_rows`（date 主键）保证。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from copper.netutil import call_with_retry
from daqin.storage import db

DAQIN_SYMBOL = "601006"
_SOURCE = "cninfo"


def fetch_dividends(symbol: str = DAQIN_SYMBOL) -> pd.DataFrame:
    """分红记录 → ex_date, dps, source（仅保留有除权日与派息比例的行）。"""
    import akshare as ak

    df = call_with_retry(ak.stock_dividend_cninfo, symbol=symbol)
    if df is None or df.empty:
        return pd.DataFrame(columns=["ex_date", "dps", "source"])
    missing = [c for c in ("除权日", "派息比例") if c not in df.columns]
    if missing:
        raise ValueError(f"akshare(stock_dividend_cninfo) 返回缺少列 {missing}，实际列 {list(df.columns)}（接口可能改版）")
    out = pd.DataFrame({
        "ex_date": pd.to_datetime(df["除权日"], errors="coerce").dt.strftime("%Y-%m-%d"),
        "dps": pd.to_numeric(df["派息比例"], errors="coerce") / 10.0,   # 10 股派 X 元 → 每股
    }).dropna(subset=["ex_date"])
    out["dps"] = out["dps"].fillna(0.0)          # 送转股方案无派息 → 0（仍保留除权日，不影响 TTM 求和）
    out["source"] = _SOURCE
    return out.drop_duplicates(subset=["ex_date"], keep="last").sort_values("ex_date").reset_index(drop=True)


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """采集分红 → upsert `dividend_events`；返回写入行数（全历史，接口不提供区间参数）。"""
    df = fetch_dividends()
    if df.empty:
        return 0
    if start:
        df = df[df["ex_date"] >= start]
    if end:
        df = df[df["ex_date"] <= end]
    return db.upsert_df(conn, "dividend_events", df, key="ex_date")
