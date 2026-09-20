"""看板数据加载层：从 SQLite 读取并做轻加工，带 5 分钟缓存。"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import streamlit as st

from copper import analytics, config, db


@st.cache_data(ttl=300, show_spinner=False)
def load_markets_usd() -> pd.DataFrame:
    conn = db.connect()
    try:
        return analytics.copper_markets_usd(conn)
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def load_series(dataset: str, key: str = "close") -> pd.Series:
    conn = db.connect()
    try:
        return db.read_series(dataset, key, conn=conn)
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def load_profit_index() -> pd.DataFrame:
    conn = db.connect()
    try:
        return analytics.profit_index(conn)
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def load_relative_strength() -> pd.DataFrame:
    conn = db.connect()
    try:
        return analytics.relative_strength(conn)
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def load_pb() -> pd.DataFrame:
    conn = db.connect()
    try:
        return analytics.pb_series(conn)
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def load_peers() -> pd.DataFrame:
    conn = db.connect()
    try:
        return analytics.peers_normalized(conn)
    finally:
        conn.close()


@st.cache_data(ttl=300, show_spinner=False)
def load_freshness() -> pd.DataFrame:
    conn = db.connect()
    try:
        return db.get_freshness(conn)
    finally:
        conn.close()


@st.cache_data(ttl=60, show_spinner=False)
def load_events() -> pd.DataFrame:
    """解析 events/events.md：每行 `- YYYY-MM-DD | 类型 | 标题 | 详情(可选)`。"""
    path: Path = config.EVENTS_FILE
    rows = []
    if path.exists():
        pat = re.compile(r"^-\s*(20\d{2}-\d{2}-\d{2})\s*\|\s*([^|]+)\s*\|\s*([^|]+?)(?:\s*\|\s*(.*))?\s*$")
        for line in path.read_text(encoding="utf-8").splitlines():
            m = pat.match(line.strip())
            if m:
                rows.append({"date": m.group(1), "type": m.group(2).strip(),
                             "title": m.group(3).strip(), "detail": (m.group(4) or "").strip()})
    df = pd.DataFrame(rows, columns=["date", "type", "title", "detail"])
    if not df.empty:
        df = df.sort_values("date", ascending=False)
    return df


def chg(series: pd.Series) -> tuple[float | None, float | None]:
    """返回 (最新值, 相对前一交易日涨跌幅%)。"""
    s = series.dropna()
    if len(s) < 2:
        return (s.iloc[-1] if len(s) else None), None
    last, prev = s.iloc[-1], s.iloc[-2]
    pct = (last / prev - 1) * 100 if prev else None
    return float(last), (float(pct) if pct is not None else None)


def freshness_status() -> list[dict]:
    """把 freshness 表整理成看板横幅用的状态列表。"""
    fr = load_freshness()
    now = datetime.now(config.TZ)
    out = []
    for _, row in fr.iterrows():
        ok = row["last_success"]
        age_days = None
        if ok:
            age_days = (now - datetime.strptime(ok, config.DATETIME_FMT).replace(tzinfo=config.TZ)).total_seconds() / 86400
        if not ok:
            level, label = "bad", "从未成功"
        elif age_days <= config.FRESH_WARN_DAYS:
            level, label = "good", "正常"
        elif age_days <= config.FRESH_BAD_DAYS:
            level, label = "warn", "滞后"
        else:
            level, label = "bad", "断更"
        out.append({"dataset": row["dataset"], "level": level, "label": label,
                    "age_days": age_days, "error": row["last_error"] or ""})
    return out


def last_update_time() -> str | None:
    fr = load_freshness()
    times = [t for t in fr["last_success"].dropna()]
    if not times:
        return None
    return max(times)


def events_on_chart(events_df: pd.DataFrame) -> list[dict]:
    """转成 plotly vline 用的结构。"""
    if events_df.empty:
        return []
    cutoff = (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d")
    return events_df[events_df["date"] >= cutoff].to_dict("records")
