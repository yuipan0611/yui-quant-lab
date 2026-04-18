"""測試共用：最小化重複的 decision / webhook payload 工廠。"""

from __future__ import annotations

from typing import Any


def trade_input_long(**overrides: Any) -> dict[str, Any]:
    """長突破基準欄位（symbol/signal/price/breakout_level/delta_strength）。"""
    base: dict[str, Any] = {
        "symbol": "MNQ",
        "signal": "long_breakout",
        "price": 20150.0,
        "breakout_level": 20140.0,
        "delta_strength": 0.95,
    }
    base.update(overrides)
    return base


def trade_input_short(**overrides: Any) -> dict[str, Any]:
    """空突破基準欄位。"""
    base: dict[str, Any] = {
        "symbol": "MNQ",
        "signal": "short_breakout",
        "price": 20150.0,
        "breakout_level": 20160.0,
        "delta_strength": 0.95,
    }
    base.update(overrides)
    return base


def nq_eod_long_room(
    *,
    resistance_above_price: float | None,
    support_below_price: float = 20000.0,
    bias: str = "bullish",
) -> dict[str, Any]:
    """long_breakout 用：可指定上方壓力（None 表示無 > price 的 level）。"""
    levels: dict[str, float] = {"s1": support_below_price}
    if resistance_above_price is not None:
        levels["r1"] = float(resistance_above_price)
    return {"levels": levels, "bias": bias}


def nq_eod_short_room(
    *,
    support_below_price: float | None,
    resistance_above_price: float = 20300.0,
    bias: str = "bearish",
) -> dict[str, Any]:
    """short_breakout 用：可指定下方支撐（None 表示無 < price 的 level）。"""
    levels: dict[str, float] = {"r1": resistance_above_price}
    if support_below_price is not None:
        levels["s1"] = float(support_below_price)
    return {"levels": levels, "bias": bias}


def qqq_regime(regime: str) -> dict[str, Any]:
    return {"regime": regime}
