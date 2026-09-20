"""长江有色网（ccmn）爬虫：《铜日报》文章抓取，获取长江现货均价、升贴水，顺带捕捉 TC 与硫酸价。

策略（2026-09 实测）：
- 频道列表页是陈年静态缓存，不可用；但 copper.ccmn.cn 首页有最新文章链接。
- 每篇文章页含 ~12 篇相关文章链接（同月为主），从最新文章 BFS 即可覆盖近期大部分交易日。
- 文章 meta description 自带结构化数字，例如：
  "长江现货1#铜均价110290元/吨、较前交易日涨1610元，铜升水走阔至810元/吨；…"
- TC（加工费）与硫酸价仅在文章提及具体数字时才有值，属稀疏观测，分析层做前向填充。
- 已抓取过的文章 URL 记入 meta 表，避免重复抓。
"""

from __future__ import annotations

import json
import re

import pandas as pd

from copper import db
from copper.netutil import fetch_text, http_get

HOME = "https://copper.ccmn.cn/"
_META_SEEN_KEY = "ccmn_seen_urls"

_RE_ARTICLE = re.compile(r'href="((?:https?:)?//copper\.ccmn\.cn/news/ZX003/(\d{6})/[0-9a-f]+\.html)"')
_RE_ANY_ARTICLE = re.compile(r'href="(/news/ZX003/(\d{6})/[0-9a-f]+\.html)"')
_RE_DATE_PUBLISHED = re.compile(r'"datePublished":\s*"(20\d{2}-\d{2}-\d{2})"')
_RE_JSONLD_DESC = re.compile(r'"description":\s*"([^"]+)"')
_RE_META_DESC = re.compile(r'<meta name="description" content="([^"]+)"')
_RE_DATE = re.compile(r"(20\d{2})-(\d{2})-(\d{2})")
_RE_SPOT = re.compile(r"长江现货1#铜均价\s*([\d,]+)\s*元/吨")
_RE_PREMIUM = re.compile(r"升水[^，。；\d]{0,10}?([\d,]+)\s*元/吨")
_RE_DISCOUNT = re.compile(r"贴水[^，。；\d]{0,10}?([\d,]+)\s*元/吨")
_RE_TC = re.compile(r"加工费[^。]{0,40}?([\d.]+)\s*美元")
_RE_H2SO4 = re.compile(r"硫酸[^。]{0,25}?([\d,]+)\s*元/吨")

_COLUMNS = ["date", "spot_avg", "premium", "tc", "h2so4"]


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _parse_summary(text: str) -> dict[str, float | None]:
    out: dict[str, float | None] = {"spot_avg": None, "premium": None, "tc": None, "h2so4": None}
    if m := _RE_SPOT.search(text):
        out["spot_avg"] = _num(m.group(1))
    if m := _RE_PREMIUM.search(text):
        out["premium"] = _num(m.group(1))
    elif m := _RE_DISCOUNT.search(text):
        out["premium"] = -_num(m.group(1))
    if m := _RE_TC.search(text):
        out["tc"] = float(m.group(1))
    if m := _RE_H2SO4.search(text):
        out["h2so4"] = _num(m.group(1))
    return out


def _article_date(html: str, yyyymm: str) -> str | None:
    """优先取 JSON-LD 的 datePublished；否则用与 URL 年月一致的页面日期（排除图片路径干扰）。"""
    if m := _RE_DATE_PUBLISHED.search(html):
        return m.group(1)
    for m in _RE_DATE.finditer(html):
        if f"{m.group(1)}{m.group(2)}" == yyyymm:
            return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    return None


def _summary_text(html: str) -> str:
    """摘要优先取 JSON-LD description，退回 meta description。"""
    if m := _RE_JSONLD_DESC.search(html):
        return m.group(1)
    if m := _RE_META_DESC.search(html):
        return m.group(1)
    return ""


def _normalise(url: str) -> str:
    return "https:" + url if url.startswith("//") else url


def fetch_daily_reports(max_articles: int = 120) -> pd.DataFrame:
    """BFS 抓取《铜日报》文章并解析。返回 DataFrame: date, spot_avg, premium, tc, h2so4。"""
    seen: set[str] = set(json.loads(db.meta_get(_META_SEEN_KEY) or "[]"))
    queue: list[str] = []

    # 种子：首页上的最新文章
    try:
        home = http_get(HOME, timeout=20).text
        for m in _RE_ARTICLE.finditer(home):
            u = _normalise(m.group(1))
            if u not in seen and u not in queue:
                queue.append(u)
    except Exception:
        pass

    rows: dict[str, dict[str, float | None]] = {}
    fetched = 0
    while queue and fetched < max_articles:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            html = fetch_text(url)
        except Exception:
            continue
        fetched += 1
        # 收集页面里的相关文章链接（含相对路径形式）
        for pat in (_RE_ARTICLE, _RE_ANY_ARTICLE):
            for m in pat.finditer(html):
                u = _normalise(m.group(1))
                if u not in seen and u not in queue:
                    queue.append(u)
        yyyymm = re.search(r"/news/ZX003/(\d{6})/", url).group(1)  # noqa: S101
        d = _article_date(html, yyyymm)
        if not d:
            continue
        parsed = _parse_summary(_summary_text(html))
        if all(v is None for v in parsed.values()):
            continue
        valid = sum(1 for v in parsed.values() if v is not None)
        old = rows.get(d)
        if old is None or valid > sum(1 for v in old.values() if v is not None):
            rows[d] = parsed

    db.meta_set(_META_SEEN_KEY, json.dumps(sorted(seen)[-8000:]))
    return pd.DataFrame([{"date": d, **vals} for d, vals in sorted(rows.items())], columns=["date"] + _COLUMNS[1:])
