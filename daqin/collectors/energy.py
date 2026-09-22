"""沿海六大电（供耗存）采集（06 M1-2，D-07）→ `energy_daily`。

数据源：`ak.macro_china_daily_energy()`（金十聚合），字段 `日期 / 沿海六大电库存 / 日耗 / 存煤可用天数`。

⚠️ **数据停更于 2019-06-21**（M1 探活结论，触发 D-26）：本采集器只回填历史段（2016-01-01 ~ 停更日），
2020+ 恒为空 → `pp_available_days` 为 NULL，D5 降级为「历史可选」，S5 自动核改由运量连续转正承担（D-26）。
"""

from __future__ import annotations

import sqlite3

import pandas as pd

from copper.netutil import call_with_retry
from daqin.storage import db

_RENAME = {
    "日期": "date",
    "沿海六大电库存": "pp_stock",
    "日耗": "pp_daily_consume",
    "存煤可用天数": "pp_available_days",
}
_COLS = ["date", "pp_stock", "pp_daily_consume", "pp_available_days"]


def fetch_energy() -> pd.DataFrame:
    """六大电全历史 → date, pp_stock, pp_daily_consume, pp_available_days。"""
    import akshare as ak

    df = call_with_retry(ak.macro_china_daily_energy)
    if df is None or df.empty:
        return pd.DataFrame(columns=_COLS)
    renamed = df.rename(columns=_RENAME)
    missing = [c for c in _COLS if c not in renamed.columns]
    if missing:
        raise ValueError(f"akshare(macro_china_daily_energy) 返回缺少列 {missing}，实际列 {list(df.columns)}（接口可能改版）")
    out = renamed[_COLS].copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    for col in _COLS[1:]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    return out.dropna(subset=["date"]).reset_index(drop=True)


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """采集六大电（接口返回全历史，本地按区间过滤）→ upsert `energy_daily`。"""
    df = fetch_energy()
    if df.empty:
        return 0
    if start:
        df = df[df["date"] >= start]
    if end:
        df = df[df["date"] <= end]
    if df.empty:
        return 0
    return db.upsert_df(conn, "energy_daily", df, key="date")
