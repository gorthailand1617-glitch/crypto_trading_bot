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
        'take_profit_pct': 0.004,
        'stop_loss_pct': 0.006,
        'max_hold_kline': 3,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -65.0,  # Stricter CMO threshold for XRP Long bottom catching (-65.0)
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    },
    'SOL_USDT': {
        'leverage': 5,
        'min_position_size': 0.1,
        'take_profit_pct': 0.005,
        'stop_loss_pct': 0.006,
        'max_hold_kline': 3,
        'order_params': {'postOnly': True, 'category': 'linear'},
        'cmo_oversold_long': -60.0,
        'cmo_overbought_short': 60.0,
        'max_notional_exposure': 50.0,
        'margin_shield_threshold_pct': 0.70
    }
}

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
    'min_profit_pct': 0.0025,    # Or when unrealized profit reaches +0.25%
    'fee_buffer_pct': 0.0006     # +0.06% buffer over entry price to guarantee zero-fee breakeven
}

# Orderbook Microstructure & Liquidity Imbalance Filter
MICROSTRUCTURE_CONFIG = {
    'orderbook_imbalance_check': True,
    'imbalance_threshold': 0.35, # Reject if opposing wall has > 35% depth skew
    'depth_levels': 10
}
