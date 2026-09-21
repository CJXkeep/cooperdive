"""daqin CLI：`collect` / `compute` / `signal`（Gate 0 验收入口）。

职责边界（D-21）：**本层是唯一落库入口**——采集编排、freshness、`signal_log` 均在此写入；
信号层（`signals/`）与对齐层（`indicators/align.py`）保持纯函数，回测与实盘共用。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import pandas as pd

from daqin.collectors import akshare_stock, fred
from daqin.indicators.daily import compute_daily
from daqin.signals.state_machine import run_daily
from daqin.storage import db
from daqin.thresholds import load_thresholds
from storage import set_freshness

# 采集器注册表：name → collect(conn, start=None, end=None) -> 写入行数
COLLECTORS = {
    "stock_daily": akshare_stock.collect,
    "macro_daily": fred.collect,
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

    args = parser.parse_args(argv)
    return int(args.fn(args))


if __name__ == "__main__":
    sys.exit(main())
