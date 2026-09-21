"""M0-7 指标层单测：均线/收益率/连续破位/量比/相对强弱/宏观 t-1 对齐（集成）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.indicators.daily import _streak, compute_daily  # noqa: E402
from daqin.storage import db  # noqa: E402


def _dates(n: int) -> list[str]:
    return pd.date_range("2019-10-01", periods=n, freq="D").strftime("%Y-%m-%d").tolist()


def _seed(conn, closes, amounts=None, csi=None) -> list[str]:
    n = len(closes)
    dates = _dates(n)
    amounts = amounts or [100.0] * n
    csi = csi or [4000.0] * n
    db.upsert_rows(conn, "stock_daily", [
        {"date": d, "daqin_close": c, "daqin_amount": a, "csi300_close": k}
        for d, c, a, k in zip(dates, closes, amounts, csi)
    ])
    return dates


def test_streak_helper() -> None:
    cond = pd.Series([False, True, True, False, True])
    assert _streak(cond).tolist() == [0, 1, 2, 0, 1]


def test_ma_return_and_break_streak() -> None:
    conn = db.connect(":memory:")
    dates = _seed(conn, [10.0] * 60 + [9.0, 8.0])
    assert compute_daily(conn) == 62
    row = conn.execute("SELECT * FROM metrics_daily WHERE date=?", (dates[-1],)).fetchone()
    assert row["ma60"] == pytest.approx(9.95, rel=1e-9)      # mean(58×10.0, 9.0, 8.0)
    assert row["daily_return_pct"] == pytest.approx(-11.1111, rel=1e-3)
    assert row["ma60_below_streak"] == 2
    # ma20 在连续平价段应等于 10（取第 30 天验证）
    mid = conn.execute("SELECT ma20 FROM metrics_daily WHERE date=?", (dates[29],)).fetchone()
    assert mid["ma20"] == pytest.approx(10.0)


def test_volume_ratio_and_relative_strength() -> None:
    conn = db.connect(":memory:")
    n = 25
    closes = [10.0] * (n - 1) + [11.0]        # 末日 +10%
    csi = [4000.0] * n                        # 指数不变
    dates = _seed(conn, closes, amounts=[100.0] * (n - 1) + [500.0], csi=csi)
    compute_daily(conn)
    row = conn.execute("SELECT volume_ratio_5d, rel_strength_20d FROM metrics_daily WHERE date=?", (dates[-1],)).fetchone()
    assert row["volume_ratio_5d"] == pytest.approx(500 / 180.0, rel=1e-6)
    assert row["rel_strength_20d"] == pytest.approx(10.0, rel=1e-6)   # 个股 +10% − 指数 0%


def test_macro_aligned_to_t_minus_1() -> None:
    """宏观按 t-1 可得性对齐：交易日 t 使用 t-1 的观测（D-13）。"""
    conn = db.connect(":memory:")
    dates = _seed(conn, [10.0] * 5)
    db.upsert_rows(conn, "macro_daily", [
        {"date": dates[0], "us10y": 1.90},
        {"date": dates[1], "us10y": 1.80},
    ])
    compute_daily(conn)
    rows = {r["date"]: r["us10y"] for r in conn.execute("SELECT date, us10y FROM metrics_daily")}
    assert rows[dates[0]] is None                          # 当日观测尚未可用
    assert rows[dates[1]] == pytest.approx(1.90)           # 用 t-1 的观测
    assert rows[dates[2]] == pytest.approx(1.80)
    assert rows[dates[3]] == pytest.approx(1.80)           # ffill
    asof = conn.execute("SELECT asof_macro FROM metrics_daily LIMIT 1").fetchone()
    assert asof["asof_macro"] == dates[1]                  # 数据截止标注


def test_empty_stock_returns_zero() -> None:
    conn = db.connect(":memory:")
    assert compute_daily(conn) == 0


def test_range_filter() -> None:
    conn = db.connect(":memory:")
    dates = _seed(conn, [10.0] * 10)
    assert compute_daily(conn, start=dates[3], end=dates[6]) == 4
