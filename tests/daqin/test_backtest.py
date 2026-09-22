"""M4 回测单测：执行模型（T+1 开盘）/ 成本与现金敏感性 / 防未来函数抽查（含反例验证）。

防未来函数抽查的可信度取决于"它真的能报出泄漏"——因此除了正向用例，还**故意注入一个未来函数**，
断言 probe 必须检出（否则"永远通过"的检查等于没有）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.backtest import engine, events  # noqa: E402
from daqin.indicators.daily import compute_daily  # noqa: E402
from daqin.storage import db  # noqa: E402


def _seed_market(conn, days: int = 300, start: str = "2019-01-02") -> list[str]:
    """构造行情（收盘价带趋势 + 末段跳变），用于抽查与模拟。"""
    dates = [d.strftime("%Y-%m-%d") for d in pd.date_range(start, periods=days, freq="D")]
    rows = []
    for i, d in enumerate(dates):
        close = 10.0 + i * 0.01 + (3.0 if i > days - 30 else 0.0)   # 末段"未来"大涨
        rows.append({
            "date": d, "daqin_open": close, "daqin_close": close,
            "daqin_amount": 1_000_000.0 + i, "csi300_close": 4000.0 + i,
        })
    db.upsert_rows(conn, "stock_daily", rows)
    return dates


def _signals(rows: list[tuple[str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame([{"date": d, "state": s, "position_advice": p} for d, s, p in rows])


# ---------------- 执行模型 ----------------

def test_simulate_uses_next_open_and_charges_cost() -> None:
    """T 日收盘信号 → T+1 **开盘**调仓：首日按前一日 100% 仓位在开盘买入，当日收益即开盘→收盘。"""
    signals = _signals([
        ("2020-01-02", "S0", 1.0),
        ("2020-01-03", "S0", 1.0),
        ("2020-01-06", "S3", 0.0),
        ("2020-01-07", "S3", 0.0),
    ])
    prices = pd.DataFrame([
        {"date": "2020-01-02", "daqin_open": 10.0, "daqin_close": 10.0},
        {"date": "2020-01-03", "daqin_open": 10.0, "daqin_close": 11.0},   # +10%
        {"date": "2020-01-06", "daqin_open": 11.0, "daqin_close": 11.0},
        {"date": "2020-01-07", "daqin_open": 11.0, "daqin_close": 11.0},
    ])
    cfg = engine.BacktestConfig(start="2020-01-03", end="2020-01-07", cost_bps=0.0)
    daily = engine._simulate(signals, prices, cfg)
    # 01-03：决策日 01-02 为 S0(100%) → 开盘 10 买入、收盘 11 → +10%
    assert daily.iloc[0]["nav"] == pytest.approx(1.10)
    assert daily.iloc[0]["position"] == pytest.approx(1.0)
    # 01-06：决策日 01-03 仍是 S0 → 换手 0（体现"信号延后一个交易日执行"）
    assert daily.iloc[1]["turnover"] == pytest.approx(0.0)
    # 01-07：决策日 01-06 的 S3(0%) → 开盘 11 全部卖出 → 换手 1.0、当日无收益
    assert daily.iloc[2]["turnover"] == pytest.approx(1.0)
    assert daily.iloc[2]["nav"] == pytest.approx(1.10)


def test_cost_and_cash_yield_move_nav_in_expected_direction() -> None:
    """单边成本降低净值；现今年化收益抬高净值。"""
    signals = _signals([("2020-01-02", "S3", 0.2), ("2020-01-03", "S0", 1.0)])
    prices = pd.DataFrame([
        {"date": "2020-01-02", "daqin_open": 10.0, "daqin_close": 10.0},
        {"date": "2020-01-03", "daqin_open": 10.0, "daqin_close": 10.0},
    ])
    base = engine._simulate(signals, prices, engine.BacktestConfig(start="2020-01-03", end="2020-01-03"))
    costly = engine._simulate(signals, prices, engine.BacktestConfig(start="2020-01-03", end="2020-01-03", cost_bps=100.0))
    cashy = engine._simulate(signals, prices, engine.BacktestConfig(start="2020-01-03", end="2020-01-03", cash_yield=0.015))
    assert costly.iloc[0]["cost"] > base.iloc[0]["cost"] == 0.0
    assert costly.iloc[0]["nav"] < base.iloc[0]["nav"] < cashy.iloc[0]["nav"]


def test_get_event_unknown_key_raises() -> None:
    with pytest.raises(KeyError):
        events.get_event("1900")
    assert events.get_event("2020").anchor == "2020-02-03"


# ---------------- 防未来函数抽查 ----------------

def test_lookahead_probe_passes_on_clean_pipeline() -> None:
    """正向：正常指标管线在抽查日与截断重算完全一致。"""
    conn = db.connect(":memory:")
    _seed_market(conn)
    compute_daily(conn)
    probe = engine.lookahead_probe(conn, ["2019-06-03"])
    assert probe["2019-06-03"] == []


def test_lookahead_probe_detects_planted_leak(monkeypatch) -> None:
    """反例：故意注入未来函数（写入全库最大日期）→ 抽查必须报出差异（否则检查形同虚设）。"""
    conn = db.connect(":memory:")
    _seed_market(conn)
    compute_daily(conn)

    def _leaky(conn_, start=None, end=None) -> int:
        max_date = conn_.execute("SELECT MAX(date) FROM stock_daily").fetchone()[0]
        rows = [{"date": r[0], "daqin_close": float(max_date.replace("-", ""))}
                for r in conn_.execute("SELECT date FROM stock_daily")]
        return db.upsert_rows(conn_, "metrics_daily", rows, key="date")

    monkeypatch.setattr(engine, "compute_daily", _leaky)
    probe = engine.lookahead_probe(conn, ["2019-06-03"])
    assert "daqin_close" in probe["2019-06-03"]


def test_lookahead_probe_skips_asof_columns() -> None:
    """`asof_*` 是数据截止日标注（随数据范围变化属正常语义），不得被报为不一致。"""
    conn = db.connect(":memory:")
    _seed_market(conn)
    db.upsert_rows(conn, "macro_daily", [
        {"date": "2019-06-01", "us10y": 2.0},
    ])
    compute_daily(conn)
    probe = engine.lookahead_probe(conn, ["2019-06-03"])
    assert probe["2019-06-03"] == []
