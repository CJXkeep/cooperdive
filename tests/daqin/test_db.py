"""M0-2 单测：daqin 库建表完整性 / 宽表 upsert 幂等 / freshness 复用基座。

注意：`tests/daqin/` **不放** `__init__.py`（同理 tests/storage/）——会遮蔽仓库根的同名包。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.storage import db  # noqa: E402
from storage import get_freshness, set_freshness  # noqa: E402

EXPECTED_TABLES = {
    "macro_daily", "stock_daily", "futures_daily", "energy_daily",
    "industry_weekly", "company_monthly", "dividend_events", "holder_quarterly",
    "metrics_daily", "signal_log", "freshness",
}


def _conn():
    return db.connect(":memory:")


def test_schema_creates_all_tables() -> None:
    tables = {r[0] for r in _conn().execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert EXPECTED_TABLES <= tables


def test_metrics_daily_has_d22_column() -> None:
    """防漂移检查：D-22 的 S5 自动核列必须与 06 §3 DDL 一致。"""
    cols = {r[1] for r in _conn().execute("PRAGMA table_info(metrics_daily)")}
    assert "pp_avail_normal_streak" in cols


def test_metrics_daily_has_k3_k5_columns() -> None:
    """防漂移检查：D-25 补齐的 K3（连续破 MA60）与 K5（PB）列。"""
    cols = {r[1] for r in _conn().execute("PRAGMA table_info(metrics_daily)")}
    assert {"ma60_below_streak", "daqin_pb"} <= cols


def test_stock_daily_has_valuation_columns() -> None:
    """防漂移检查：D-27 的不复权价与每股净资产列（PB / 股息率 TTM 的计算基础）。"""
    cols = {r[1] for r in _conn().execute("PRAGMA table_info(stock_daily)")}
    assert {"daqin_close_raw", "daqin_bps"} <= cols


def test_migrate_adds_missing_columns_on_old_db(tmp_path) -> None:
    """旧库（缺列）连接时自动补列：CREATE TABLE IF NOT EXISTS 不会修改已存在的表。"""
    import sqlite3

    old_path = tmp_path / "old.db"
    old = sqlite3.connect(old_path)
    old.execute("CREATE TABLE stock_daily (date TEXT PRIMARY KEY, daqin_close REAL)")
    old.execute("INSERT INTO stock_daily VALUES ('2020-01-02', 7.5)")
    old.commit()
    old.close()

    conn = db.connect(old_path)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(stock_daily)")}
    assert {"daqin_close_raw", "daqin_bps"} <= cols
    assert conn.execute("SELECT daqin_close FROM stock_daily").fetchone()[0] == 7.5   # 既有数据保留


def test_upsert_rows_idempotent_and_updates() -> None:
    conn = _conn()
    row = {"date": "2020-03-23", "daqin_close": 7.22}
    assert db.upsert_rows(conn, "stock_daily", [row]) == 1
    assert db.upsert_rows(conn, "stock_daily", [row]) == 1
    assert conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0] == 1
    db.upsert_rows(conn, "stock_daily", [{"date": "2020-03-23", "daqin_close": 7.30}])
    got = conn.execute("SELECT daqin_close FROM stock_daily WHERE date='2020-03-23'").fetchone()[0]
    assert got == pytest.approx(7.30)
    assert conn.execute("SELECT COUNT(*) FROM stock_daily").fetchone()[0] == 1


def test_upsert_rows_empty_returns_zero() -> None:
    assert db.upsert_rows(_conn(), "stock_daily", []) == 0


def test_upsert_rows_missing_key_raises() -> None:
    with pytest.raises(ValueError, match="主键列"):
        db.upsert_rows(_conn(), "stock_daily", [{"daqin_close": 1.0}])


def test_upsert_rows_inconsistent_columns_raises() -> None:
    rows = [{"date": "2020-01-02", "daqin_close": 1.0}, {"date": "2020-01-03", "daqin_high": 2.0}]
    with pytest.raises(ValueError, match="列与首行不一致"):
        db.upsert_rows(_conn(), "stock_daily", rows)


def test_upsert_df_nan_becomes_null() -> None:
    conn = _conn()
    df = pd.DataFrame([{"date": "2020-01-02", "daqin_close": 7.2, "csi300_close": float("nan")}])
    assert db.upsert_df(conn, "stock_daily", df) == 1
    row = conn.execute("SELECT daqin_close, csi300_close FROM stock_daily").fetchone()
    assert row["daqin_close"] == pytest.approx(7.2)
    assert row["csi300_close"] is None
    assert db.upsert_df(conn, "stock_daily", pd.DataFrame()) == 0


def test_freshness_reused_from_base() -> None:
    conn = _conn()
    set_freshness(conn, "stock_daily", ok=True, rows=5, now="2026-09-21 21:30:00")
    row = get_freshness(conn).set_index("dataset").loc["stock_daily"]
    assert row["last_success"] == "2026-09-21 21:30:00"
    assert row["rows_last"] == 5
