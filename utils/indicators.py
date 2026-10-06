import numpy as np
import pandas as pd

def calculate_cmo(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """
    Chande Momentum Oscillator (CMO).
    Calculates pure math momentum without the lagging smoothing artifacts of RSI.
    Formula: CMO = 100 * (Sum(Gains) - Sum(Losses)) / (Sum(Gains) + Sum(Losses))
    """
    close = df['close']
    diff = close.diff()
    gain = diff.clip(lower=0)
    loss = (-diff).clip(lower=0)
    
    sum_gain = gain.rolling(window=period).sum()
    sum_loss = loss.rolling(window=period).sum()
    
    cmo = 100 * (sum_gain - sum_loss) / (sum_gain + sum_loss + 1e-9)
    return cmo

def calculate_vwap_bands(df: pd.DataFrame, atr_period: int = 14, multiplier: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    """
    Calculate Volume-Weighted Average Price acting as the dynamic baseline,
    anchored with ATR volatility bands.
    """
    high = df['high']
    low = df['low']
    close = df['close']
    volume = df['volume']
    
    tp = (high + low + close) / 3.0
    vwap = (tp * volume).cumsum() / (volume.cumsum() + 1e-9)
    
    # Calculate ATR
    close_prev = close.shift(1)
    tr1 = high - low
    tr2 = (high - close_prev).abs()
    tr3 = (low - close_prev).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = tr.rolling(window=atr_period).mean()
    
    vwap_upper = vwap + multiplier * atr
    vwap_lower = vwap - multiplier * atr
    
    return vwap, vwap_upper, vwap_lower

def calculate_tii(df: pd.DataFrame, period: int = 30) -> pd.Series:
    """
    Trend Intensity Index (TII) to gauge market strength.
    Measures positive/negative price deviation from SMA.
    """
    close = df['close']
    sma = close.rolling(window=period).mean()
    
    dev = close - sma
    pos_dev = dev.clip(lower=0)
    neg_dev = (-dev).clip(lower=0)
    
    sum_pos_dev = pos_dev.rolling(window=period).sum()
    sum_neg_dev = neg_dev.rolling(window=period).sum()
    
    tii = 100 * sum_pos_dev / (sum_pos_dev + sum_neg_dev + 1e-9)
    return tii

def classify_market_regime(df: pd.DataFrame) -> pd.Series:
    """
    Returns "IMPULSE_BULL" (TII > 60, volume expanding), 
    "IMPULSE_BEAR" (TII < 40, volume expanding), 
    or "MEAN_REVERSION" (TII between 40-60 or volume not expanding).
    """
    tii = calculate_tii(df)
    vol_sma = df['volume'].rolling(window=14).mean()
    vol_expanding = df['volume'] > vol_sma
    
    regimes = []
    for t, ve in zip(tii, vol_expanding):
        if pd.isna(t) or pd.isna(ve):
            regimes.append("MEAN_REVERSION")
        elif t > 60 and ve:
            regimes.append("IMPULSE_BULL")
        elif t < 40 and ve:
            regimes.append("IMPULSE_BEAR")
        else:
            regimes.append("MEAN_REVERSION")
            
    return pd.Series(regimes, index=df.index)

def calculate_ema(df: pd.DataFrame, period: int = 200) -> pd.Series:
    """
    Exponential Moving Average (EMA).
    Calculates moving average with weight exponentially decreasing for older data points.
    Used as an overall trend filter (e.g. EMA 200 for MEAN_REVERSION signals).
    """
    return df['close'].ewm(span=period, adjust=False).mean()

