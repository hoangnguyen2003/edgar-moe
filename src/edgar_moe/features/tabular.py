from __future__ import annotations

import numpy as np
import pandas as pd


def safe_divide(numerator: pd.Series, denominator: pd.Series) -> pd.Series:
    clean = denominator.replace(0, np.nan)
    return numerator / clean


def build_fundamental_ratios(frame: pd.DataFrame) -> pd.DataFrame:
    """Create a compact, missing-aware set of comparable XBRL ratios."""
    result = pd.DataFrame(index=frame.index)

    def col(name: str) -> pd.Series:
        if name in frame:
            return pd.to_numeric(frame[name], errors="coerce")
        return pd.Series(np.nan, index=frame.index, dtype=float)

    assets = col("Assets")
    liabilities = col("Liabilities")
    equity = col("StockholdersEquity")
    revenue = col("Revenues")
    net_income = col("NetIncomeLoss")
    operating_income = col("OperatingIncomeLoss")
    cash = col("CashAndCashEquivalentsAtCarryingValue")
    current_assets = col("AssetsCurrent")
    current_liabilities = col("LiabilitiesCurrent")
    operating_cash_flow = col("NetCashProvidedByUsedInOperatingActivities")

    result["debt_to_assets"] = safe_divide(liabilities, assets)
    result["equity_to_assets"] = safe_divide(equity, assets)
    result["return_on_assets"] = safe_divide(net_income, assets)
    result["net_margin"] = safe_divide(net_income, revenue)
    result["operating_margin"] = safe_divide(operating_income, revenue)
    result["cash_to_assets"] = safe_divide(cash, assets)
    result["current_ratio"] = safe_divide(current_assets, current_liabilities)
    result["cash_conversion"] = safe_divide(operating_cash_flow, net_income.abs())
    result = result.replace([np.inf, -np.inf], np.nan).clip(-20, 20)
    for column in list(result.columns):
        result[f"{column}__missing"] = result[column].isna().astype(float)
    return result


def cross_sectional_winsorize(
    frame: pd.DataFrame, columns: list[str], lower: float = 0.01, upper: float = 0.99
) -> pd.DataFrame:
    """Winsorize by date without allowing one date to influence another."""
    result = frame.copy()
    for column in columns:
        for _, indices in result.groupby("event_date").groups.items():
            values = result.loc[indices, column]
            if values.notna().any():
                lo, hi = values.quantile([lower, upper])
                result.loc[indices, column] = values.clip(lo, hi)
    return result
