"""
AlphaScalp Configuration File.
Contains core exchange safety settings and symbol-specific risk control parameters.
"""

BOT_VERSION = "V6.0"


SAFE_TRADING_CORE = {
    'enableRateLimit': True,
    'rateLimit': 120,
    'timeout': 60000,  # CCXT Bybit V5 60-second timeout for network resilience
    'options': {
        'defaultType': 'linear',
        'adjustForTimeDifference': True
    }
}

RISK_CONTROL_REGIME = {
    'XRP_USDT': {
        'leverage': 5,
        'min_position_size': 3.0,
        'take_profit_pct': 0.012,        # Optimized: 1.2% profit target so profit significantly beats fees
        'stop_loss_pct': 0.008,          # Optimized: 0.8% stop loss (R:R = 1.5 : 1)
        'max_hold_kline': 6,             # Allow up to 6 bars for price to reach higher 1.2% target
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -65.0,      # Stricter CMO threshold for XRP Long bottom catching (-65.0)
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'SOL_USDT': {
        'leverage': 5,
        'min_position_size': 0.1,
        'take_profit_pct': 0.015,
        'stop_loss_pct': 0.010,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -60.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'DOGE_USDT': {
        'leverage': 5,
        'min_position_size': 10.0,
        'take_profit_pct': 0.018,
        'stop_loss_pct': 0.010,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -65.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'SUI_USDT': {
        'leverage': 5,
        'min_position_size': 1.0,
        'take_profit_pct': 0.018,
        'stop_loss_pct': 0.010,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -60.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'PEPE_USDT': {
        'leverage': 5,
        'min_position_size': 10000.0,
        'take_profit_pct': 0.020,
        'stop_loss_pct': 0.012,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -65.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'BTC_USDT': {
        'leverage': 5,
        'min_position_size': 0.001,
        'take_profit_pct': 0.010,
        'stop_loss_pct': 0.006,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -60.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'ETH_USDT': {
        'leverage': 5,
        'min_position_size': 0.01,
        'take_profit_pct': 0.012,
        'stop_loss_pct': 0.008,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -60.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'ADA_USDT': {
        'leverage': 5,
        'min_position_size': 5.0,
        'take_profit_pct': 0.015,
        'stop_loss_pct': 0.010,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -65.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'DEFAULT': {
        'leverage': 5,
        'min_position_size': 1.0,
        'take_profit_pct': 0.015,
        'stop_loss_pct': 0.010,
        'max_hold_kline': 6,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -60.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    }
}

def get_regime_config(symbol: str) -> dict:
    """Safely retrieves risk control parameters for any symbol with default fallback."""
    clean = symbol.replace('/', '_').split(':')[0].upper()
    return RISK_CONTROL_REGIME.get(clean, RISK_CONTROL_REGIME['DEFAULT'])

MARGIN_SHIELD_CONFIG = {
    'emergency_drawdown_pct': 0.70,  # 70% of isolated margin triggers emergency cut
    'max_leverage_limit': 5,
    'max_notional_usd': 50.0,
    'poll_interval_seconds': 1.0
}

# Capital Preservation & Profit Allocation System (1/3 Vault, 2/3 Reinvestment)
PROFIT_ALLOCATION_CONFIG = {
    'vault_ratio': 0.3333,       # 1/3 of net profit stored into safe capital vault
    'reinvest_ratio': 0.6667,    # 2/3 of net profit reinvested/compounded into trading pool
    'daily_loss_limit_pct': 0.03, # 3.0% maximum daily drawdown circuit breaker
    'auto_transfer_to_spot': False # Optional live internal transfer from derivative to spot
}

# Dynamic Early Breakeven Protection
BREAKEVEN_CONFIG = {
    'enabled': True,
    'trigger_ratio': 0.50,       # Lock to breakeven when 50% distance to target 1 is achieved
    'min_profit_pct': 0.0050,    # Or when unrealized profit reaches +0.50%
    'fee_buffer_pct': 0.0012     # +0.12% buffer over entry price to guarantee zero-fee breakeven (covers round-trip taker fees)
}

# Orderbook Microstructure & Liquidity Imbalance Filter
MICROSTRUCTURE_CONFIG = {
    'orderbook_imbalance_check': True,
    'imbalance_threshold': 0.35, # Reject if opposing wall has > 35% depth skew
    'depth_levels': 10
}
