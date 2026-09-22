"""定期报告运量采集（M5 / **D-30**）→ `company_periodic`。

背景：月度「生产经营数据简报」自 2014 起才有；此前 D4（运量同比）在回测中不可用。
实测发现**半年度报告与年度报告均披露大秦线累计运量**（例：2008 半年报「大秦线完成货物运输量
17,096.4 万吨，较上年同期增加 1,417.7 万吨，增幅 11.1%」），可回溯到 2006 年上市之初。
故新增本采集器，作为 2014 前的 **D4 补充口径**（半年/年频，与月频 `dq_vol_yoy` 并存）。

链路与 M1-3 相同：巨潮列表定位「年度报告 / 半年度报告」→ 官方 PDF → `pdfplumber` → 宽松正则。
措辞实测有三种以上（句式 / 「全年…」/ 表格「货物运输量 40,588 37,866」），故解析策略为：
1. 优先匹配「大秦线…货物运输量…万吨…较上年同期…增幅 X%」句式；
2. 退化为「货物运输量」后取前两个 4 位以上数字（本期、上年同期）→ 自算同比；
3. 仍失败则留痕（打印公告标识），不静默跳过。
"""

from __future__ import annotations

import io
import re
import sqlite3
import time

import pandas as pd

from copper.netutil import call_with_retry, http_get
from daqin import config
from daqin.storage import db
from daqin.thresholds import load_thresholds
from storage import now_str

SYMBOL = "601006"
_MARKET = "沪深京"
_SOURCE = "cninfo_periodic"
# **只取半年报**：实测年报 PDF 中「货物运输量」会在多个口径/表格中重复出现（如「周转量」「累计」），
# 解析出的累计值明显失真（2011-2013 年报得到 74,924~76,330 万吨，而大秦线真实年运量约 4.4 亿吨）；
# 半年报的措辞稳定（「大秦线完成货物运输量 X 万吨，同比增运 Y 万吨，增幅 Z%」），解析可靠。
# 该收窄意味着 D-30 口径为**半年频**，且需在文档中如实标注年报不可用。
_RE_TITLE = re.compile(r"^\d{4}年半年度报告")
_EXCLUDE_TITLE = ("摘要", "持续督导", "更正", "英文", "审计")
_THROTTLE_SEC = 0.5
_PDF_BASE = "http://static.cninfo.com.cn/finalpage"

# 数字模式：千分位（40,588）或不带分隔的 4 位以上整数（16000）——不能写成 [\d,]{4,}
# 否则拍平文本后相邻两个数会被连成一个（"40,58837,866"）。
_NUM = r"(\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d{4,}(?:\.\d+)?)"
# 方向词：实测措辞含「同比增运 / 较上年同期增加 / 同比减少」等
_DIRECTION = r"(?:较上年同期|同比)?\s*(?:增运|减运|多运|少运|增加|减少|增长|下降|上升)"
_NEG_DIR = ("减运", "少运", "减少", "下降")
# 句式型：大秦线…完成货物运输量 X 万吨…(同比增运|较上年同期增加) Y 万吨…增幅 Z%
_RE_SENTENCE = re.compile(
    rf"大秦线[^。]{{0,40}}?货物运输量\s*{_NUM}\s*万吨[^。]{{0,20}}?"
    rf"{_DIRECTION}\s*{_NUM}\s*万吨[^。]{{0,20}}?([\d.]+)\s*%"
)
# 表格/退化型：货物运输量 后跟 1~2 个数字（本期、上年同期）
_RE_NUMBERS = re.compile(rf"货物运输量[^\d]{{0,20}}{_NUM}(?:[^\d]{{0,20}}{_NUM})?")
_YOY_NEAR = re.compile(r"同比[^\d]{0,6}([\d.]+)\s*%|较上年同期[^\d]{0,6}([\d.]+)\s*%|增(?:幅|长)\s*([\d.]+)\s*%")
_RATIO_MIN, _RATIO_MAX = 0.5, 2.0   # 「本期/上年」比值合理区间：超出则说明取到的不是同口径数字


