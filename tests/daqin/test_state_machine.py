"""M0-6 状态机单测：跳级 / 逐级降级 / 滞回 / 防抖 / D-10 降级 / D-22 的 S5 可达性 / 全序列输出。"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.signals.state_machine import (  # noqa: E402
    Debouncer, new_debouncers, next_state, position_advice, run_daily,
)
from daqin.thresholds import load_thresholds  # noqa: E402

CFG = load_thresholds()
M1_ROW = {"us10y": 5.2}
K2_ROW = {"daily_return_pct": -6.0, "volume_ratio_5d": 2.0}
S2_ROW = {"pp_available_days": 30.0, "rel_strength_20d": -1.0, "rs_60d_peak": 6.0}
S5_ROW = {"dq_vol_yoy": 1.0, "dq_vol_yoy_lag1": 0.5}   # D-26：运量连续 2 月转正


def _row(**kw) -> pd.Series:
    return pd.Series(kw)


def test_s0_to_s1_requires_debounce() -> None:
    deb = new_debouncers(CFG)
    assert next_state("S0", _row(**M1_ROW), CFG, deb) == "S0"   # 第 1 日：防抖未过
    assert next_state("S0", _row(**M1_ROW), CFG, deb) == "S1"   # 第 2 日：放行


def test_jump_s0_to_s3_on_k2() -> None:
    deb = new_debouncers(CFG)
    assert next_state("S0", _row(**K2_ROW), CFG, deb) == "S0"
    assert next_state("S0", _row(**K2_ROW), CFG, deb) == "S3"   # 允许跳级


def test_s0_to_s2_needs_demand_and_k1() -> None:
    deb = new_debouncers(CFG)
    assert next_state("S0", _row(**S2_ROW), CFG, deb) == "S0"   # K1 防抖
    assert next_state("S0", _row(**S2_ROW), CFG, deb) == "S2"   # 自动核 D5 ∧ K1


def test_hysteresis_release_and_timeout() -> None:
    deb = new_debouncers(CFG)
    assert next_state("S1", _row(us10y=4.9), CFG, deb) == "S1"          # 未达解除阈值 4.8
    assert next_state("S1", _row(us10y=4.7), CFG, deb) == "S0"          # 滞回释放
    deb2 = new_debouncers(CFG)
    assert next_state("S1", _row(), CFG, deb2, state_days=60) == "S0"   # S1 超时


def test_s2_demotes_to_s1_when_demand_recovers() -> None:
    deb = new_debouncers(CFG)
    assert next_state("S2", _row(dq_vol_yoy=2.0), CFG, deb) == "S1"     # D 修复 → 逐级降
    assert next_state("S2", _row(dq_vol_yoy=-1.0), CFG, deb) == "S2"    # D 未修复 → 保持


def test_s3_to_s4_then_s5_without_qhd() -> None:
    """D-22/D-26 核心：qhd 关闭、电厂数据停更时，S4 → S5 仍可达（运量连续 2 月转正确认）。"""
    deb = new_debouncers(CFG)
    s4_row = _row(daqin_pb=0.55, dividend_yield_ttm=6.0, dq_vol_yoy_narrow_streak=2)
    assert next_state("S3", s4_row, CFG, deb) == "S4"
    assert next_state("S4", _row(**S5_ROW), CFG, deb) == "S5"


def test_s4_s5_demote_immediately_on_k2_k3() -> None:
    """D-10：S4/S5 期间 K2/K3 成立立即回 S3（不经防抖，风险方向快速响应）。"""
    assert next_state("S4", _row(**K2_ROW), CFG, new_debouncers(CFG)) == "S3"
    assert next_state("S5", _row(ma60_below_streak=3), CFG, new_debouncers(CFG)) == "S3"


def test_s3_to_s2_on_k1_with_demand_still_bad() -> None:
    """03 §3.2 字面：K1 复现 ∧ D 未修复（demand_bad 成立）→ S3 回 S2。"""
    row = _row(rel_strength_20d=-1.0, rs_60d_peak=6.0, dq_vol_yoy=-1.0)
    assert next_state("S3", row, CFG, new_debouncers(CFG)) == "S2"
    # D 已修复时不回 S2
    recovered = _row(rel_strength_20d=-1.0, rs_60d_peak=6.0, dq_vol_yoy=2.0)
    assert next_state("S3", recovered, CFG, new_debouncers(CFG)) == "S3"


def test_all_nan_row_is_inert() -> None:
    """数据缺失时状态机不误动。"""
    deb = new_debouncers(CFG)
    assert next_state("S0", _row(), CFG, deb) == "S0"
    assert next_state("S2", _row(), CFG, deb) == "S1"    # 仅滞回降级
    assert next_state("S3", _row(), CFG, deb) == "S3"


def test_position_advice_table() -> None:
    assert position_advice("S0", CFG) == 0.6
    assert position_advice("S2", CFG) == 0.402      # base × (1 − s2_reduce_ratio)
    assert position_advice("S3", CFG) == 0.2
    assert position_advice("S5", CFG) == 0.3


def test_debouncer_counts_consecutive_only() -> None:
    d = Debouncer(2)
    assert d.feed(True) is False
    assert d.feed(False) is False     # 中断归零
    assert d.feed(True) is False
    assert d.feed(True) is True


def test_run_daily_sequence_output() -> None:
    rows = [
        {},                                    # 平静
        {"us10y": 5.2},                        # M1 第 1 日
        {"us10y": 5.2},                        # M1 第 2 日 → S1
        {"us10y": 5.2, **K2_ROW},              # K2 第 1 日
        {"us10y": 5.2, **K2_ROW},              # K2 第 2 日 → S3（跳级）
    ]
    metrics = pd.DataFrame([{"date": f"2020-01-{i + 2:02d}", **r} for i, r in enumerate(rows)])
    out = run_daily(metrics, CFG)
    assert out["state"].tolist() == ["S0", "S0", "S1", "S1", "S3"]
    assert out["prev_state"].tolist() == ["S0", "S0", "S0", "S1", "S1"]
    assert out["changed"].tolist() == [0, 0, 1, 0, 1]
    assert out["position_advice"].tolist() == [0.6, 0.6, 0.6, 0.6, 0.2]
    assert out.loc[2, "triggered_rules"] == "M1"
    assert out.loc[4, "triggered_rules"] == "M1,K2"
