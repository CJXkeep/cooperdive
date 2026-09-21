"""M0-6 规则单测：触发/不触发 / 缺失值不误触发 / qhd 开关 / 煤价代理有效性 / S4-S5 条件。"""

from __future__ import annotations

import dataclasses
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.signals import rules  # noqa: E402
from daqin.thresholds import Thresholds, load_thresholds  # noqa: E402

CFG: Thresholds = load_thresholds()


def _row(**kw) -> pd.Series:
    """缺省列不存在 → 规则读取为 None（等价 NULL）。"""
    return pd.Series(kw)


def _cfg_qhd_on() -> Thresholds:
    return dataclasses.replace(CFG, demand=dataclasses.replace(CFG.demand, qhd_enabled=True))


def test_m1_and_m4() -> None:
    assert rules.r_m1(_row(us10y=5.2), CFG)[0] is True
    assert rules.r_m1(_row(us10y=4.9), CFG)[0] is False
    assert rules.r_m4(_row(csi300_drawdown_20d=-11.0), CFG)[0] is True
    assert rules.r_m4(_row(csi300_drawdown_20d=-9.0), CFG)[0] is False


def test_k2_and_k3() -> None:
    assert rules.r_k2(_row(daily_return_pct=-6.0, volume_ratio_5d=2.0), CFG)[0] is True
    assert rules.r_k2(_row(daily_return_pct=-6.0, volume_ratio_5d=1.0), CFG)[0] is False   # 量比未确认
    assert rules.r_k3(_row(ma60_below_streak=3), CFG)[0] is True
    assert rules.r_k3(_row(ma60_below_streak=2), CFG)[0] is False


def test_k1_requires_peak_and_turn() -> None:
    assert rules.r_k1(_row(rel_strength_20d=-1.0, rs_60d_peak=6.0), CFG)[0] is True
    assert rules.r_k1(_row(rel_strength_20d=-1.0, rs_60d_peak=4.0), CFG)[0] is False   # 此前从未强过
    assert rules.r_k1(_row(rel_strength_20d=1.0, rs_60d_peak=6.0), CFG)[0] is False    # 仍为正


def test_k5_bottom_requires_both() -> None:
    assert rules.r_k5(_row(daqin_pb=0.55, dividend_yield_ttm=6.0), CFG)[0] is True
    assert rules.r_k5(_row(daqin_pb=0.65, dividend_yield_ttm=6.0), CFG)[0] is False
    assert rules.r_k5(_row(daqin_pb=0.55, dividend_yield_ttm=5.0), CFG)[0] is False


def test_missing_values_never_fire() -> None:
    """缺失数据不误触发（04 §3）。"""
    empty = _row()
    for fn in (rules.r_m1, rules.r_m2, rules.r_m3, rules.r_m4, rules.r_d1, rules.r_d2, rules.r_d3,
               rules.r_d4, rules.r_d5, rules.r_d6, rules.r_k1, rules.r_k2, rules.r_k3, rules.r_k5):
        assert fn(empty, CFG)[0] is False


def test_qhd_switch_gates_d1_d3() -> None:
    row = _row(qhd_inv_level=820.0, qhd_inv_wow=6.0, qhd_inv_wow_streak=3, qhd_inv_yoy=25.0)
    off, on = CFG, _cfg_qhd_on()
    assert rules.r_d1(row, off)[0] is False and rules.r_d2(row, off)[0] is False and rules.r_d3(row, off)[0] is False
    assert rules.r_d1(row, on)[0] is True and rules.r_d2(row, on)[0] is True and rules.r_d3(row, on)[0] is True


def test_demand_bad_auto_core_and_release() -> None:
    # 自动核：D5 单条即成立（qhd 关闭）
    assert rules.demand_bad(_row(pp_available_days=30.0), CFG) is True
    assert rules.demand_bad(_row(dq_vol_yoy=-3.0), CFG) is True
    assert rules.demand_bad(_row(dq_vol_yoy=2.0), CFG) is False
    # 滞回释放：库存 740 在释放阈值(720)之上 → 仍视为未修复；700 → 修复
    on = _cfg_qhd_on()
    assert rules.demand_bad(_row(qhd_inv_level=740.0), on, release=True) is True
    assert rules.demand_bad(_row(qhd_inv_level=700.0), on, release=True) is False


def test_d6_requires_valid_zc() -> None:
    weak = _row(zc_dev_20d=-12.0, zc_valid=1)
    assert rules.r_d6(weak, CFG)[0] is True
    assert rules.r_d6(_row(zc_dev_20d=-12.0, zc_valid=0), CFG)[0] is False   # 无效主力 → 禁用


def test_s4_and_s5_conditions() -> None:
    s4 = _row(daqin_pb=0.55, dividend_yield_ttm=6.0, dq_vol_yoy_narrow_streak=2)
    assert rules.s4_ready(s4, CFG)[0] is True
    # D-22：S5 自动核不依赖 qhd（默认关闭时仍可达）
    assert rules.s5_ready(_row(dq_vol_yoy=1.0, pp_avail_normal_streak=2), CFG)[0] is True
    assert rules.s5_ready(_row(dq_vol_yoy=-1.0, pp_avail_normal_streak=5), CFG)[0] is False   # 运量未转正
    assert rules.s5_ready(_row(dq_vol_yoy=1.0, pp_avail_normal_streak=1), CFG)[0] is False    # 回落未确认


def test_fired_rules_lists_hits() -> None:
    got = rules.fired_rules(_row(us10y=5.2, daily_return_pct=-6.0, volume_ratio_5d=2.0), CFG)
    assert got == ["M1", "K2"]
