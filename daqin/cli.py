"""daqin CLI：`collect` / `compute` / `signal`（Gate 0 验收入口）。

职责边界（D-21）：**本层是唯一落库入口**——采集编排、freshness、`signal_log` 均在此写入；
信号层（`signals/`）与对齐层（`indicators/align.py`）保持纯函数，回测与实盘共用。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import pandas as pd

from daqin.collectors import akshare_stock, company_report, dividend, energy, fred, futures, valuation
from daqin.indicators.daily import compute_daily
from daqin.signals.state_machine import run_daily
from daqin.storage import db
from daqin.thresholds import load_thresholds
from storage import set_freshness

# 采集器注册表：name → collect(conn, start=None, end=None) -> 写入行数
COLLECTORS = {
    "stock_daily": akshare_stock.collect,
    "macro_daily": fred.collect,
    "futures_daily": futures.collect,          # M1-1：ZC 动力煤主连（D6 代理）
    "dividend_events": dividend.collect,       # M1-5：分红派息（股息率 TTM）
    "company_monthly": company_report.collect, # M1-3：大秦线月度运量（公告自动解析）
    "energy_daily": energy.collect,            # M1-2：沿海六大电（仅历史段，D-26）
    "valuation": valuation.collect,            # M1-5：不复权价 + 季报 bps（写入 stock_daily，D-27）
}


def cmd_collect(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        failed = 0
        for name, fn in COLLECTORS.items():
            if args.names and name not in args.names:
                continue
            try:
                n = fn(conn, start=args.start, end=args.end)
                set_freshness(conn, name, ok=True, rows=n)
                print(f"[OK]   {name}: {n} rows")
            except Exception as exc:   # noqa: BLE001 单源失败不拖垮整轮
                failed += 1
                set_freshness(conn, name, ok=False, error=f"{type(exc).__name__}: {exc}")
                print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
        return 1 if failed else 0
    finally:
        conn.close()


def cmd_compute(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        n = compute_daily(conn, start=args.start, end=args.end)
        set_freshness(conn, "metrics_daily", ok=True, rows=n)
        print(f"[OK]   metrics_daily: {n} rows")
        return 0
    except Exception as exc:   # noqa: BLE001
        set_freshness(conn, "metrics_daily", ok=False, error=f"{type(exc).__name__}: {exc}")
        print(f"[FAIL] metrics_daily: {type(exc).__name__}: {exc}")
        return 1
    finally:
        conn.close()


def cmd_signal(args: argparse.Namespace) -> int:
    conn = db.connect()
    try:
        metrics = pd.read_sql_query("SELECT * FROM metrics_daily ORDER BY date", conn)
        if metrics.empty:
            print("metrics_daily 为空：请先运行 collect 与 compute")
            return 1
        cfg = load_thresholds()
        states = run_daily(metrics, cfg)                       # 纯函数回放（不落库）
        written = _write_signal_log(conn, states)              # 落库职责在本层（D-21）

        target = args.date or str(states.iloc[-1]["date"])
        row = states[states["date"] == target]
        if row.empty:
            print(f"无 {target} 的状态（区间 {states.iloc[0]['date']} ~ {states.iloc[-1]['date']}）")
            return 1
        r = row.iloc[0]
        print(f"{r['date']}  状态 {r['state']}（前值 {r['prev_state']}）  仓位建议 {float(r['position_advice']):.0%}")
        print(f"触发规则：{r['triggered_rules'] or '无'}")
        print(f"signal_log 累计写入 {written} 行（区间 {states.iloc[0]['date']} ~ {states.iloc[-1]['date']}）")
        return 0
    finally:
        conn.close()


def _write_signal_log(conn: sqlite3.Connection, states: pd.DataFrame) -> int:
    rows = [
        {
            "date": str(r["date"]),
            "state": str(r["state"]),
            "prev_state": str(r["prev_state"]),
            "changed": int(r["changed"]),
            "triggered_rules": (str(r["triggered_rules"]) or None),
            "position_advice": float(r["position_advice"]),
            "note": f"{r['state']}（触发：{r['triggered_rules'] or '无'}）",
        }
        for _, r in states.iterrows()
    ]
    return db.upsert_rows(conn, "signal_log", rows, key="date")


def cmd_backtest(args: argparse.Namespace) -> int:
    """回测（06 §4.6，M4）：重算指标 → 回放状态 → 净值模拟 → 报告；可选防未来函数抽查。"""
    from daqin.backtest import engine, events, report

    conn = db.connect()
    try:
        thresholds = load_thresholds()
        event = events.get_event(args.event)
        warm_from = (pd.Timestamp(event.start) - pd.Timedelta(days=args.warmup)).strftime("%Y-%m-%d")
        n = compute_daily(conn, start=warm_from, end=event.end)      # 保证覆盖 warm-up
        print(f"[OK]   指标重算 {n} 行（{warm_from} ~ {event.end}）")

        scenarios = [
            ("基准（无成本 / 现金不计息）", 0.0, 0.0),
            ("含成本（单边 0.1%）", 10.0, 0.0),
            ("现金 1.5% 年化", 0.0, 0.015),
        ]
        results = []
        for label, cost, cash in scenarios:
            cfg = engine.BacktestConfig(
                start=event.start, end=event.end, warmup_days=args.warmup,
                cost_bps=cost, cash_yield=cash, label=event.name,
            )
            res = engine.run_backtest(conn, cfg, thresholds)
            results.append((label, res))
            print(f"       {label}: 收益 {res.summary['total_return']:+.2%}"
                  f" | 最大回撤 {res.summary['max_drawdown']:.2%}"
                  f" | 换手 {res.summary['total_turnover']:.2f} | 调仓 {int(res.summary['trades'])} 次")

        main_res = results[0][1]
        path = report.write_report(
            main_res, engine.BacktestConfig(start=event.start, end=event.end, warmup_days=args.warmup),
            event, scenarios=[(lbl, r.summary) for lbl, r in results],
        )
        print(f"[OK]   报告：{path}")

        if args.probe:
            diffs = engine.lookahead_probe(conn, args.probe)
            bad = {k: v for k, v in diffs.items() if v}
            print(f"[OK]   防未来函数抽查 {len(diffs)} 个时点：{'全部一致' if not bad else f'不一致 {bad}'}")
            return 1 if bad else 0
        return 0
    except Exception as exc:   # noqa: BLE001
        print(f"[FAIL] backtest: {type(exc).__name__}: {exc}")
        return 1
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m daqin.cli", description="daqin 命令行入口")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect", help="采集 raw 数据（失败隔离 + freshness）")
    p.add_argument("--names", nargs="*", default=None, help=f"只跑指定数据集：{' / '.join(COLLECTORS)}")
    p.add_argument("--start", default=None, help="区间起点 YYYY-MM-DD（默认增量）")
    p.add_argument("--end", default=None, help="区间终点 YYYY-MM-DD（默认今天）")
    p.set_defaults(fn=cmd_collect)

    p = sub.add_parser("compute", help="计算 metrics_daily")
    p.add_argument("--start", default=None)
    p.add_argument("--end", default=None)
    p.set_defaults(fn=cmd_compute)

    p = sub.add_parser("signal", help="状态机回放并写入 signal_log")
    p.add_argument("--date", default=None, help="输出指定日期的状态（默认最新）")
    p.set_defaults(fn=cmd_signal)

    p = sub.add_parser("backtest", help="回测（M4）：状态序列 → 净值 + 报告 + 防未来函数抽查")
    p.add_argument("--event", default="2020", help="事件键（见 daqin/backtest/events.py）")
    p.add_argument("--warmup", type=int, default=180, help="指标预热自然日（≥120 交易日）")
    p.add_argument("--probe", nargs="*", default=["2020-03-23", "2020-09-30"],
                   help="防未来函数抽查日期（传空则跳过）")
    p.set_defaults(fn=cmd_backtest)

    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
