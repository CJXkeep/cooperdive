"""大秦线月度运量采集（全自动，06 M1-3）→ `company_monthly`。

链路（**巨潮 cninfo**，法定披露渠道）：
1. `ak.stock_zh_a_disclosure_report_cninfo` 按年拉公告列表 → 标题含「生产经营数据」的即月度运量简报；
2. 由公告链接解析 `announcementId` + 公告日，构造官方 PDF 地址（`static.cninfo.com.cn/finalpage/{date}/{id}.PDF`）；
3. `pdfplumber` 提取文本 → 正则提取「X 年 Y 月…完成货物运输量 Z 万吨…同比(增长|减少) W%」，入库（`month` = `YYYY-MM`）。

实现偏差（M1 §5）：
- 06 §1.1 原设计为「东财公告 API 定位 + PDF 解析」；实测东财详情 API 在批量抓取下返回 HTML 拦截页（限流），
  且其 `notice_content` 不可稳定获取，故改用巨潮列表 + 官方 PDF（`pdfplumber` 解析）；
- **解析失败必须留痕**：单条失败打印警告（附公告日与标题）；若该轮有公告但全部失败，则抛错让 cli 记录 freshness 红灯，
  不静默丢月。
"""

from __future__ import annotations

import io
import re
import sqlite3
import time
from urllib.parse import parse_qs, urlparse

import pandas as pd

from copper.netutil import call_with_retry, http_get
from daqin import config
from daqin.storage import db
from daqin.thresholds import load_thresholds
from storage import now_str

SYMBOL = "601006"
_MARKET = "沪深京"
_KEYWORD = "生产经营数据"
_SOURCE = "cninfo_pdf"
_THROTTLE_SEC = 0.5            # 请求间隔，避免触发源站限流
LOOKBACK_MONTHS = 3            # 增量回看月数（覆盖公告事后修正）
_PDF_BASE = "http://static.cninfo.com.cn/finalpage"

# 「X 年 Y 月…货物运输量（完成）Z 万吨(…同比(增长|减少|持平) W%)」；[^。] 限制在同一句内，避免跨句误取。
# 兼容三种真实变体（按时间序）：①「完成货物运输量 3779 万吨，同比增长 6.72%」；
# ②「货物运输量完成 3636 万吨，同比增长 7.48%」（2022 年中起语序变更）；
# ③「货物运输量完成 3484 万吨，日均运量…」（2023 年下半年起当月同比被省略，只给累计同比）→ 同比可为空。
_RE_MONTHLY = re.compile(
    r"(\d{4})\s*年\s*(\d{1,2})\s*月.{0,80}?货物运输量(?:完成)?\s*([\d.,]+)\s*万吨"
    r"(?:[^。]{0,40}?同比(增长|增加|减少|下降|下滑|持平)\s*([\d.]*)\s*%?)?"
)
_NEGATIVE_WORDS = ("减", "下降", "下滑")


def parse_monthly(text: str) -> dict | None:
    """从公告文本提取 `month` / `dq_line_volume` / `dq_line_volume_yoy`；不匹配返回 None。"""
    if not text:
        return None
    flat = re.sub(r"\s+", "", text)
    m = _RE_MONTHLY.search(flat)
    if not m:
        return None
    year, month, volume, direction, yoy = m.groups()
    pct: float | None = None
    if direction:
        pct = float(yoy) if yoy else 0.0    # 「同比持平」无数字 → 0
        if any(w in direction for w in _NEGATIVE_WORDS):
            pct = -pct
    return {
        "month": f"{int(year):04d}-{int(month):02d}",
        "dq_line_volume": float(volume.replace(",", "")),
        "dq_line_volume_yoy": pct,
    }


def _yoy_from_history(conn: sqlite3.Connection, month: str, volume: float) -> float | None:
    """当月同比缺失时，用去年同月运量推算（同源同口径）。"""
    prev_month = f"{int(month[:4]) - 1:04d}{month[4:]}"
    row = conn.execute("SELECT dq_line_volume FROM company_monthly WHERE month = ?", (prev_month,)).fetchone()
    if row is None or not row[0]:
        return None
    return (volume - float(row[0])) / float(row[0]) * 100.0


