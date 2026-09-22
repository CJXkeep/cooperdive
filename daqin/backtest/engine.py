"""回测引擎（06 §4.6，M4）：状态序列 → 仓位 → 净值曲线；含**防未来函数可执行抽查**（D-13）。

执行模型（T 日收盘出状态 → **T+1 开盘**调仓）：

1. `run_daily(metrics)` 整段回放得到每个交易日的状态与仓位建议（只用 ≤ T 的数据，D-13）；
2. T+1 **开盘**按目标仓位调仓，换手按单边成本 `cost_bps` 计费；
3. 持有至下次调仓；现金按 `cash_yield` 年化计息（默认 0，报告另出 1.5% 敏感性）；
4. 基准 = 首日开盘买入并持有。

**防未来函数检查（`lookahead_probe`）**：对抽查日 T，把全部 raw 表截断到「≤ T 可得」后灌入**内存库**重算指标，
再与完整库的 T 行逐列比对。若某指标在 T 行用到了 T 之后的数据，截断重算必然不同——
这比"人工审查代码"更可靠（与 `uncovered_columns`、转换矩阵断言同一思路：把约束变成可执行检查）。
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field

import pandas as pd

from daqin.indicators.daily import compute_daily
from daqin.signals import state_machine
from daqin.storage import db
from daqin.thresholds import Thresholds

TRADING_DAYS = 252
PRICE_PAD_DAYS = 30   # 价格区间前多读的天数（区间首日的"决策日"落在区间之前）

# raw 表 → 时间列（防未来函数截断用）
_RAW_KEYS: dict[str, str] = {
    "stock_daily": "date", "macro_daily": "date", "futures_daily": "date", "energy_daily": "date",
    "company_monthly": "month", "dividend_events": "ex_date",
    "industry_weekly": "week_end", "holder_quarterly": "quarter",
}


@dataclass(frozen=True)
class BacktestConfig:
    start: str
    end: str
    warmup_days: int = 180
    cost_bps: float = 0.0      # 单边成本（基点；1 bp = 0.01%）
    cash_yield: float = 0.0    # 现金年化收益
    initial: float = 1.0
    label: str = "backtest"


@dataclass
class BacktestResult:
    daily: pd.DataFrame
    summary: dict[str, float] = field(default_factory=dict)
    signals: pd.DataFrame | None = None      # 完整状态序列（含 warm-up 段，用于「何时进入风险态」核对）


def run_backtest(conn: sqlite3.Connection, cfg: BacktestConfig, thresholds: Thresholds) -> BacktestResult:
    """整段回放 + 净值模拟。调用前请确保 `metrics_daily` 已覆盖 `[start - warmup, end]`。"""
    metrics = load_metrics(conn, cfg)
    if metrics.empty:
        raise ValueError(f"metrics_daily 在 [{cfg.start} - warmup, {cfg.end}] 无数据，请先 compute")
    signals = state_machine.run_daily(metrics, thresholds)
    prices = load_prices(conn, cfg)
    if prices.empty:
        raise ValueError("stock_daily 缺少区间内的开盘价/收盘价（需 daqin_open，M4 新增）")
    daily = _simulate(signals, prices, cfg)
    return BacktestResult(daily=daily, summary=summarize(daily, cfg), signals=signals)


def load_metrics(conn: sqlite3.Connection, cfg: BacktestConfig) -> pd.DataFrame:
    """读指标层（含 warm-up 段），剔除 `asof_*` 文本列。"""
    warm_from = (pd.Timestamp(cfg.start) - pd.Timedelta(days=cfg.warmup_days)).strftime("%Y-%m-%d")
    df = pd.read_sql_query(
        "SELECT * FROM metrics_daily WHERE date >= ? AND date <= ? ORDER BY date",
        conn, params=(warm_from, cfg.end),
    )
    return df.drop(columns=[c for c in df.columns if c.startswith("asof_")])


def load_prices(conn: sqlite3.Connection, cfg: BacktestConfig) -> pd.DataFrame:
    """读执行价（T+1 开盘 / 收盘），区间前多读 `PRICE_PAD_DAYS` 以取得首日的决策日。"""
    pad_from = (pd.Timestamp(cfg.start) - pd.Timedelta(days=PRICE_PAD_DAYS)).strftime("%Y-%m-%d")
    df = pd.read_sql_query(
        "SELECT date, daqin_open, daqin_close FROM stock_daily"
        " WHERE date >= ? AND date <= ? ORDER BY date",
        conn, params=(pad_from, cfg.end),
    )
    df["daqin_open"] = pd.to_numeric(df["daqin_open"], errors="coerce")
    df["daqin_close"] = pd.to_numeric(df["daqin_close"], errors="coerce")
    return df.dropna(subset=["daqin_open", "daqin_close"]).reset_index(drop=True)


def _simulate(signals: pd.DataFrame, prices: pd.DataFrame, cfg: BacktestConfig) -> pd.DataFrame:
    """按「T 日收盘信号 → T+1 开盘调仓」模拟净值。"""
    st = signals.set_index("date")
    px = prices.set_index("date")
    idx = px.index
    cash_daily = (1.0 + cfg.cash_yield) ** (1.0 / TRADING_DAYS) - 1.0

    nav, pos = cfg.initial, 0.0
    bench0: float | None = None
    rows: list[dict] = []

    for pos_i, date in enumerate(idx):
        if date < cfg.start or date > cfg.end:
            continue
        if pos_i == 0:
            continue
        dec_date = idx[pos_i - 1]                 # 决策日（前一交易日收盘）
        if dec_date not in st.index:
            continue
        target = float(st.loc[dec_date, "position_advice"])
        turnover = abs(target - pos)
        cost = turnover * cfg.cost_bps / 10_000.0
        pos = target

        open_px = float(px.loc[date, "daqin_open"])
        close_px = float(px.loc[date, "daqin_close"])
        if bench0 is None:
            bench0 = open_px
        ret = pos * (close_px / open_px - 1.0) + (1.0 - pos) * cash_daily
        nav *= (1.0 + ret) * (1.0 - cost)
        rows.append({
            "date": date,
            "state": str(st.loc[dec_date, "state"]),
            "position": pos,
            "turnover": turnover,
            "cost": cost,
            "nav": nav,
            "benchmark_nav": close_px / bench0,
        })

    out = pd.DataFrame(rows)
    if out.empty:
        raise ValueError("回测区间内没有可模拟的交易日（检查日期范围与数据覆盖）")
    return out


def summarize(daily: pd.DataFrame, cfg: BacktestConfig) -> dict[str, float]:
    """汇总指标：收益、最大回撤、波动、夏普、换手与成本。"""
    nav = daily["nav"]
    bm = daily["benchmark_nav"]
    ret = nav.pct_change().dropna()
    total = float(nav.iloc[-1] / cfg.initial - 1.0)
    bm_total = float(bm.iloc[-1] - 1.0)
    mdd = float((nav / nav.cummax() - 1.0).min())
    bm_mdd = float((bm / bm.cummax() - 1.0).min())
    vol = float(ret.std() * math.sqrt(TRADING_DAYS)) if len(ret) > 1 else 0.0
    sharpe = float(ret.mean() / ret.std() * math.sqrt(TRADING_DAYS)) if len(ret) > 1 and ret.std() else 0.0
    return {
        "total_return": total,
        "benchmark_return": bm_total,
        "excess_return": total - bm_total,
        "max_drawdown": mdd,
        "benchmark_max_drawdown": bm_mdd,
        "annual_vol": vol,
        "sharpe": sharpe,
        "trades": int((daily["turnover"] > 1e-12).sum()),
        "total_turnover": float(daily["turnover"].sum()),
        "total_cost": float(daily["cost"].sum()),
        "days": int(len(daily)),
    }


# ---------------- 防未来函数抽查（D-13） ----------------

def lookahead_probe(conn: sqlite3.Connection, probe_dates: list[str]) -> dict[str, list[str]]:
    """抽查「任一时点只使用 ≤ 该时点可得的数据」。

    做法：把 raw 表截断到「≤ T 可得」灌入内存库 → 重算指标 → 与完整库的 T 行逐列比对。
    返回 `{T: [不一致列]}`；全为空表示通过。
    """
    full = _snapshot(conn)
    result: dict[str, list[str]] = {}
    for t in probe_dates:
        mem = db.connect(":memory:")
        _copy_raw_until(conn, mem, t)
        compute_daily(mem)
        got = _snapshot(mem)
        result[t] = _diff_row(full, got, t)
    return result


def _snapshot(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql_query("SELECT * FROM metrics_daily ORDER BY date", conn)
    return df.set_index("date") if not df.empty else df


def _copy_raw_until(src: sqlite3.Connection, dst: sqlite3.Connection, until: str) -> None:
    """把 raw 表复制到 `dst`，仅保留「≤ until 可得」的行。

    `company_monthly` 以月份为键（`YYYY-MM`），故用 `until[:7]` 比较；
    其余表按日期字符串比较（ISO 格式可直接比大小）。
    """
    for table, key in _RAW_KEYS.items():
        df = pd.read_sql_query(f"SELECT * FROM {table}", src)
        if df.empty:
            continue
        limit = until[:7] if key == "month" else until
        df = df[df[key].astype(str) <= limit]
        if not df.empty:
            db.upsert_df(dst, table, df, key=key)


def _diff_row(full: pd.DataFrame, got: pd.DataFrame, date: str, tol: float = 1e-9) -> list[str]:
    """比较两个快照在 `date` 行的指标差异（浮点容差；双方均为 NaN 视为一致）。

    **跳过 `asof_*`**：它们记录各源的数据截止日，随数据范围变化是正常语义（不是信号输入）。
    """
    if date not in full.index or date not in got.index:
        return ["<missing date>"]
    a, b = full.loc[date], got.loc[date]
    diff: list[str] = []
    for col in full.columns:
        if col.startswith("asof_"):
            continue
        va, vb = a.get(col), b.get(col)
        if pd.isna(va) and pd.isna(vb):
            continue
        if pd.isna(va) != pd.isna(vb):
            diff.append(col)
            continue
        try:
            if abs(float(va) - float(vb)) > tol:
                diff.append(col)
        except (TypeError, ValueError):
            if str(va) != str(vb):
                diff.append(col)
    return diff
