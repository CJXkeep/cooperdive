"""采集器注册表：每个数据集一个采集函数（抓取 → 标准化 → 幂等入库）。

数据集命名（series.dataset）：
  cu_shfe          沪铜主力日线（元/吨）
  cu_lme           LME 铜日线（美元/吨）
  cu_comex         COMEX 铜日线（含折算美元/吨 close_usd_ton）
  spot_ccmn        长江现货均价/升贴水 + 稀疏 TC/硫酸（元/吨、美元/干吨）
  spot_basis       上期所期现表：现货价与基差（元/吨）
  inv_lme          LME 铜库存/注册仓单/注销仓单（吨）
  inv_shfe         上期所铜库存（吨）
  stock_000630     铜陵有色日线（前复权）
  stock_000630_raw 铜陵有色日线（不复权，用于 PB）
  stock_600362 / stock_000878 / stock_601899   同行日线（前复权）
  bps_000630       铜陵有色每股净资产（季频）
  usdcny           美元兑人民币
  usd_index        美元指数（best-effort，源在东财）
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Callable

import pandas as pd

from copper import config, db
from copper.sources import ak_api, ccmn


def _start_from(db_name: str, key: str = "close", overlap_days: int = 7) -> str:
    """增量起点：库里最大日期往前多取几天，便于修正。"""
    last = db.last_date(db_name, key)
    if last is None:
        return config.iso(config.history_start())
    d = pd.Timestamp(last).date() - timedelta(days=overlap_days)
    return config.iso(max(d, config.history_start()))


def _collect_series(conn: sqlite3.Connection, dataset: str, df: pd.DataFrame,
                    key_map: dict[str, str]) -> int:
    rows = db.df_to_rows(df, dataset, key_map)
    return db.upsert_series(rows, conn)


# ---------------- 各采集器实现 ----------------

def _c_cu_shfe(conn: sqlite3.Connection) -> int:
    start = _start_from("cu_shfe")
    df = ak_api.shfe_copper_main(start, config.iso(config.today_cn()))
    return _collect_series(conn, "cu_shfe", df, {
        "open": "open", "high": "high", "low": "low", "close": "close",
        "volume": "volume", "open_interest": "open_interest", "settle": "settle",
    })


def _c_cu_lme(conn: sqlite3.Connection) -> int:
    df = ak_api.lme_copper()
    df = df[df["date"] >= config.iso(config.history_start())]
    return _collect_series(conn, "cu_lme", df, {
        "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume",
    })


def _c_cu_comex(conn: sqlite3.Connection) -> int:
    df = ak_api.comex_copper()
    df = df[df["date"] >= config.iso(config.history_start())]
    return _collect_series(conn, "cu_comex", df, {
        "open": "open", "high": "high", "low": "low", "close": "close",
        "close_usd_ton": "close_usd_ton",
    })


def _c_spot_ccmn(conn: sqlite3.Connection) -> int:
    # 首次运行多抓一些（BFS 文章页）；日常增量抓 ~25 篇足够
    max_articles = 150 if db.last_date("spot_ccmn") is None else 25
    df = ccmn.fetch_daily_reports(max_articles=max_articles)
    if df.empty:
        return 0
    df = df[df["date"] >= config.iso(config.history_start())]
    return _collect_series(conn, "spot_ccmn", df, {
        "spot_avg": "spot_avg", "premium": "premium", "tc": "tc", "h2so4": "h2so4",
    })


def _c_spot_basis(conn: sqlite3.Connection) -> int:
    start = _start_from("spot_basis")
    df = ak_api.copper_spot_basis(start, config.iso(config.today_cn()))
    return _collect_series(conn, "spot_basis", df, {
        "spot_price": "spot_price", "basis_main": "basis_main", "basis_near": "basis_near",
    })


def _c_inv_lme(conn: sqlite3.Connection) -> int:
    df = ak_api.lme_inventory()
    df = df[df["date"] >= config.iso(config.history_start())]
    return _collect_series(conn, "inv_lme", df, {
        "lme_stock": "lme_stock", "lme_warrant": "lme_warrant", "lme_cancel": "lme_cancel",
    })


def _c_inv_shfe(conn: sqlite3.Connection) -> int:
    df = ak_api.shfe_inventory()
    df = df[df["date"] >= config.iso(config.history_start())]
    return _collect_series(conn, "inv_shfe", df, {
        "shfe_stock": "shfe_stock", "shfe_delta": "shfe_delta",
    })


def _stock_collector(dataset: str, symbol: str, adjust: str):
    def _c(conn: sqlite3.Connection) -> int:
        start = _start_from(dataset)
        df = ak_api.stock_daily(symbol, start, config.iso(config.today_cn()), adjust=adjust)
        return _collect_series(conn, dataset, df, {
            "open": "open", "high": "high", "low": "low", "close": "close", "volume": "volume",
        })
    return _c


def _c_bps(conn: sqlite3.Connection) -> int:
    df = ak_api.stock_bps(config.STOCK_MAIN)
    return _collect_series(conn, "bps_000630", df, {"bps": "bps"})


def _c_usdcny(conn: sqlite3.Connection) -> int:
    start = _start_from("usdcny")
    df = ak_api.usdcny(start, config.iso(config.today_cn()))
    return _collect_series(conn, "usdcny", df, {"usdcny": "usdcny"})


def _c_usd_index(conn: sqlite3.Connection) -> int:
    df = ak_api.usd_index()
    df = df[df["date"] >= config.iso(config.history_start())]
    return _collect_series(conn, "usd_index", df, {"close": "close"})


@dataclass
class Collector:
    name: str      # dataset 名
    group: str     # 分组（看板新鲜度展示用）
    desc: str      # 人话描述
    fn: Callable[[sqlite3.Connection], int]


COLLECTORS: list[Collector] = [
    Collector("cu_shfe", "铜价", "沪铜主力日线", _c_cu_shfe),
    Collector("cu_lme", "铜价", "LME铜日线", _c_cu_lme),
    Collector("cu_comex", "铜价", "COMEX铜日线", _c_cu_comex),
    Collector("spot_ccmn", "利润", "长江现货/升贴水/TC", _c_spot_ccmn),
    Collector("spot_basis", "利润", "上期所现货与基差", _c_spot_basis),
    Collector("inv_lme", "库存", "LME铜库存", _c_inv_lme),
    Collector("inv_shfe", "库存", "上期所铜库存", _c_inv_shfe),
    Collector("stock_000630", "股票", "铜陵有色日线(前复权)", _stock_collector("stock_000630", config.STOCK_MAIN, "qfq")),
    Collector("stock_000630_raw", "股票", "铜陵有色日线(不复权)", _stock_collector("stock_000630_raw", config.STOCK_MAIN, "")),
    Collector("stock_600362", "股票", "江西铜业日线", _stock_collector("stock_600362", "sh600362", "qfq")),
    Collector("stock_000878", "股票", "云南铜业日线", _stock_collector("stock_000878", "sz000878", "qfq")),
    Collector("stock_601899", "股票", "紫金矿业日线", _stock_collector("stock_601899", "sh601899", "qfq")),
    Collector("bps_000630", "股票", "铜陵有色每股净资产", _c_bps),
    Collector("usdcny", "宏观", "美元兑人民币", _c_usdcny),
    Collector("usd_index", "宏观", "美元指数(best-effort)", _c_usd_index),
]

COLLECTOR_MAP = {c.name: c for c in COLLECTORS}