def parse_periodic(text: str) -> dict | None:
    """从定期报告文本提取 `dq_vol_cum` 与 `dq_vol_cum_yoy`；失败返回 None。"""
    if not text:
        return None
    flat = re.sub(r"\s+", "", text)
    sent = _RE_SENTENCE.search(flat)
    if sent:
        cum = float(sent.group(1).replace(",", ""))
        pct = float(sent.group(3))
        seg = flat[sent.start():sent.end()]
        if any(w in seg for w in _NEG_DIR):
            pct = -pct
        return {"dq_vol_cum": cum, "dq_vol_cum_yoy": pct}
    nums = _RE_NUMBERS.search(flat)
    if not nums:
        return None
    cum = float(nums.group(1).replace(",", ""))
    prev = nums.group(2)
    if prev:
        prev_v = float(prev.replace(",", ""))
        # 合理性校验：只有两数同口径（比值接近 1）时才自算同比。
        # 否则第二个数可能是「同比增量」（例：17,096.4 与 2,584.7），自算会得出 561% 这类荒谬值。
        if prev_v > 0 and _RATIO_MIN <= cum / prev_v <= _RATIO_MAX:
            return {"dq_vol_cum": cum, "dq_vol_cum_yoy": (cum - prev_v) / prev_v * 100.0}
    near = _YOY_NEAR.search(flat)
    if near:
        pct = next(g for g in near.groups() if g)
        window = flat[max(0, near.start() - 8):near.end()]
        return {"dq_vol_cum": cum, "dq_vol_cum_yoy": float(pct) * (-1 if any(w in window for w in _NEG_DIR) else 1)}
    return None


def fetch_reports(start_year: int, end_year: int) -> list[dict]:
    """按年拉取定期报告列表 → `[{title, date, announcement_id, period}]`。"""
    import akshare as ak

    out: list[dict] = []
    for year in range(start_year, end_year + 1):
        df = call_with_retry(
            ak.stock_zh_a_disclosure_report_cninfo,
            symbol=SYMBOL, market=_MARKET,
            start_date=f"{year}0101", end_date=f"{year}1231",
        )
        if df is None or df.empty:
            continue
        for _, r in df.iterrows():
            title = str(r["公告标题"]).strip()
            if _RE_TITLE.match(title) is None or any(x in title for x in _EXCLUDE_TITLE):
                continue
            from urllib.parse import parse_qs, urlparse

            q = parse_qs(urlparse(str(r["公告链接"])).query)
            out.append({
                "title": title,
                "date": str(r["公告时间"])[:10],
                "announcement_id": (q.get("announcementId") or [""])[0],
                "period": _period_of(title),
            })
    return out


def _period_of(title: str) -> str:
    """报告期标题 → 截止日（`2013年年度报告` → `2013-12-31`；`2013年半年度报告` → `2013-06-30`）。"""
    m = re.search(r"(\d{4})年", title)
    year = m.group(1) if m else ""
    return f"{year}-06-30" if "半年度" in title else f"{year}-12-31"


def fetch_pdf_text(announcement_id: str, announce_date: str) -> str:
    """下载公告 PDF 并提取纯文本。"""
    import pdfplumber

    url = f"{_PDF_BASE}/{announce_date}/{announcement_id}.PDF"
    resp = http_get(url, timeout=60)
    resp.raise_for_status()
    with pdfplumber.open(io.BytesIO(resp.content)) as pdf:
        return "\n".join((page.extract_text() or "") for page in pdf.pages)


def collect(conn: sqlite3.Connection, start: str | None = None, end: str | None = None) -> int:
    """采集定期报告运量 → upsert `company_periodic`；返回写入行数。"""
    start = start or load_thresholds().data.hist_start
    end = end or config.iso(config.today_cn())
    # 定期报告口径只用于补 2014 前（月度简报自 2014 起），避免重复抓取
    rows: list[dict] = []
    failures: list[str] = []
    for item in fetch_reports(int(start[:4]), int(end[:4])):
        if not item["period"] or item["period"] > "2013-12-31":
            continue
        label = f"{item['date']} {item['title'][:30]}"
        try:
            text = fetch_pdf_text(item["announcement_id"], item["date"])
        except Exception as exc:   # noqa: BLE001 单篇失败不影响其余
            failures.append(f"{label}（下载失败：{type(exc).__name__}）")
            continue
        time.sleep(_THROTTLE_SEC)
        parsed = parse_periodic(text)
        if parsed is None:
            failures.append(f"{label}（解析失败）")
            continue
        rows.append({
            "period": item["period"],
            "release_date": item["date"],
            **parsed,
            "source": _SOURCE,
            "announcement_id": item["announcement_id"],
            "fetched_at": now_str(),
        })
    if failures:
        for f in failures:
            print(f"[WARN] 定期报告运量处理失败（需人工核对）：{f}")
    if not rows:
        return 0
    return db.upsert_rows(conn, "company_periodic", rows, key="period")
