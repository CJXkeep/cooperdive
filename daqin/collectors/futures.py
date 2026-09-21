"""ZC 动力煤主连采集（新浪主连，D-05 煤价粗代理）→ `futures_daily`。

- 数据源：`ak.futures_main_sina(symbol="ZC0")`（与 copper 同源）。06 §1.1 原定 `futures_hist_em`，
  M1 探活实测该函数无动力煤主连映射（仅具体合约），故改用新浪主连（见 M1 §1.1 偏差记录）。
- `zc_amount` 用 `成交量 × 收盘价` 近似（新浪主连不提供成交额；`zc_valid` 判定为比值，与单位无关）。
- `zc_valid`（有效主力标记）由指标层按 20 日均额 × `coal.zc_min_volume_ratio` 计算（D-05），
  不在本采集器内计算（需要跨行滚动统计）。
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pandas as pd

from copper.netutil import call_with_retry
from daqin import config
from daqin.storage import db
from daqin.thresholds import load_thresholds

ZC_SYMBOL = "ZC0"
OVERLAP_DAYS = 7

_ZC_RENAME = {"日期": "date", "收盘价": "zc_close", "成交量": "zc_volume", "持仓量": "zc_hold"}
_COLS = ["date", "zc_close", "zc_volume", "zc_amount", "zc_hold"]


def fetch_zc(start: str, end: str) -> pd.DataFrame:
    """ZC 主连日线 → date, zc_close, zc_volume, zc_amount(近似), zc_hold。"""
    import akshare as ak

    df = call_with_retry(
        ak.futures_main_sina, symbol=ZC_SYMBOL,
        start_date=start.replace("-", ""), end_date=end.replace("-", ""),
    )
    if df is None or df.empty:
        return pd.DataFrame(columns=_COLS)
    renamed = df.rename(columns=_ZC_RENAME)
    need = ["date", "zc_close", "zc_volume", "zc_hold"]
    missing = [c for c in need if c not in renamed.columns]
    if missing:
        raise ValueError(f"akshare(futures_main_sina/ZC0) 返回缺少列 {missing}，实际列 {list(df.columns)}（接口可能改版）")
    out = renamed[need].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out["zc_amount"] = pd.to_numeric(out["zc_volume"], errors="coerce") * pd.to_numeric(out["zc_close"], errors="coerce")
    return out[_COLS]


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """采集 ZC 主连 → upsert `futures_daily`；返回写入行数。"""
    start = start or _incremental_start(conn, load_thresholds().data.hist_start)
    end = end or config.iso(config.today_cn())
    df = fetch_zc(start, end)
    if df.empty:
        return 0
    return db.upsert_df(conn, "futures_daily", df, key="date")


def _incremental_start(conn: sqlite3.Connection, hist_start: str, overlap_days: int = OVERLAP_DAYS) -> str:
    row = conn.execute("SELECT MAX(date) FROM futures_daily").fetchone()
    last = row[0] if row else None
    if not last:
        return hist_start
    d = pd.Timestamp(last).date() - timedelta(days=overlap_days)
    return max(config.iso(d), hist_start)
