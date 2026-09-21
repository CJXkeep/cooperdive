"""FRED 采集（D-01：免 key 的 `fredgraph.csv` 端点）→ `macro_daily`。

- 端点：`{thresholds.data.fred_csv_endpoint}?id=DGS10&cosd=YYYY-MM-DD&coed=YYYY-MM-DD`
- 无效 id 返回 404 → `fetch_text` 的 `raise_for_status()` 即抛错（06 §1.1）
- CSV 缺失值以 "." 表示 → 转 NaN 后丢弃（不插值，04 §3）

网络提示（M0 §6）：本机国际流量依赖本地代理；代理未开时本模块必然超时。
"""

from __future__ import annotations

import io
import sqlite3
from datetime import timedelta

import pandas as pd

from copper.netutil import fetch_text
from daqin import config
from daqin.storage import db
from daqin.thresholds import load_thresholds

FRED_SERIES = {"us10y": "DGS10", "us10y_real": "DFII10", "us10y_ie": "T10YIE"}
OVERLAP_DAYS = 30   # FRED 近期值可能被事后修订，回退重取


def fetch_series(series_id: str, start: str, end: str) -> pd.Series:
    """单个序列 → 以 ISO 日期为索引的 Series（缺失观测丢弃）。"""
    endpoint = load_thresholds().data.fred_csv_endpoint
    text = fetch_text(f"{endpoint}?id={series_id}&cosd={start}&coed={end}")
    df = pd.read_csv(io.StringIO(text))
    if df.shape[1] < 2:
        raise ValueError(f"FRED CSV 列数异常（id={series_id}）：{list(df.columns)}")
    df = df.iloc[:, :2]
    df.columns = ["date", "value"]
    df["date"] = pd.to_datetime(df["date"]).dt.strftime("%Y-%m-%d")
    s = pd.Series(
        pd.to_numeric(df["value"], errors="coerce").to_numpy(),
        index=df["date"], name=series_id,
    )
    return s.dropna()


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """采集三个序列 → 合并 upsert 到 `macro_daily`；返回写入行数。"""
    start = start or _incremental_start(conn, load_thresholds().data.hist_start)
    end = end or config.iso(config.today_cn())
    merged = pd.DataFrame({name: fetch_series(sid, start, end) for name, sid in FRED_SERIES.items()})
    if merged.empty:
        return 0
    merged = merged.reset_index(names="date")
    rows = [
        {"date": r["date"], **{k: (None if pd.isna(r[k]) else float(r[k])) for k in FRED_SERIES}}
        for _, r in merged.iterrows()
    ]
    return db.upsert_rows(conn, "macro_daily", rows, key="date")


def _incremental_start(conn: sqlite3.Connection, hist_start: str, overlap_days: int = OVERLAP_DAYS) -> str:
    row = conn.execute("SELECT MAX(date) FROM macro_daily").fetchone()
    last = row[0] if row else None
    if not last:
        return hist_start
    d = pd.Timestamp(last).date() - timedelta(days=overlap_days)
    return max(config.iso(d), hist_start)
