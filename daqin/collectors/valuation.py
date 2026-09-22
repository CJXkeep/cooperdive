"""估值与基本面采集（06 M1-5）→ `stock_daily.daqin_close_raw` / `stock_daily.daqin_bps`。

- **不复权收盘价**：新浪 `stock_zh_a_daily(adjust="")`。必须存原始价——前复权价会低估历史 PB、
  高估历史股息率（前复权把历史价格下调了）。
- **每股净资产（bps）**：新浪财务分析指标（季频，2008 起），按「报告期 + `BPS_LAG_DAYS` 天」转为
  **可用日** 后入库（财务数据的公布滞后，防未来函数；M1 用保守固定滞后，M2 可改为公告日精确对齐）。
- PB（`raw_close ÷ bps`）与股息率 TTM 由指标层计算（`indicators/daily.py`）。

数据源探活否决：百度估值 `stock_zh_valuation_baidu(市净率)` 虽覆盖 2006 起，但 20 年仅 ~614 个采样点
（非日频、缺口大）→ 不可用，记为 **R-10**。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from copper.netutil import call_with_retry
from daqin import config
from daqin.storage import db
from daqin.thresholds import load_thresholds

SYMBOL = "sh601006"
BPS_LAG_DAYS = 90   # 报告期 → 可用日的保守滞后（季报公布窗口）
_BPS_COL = "每股净资产_调整后(元)"


def fetch_raw_close(start: str, end: str) -> pd.DataFrame:
    """不复权日线收盘价 → date, daqin_close_raw。"""
    import akshare as ak

    df = call_with_retry(
        ak.stock_zh_a_daily, symbol=SYMBOL,
        start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust="",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "daqin_close_raw"])
    if "close" not in df.columns:
        raise ValueError(f"akshare(stock_zh_a_daily/不复权) 返回缺少 close，实际列 {list(df.columns)}（接口可能改版）")
    out = pd.DataFrame({
        "date": pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d"),
        "daqin_close_raw": pd.to_numeric(df["close"], errors="coerce"),
    })
    return out.dropna(subset=["daqin_close_raw"]).reset_index(drop=True)


def fetch_bps(start_year: int) -> pd.DataFrame:
    """季报每股净资产 → date（可用日）, daqin_bps。"""
    import akshare as ak

    df = call_with_retry(ak.stock_financial_analysis_indicator, symbol="601006", start_year=str(start_year))
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "daqin_bps"])
    if _BPS_COL not in df.columns or "日期" not in df.columns:
        raise ValueError(
            f"akshare(stock_financial_analysis_indicator) 返回缺少 {_BPS_COL}/日期，实际列 {list(df.columns)[:8]}（接口可能改版）"
        )
    report = pd.to_datetime(df["日期"], errors="coerce")
    out = pd.DataFrame({
        "date": (report + pd.Timedelta(days=BPS_LAG_DAYS)).dt.strftime("%Y-%m-%d"),
        "daqin_bps": pd.to_numeric(df[_BPS_COL], errors="coerce"),
    })
    return out.dropna().sort_values("date").reset_index(drop=True)


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """采集不复权价（日频）与 bps（季频，按可用日）→ upsert `stock_daily`；返回写入行数。"""
    start = start or load_thresholds().data.hist_start
    end = end or config.iso(config.today_cn())
    written = 0
    raw = fetch_raw_close(start, end)
    if not raw.empty:
        written += db.upsert_df(conn, "stock_daily", raw, key="date")
    bps = fetch_bps(int(start[:4]))
    if not bps.empty:
        bps = bps[(bps["date"] >= start) & (bps["date"] <= end)]
        if not bps.empty:
            written += db.upsert_df(conn, "stock_daily", bps, key="date")
    return written
