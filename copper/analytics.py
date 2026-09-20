"""分析层：冶炼利润代理指数、相对强弱、PB 及分位。

口径提醒：利润指数是"代理"——用 TC + 硫酸副产粗拟冶炼利润趋势，不求绝对金额精确。
"""

from __future__ import annotations

import sqlite3

import numpy as np
import pandas as pd

from copper import config, db


def _s(conn, dataset: str, key: str) -> pd.Series:
    return db.read_series(dataset, key, conn=conn)


def profit_index(conn: sqlite3.Connection) -> pd.DataFrame:
    """冶炼利润代理指数（起点=100）。

    raw = TC(美元/干吨) × (1/入炉品位) × 汇率 + 硫酸价 × 吨铜副产酸量
    TC/硫酸为新闻稿口径的稀疏观测，按上限前向填充；硫酸缺失时指数仅含加工费。
    """
    tc_obs = _s(conn, "spot_ccmn", "tc")
    h2so4 = _s(conn, "spot_ccmn", "h2so4")
    fx = _s(conn, "usdcny", "usdcny")

    if tc_obs.empty:
        return pd.DataFrame(columns=["index", "tc", "tc_observed", "h2so4", "fx"])

    all_dates = pd.date_range(tc_obs.index.min(), tc_obs.index.max(), freq="D")
    tc_obs = tc_obs[~tc_obs.index.duplicated()]
    h2so4 = h2so4[~h2so4.index.duplicated()]
    fx = fx[~fx.index.duplicated()]

    tc_ffill = tc_obs.reindex(all_dates).ffill(limit=config.TC_FFILL_LIMIT_DAYS)
    h2so4_ffill = h2so4.reindex(all_dates).ffill(limit=config.TC_FFILL_LIMIT_DAYS)
    fx_daily = fx.reindex(all_dates).ffill(limit=10)

    df = pd.DataFrame(
        {
            "tc": tc_ffill,
            "tc_observed": tc_obs.reindex(all_dates),
            "h2so4": h2so4_ffill,
            "fx": fx_daily,
        }
    ).dropna(subset=["tc", "fx"])

    df["tc_rmb"] = df["tc"] * config.TC_PER_TON_CU * df["fx"]
    has_acid = df["h2so4"].notna().any()
    if has_acid:
        df["acid_rmb"] = df["h2so4"].fillna(0) * config.H2SO4_PER_TON_CU
        raw = df["tc_rmb"] + df["acid_rmb"]
    else:
        raw = df["tc_rmb"]
    df["index"] = raw / raw.iloc[0] * 100.0
    df["with_acid"] = has_acid
    return df


def relative_strength(conn: sqlite3.Connection) -> pd.DataFrame:
    """铜陵有色（前复权）相对沪铜主力的比值，首个共同交易日=100。"""
    stock = _s(conn, "stock_000630", "close")
    cu = _s(conn, "cu_shfe", "close")
    df = pd.concat([stock, cu], axis=1, join="inner").dropna()
    if df.empty:
        return pd.DataFrame(columns=["rs"])
    rs = df.iloc[:, 0] / df.iloc[:, 1]
    rs = rs / rs.iloc[0] * 100.0
    return pd.DataFrame({"rs": rs})


def pb_series(conn: sqlite3.Connection) -> pd.DataFrame:
    """PB = 不复权收盘价 / 每股净资产（季报前向填充），及自窗口起点以来的历史分位。"""
    close = _s(conn, "stock_000630_raw", "close")
    bps = _s(conn, "bps_000630", "bps")
    if close.empty or bps.empty:
        return pd.DataFrame(columns=["close", "bps", "pb", "pctl"])
    bps_daily = bps.reindex(pd.date_range(bps.index.min(), close.index.max(), freq="D")).ffill()
    bps_daily.index = bps_daily.index.normalize()
    df = pd.DataFrame({"close": close}).join(pd.DataFrame({"bps": bps_daily}), how="left").ffill()
    df = df.dropna()
    df["pb"] = df["close"] / df["bps"]
    df["pctl"] = df["pb"].expanding(min_periods=60).apply(
        lambda arr: float(np.mean(arr <= arr[-1])) * 100, raw=True
    )
    return df


def peers_normalized(conn: sqlite3.Connection) -> pd.DataFrame:
    """四只股票前复权收盘归一化（首个共同交易日=100）。"""
    codes = {
        config.STOCK_MAIN_NAME: ("stock_000630", "close"),
        "江西铜业": ("stock_600362", "close"),
        "云南铜业": ("stock_000878", "close"),
        "紫金矿业": ("stock_601899", "close"),
    }
    cols = {}
    for name, (ds, key) in codes.items():
        s = _s(conn, ds, key)
        if not s.empty:
            cols[name] = s
    df = pd.DataFrame(cols).dropna(how="all").dropna()
    if df.empty:
        return df
    return df / df.iloc[0] * 100.0


def copper_markets_usd(conn: sqlite3.Connection) -> pd.DataFrame:
    """三市场铜价统一折算美元/吨：沪铜÷汇率、LME 原值、COMEX 折算列。"""
    shfe = _s(conn, "cu_shfe", "close")
    lme = _s(conn, "cu_lme", "close")
    comex = _s(conn, "cu_comex", "close_usd_ton")
    fx = _s(conn, "usdcny", "usdcny")
    fx_daily = fx.reindex(pd.date_range(fx.index.min(), max(shfe.index.max(), fx.index.max()), freq="D")).ffill()
    shfe_usd = shfe / fx_daily.reindex(shfe.index)
    df = pd.DataFrame({"沪铜(折美元)": shfe_usd, "LME": lme, "COMEX": comex}).dropna(how="all")
    return df
