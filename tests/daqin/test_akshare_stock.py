"""M0-3 单测（mock，不联网）：列映射与日期归一 / 接口改版显式报错 / 双源合并入库幂等 / 增量起点。

数据源为新浪（`stock_zh_a_daily` + `stock_zh_index_daily`），列名已是英文。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.collectors import akshare_stock as mod  # noqa: E402
from daqin.storage import db  # noqa: E402


def _raw_stock() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2020-01-02", "2020-01-03"],
        "open": [9.1, 9.2], "high": [9.3, 9.4], "low": [9.0, 9.1], "close": [9.2, 9.3],
        "volume": [1000.0, 1100.0], "amount": [9200.0, 10230.0],
        "outstanding_share": [1.4e10, 1.4e10], "turnover": [0.001, 0.002],
    })


def _raw_index() -> pd.DataFrame:
    return pd.DataFrame({
        "date": ["2019-12-31", "2020-01-02", "2020-01-03", "2020-01-06"],
        "open": [3990.0, 4000.0, 4010.0, 4020.0], "high": [4000.0, 4020.0, 4030.0, 4040.0],
        "low": [3980.0, 3990.0, 4000.0, 4010.0], "close": [3995.0, 4010.0, 4020.0, 4030.0],
        "volume": [1.0, 2.0, 3.0, 4.0],
    })


def test_fetch_stock_maps_and_normalizes(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw_stock())
    df = mod.fetch_daqin_daily("2020-01-01", "2020-01-10")
    assert list(df.columns) == ["date", "daqin_open", "daqin_close", "daqin_high", "daqin_low", "daqin_volume", "daqin_amount"]
    assert df["date"].tolist() == ["2020-01-02", "2020-01-03"]
    assert df["daqin_open"].tolist() == [9.1, 9.2]      # M4：回测执行价
    assert df["daqin_close"].tolist() == [9.2, 9.3]


def test_fetch_stock_missing_column_raises(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw_stock().drop(columns=["close"]))
    with pytest.raises(ValueError, match="缺少列"):
        mod.fetch_daqin_daily("2020-01-01", "2020-01-10")


def test_fetch_empty_returns_empty_frame(monkeypatch) -> None:
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: pd.DataFrame())
    df = mod.fetch_daqin_daily("2020-01-01", "2020-01-02")
    assert df.empty and list(df.columns) == mod._STOCK_COLS


def test_fetch_index_filters_range(monkeypatch) -> None:
    """指数接口返回全历史，需按区间过滤。"""
    monkeypatch.setattr(mod, "call_with_retry", lambda *a, **kw: _raw_index())
    df = mod.fetch_csi300_daily("2020-01-01", "2020-01-03")
    assert df["date"].tolist() == ["2020-01-02", "2020-01-03"]
    assert df["csi300_close"].tolist() == [4010.0, 4020.0]


def test_collect_merges_and_is_idempotent(monkeypatch) -> None:
    monkeypatch.setattr(mod, "fetch_daqin_daily", lambda s, e: _mapped_stock())
    monkeypatch.setattr(mod, "fetch_csi300_daily", lambda s, e: _mapped_index())
    conn = db.connect(":memory:")
    # 个股 2 天 + 指数 3 天（01-06 为个股停牌日）→ outer join 共 3 行
    assert mod.collect(conn, start="2020-01-01", end="2020-01-10") == 3
    rows = conn.execute("SELECT date, daqin_close, csi300_close FROM stock_daily ORDER BY date").fetchall()
    assert rows[0]["daqin_close"] == pytest.approx(9.2)
    assert rows[0]["csi300_close"] == pytest.approx(4010.0)
    assert rows[-1]["date"] == "2020-01-06"
    assert rows[-1]["daqin_close"] is None          # 停牌日：指数有值、个股为 NULL
    assert rows[-1]["csi300_close"] == pytest.approx(4030.0)
    assert mod.collect(conn, start="2020-01-01", end="2020-01-10") == 3
    assert conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0] == 3


def test_collect_single_source_still_writes(monkeypatch) -> None:
    """只有指数（个股源空）时仍写入，不因缺源整体失败。"""
    monkeypatch.setattr(mod, "fetch_daqin_daily", lambda s, e: pd.DataFrame(columns=mod._STOCK_COLS))
    monkeypatch.setattr(mod, "fetch_csi300_daily", lambda s, e: _mapped_index())
    conn = db.connect(":memory:")
    assert mod.collect(conn, start="2020-01-01", end="2020-01-10") == 3
    row = conn.execute("SELECT date, daqin_close, csi300_close FROM stock_daily ORDER BY date").fetchone()
    assert row["daqin_close"] is None
    assert row["csi300_close"] == pytest.approx(4010.0)


def test_incremental_start_overlap_and_floor() -> None:
    conn = db.connect(":memory:")
    assert mod._incremental_start(conn, "2006-01-01") == "2006-01-01"
    db.upsert_rows(conn, "stock_daily", [{"date": "2020-06-30", "daqin_close": 7.0}])
    assert mod._incremental_start(conn, "2006-01-01") == "2020-06-23"
    assert mod._incremental_start(conn, "2020-07-01") == "2020-07-01"   # 不早于 hist_start


def _mapped_stock() -> pd.DataFrame:
    """模拟 fetch_daqin_daily 的输出（date + daqin_* 列）。"""
    out = _raw_stock().rename(columns=mod._STOCK_RENAME)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    return out[mod._STOCK_COLS]


def _mapped_index() -> pd.DataFrame:
    """模拟 fetch_csi300_daily 的输出（date + csi300_close，已按区间过滤）。"""
    out = _raw_index().copy()
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out = out[(out["date"] >= "2020-01-01") & (out["date"] <= "2020-01-10")]
    return out.rename(columns={"close": "csi300_close"})[["date", "csi300_close"]].reset_index(drop=True)
