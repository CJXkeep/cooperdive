"""股票行情采集（akshare）：601006 与沪深300 日线 → `stock_daily` 宽表。

数据源（**新浪**，与 copper 同源；06 §1.1 原定东财接口，本机实测不可达后切换，偏差见 M0 §5 实现记录）：
- 601006：`ak.stock_zh_a_daily`（sh601006，前复权 qfq）
- 沪深300：`ak.stock_zh_index_daily`（sh000300，全历史接口 + 本地过滤区间）

约定：
- 网络调用统一经 `copper/netutil.call_with_retry` 重试（D-19）；
- freshness 由调用方（cli / 调度层）统一记录，采集函数只负责抓取与入库（与 copper pipeline 模式一致）；
- 接口列名缺失时**显式报错**（防 akshare 改版后静默写入错值，02 §4.3）。
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pandas as pd

from copper.netutil import call_with_retry
from daqin import config
from daqin.storage import db
from daqin.thresholds import load_thresholds

DAQIN_SYMBOL = "sh601006"
CSI300_SYMBOL = "sh000300"
OVERLAP_DAYS = 7   # 增量起点回退天数（覆盖数据源事后修正）

_STOCK_RENAME = {
    "open": "daqin_open", "close": "daqin_close", "high": "daqin_high", "low": "daqin_low",
    "volume": "daqin_volume", "amount": "daqin_amount",
}
_STOCK_COLS = ["date", "daqin_open", "daqin_close", "daqin_high", "daqin_low", "daqin_volume", "daqin_amount"]


def fetch_daqin_daily(start: str, end: str) -> pd.DataFrame:
    """601006 前复权日线（新浪）→ date, daqin_close/high/low, daqin_volume, daqin_amount。"""
    import akshare as ak

    df = call_with_retry(
        ak.stock_zh_a_daily, symbol=DAQIN_SYMBOL,
        start_date=start.replace("-", ""), end_date=end.replace("-", ""), adjust="qfq",
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=_STOCK_COLS)
    renamed = df.rename(columns=_STOCK_RENAME)
    missing = [c for c in _STOCK_COLS if c not in renamed.columns]
    if missing:
        raise ValueError(f"akshare(stock_zh_a_daily) 返回缺少列 {missing}，实际列 {list(df.columns)}（接口可能改版）")
    return _norm(renamed[_STOCK_COLS])


def fetch_csi300_daily(start: str, end: str) -> pd.DataFrame:
    """沪深300 日线（新浪，全历史接口 + 本地过滤区间）→ date, csi300_close。"""
    import akshare as ak

    df = call_with_retry(ak.stock_zh_index_daily, symbol=CSI300_SYMBOL)
    if df is None or df.empty:
        return pd.DataFrame(columns=["date", "csi300_close"])
    missing = [c for c in ("date", "close") if c not in df.columns]
    if missing:
        raise ValueError(f"akshare(stock_zh_index_daily) 返回缺少列 {missing}，实际列 {list(df.columns)}（接口可能改版）")
    out = df.copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out = out[(out["date"] >= start) & (out["date"] <= end)]
    return out.rename(columns={"close": "csi300_close"})[["date", "csi300_close"]].reset_index(drop=True)


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """采集 601006 + 沪深300 → 合并 upsert 到 `stock_daily`；返回写入行数。

    `start` 缺省：库内最大日期回退 `OVERLAP_DAYS` 天（增量 + 修正），库空时取 `thresholds.data.hist_start`。
    """
    start = start or _incremental_start(conn, load_thresholds().data.hist_start)
    end = end or config.iso(config.today_cn())
    stock = fetch_daqin_daily(start, end)
    csi = fetch_csi300_daily(start, end)
    if stock.empty and csi.empty:
        return 0
    merged = stock.merge(csi, on="date", how="outer").sort_values("date").reset_index(drop=True)
    return db.upsert_df(conn, "stock_daily", merged, key="date")


def _incremental_start(conn: sqlite3.Connection, hist_start: str, overlap_days: int = OVERLAP_DAYS) -> str:
    row = conn.execute("SELECT MAX(date) FROM stock_daily").fetchone()
    last = row[0] if row else None
    if not last:
        return hist_start
    d = pd.Timestamp(last).date() - timedelta(days=overlap_days)
    return max(config.iso(d), hist_start)


def _norm(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()   # 入参可能是切片视图，避免 SettingWithCopyWarning
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    return out
