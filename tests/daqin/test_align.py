"""M0-5 单测：t-1 可得性对齐 / 缺口 ffill / 未来观测不可见（防未来函数）/ lag=0。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.indicators.align import align_to_trading_days  # noqa: E402

TRADING = pd.DatetimeIndex(["2020-01-02", "2020-01-03", "2020-01-06"])


def test_macro_available_next_day() -> None:
    """美债 t 日观测在 t+1 可用：交易日 t 只能拿到 t-1 的观测。"""
    ext = pd.Series([1.88, 1.80], index=["2020-01-02", "2020-01-03"])
    out = align_to_trading_days(ext, TRADING, lag_days=1)
    assert pd.isna(out.loc["2020-01-02"])                 # 当天观测尚未发布
    assert out.loc["2020-01-03"] == 1.88                  # 用到 01-02 的观测
    assert out.loc["2020-01-06"] == 1.80                  # 用到 01-03 的观测（01-04/05 周末）


def test_future_observation_invisible() -> None:
    """future：观测日在交易日之后（如回测期间误用未来数据）必须不可见。"""
    ext = pd.Series([1.60], index=["2020-01-10"])
    out = align_to_trading_days(ext, TRADING, lag_days=1)
    assert out.isna().all()


def test_gap_ffill() -> None:
    """观测缺口（如假期未发布）→ ffill 最近可得值。"""
    ext = pd.Series([1.88], index=["2020-01-02"])
    out = align_to_trading_days(ext, TRADING, lag_days=1)
    assert out.loc["2020-01-03"] == 1.88
    assert out.loc["2020-01-06"] == 1.88


def test_lag_zero_same_day() -> None:
    """lag=0：观测日当天即可用。"""
    ext = pd.Series([1.88], index=["2020-01-02"])
    out = align_to_trading_days(ext, TRADING, lag_days=0)
    assert out.loc["2020-01-02"] == 1.88


def test_empty_external_returns_nan_series() -> None:
    out = align_to_trading_days(pd.Series(dtype=float), TRADING, lag_days=1)
    assert out.isna().all() and len(out) == len(TRADING)


def test_trading_days_outside_obs_range() -> None:
    """外部序列起点晚于交易日 → 早段为 NaN，不向前填充伪造数据。"""
    ext = pd.Series([1.80], index=["2020-01-03"])
    out = align_to_trading_days(ext, TRADING, lag_days=1)
    assert pd.isna(out.loc["2020-01-02"])
    assert out.loc["2020-01-06"] == 1.80
