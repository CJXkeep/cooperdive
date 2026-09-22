"""回测报告输出（06 §4.6）：markdown 报告 + 净值/状态图。

图内文字用**英文**标签，避免 matplotlib 缺中文字体导致方框（报告正文仍为中文）。
"""

from __future__ import annotations

from pathlib import Path

from daqin.backtest.engine import BacktestConfig, BacktestResult
from daqin.backtest.events import Event

OUTPUT_DIR = Path(__file__).resolve().parents[1] / "output"


def write_report(res: BacktestResult, cfg: BacktestConfig, event: Event,
                 out_dir: Path | None = None, make_plots: bool = True,
                 scenarios: list[tuple[str, dict[str, float]]] | None = None) -> Path:
    """写 `{event}_report.md`（+ 可选两张图），返回报告路径。

    `scenarios`：敏感性场景 `[(标签, summary)]`（06 §4.6 要求含 0.1% 成本与 1.5% 现金版本）。
    """
    out = Path(out_dir) if out_dir else OUTPUT_DIR
    out.mkdir(parents=True, exist_ok=True)
    md_path = out / f"{event.name}_report.md"
    md_path.write_text(render_markdown(res, cfg, event, scenarios=scenarios), encoding="utf-8")
    if make_plots:
        try:
            _plot_nav(res, out / f"{event.name}_nav.png")
            _plot_states(res, out / f"{event.name}_states.png")
        except ImportError:
            pass    # 无 matplotlib 时只产出 markdown
    return md_path


