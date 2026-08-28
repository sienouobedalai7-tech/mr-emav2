"""
MR EMA - Indicateurs techniques

EMA, MACD et ATR utilisent des formules standards universellement reconnues.
Le TDI (Traders Dynamic Index) n'existe pas nativement dans les librairies Python
courantes (ta/pandas-ta le proposent rarement à l'identique) : il est recalculé ici
à partir de sa définition originale (RSI lissé + bandes de Bollinger sur ce RSI).
"""

import pandas as pd
import numpy as np


# ============================================================
# EMA - Exponential Moving Average
# ============================================================
def calculer_ema(close: pd.Series, period: int) -> pd.Series:
    """EMA standard, formule pandas native (span = period)."""
    return close.ewm(span=period, adjust=False).mean()


# ============================================================
# MACD - Moving Average Convergence Divergence
# ============================================================
def calculer_macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    """
    Retourne un DataFrame avec les colonnes: macd_line, signal_line, histogram
    """
    ema_fast = calculer_ema(close, fast)
    ema_slow = calculer_ema(close, slow)
    macd_line = ema_fast - ema_slow
    signal_line = calculer_ema(macd_line, signal)
    histogram = macd_line - signal_line

    return pd.DataFrame({
        "macd_line": macd_line,
        "signal_line": signal_line,
        "histogram": histogram,
    })


# ============================================================
# ATR - Average True Range
# ============================================================
def calculer_atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """
    True Range = max(high-low, |high-close_prec|, |low-close_prec|)
    ATR = moyenne mobile (Wilder) du True Range
    """
    close_prec = close.shift(1)
    tr1 = high - low
    tr2 = (high - close_prec).abs()
    tr3 = (low - close_prec).abs()

    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)

    # Lissage de Wilder (équivalent à un EMA avec alpha = 1/period)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return atr


# ============================================================
# RSI - nécessaire comme brique de base pour le TDI
# ============================================================
def calculer_rsi(close: pd.Series, period: int = 13) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)

    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)  # neutre si division par zéro (pas de mouvement)
    return rsi


# ============================================================
# TDI - Traders Dynamic Index
# ============================================================
def calculer_tdi(close: pd.Series, rsi_period: int = 13, price_line_period: int = 2,
                  signal_period: int = 7, volatility_band_period: int = 34) -> pd.DataFrame:
    """
    Traders Dynamic Index (indicateur créé par Dean Malone), composé de :
      - RSI de base
      - "RSI Price Line" (ligne verte) : SMA courte du RSI -> réactive, sert d'entrée
      - "Trade Signal Line" (ligne rouge) : SMA plus longue du RSI -> ligne de signal
      - Bandes de volatilité : Bollinger Bands appliquées au RSI (bande haute/basse/médiane)

    Signal d'achat classique : RSI Price Line (verte) croise au-dessus de la Trade Signal Line (rouge)
    Signal de vente classique : croisement inverse

    Retourne un DataFrame avec: rsi, price_line, signal_line, bb_upper, bb_lower, bb_mid
    """
    rsi = calculer_rsi(close, rsi_period)

    price_line = rsi.rolling(window=price_line_period).mean()
    signal_line = rsi.rolling(window=signal_period).mean()

    bb_mid = rsi.rolling(window=volatility_band_period).mean()
    bb_std = rsi.rolling(window=volatility_band_period).std()
    bb_upper = bb_mid + (bb_std * 1.6185)  # écart standard du TDI original
    bb_lower = bb_mid - (bb_std * 1.6185)

    return pd.DataFrame({
        "rsi": rsi,
        "price_line": price_line,
        "signal_line": signal_line,
        "bb_upper": bb_upper,
        "bb_lower": bb_lower,
        "bb_mid": bb_mid,
    })


# ============================================================
# Fonction globale : calcule tous les indicateurs sur un DataFrame OHLC
# ============================================================
def calculer_tous_indicateurs(df: pd.DataFrame, ema_fast: int, ema_slow: int,
                                macd_fast: int, macd_slow: int, macd_signal: int,
                                atr_period: int, tdi_rsi_period: int, tdi_price_line: int,
                                tdi_signal_line: int, tdi_bb_period: int) -> pd.DataFrame:
    """
    Ajoute toutes les colonnes d'indicateurs à un DataFrame OHLCV.
    Ne modifie pas le DataFrame original (retourne une copie).
    """
    result = df.copy()

    result["ema_fast"] = calculer_ema(df["Close"], ema_fast)
    result["ema_slow"] = calculer_ema(df["Close"], ema_slow)

    macd_df = calculer_macd(df["Close"], macd_fast, macd_slow, macd_signal)
    result["macd_line"] = macd_df["macd_line"]
    result["macd_signal"] = macd_df["signal_line"]
    result["macd_histogram"] = macd_df["histogram"]

    result["atr"] = calculer_atr(df["High"], df["Low"], df["Close"], atr_period)

    tdi_df = calculer_tdi(df["Close"], tdi_rsi_period, tdi_price_line, tdi_signal_line, tdi_bb_period)
    result["tdi_rsi"] = tdi_df["rsi"]
    result["tdi_price_line"] = tdi_df["price_line"]
    result["tdi_signal_line"] = tdi_df["signal_line"]
    result["tdi_bb_upper"] = tdi_df["bb_upper"]
    result["tdi_bb_lower"] = tdi_df["bb_lower"]

    return result
