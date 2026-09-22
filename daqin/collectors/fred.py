"""FRED 采集（D-01：免 key 的 `fredgraph.csv` 端点）→ `macro_daily`。

- 端点：`{thresholds.data.fred_csv_endpoint}?id=DGS10&cosd=YYYY-MM-DD&coed=YYYY-MM-DD`
- 无效 id 返回 404 → `fetch_text` 的 `raise_for_status()` 即抛错（06 §1.1）
- CSV 缺失值以 "." 表示 → 转 NaN 后丢弃（不插值，04 §3）

网络提示（M0 §6）：本机国际流量依赖本地代理；代理未开时本模块必然超时。
"""

from __future__ import annotations

import io
import os
import sqlite3
import time
from datetime import timedelta

import pandas as pd

from daqin import config
from daqin.storage import db
from daqin.thresholds import load_thresholds

FRED_SERIES = {"us10y": "DGS10", "us10y_real": "DFII10", "us10y_ie": "T10YIE"}
OVERLAP_DAYS = 30   # FRED 近期值可能被事后修订，回退重取

def _fetch_csv_text(url: str, timeout: int = 30, tries: int = 3) -> str:
    """FRED 专用请求：最小会话 + 显式代理（`REPO_HTTP_PROXY`）+ 简单重试。

    偏差说明（M1 §5 实验记录）：不复用 `copper/netutil` 的共享会话——实测该会话
    （浏览器 UA + Retry 适配器）在本机代理环境下访问 FRED 表现为读超时/代理断开，
    而同一时刻「最小请求形态 + 显式代理」稳定返回 200（多种组合对照）。服务器直连环境下两者等价。
    """
    import requests

    proxy = os.environ.get("REPO_HTTP_PROXY")
    proxies = {"http": proxy, "https": proxy} if proxy else None
    last: Exception | None = None
    for i in range(tries):
        try:
            r = requests.get(url, proxies=proxies, timeout=timeout)
            r.raise_for_status()
            r.encoding = "utf-8"
            return r.text
        except Exception as exc:   # noqa: BLE001 网络类错误统一重试
            last = exc
            if i < tries - 1:
                time.sleep(2.0 * (i + 1))
    assert last is not None
    raise last


def fetch_series(series_id: str, start: str, end: str) -> pd.Series:
    """单个序列 → 以 ISO 日期为索引的 Series（缺失观测丢弃）。"""
    endpoint = load_thresholds().data.fred_csv_endpoint
    text = _fetch_csv_text(f"{endpoint}?id={series_id}&cosd={start}&coed={end}")
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