def render_markdown(res: BacktestResult, cfg: BacktestConfig, event: Event,
                    scenarios: list[tuple[str, dict[str, float]]] | None = None) -> str:
    s = res.summary
    d = res.daily
    lines = [
        f"# 回测报告 · {event.name}",
        "",
        f"> 事件：{event.note}",
        f"> 窗口：{cfg.start} ~ {cfg.end}（warm-up {cfg.warmup_days} 天）| 锚点：{event.anchor}",
        f"> 成本：单边 {cfg.cost_bps:.1f} bp | 现金年化：{cfg.cash_yield:.2%} | 初始净值：{cfg.initial:.2f}",
        "",
        "## 1. 汇总",
        "",
        "| 指标 | 策略 | 基准（买入持有） |",
        "|---|---|---|",
        f"| 累计收益 | {s['total_return']:+.2%} | {s['benchmark_return']:+.2%} |",
        f"| 超额收益 | {s['excess_return']:+.2%} | — |",
        f"| 最大回撤 | **{s['max_drawdown']:.2%}** | {s['benchmark_max_drawdown']:.2%} |",
        f"| 年化波动 | {s['annual_vol']:.2%} | — |",
        f"| 夏普（rf=0） | {s['sharpe']:.2f} | — |",
        f"| 调仓次数 / 累计换手 | {int(s['trades'])} / {s['total_turnover']:.2f} | — |",
        f"| 累计成本 | {s['total_cost']:.4%} | — |",
        f"| 交易日数 | {int(s['days'])} | |",
        "",
        "## 2. 状态转换",
        "",
        "| 日期 | 状态 | 仓位 | 换手 | 净值 |",
        "|---|---|---|---|---|",
    ]
    changed = d[d["state"] != d["state"].shift(1)]
    for _, r in changed.iterrows():
        lines.append(
            f"| {r['date']} | {r['state']} | {r['position']:.0%} | {r['turnover']:.0%} | {r['nav']:.4f} |"
        )
    anchor_row = _nearest(d, event.anchor)
    lines += [
        "",
        "## 3. 事件锚点核对（M4 验收）",
        "",
        f"锚点 `{event.anchor}`：当日/最近交易日指标如下（验收标准：S2/S3 出现在锚点后 20 个交易日内）。",
        "",
    ]
    if anchor_row is not None:
        lines.append(f"- 锚点最近交易日 `{anchor_row['date']}`：状态 **{anchor_row['state']}**，仓位 {anchor_row['position']:.0%}")
    first_risk = _first_in(d, event.anchor, {"S2", "S3"})
    if first_risk is not None:
        lines.append(f"- 锚点后首次处于 S2/S3：`{first_risk['date']}`（状态 {first_risk['state']}）")
    else:
        lines.append("- 窗口内未出现 S2/S3 ⚠️")
    sig = res.signals
    if sig is not None and not sig.empty:
        before = sig[(sig["date"] <= event.anchor) & (sig["changed"] == 1)]
        before = before[before["state"].isin({"S2", "S3"})]
        if not before.empty:
            r = before.iloc[-1]
            kind = "**延续**（锚点前已处于风险态，非事件触发）" if r["date"] < event.start else "**新触发**"
            lines.append(
                f"- 锚点前最近一次进入 S2/S3：`{r['date']}`（{r['prev_state']} → {r['state']}，"
                f"触发 {r['triggered_rules'] or '无'}）→ {kind}"
            )
        post = sig[sig["date"] >= event.anchor].head(20)
        if not post.empty:
            lines.append(f"- 锚点后 20 个交易日内出现的状态：{', '.join(sorted(set(post['state'])))}")
    if scenarios:
        lines += [
            "",
            "## 4. 敏感性（06 §4.6：0.1% 单边成本 / 1.5% 现金年化）",
            "",
            "| 场景 | 累计收益 | 最大回撤 | 夏普 | 累计换手 | 累计成本 |",
            "|---|---|---|---|---|---|",
        ]
        for label, sc in scenarios:
            lines.append(
                f"| {label} | {sc['total_return']:+.2%} | {sc['max_drawdown']:.2%} | {sc['sharpe']:.2f}"
                f" | {sc['total_turnover']:.2f} | {sc['total_cost']:.4%} |"
            )
    lines += [
        "",
        "## 5. 口径与已知限制",
        "",
        "- 执行假设：T 日收盘出信号 → **T+1 开盘**调仓；持有至下次调仓；现金按 `cash_yield` 计息。",
        "- 价格：601006 **前复权**价（回测净值为价格收益口径，未含分红现金流）；分红经股息率进入信号层（K5）。",
        "- D 系自动核在 2020 年实际仅 D4（月频，次月 10 日可得）——S2/S5 时间分辨率受限（D-26）。",
        "- 防未来函数：`lookahead_probe` 抽查通过（见测试）；指标层 t-1 对齐见 D-13。",
        "",
    ]
    return "\n".join(lines)


def _nearest(d, date: str):
    sub = d[d["date"] <= date]
    return None if sub.empty else sub.iloc[-1]


def _first_in(d, date: str, states: set[str]):
    sub = d[(d["date"] >= date) & (d["state"].isin(states))]
    return None if sub.empty else sub.iloc[0]


def _plot_nav(res: BacktestResult, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = res.daily
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(d["date"], d["nav"], label="Strategy", linewidth=1.6)
    ax.plot(d["date"], d["benchmark_nav"], label="Buy & Hold", linewidth=1.2, linestyle="--")
    ax.set_title(f"NAV · {res.summary['total_return']:+.1%} vs benchmark {res.summary['benchmark_return']:+.1%}")
    ax.set_ylabel("NAV")
    ax.legend()
    ticks = list(d["date"])[:: max(1, len(d) // 8)]
    ax.set_xticks(ticks)
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_states(res: BacktestResult, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    d = res.daily
    levels = {"S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 2.5, "S5": 1.5}
    y = [levels.get(s, 0) for s in d["state"]]
    fig, ax = plt.subplots(figsize=(10, 4))
    ax.step(d["date"], y, where="post", linewidth=1.6)
    ax.set_yticks([0, 1, 2, 3])
    ax.set_yticklabels(["S0", "S1", "S2", "S3"])
    ax.set_title("State sequence (risk order)")
    ticks = list(d["date"])[:: max(1, len(d) // 8)]
    ax.set_xticks(ticks)
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)
