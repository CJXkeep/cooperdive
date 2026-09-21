"""M1-1 / M1-5 单测（mock，不联网）：ZC 主连与分红派息的抓取映射与入库。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.collectors import dividend as dv  # noqa: E402
from daqin.collectors import futures as fu  # noqa: E402
from daqin.storage import db  # noqa: E402


# ---------------- M1-1：ZC 主连 ----------------

def _raw_zc() -> pd.DataFrame:
    return pd.DataFrame({
        "日期": ["2020-01-02", "2020-01-03"],
        "开盘价": [560.0, 565.0], "最高价": [570.0, 572.0], "最低价": [555.0, 560.0],
        "收盘价": [565.0, 570.0], "成交量": [100.0, 200.0], "持仓量": [50.0, 55.0],
        "动态结算价": [564.0, 569.0],
    })


def test_fetch_zc_maps_and_approximates_amount(monkeypatch) -> None:
    monkeypatch.setattr(fu, "call_with_retry", lambda *a, **kw: _raw_zc())
    df = fu.fetch_zc("2020-01-01", "2020-01-10")
    assert list(df.columns) == fu._COLS
    assert df["date"].tolist() == ["2020-01-02", "2020-01-03"]
    assert df["zc_amount"].tolist() == [56500.0, 114000.0]      # 成交量 × 收盘价（近似）


def test_fetch_zc_missing_column_raises(monkeypatch) -> None:
    monkeypatch.setattr(fu, "call_with_retry", lambda *a, **kw: _raw_zc().drop(columns=["收盘价"]))
    with pytest.raises(ValueError, match="缺少列"):
        fu.fetch_zc("2020-01-01", "2020-01-10")


def test_zc_collect_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(fu, "fetch_zc", lambda s, e: pd.DataFrame([
        {"date": "2020-01-02", "zc_close": 565.0, "zc_volume": 100.0, "zc_amount": 56500.0, "zc_hold": 50.0},
    ]))
    conn = db.connect(":memory:")
    assert fu.collect(conn, start="2020-01-01", end="2020-12-31") == 1
    assert fu.collect(conn, start="2020-01-01", end="2020-12-31") == 1
    assert conn.execute("SELECT COUNT(*) FROM futures_daily").fetchone()[0] == 1
    row = conn.execute("SELECT zc_close FROM futures_daily").fetchone()
    assert row["zc_close"] == pytest.approx(565.0)


# ---------------- M1-5：分红派息 ----------------

def _raw_dividends() -> pd.DataFrame:
    return pd.DataFrame({
        "实施方案公告日期": ["2020-06-10", "2021-06-15"],
        "分红类型": ["年度分红", "年度分红"],
        "送股比例": [None, None], "转增比例": [None, None],
        "派息比例": [2.0, 2.2],                    # 10 股派 2 元 → 每股 0.2 元
        "股权登记日": ["2020-06-18", "2021-06-22"],
        "除权日": ["2020-06-19", "2021-06-23"],
        "派息日": ["2020-06-24", "2021-06-29"],
        "股份到账日": [None, None],
        "实施方案分红说明": ["10派2元（含税）", "10派2.2元（含税）"],
        "报告时间": ["2019年报", "2020年报"],
    })


def test_fetch_dividends_normalizes_dps(monkeypatch) -> None:
    monkeypatch.setattr(dv, "call_with_retry", lambda *a, **kw: _raw_dividends())
    df = dv.fetch_dividends()
    assert list(df.columns) == ["ex_date", "dps", "source"]
    assert df["ex_date"].tolist() == ["2020-06-19", "2021-06-23"]
    assert df["dps"].tolist() == [0.2, pytest.approx(0.22)]
    assert set(df["source"]) == {"cninfo"}


def test_fetch_dividends_missing_column_raises(monkeypatch) -> None:
    monkeypatch.setattr(dv, "call_with_retry", lambda *a, **kw: _raw_dividends().drop(columns=["除权日"]))
    with pytest.raises(ValueError, match="缺少列"):
        dv.fetch_dividends()


def test_dividend_collect_idempotent_and_range(monkeypatch) -> None:
    monkeypatch.setattr(dv, "fetch_dividends", lambda symbol=dv.DAQIN_SYMBOL: pd.DataFrame({
        "ex_date": ["2020-06-19", "2021-06-23"],
        "dps": [0.2, 0.22],
        "source": ["cninfo", "cninfo"],
    }))
    conn = db.connect(":memory:")
    assert dv.collect(conn, start="2020-01-01", end="2020-12-31") == 1     # 区间过滤
    assert dv.collect(conn, start="2020-01-01", end="2020-12-31") == 1
    assert conn.execute("SELECT COUNT(*) FROM dividend_events").fetchone()[0] == 1
    assert dv.collect(conn) == 2                                            # 全量
    assert conn.execute("SELECT COUNT(*) FROM dividend_events").fetchone()[0] == 2