def fetch_notices(year: int) -> list[dict]:
    """拉某年公告列表，筛出运量简报 → `[{title, date, announcement_id}]`。"""
    import akshare as ak

    df = call_with_retry(
        ak.stock_zh_a_disclosure_report_cninfo,
        symbol=SYMBOL, market=_MARKET,
        start_date=f"{year}0101", end_date=f"{year}1231",   # 该接口要求 YYYYMMDD 格式
    )
    if df is None or df.empty:
        return []
    need = ["公告标题", "公告时间", "公告链接"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"akshare(stock_zh_a_disclosure_report_cninfo) 返回缺少列 {missing}，实际列 {list(df.columns)}（接口可能改版）")
    out: list[dict] = []
    for _, r in df.iterrows():
        title = str(r["公告标题"])
        if _KEYWORD not in title:
            continue
        query = parse_qs(urlparse(str(r["公告链接"])).query)
        out.append({
            "title": title,
            "date": str(r["公告时间"])[:10],
            "announcement_id": (query.get("announcementId") or [""])[0],
        })
    return out


def fetch_pdf_text(announcement_id: str, announce_date: str) -> str:
    """下载公告 PDF 并提取纯文本。"""
    import pdfplumber

    url = f"{_PDF_BASE}/{announce_date}/{announcement_id}.PDF"
    resp = http_get(url, timeout=60)
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def _incremental_start(conn: sqlite3.Connection, hist_start: str, months: int = LOOKBACK_MONTHS) -> str:
    """增量起点：库内最新月往前 `months` 个月（覆盖事后修正），库空时取全量起点。"""
    row = conn.execute("SELECT MAX(month) FROM company_monthly").fetchone()
    last = row[0] if row else None
    if not last:
        return hist_start
    total = int(last[:4]) * 12 + (int(last[5:7]) - 1) - months
    return max(f"{total // 12:04d}-{total % 12 + 1:02d}-01", hist_start)


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """抓取并解析月度运量 → upsert `company_monthly`；返回写入行数。

    `start` 缺省为库内最新月往前 3 个月（增量 + 修正），库空时按 `thresholds.data.hist_start` 全量回填。
    """
    start = start or _incremental_start(conn, load_thresholds().data.hist_start)
    end = end or config.iso(config.today_cn())
    rows: list[dict] = []
    failures: list[str] = []
    seen = 0
    for year in range(int(start[:4]), int(end[:4]) + 1):
        for item in fetch_notices(year):
            seen += 1
            label = f"{item['date']} {item['title'][:34]}"
            try:
                text = fetch_pdf_text(item["announcement_id"], item["date"])
            except Exception as exc:   # noqa: BLE001 单篇失败不影响其余月份
                failures.append(f"{label}（下载失败：{type(exc).__name__}）")
                continue
            time.sleep(_THROTTLE_SEC)
            parsed = parse_monthly(text)
            if parsed is None:
                failures.append(f"{label}（解析失败）")
                continue
            if parsed["dq_line_volume_yoy"] is None:
                parsed["dq_line_volume_yoy"] = _yoy_from_history(conn, parsed["month"], parsed["dq_line_volume"])
            # 只限上界：公告发布日跨年时（次年 1 月发布的上年 12 月数据）仍需入库
            if parsed["month"] > end[:7]:
                continue
            rows.append({
                **parsed,
                "source": _SOURCE,
                "art_code": item["announcement_id"],
                "fetched_at": now_str(),
            })
    if failures:
        for f in failures:
            print(f"[WARN] 运量公告处理失败（需人工核对）：{f}")
    if seen and not rows and failures:
        raise ValueError(f"运量公告 {seen} 篇全部失败（下载或解析），疑似源站改版")
    if not rows:
        return 0
    return db.upsert_rows(conn, "company_monthly", rows, key="month")
