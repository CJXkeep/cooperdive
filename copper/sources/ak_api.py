"""akshare 行情类数据源的标准化封装。

每个函数返回 tidy DataFrame：必有 date 列（YYYY-MM-DD 字符串），其余为数值列。
接口失败抛异常，由 collectors 层统一捕获并记入 freshness。
"""

from __future__ import annotations

import pandas as pd

from copper import config
from copper.netutil import call_with_retry

TON_PER_LB = 1.0 / 2204.62  # 磅 -> 吨


# ---------------- 期货 ----------------
def shfe_copper_main(start: str, end: str) -> pd.DataFrame:
    """沪铜主力连续日线（新浪）。date, open, high, low, close, volume, open_interest。"""
    df = call_with_retry(
        __import__("akshare").futures_main_sina,
        symbol="CU0", start_date=start.replace("-", ""), end_date=end.replace("-", ""),
    )
    df = df.rename(
        columns={
            "日期": "date", "开盘价": "open", "最高价": "high", "最低价": "low",
            "收盘价": "close", "成交量": "volume", "持仓量": "open_interest",
            "动态结算价": "settle",
        }
    )
    df["date"] = df["date"].astype(str)
    return df


def lme_copper() -> pd.DataFrame:
    """LME 铜日线，美元/吨（新浪外盘 CAD）。"""
    import akshare as ak

    df = call_with_retry(ak.futures_foreign_hist, symbol="CAD")
    df["date"] = df["date"].astype(str)
    return df[["date", "open", "high", "low", "close", "volume"]]


def comex_copper() -> pd.DataFrame:
    """COMEX 铜日线（新浪外盘 HG，美分/磅），close_usd_ton = close * 2204.62 / 100 折算美元/吨。"""
    import akshare as ak

    df = call_with_retry(ak.futures_foreign_hist, symbol="HG")
    df["date"] = df["date"].astype(str)
    df["close_usd_ton"] = df["close"] * 2204.62 / 100.0
    return df[["date", "open", "high", "low", "close", "close_usd_ton"]]


# ---------------- 股票 ----------------
def stock_daily(symbol: str, start: str, end: str, adjust: str = "qfq") -> pd.DataFrame:
    """A股日线（新浪）。adjust: 'qfq' 前复权 / '' 不复权。"""
    import akshare as ak

    df = call_with_retry(
        ak.stock_zh_a_daily,
        symbol=symbol,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        adjust=adjust,
    )
    df["date"] = df["date"].astype(str)
    return df[["date", "open", "high", "low", "close", "volume"]]


def stock_bps(symbol: str) -> pd.DataFrame:
    """每股净资产（新浪财务指标，季度）。返回 date(报告期), bps。"""
    import akshare as ak

    df = call_with_retry(
        ak.stock_financial_analysis_indicator,
        symbol=symbol.replace("sz", "").replace("sh", ""),
        start_year=str(config.today_cn().year - config.HISTORY_YEARS),
    )
    df = df.rename(columns={"日期": "date", "每股净资产_调整前(元)": "bps"})
    df["date"] = df["date"].astype(str)
    df = df[["date", "bps"]].dropna()
    return df


# ---------------- 宏观 ----------------
def usdcny(start: str, end: str) -> pd.DataFrame:
    """美元兑人民币中间价（中行折算价，接口返回值为每 100 美元价，需 /100）。"""
    import akshare as ak

    df = call_with_retry(
        ak.currency_boc_sina,
        symbol="美元",
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
    )
    df = df.rename(columns={"日期": "date", "中行折算价": "usdcny"})
    df["date"] = df["date"].astype(str)
    df["usdcny"] = pd.to_numeric(df["usdcny"], errors="coerce") / 100.0
    return df[["date", "usdcny"]].dropna()


def usd_index() -> pd.DataFrame:
    """美元指数日线（东财）。本机代理环境下可能失败，服务器上通常可用。"""
    import akshare as ak

    df = call_with_retry(ak.index_global_hist_em, symbol="美元指数", tries=2)
    df = df.rename(columns={"日期": "date", "收盘": "close"})
    df["date"] = df["date"].astype(str)
    return df[["date", "close"]]


# ---------------- 库存 ----------------
def lme_inventory() -> pd.DataFrame:
    """LME 各金属库存（东财数据中心）。提取铜三列，单位：吨。"""
    import akshare as ak

    df = call_with_retry(ak.macro_euro_lme_stock, tries=2)
    df = df.rename(
        columns={
            "日期": "date",
            "铜-库存": "lme_stock",
            "铜-注册仓单": "lme_warrant",
            "铜-注销仓单": "lme_cancel",
        }
    )
    df["date"] = df["date"].astype(str)
    return df[["date", "lme_stock", "lme_warrant", "lme_cancel"]]


def shfe_inventory() -> pd.DataFrame:
    """上期所铜库存（东财，周频口径）。单位：吨。"""
    import akshare as ak

    df = call_with_retry(ak.futures_inventory_em, symbol="沪铜", tries=2)
    df = df.rename(columns={"日期": "date", "库存": "shfe_stock", "增减": "shfe_delta"})
    df["date"] = df["date"].astype(str)
    return df[["date", "shfe_stock", "shfe_delta"]]


# ---------------- 现货与基差 ----------------
def copper_spot_basis(start: str, end: str) -> pd.DataFrame:
    """上期所铜期现表（99期货）：现货价与近月/主力基差。单位：元/吨。"""
    import akshare as ak

    df = call_with_retry(
        ak.futures_spot_price_daily,
        start_day=start.replace("-", ""),
        end_day=end.replace("-", ""),
        vars_list=["CU"],
    )
    df = df.rename(
        columns={
            "date": "date",
            "spot_price": "spot_price",
            "dom_basis": "basis_main",
            "near_basis": "basis_near",
        }
    )
    df["date"] = df["date"].astype(str)
    return df[["date", "spot_price", "basis_main", "basis_near"]]
