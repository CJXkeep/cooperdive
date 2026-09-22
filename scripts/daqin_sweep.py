"""daqin 阈值敏感度扫描（M5 校准）。

对若干阈值做**单变量扰动**，在三事件（2008 / 2015 / 2020）上跑回测，输出回撤/收益对比。
校准目标（M5 约定）：**主目标为最大回撤最小**（防御型策略的第一诉求），
「事件响应及时性」作为报告中的辅助观察项，不单独作为优化目标。

用法：`python -m scripts.daqin_sweep`
"""

from __future__ import annotations

from dataclasses import replace

from daqin.backtest import engine, events
from daqin.storage import db
from daqin.thresholds import Thresholds, load_thresholds

EVENT_KEYS = ("2008", "2015", "2020")


def build_grid(base: Thresholds) -> list[tuple[str, Thresholds]]:
    """构建单变量对比网格（每次只扰动一个参数，便于归因）。"""
    return [
        ("base（未校准）", base),
        ("macro.us10y_high 5.0→4.5", replace(base, macro=replace(base.macro, us10y_high=4.5))),
        ("macro.us10y_high 5.0→5.5", replace(base, macro=replace(base.macro, us10y_high=5.5))),
        ("macro.csi300_drawdown_20d -10→-8", replace(base, macro=replace(base.macro, csi300_drawdown_20d=-8.0))),
        ("macro.csi300_drawdown_20d -10→-12", replace(base, macro=replace(base.macro, csi300_drawdown_20d=-12.0))),
        ("market.ma_below_days 3→2", replace(base, market=replace(base.market, ma_below_days=2))),
        ("market.rs_60d_lookback_peak 5.0→3.0", replace(base, market=replace(base.market, rs_60d_lookback_peak=3.0))),
        ("market.single_day_drop_pct 5.0→4.0", replace(base, market=replace(base.market, single_day_drop_pct=4.0))),
        ("bottom.pb_below 0.60→0.70", replace(base, bottom=replace(base.bottom, pb_below=0.70))),
        ("debounce.daily_confirm_days 2→1", replace(base, debounce=replace(base.debounce, daily_confirm_days=1))),
        ("debounce.daily_confirm_days 2→3", replace(base, debounce=replace(base.debounce, daily_confirm_days=3))),
        ("position.base 0.60→0.40", replace(base, position=replace(base.position, base=0.40))),
        ("position.s3_floor 0.20→0.00", replace(base, position=replace(base.position, s3_floor=0.00))),
    ]


def main() -> int:
    conn = db.connect()
    try:
        base = load_thresholds()
        head = " | ".join(f"{k}: 回撤 / 收益".ljust(26) for k in EVENT_KEYS)
        print(f"{'配置':<36} | {head}")
        print("-" * 132)
        for label, cfg in build_grid(base):
            cells = []
            for key in EVENT_KEYS:
                ev = events.get_event(key)
                bt = engine.BacktestConfig(start=ev.start, end=ev.end)
                s = engine.run_backtest(conn, bt, cfg).summary
                cells.append(f"{s['max_drawdown']:>7.2%} / {s['total_return']:>+7.2%}".ljust(26))
            print(f"{label:<36} | " + " | ".join(cells))
        return 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
