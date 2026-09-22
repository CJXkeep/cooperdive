"""M3：03 §3.2 状态转换矩阵的**参数化逐格覆盖**。

矩阵（行=当前状态，列=目标状态；`-` 为设计上不可直达）：

```
     → S0        → S1    → S2                      → S3     → S4          → S5
S0      -       M1∨M4   (D1∨D2∨D3∨D4)∧K1           K2∨K3      -             -
S1    M1解除∨超时   -    (D1∨D2∨D3∨D4)∧K1           K2∨K3      -             -
S2      -        D系修复     -                       K2∨K3      -             -
S3      -          -     K1复现∧D未修复(K已解除)      -      K5∧降幅收窄      -
S4      -          -        -                       K2∨K3      -        运量转正
S5    回补满        -        -                       K2∨K3      -             -
```

同时覆盖 M3 验收清单的其余项：滞回未达解除阈值不降级、防抖未确认不转换、同日多条件冲突取高风险、
`qhd_enabled=false` 时 D1-D3 恒 False、不可达路径（S0/S1/S2 不得直达 S4/S5）。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from daqin.signals import rules  # noqa: E402
from daqin.signals.state_machine import (  # noqa: E402
    new_debouncers, next_state, position_advice, rebuild_days_needed,
    rebuild_steps_needed, run_daily,
)
from daqin.thresholds import load_thresholds  # noqa: E402

CFG = load_thresholds()

_COLS = [
    "us10y", "us10y_20d_chg", "spread_daqin_us10y", "csi300_drawdown_20d",
    "rel_strength_20d", "rs_60d_peak", "daily_return_pct", "volume_ratio_5d", "ma60_below_streak",
    "daqin_pb", "dividend_yield_ttm",
    "dq_vol_yoy", "dq_vol_yoy_lag1", "dq_vol_yoy_narrow_streak",
    "pp_available_days", "pp_avail_high_streak", "pp_avail_normal_streak",
    "qhd_inv_level", "qhd_inv_wow", "qhd_inv_yoy", "qhd_inv_wow_streak", "qhd_inv_down_streak",
    "zc_valid", "zc_dev_20d",
]

_M4 = {"csi300_drawdown_20d": -12.0}                       # 系统性下跌
_M1 = {"us10y": 5.5}                                       # 美债高位
_D4_K1 = {"dq_vol_yoy": -3.0, "rel_strength_20d": -1.0, "rs_60d_peak": 6.0}
_K2 = {"daily_return_pct": -6.0, "volume_ratio_5d": 2.0}
_K3 = {"ma60_below_streak": 4}
_K5_NARROW = {"daqin_pb": 0.5, "dividend_yield_ttm": 6.0, "dq_vol_yoy_narrow_streak": 2}
_VOL_RECOVER = {"dq_vol_yoy": 2.0, "dq_vol_yoy_lag1": 1.0}


def _row(**kw) -> pd.Series:
    return pd.Series({**{c: None for c in _COLS}, **kw}, dtype=object)


def _settle(cur: str, row, cfg=CFG, days: int = 2, state_days: int = 1) -> str:
    """连续喂入 `days` 次（满足日频防抖）后返回状态。"""
    deb = new_debouncers(cfg)
    state = cur
    for i in range(days):
        state = next_state(state, row, cfg, deb, state_days + i)
    return state


# ---------------- 矩阵：可转移单元格 ----------------

_TRANSITIONS = [
    ("S0", _M1, "S1", "M1 美债高位"),
    ("S0", _M4, "S1", "M4 系统性下跌"),
    ("S0", _D4_K1, "S2", "需求恶化 ∧ K1"),
    ("S0", _K2, "S3", "K2 放量下跌（跳级）"),
    ("S0", _K3, "S3", "K3 破位（跳级）"),
    ("S1", {"us10y": 4.0}, "S0", "M1 解除（低于滞回解除阈值且无 M4）"),
    ("S1", _D4_K1, "S2", "需求恶化 ∧ K1"),
    ("S1", _K3, "S3", "K3 破位（跳级）"),
    ("S2", {}, "S1", "D 系修复"),
    ("S2", _K3, "S3", "K3 破位"),
    ("S3", _D4_K1, "S2", "K1 复现 ∧ D 未修复 ∧ 技术恶化已解除（D-28）"),
    ("S3", _K5_NARROW, "S4", "K5 ∧ 运量降幅连续收窄"),
    ("S4", _K3, "S3", "风险降级：K3（D-10）"),
    ("S4", _VOL_RECOVER, "S5", "运量连续转正（D-26 自动核）"),
    ("S5", _K2, "S3", "风险降级：K2（D-10）"),
]


@pytest.mark.parametrize("cur,row_kw,expect,note", _TRANSITIONS, ids=[t[3] for t in _TRANSITIONS])
def test_matrix_reachable_cells(cur, row_kw, expect, note) -> None:
    assert _settle(cur, _row(**row_kw)) == expect, note


# ---------------- 矩阵：不可达单元格 ----------------

@pytest.mark.parametrize("cur", ["S0", "S1"])
def test_s4_s5_not_reachable_without_s3(cur) -> None:
    """S4/S5 只能经 S3 进入：S0/S1 即使在 S4/S5 条件成立时也不被拉过去（03 §3.2 矩阵的 `-`）。"""
    row = _row(**{**_K5_NARROW, **_VOL_RECOVER})
    assert _settle(cur, row) == cur


def test_s3_does_not_self_jump_to_s5() -> None:
    """S3 在运量转正时先到 S4（K5∧收窄），不跳过 S4 直达 S5。"""
    assert _settle("S3", _row(**_K5_NARROW)) == "S4"


# ---------------- S5 → S0「回补满」（D-29） ----------------

def test_s5_rebuild_completes_to_s0() -> None:
    """S5 走满回补步即回 S0：默认 4 步（20%→60%，每步 10%）、每 5 交易日一步 → 16 天。"""
    assert rebuild_steps_needed(CFG) == 4
    assert rebuild_days_needed(CFG) == 16
    row = _row()                                           # 无风险条件
    assert _settle("S5", row, days=1, state_days=rebuild_days_needed(CFG) - 1) == "S5"
    assert _settle("S5", row, days=1, state_days=rebuild_days_needed(CFG)) == "S0"


def test_s5_position_steps_up() -> None:
    """S5 仓位按步进：第 1 天 30% → 第 6 天 40% → 第 11 天 50% → 第 16 天 60%。"""
    assert position_advice("S5", CFG, 1) == pytest.approx(0.30)
    assert position_advice("S5", CFG, 6) == pytest.approx(0.40)
    assert position_advice("S5", CFG, 11) == pytest.approx(0.50)
    assert position_advice("S5", CFG, 16) == pytest.approx(0.60)


# ---------------- 滞回 / 防抖 / 冲突 ----------------

def test_hysteresis_zone_does_not_release() -> None:
    """S1：us10y 落在滞回区（解除 4.8 < 值 < 升级 5.0）→ 不降级。"""
    assert _settle("S1", _row(us10y=4.9)) == "S1"


def test_s1_timeout_releases() -> None:
    """S1 停留超过 `s1_timeout_days` 且无 M4 → 回 S0。"""
    row = _row(us10y=5.5)                                  # 仍高位，未达解除阈值
    assert _settle("S1", row, days=1, state_days=CFG.macro.s1_timeout_days) == "S0"


def test_debounce_blocks_first_day() -> None:
    """日频规则需连续 2 日：第 1 日不放行，第 2 日放行。"""
    deb = new_debouncers(CFG)
    row = _row(**_M1)
    assert next_state("S0", row, CFG, deb, 1) == "S0"
    assert next_state("S0", row, CFG, deb, 2) == "S1"


def test_conflict_takes_higher_risk() -> None:
    """同日 K3（→S3）与 D4∧K1（→S2）同时成立 → 取更高风险 S3。"""
    assert _settle("S0", _row(**{**_K3, **_D4_K1})) == "S3"


# ---------------- qhd 关闭时 D1-D3 恒 False ----------------

def test_qhd_disabled_makes_d1_d3_inert() -> None:
    """`qhd_enabled=false`（默认）时 D1/D2/D3 恒 False，即使 qhd 列有值。"""
    assert CFG.demand.qhd_enabled is False
    row = _row(qhd_inv_level=900.0, qhd_inv_wow=10.0, qhd_inv_wow_streak=5, qhd_inv_yoy=30.0)
    for fn in (rules.r_d1, rules.r_d2, rules.r_d3):
        assert fn(row, CFG)[0] is False
    assert rules.demand_bad(row, CFG) is False


# ---------------- signal_log 每日一行 + changed ----------------

def test_run_daily_one_row_per_day_and_changed_flag() -> None:
    """`run_daily` 每日恰好一行；`changed` 仅在状态变化日为 1（M3 验收）。"""
    metrics = pd.DataFrame([
        {"date": "2020-03-02", "us10y": 1.0},
        {"date": "2020-03-03", "us10y": 1.0},
        {"date": "2020-03-04", "csi300_drawdown_20d": -12.0},   # 防抖第 1 日
        {"date": "2020-03-05", "csi300_drawdown_20d": -12.0},   # 确认 → S1
    ])
    out = run_daily(metrics, CFG)
    assert len(out) == len(metrics)
    assert out["state"].tolist() == ["S0", "S0", "S0", "S1"]
    assert out["changed"].tolist() == [0, 0, 0, 1]
    assert out["prev_state"].tolist() == ["S0", "S0", "S0", "S0"]
    assert out["position_advice"].tolist() == [0.60, 0.60, 0.60, 0.60]
