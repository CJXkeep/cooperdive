"""M1-5 单测（mock，不联网）：不复权价映射 / bps 可用日滞后 / 合并入库不覆盖既有列。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.collectors import valuation as mod  # noqa: E402
from daqin.storage import db  # noqa: E402


def _raw_close() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2020-01-02", "2020-01-03"],
        "open": [8.22, 8.17], "high": [8.25, 8.22], "low": [8.17, 8.13],
        "close": [8.17, 8.16], "volume": [1.0, 2.0], "amount": [3.0, 4.0],
        "outstanding_share": [1.4e10, 1.4e10], "turnover": [0.001, 0.002],
    })


def _raw_financial() -> pd.DataFrame:
    return pd.DataFrame({
        "日期": ["2019-12-31", "2020-03-31"],
        "每股净资产_调整后(元)": [7.31, 7.38],
        "摊薄每股收益(元)": [0.6, 0.1],
    })


def test_fetch_raw_close_maps(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw_close())
    df = mod.fetch_raw_close("2020-01-01", "2020-01-10")
    assert list(df.columns) == ["date", "daqin_close_raw"]
    assert df["daqin_close_raw"].tolist() == pytest.approx([8.17, 8.16])


def test_fetch_bps_shifts_to_available_date(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw_financial())
    df = mod.fetch_bps(2019)
    assert list(df.columns) == ["date", "daqin_bps"]
    # 报告期 + BPS_LAG_DAYS 天 = 可用日（2019-12-31 + 90 天 = 2020-03-30）
    assert df["date"].tolist() == ["2020-03-30", "2020-06-29"]
    assert df["daqin_bps"].tolist() == pytest.approx([7.31, 7.38])


def test_fetch_bps_missing_column_raises(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw_financial().rename(columns={"每股净资产_调整后(元)": "x"}))
    with pytest.raises(ValueError, match="缺少"):
        mod.fetch_bps(2019)


def test_collect_does_not_clobber_existing_columns(monkeypatch) -> None:
    """只写传入的列：已有行情列（daqin_close 等）不被 NULL 覆盖。"""
    conn = db.connect(":memory:")
    db.upsert_rows(conn, "stock_daily", [{"date": "2020-01-02", "daqin_close": 7.5, "daqin_amount": 100.0}])
    monkeypatch.setattr(mod, "fetch_raw_close", lambda s, e: pd.DataFrame({"date": ["2020-01-02"], "daqin_close_raw": [8.17]}))
    monkeypatch.setattr(mod, "fetch_bps", lambda y: pd.DataFrame({"date": ["2020-03-30"], "daqin_bps": [7.31]}))
    assert mod.collect(conn, start="2020-01-01", end="2020-12-31") == 2
    row = conn.execute("SELECT daqin_close, daqin_amount, daqin_close_raw FROM stock_daily WHERE date='2020-01-02'").fetchone()
    assert row["daqin_close"] == pytest.approx(7.5)          # 既有列保留
    assert row["daqin_amount"] == pytest.approx(100.0)
    assert row["daqin_close_raw"] == pytest.approx(8.17)
    bps_row = conn.execute("SELECT daqin_bps FROM stock_daily WHERE date='2020-03-30'").fetchone()
    assert bps_row["daqin_bps"] == pytest.approx(7.31)
