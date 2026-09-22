"""M0-7 指标层单测：均线/收益率/连续破位/量比/相对强弱/宏观 t-1 对齐（集成）。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.indicators import demand as demand_mod  # noqa: E402
from daqin.indicators.common import streak as _streak  # noqa: E402
from daqin.indicators.daily import compute_daily, uncovered_columns  # noqa: E402
from daqin.thresholds import load_thresholds  # noqa: E402
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


def test_zc_valid_and_dev_alignment() -> None:
    """M1-1：ZC 有效主力过滤（D-05）+ 20 日偏离，对齐到交易日（lag=0）。"""
    conn = db.connect(":memory:")
    dates = _seed(conn, [10.0] * 30)
    zc_rows = []
    for i, d in enumerate(dates):
        amount = 1000.0 if i < 28 else 100.0        # 最后 2 天成交额骤降 → 无效主力
        zc_rows.append({"date": d, "zc_close": 600.0, "zc_volume": amount / 600.0,
                        "zc_amount": amount, "zc_hold": 1.0})
    db.upsert_rows(conn, "futures_daily", zc_rows)
    compute_daily(conn)
    rows = {r["date"]: r for r in conn.execute("SELECT date, zc_valid, zc_dev_20d FROM metrics_daily")}
    assert rows[dates[10]]["zc_valid"] == 0                 # 20 日均额未成形 → 保守禁用
    assert rows[dates[25]]["zc_valid"] == 1                 # 成交额正常 → 有效主力
    assert rows[dates[29]]["zc_valid"] == 0                 # 骤降 → 无效，D6 被禁用
    assert rows[dates[29]]["zc_dev_20d"] == pytest.approx(0.0)
    asof = conn.execute("SELECT asof_industry FROM metrics_daily LIMIT 1").fetchone()
    assert asof["asof_industry"] == dates[-1]               # 行业数据截止标注


def test_empty_stock_returns_zero() -> None:
    conn = db.connect(":memory:")
    assert compute_daily(conn) == 0


def test_range_filter() -> None:
    conn = db.connect(":memory:")
    dates = _seed(conn, [10.0] * 10)
    assert compute_daily(conn, start=dates[3], end=dates[6]) == 4


def test_pb_and_dividend_yield() -> None:
    """M1-5：PB = 不复权价 ÷ bps；股息率 TTM 按除权日滚动 365 天。"""
    conn = db.connect(":memory:")
    dates = _seed(conn, [10.0] * 5)
    db.upsert_rows(conn, "stock_daily", [
        {"date": dates[i], "daqin_close_raw": 8.0 + i * 0.1} for i in range(5)
    ])
    db.upsert_rows(conn, "stock_daily", [{"date": dates[0], "daqin_bps": 4.0}])
    db.upsert_rows(conn, "dividend_events", [{"ex_date": dates[1], "dps": 0.4, "source": "t"}], key="ex_date")
    compute_daily(conn)
    rows = {r["date"]: r for r in conn.execute("SELECT * FROM metrics_daily")}
    assert rows[dates[0]]["daqin_pb"] == pytest.approx(2.0)                       # 8.0 / 4.0
    assert rows[dates[0]]["dividend_yield_ttm"] == pytest.approx(0.0)             # 除权日前无 TTM 分红
    assert rows[dates[1]]["dividend_yield_ttm"] == pytest.approx(0.4 / 8.1 * 100, rel=1e-6)


def test_metric_columns_fully_covered() -> None:
    """自检：DDL 的 metrics_daily 全部列都有指标层产出（新增列漏接会立刻失败）。"""
    assert uncovered_columns(db.connect(":memory:")) == set()


def test_qhd_columns(monkeypatch) -> None:
    """qhd 增强列（D1/D2/D3）：周环比 / 同比 / 连续累库与下降周数（qhd_enabled 时）。"""
    conn = db.connect(":memory:")
    _seed(conn, [10.0] * 25)                                   # 2019-10-01 起 25 个自然日
    import dataclasses

    cfg = load_thresholds()
    cfg = dataclasses.replace(cfg, demand=dataclasses.replace(cfg.demand, qhd_enabled=True))  # frozen dataclass
    monkeypatch.setattr(demand_mod, "load_thresholds", lambda: cfg)
    db.upsert_rows(conn, "industry_weekly", [
        {"week_end": "2019-10-04", "qhd_inventory": 700.0},
        {"week_end": "2019-10-11", "qhd_inventory": 750.0},    # +7.14% > 5% → streak 1
        {"week_end": "2019-10-18", "qhd_inventory": 800.0},    # +6.67% > 5% → streak 2
    ], key="week_end")
    compute_daily(conn)
    rows = {r["date"]: r for r in conn.execute(
        "SELECT date, qhd_inv_level, qhd_inv_wow, qhd_inv_wow_streak, qhd_inv_down_streak FROM metrics_daily")}
    assert rows["2019-10-04"]["qhd_inv_level"] == pytest.approx(700.0)
    assert rows["2019-10-11"]["qhd_inv_wow"] == pytest.approx(50.0 / 700.0 * 100, rel=1e-6)
    assert rows["2019-10-11"]["qhd_inv_wow_streak"] == 1
    assert rows["2019-10-18"]["qhd_inv_wow_streak"] == 2
    assert rows["2019-10-18"]["qhd_inv_down_streak"] == 0


def test_qhd_disabled_leaves_columns_null() -> None:
    """qhd_enabled=false（默认）时不写 qhd 列（保持 NULL）。"""
    conn = db.connect(":memory:")
    _seed(conn, [10.0] * 10)
    db.upsert_rows(conn, "industry_weekly", [{"week_end": "2019-10-04", "qhd_inventory": 800.0}], key="week_end")
    compute_daily(conn)
    assert conn.execute("SELECT qhd_inv_level FROM metrics_daily").fetchone()[0] is None


def test_demand_alignment_from_monthly() -> None:
    """M1-3：月频运量按「次月 10 日可得」对齐，lag1 取上一月，收窄连续月数正确。"""
    conn = db.connect(":memory:")
    _seed(conn, [10.0] * 80)                                  # 2019-10-01 ~ 2019-12-19
    db.upsert_rows(conn, "company_monthly", [
        {"month": "2019-09", "dq_line_volume": 3000.0, "dq_line_volume_yoy": -5.0},
        {"month": "2019-10", "dq_line_volume": 3200.0, "dq_line_volume_yoy": -2.0},
    ], key="month")
    compute_daily(conn)
    rows = {r["date"]: r for r in conn.execute(
        "SELECT date, dq_vol_yoy, dq_vol_yoy_lag1, dq_vol_yoy_narrow_streak, asof_company FROM metrics_daily")}
    assert rows["2019-10-09"]["dq_vol_yoy"] is None            # 9 月数据在 10-10 前不可得
    assert rows["2019-10-10"]["dq_vol_yoy"] == pytest.approx(-5.0)
    assert rows["2019-11-10"]["dq_vol_yoy"] == pytest.approx(-2.0)
    assert rows["2019-11-10"]["dq_vol_yoy_lag1"] == pytest.approx(-5.0)
    assert rows["2019-11-10"]["dq_vol_yoy_narrow_streak"] == 1  # -2.0 > -5.0 收窄
    assert rows["2019-11-10"]["asof_company"] == "2019-10"


def test_energy_streaks() -> None:
    """M1-2：电厂可用天数对齐 + 高位连续天数（D5）。"""
    conn = db.connect(":memory:")
    dates = _seed(conn, [10.0] * 40)
    db.upsert_rows(conn, "energy_daily", [
        {"date": dates[i], "pp_available_days": 30.0 if i < 10 else 15.0} for i in range(40)
    ])
    compute_daily(conn)
    rows = {r["date"]: r for r in conn.execute(
        "SELECT date, pp_avail_high_streak, pp_avail_normal_streak FROM metrics_daily")}
    assert rows[dates[5]]["pp_avail_high_streak"] == 6          # 30 > 25 连续 6 天
    assert rows[dates[12]]["pp_avail_high_streak"] == 0         # 回落后归零
    assert rows[dates[12]]["pp_avail_normal_streak"] == 3       # 15 < 20 连续 3 天
