import os
import time
import logging
import asyncio
import traceback
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("TradingEngine")


def safe_float(val, default=0.0) -> float:
    try:
        if val is None or val == "":
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


class RiskManager:
    """
    AlphaScalp Enhanced Risk Manager & Margin Shield (v5.6).
    
    Provides independent emergency circuit breakers, active equity & margin monitoring,
    and strict pre-flight leverage and notional exposure enforcement to prevent liquidations.
    """
    def __init__(
        self,
        state_manager: Any = None,
        db_manager: Any = None,
        order_manager: Any = None,
        exchange: Optional[Any] = None
    ):
        self.state_manager = state_manager
        self.db_manager = db_manager
        self.order_manager = order_manager
        self.exchange = exchange

        # Load environment-level risk constraints
        self.max_leverage = int(os.getenv("MAX_LEVERAGE", os.getenv("LEVERAGE", "5")))
        self.max_notional_exposure = safe_float(os.getenv("MAX_NOTIONAL_EXPOSURE", "50.0"), 50.0)
        self.margin_shield_threshold_pct = safe_float(
            os.getenv("MARGIN_SHIELD_THRESHOLD_PCT", os.getenv("EMERGENCY_LOSS_THRESHOLD_PCT", "0.70")),
            0.70
        )
        
        # State tracking for alerts to prevent spamming
        self.last_alert_time = 0.0
        self.circuit_breaker_triggered = False

    def validate_leverage(self, requested_leverage: int) -> Tuple[bool, int, str]:
        """
        Enforce strict upper limit on leverage from .env (MAX_LEVERAGE).
        Clamps or rejects if leverage exceeds configured boundary.
        """
        if requested_leverage <= 0:
            return False, 1, "Leverage must be >= 1"

        if requested_leverage > self.max_leverage:
            reason = f"Requested leverage {requested_leverage}x exceeds MAX_LEVERAGE ({self.max_leverage}x). Clamping to {self.max_leverage}x."
            if self.db_manager:
                self.db_manager.log_message("WARNING", reason, "RiskManager")
            return True, self.max_leverage, reason

        return True, requested_leverage, "Leverage within allowed limits."

    def validate_notional_exposure(self, symbol: str, size: float, price: float) -> Tuple[bool, str]:
        """
        Enforce strict notional exposure limit (size * price <= MAX_NOTIONAL_EXPOSURE).
        """
        notional = size * price
        if notional > self.max_notional_exposure:
            reason = (
                f"❌ [NOTIONAL LIMIT EXCEEDED] Order notional ${notional:.2f} for {symbol} "
                f"exceeds MAX_NOTIONAL_EXPOSURE limit (${self.max_notional_exposure:.2f})."
            )
            if self.db_manager:
                self.db_manager.log_message("WARNING", reason, "RiskManager")
            logger.warning(reason)
            return False, reason

        return True, f"Notional value ${notional:.2f} within limit (${self.max_notional_exposure:.2f})."

    def calculate_isolated_margin(self, size: float, entry_price: float, leverage: int) -> float:
        """
        Calculate the allocated isolated margin for a given position.
        Isolated Margin = (Size * Entry Price) / Leverage
        """
        if leverage <= 0:
            leverage = 1
        notional = abs(size) * entry_price
        return notional / float(leverage)

    def calculate_drawdown_on_isolated_margin(
        self,
        size: float,
        entry_price: float,
        current_price: float,
        side: str,
        leverage: int,
        unrealized_pnl: Optional[float] = None
    ) -> Tuple[float, float, float]:
        """
        Calculate unrealized PnL, allocated isolated margin, and drawdown ratio.
        Returns: (unrealized_pnl, isolated_margin, drawdown_ratio)
        where drawdown_ratio = (-unrealized_pnl / isolated_margin) if loss else 0.0.
        """
        allocated_margin = self.calculate_isolated_margin(size, entry_price, leverage)
        if allocated_margin <= 0:
            return 0.0, 0.0, 0.0

        if unrealized_pnl is None:
            if side.upper() in ["BUY", "LONG"]:
                unrealized_pnl = size * (current_price - entry_price)
            else:
                unrealized_pnl = size * (entry_price - current_price)

        if unrealized_pnl < 0:
            drawdown_ratio = abs(unrealized_pnl) / allocated_margin
        else:
            drawdown_ratio = 0.0

        return unrealized_pnl, allocated_margin, drawdown_ratio

    def check_margin_shield_breach(
        self,
        symbol: str,
        size: float,
        entry_price: float,
        current_price: float,
        side: str,
        leverage: int,
        unrealized_pnl: Optional[float] = None
    ) -> Tuple[bool, float, float, float, str]:
        """
        Check if unrealized loss on a single asset exceeds the critical margin shield threshold (e.g. 70%).
        Returns: (is_breached, unrealized_pnl, isolated_margin, drawdown_ratio, reason)
        """
        u_pnl, isolated_margin, drawdown_ratio = self.calculate_drawdown_on_isolated_margin(
            size=size,
            entry_price=entry_price,
            current_price=current_price,
            side=side,
            leverage=leverage,
            unrealized_pnl=unrealized_pnl
        )

        if drawdown_ratio >= self.margin_shield_threshold_pct:
            pct_lost = drawdown_ratio * 100.0
            threshold_pct = self.margin_shield_threshold_pct * 100.0
            reason = (
                f"🚨 [MARGIN SHIELD BREACH] {symbol} unrealized loss (${abs(u_pnl):.2f}) "
                f"reached {pct_lost:.1f}% of allocated isolated margin (${isolated_margin:.2f}), "
                f"exceeding emergency threshold ({threshold_pct:.0f}%)."
            )
            return True, u_pnl, isolated_margin, drawdown_ratio, reason

        return False, u_pnl, isolated_margin, drawdown_ratio, "Margin shield within normal tolerance."

    async def monitor_equity_and_margin_shield(self, bot_instance: Any) -> bool:
        """
        Active equity and margin monitor executed every loop tick.
        Queries exchange balances/positions and triggers emergency close_all_positions()
        if any single asset's unrealized loss exceeds 70% of allocated isolated margin.
        """
        dry_run = getattr(bot_instance, "dry_run", True)
        exchange = getattr(bot_instance, "exchange", None) or self.exchange

        try:
            # 1. LIVE TRADING MONITOR
            if not dry_run and exchange:
                # Query exchange balances
                try:
                    balance_data = await exchange.fetch_balance(params={'type': 'unified'})
                    result_list = balance_data.get('info', {}).get('result', {}).get('list', [{}])[0]
                    total_equity = safe_float(result_list.get('totalEquity'), 0.0)
                    avail_balance = safe_float(result_list.get('totalAvailableBalance'), 0.0)
                    if avail_balance == 0.0 and 'USDT' in balance_data:
                        avail_balance = safe_float(balance_data['USDT'].get('free'), 0.0)
                        total_equity = safe_float(balance_data['USDT'].get('total'), 0.0)
                except Exception as b_err:
                    logger.debug(f"⚠️ [RISK MONITOR] Fetch balance fallback: {b_err}")
                    avail_balance = getattr(bot_instance, "wallet_free_margin", 0.0)
                    total_equity = getattr(bot_instance, "wallet_total_equity", 0.0)

                # Fetch all open linear positions
                active_positions = []
                try:
                    symbols_to_query = [bot_instance.symbol] if hasattr(bot_instance, 'symbol') else []
                    active_positions = await exchange.fetch_positions(
                        symbols=symbols_to_query if symbols_to_query else None,
                        params={'category': 'linear'}
                    )
                except Exception as p_err:
                    logger.warning(f"⚠️ [RISK MONITOR] Could not fetch positions from exchange: {p_err}")

                for pos in active_positions:
                    contracts = safe_float(pos.get('contracts') or pos.get('size'), 0.0)
                    if contracts <= 0:
                        continue

                    symbol = pos.get('symbol', bot_instance.symbol if hasattr(bot_instance, 'symbol') else 'UNKNOWN')
                    entry_price = safe_float(pos.get('entryPrice'), 0.0)
                    mark_price = safe_float(pos.get('markPrice') or pos.get('lastPrice'), entry_price)
                    leverage = int(safe_float(pos.get('leverage'), bot_instance.leverage if hasattr(bot_instance, 'leverage') else 5))
                    side = str(pos.get('side', '')).upper()
                    unrealized_pnl = safe_float(pos.get('unrealizedPnl'), 0.0)

                    # Check margin shield breach
                    is_breached, u_pnl, iso_margin, dd_ratio, reason = self.check_margin_shield_breach(
                        symbol=symbol,
                        size=contracts,
                        entry_price=entry_price,
                        current_price=mark_price,
                        side=side,
                        leverage=leverage,
                        unrealized_pnl=unrealized_pnl
                    )

                    if is_breached:
                        logger.critical(f"🛑 [CIRCUIT BREAKER TRIGGERED] {reason}")
                        if self.db_manager:
                            self.db_manager.log_message("CRITICAL", reason, "RiskManager")
                        
                        # Trigger Emergency Close All Positions
                        await self.close_all_positions(
                            bot_instance=bot_instance,
                            symbol=symbol,
                            reason=f"EMERGENCY_MARGIN_SHIELD_LOSS_{dd_ratio*100:.1f}PCT"
                        )
                        return True

            # 2. DRY RUN / LOCAL STATE MONITOR
            else:
                positions = self.state_manager.get("positions", {}) if self.state_manager else {}
                for sym, pos_data in list(positions.items()):
                    size = safe_float(pos_data.get("remaining_size", pos_data.get("size", 0.0)))
                    if size <= 0:
                        continue

                    entry_price = safe_float(pos_data.get("entry_price"), 0.0)
                    side = str(pos_data.get("side", "BUY")).upper()
                    leverage = int(safe_float(pos_data.get("leverage"), 5))

                    # Fetch latest ticker price if available
                    current_price = entry_price
                    if hasattr(bot_instance, 'fetch_ticker_safe'):
                        try:
                            ticker = await bot_instance.fetch_ticker_safe()
                            current_price = safe_float(ticker.get("last"), entry_price)
                        except Exception:
                            current_price = entry_price

                    is_breached, u_pnl, iso_margin, dd_ratio, reason = self.check_margin_shield_breach(
                        symbol=sym,
                        size=size,
                        entry_price=entry_price,
                        current_price=current_price,
                        side=side,
                        leverage=leverage
                    )

                    if is_breached:
                        logger.critical(f"🛑 [DRY RUN CIRCUIT BREAKER TRIGGERED] {reason}")
                        if self.db_manager:
                            self.db_manager.log_message("CRITICAL", reason, "RiskManager")

                        await self.close_all_positions(
                            bot_instance=bot_instance,
                            symbol=sym,
                            reason=f"EMERGENCY_DRY_RUN_MARGIN_SHIELD_LOSS_{dd_ratio*100:.1f}PCT"
                        )
                        return True

        except Exception as e:
            logger.error(f"❌ [RISK MONITOR ERROR] Exception during equity & margin shield check: {e}\n{traceback.format_exc()}")

        return False

    async def close_all_positions(
        self,
        bot_instance: Any,
        symbol: Optional[str] = None,
        reason: str = "EMERGENCY_CIRCUIT_BREAKER"
    ) -> bool:
        """
        Emergency hard-cut circuit breaker.
        Cancels all active open orders and executes market reduce-only close orders
        to liquidate positions immediately independently of exchange-level liquidations.
        """
        target_symbol = symbol or getattr(bot_instance, 'symbol', 'XRP/USDT')
        dry_run = getattr(bot_instance, 'dry_run', True)
        exchange = getattr(bot_instance, 'exchange', None) or self.exchange

        logger.warning(f"🚨 [EMERGENCY HARD-CUT] Executing close_all_positions for {target_symbol}. Reason: {reason}")
        if self.db_manager:
            self.db_manager.log_message("WARNING", f"🚨 [EMERGENCY CLOSE] Triggered for {target_symbol}: {reason}", "RiskManager")

        # 1. Cancel all open orders for symbol
        if self.order_manager and hasattr(self.order_manager, 'cancel_active_close_orders'):
            try:
                await self.order_manager.cancel_active_close_orders(target_symbol)
            except Exception as c_err:
                logger.error(f"⚠️ Failed order cleanup during emergency close: {c_err}")

        # 2. Execute Market Close on Exchange (Live Mode)
        exit_price = 0.0
        pnl = 0.0
        side = "BUY"
        remaining_size = 0.0

        positions = self.state_manager.get("positions", {}) if self.state_manager else {}
        pos_data = positions.get(target_symbol, {})
        if pos_data:
            remaining_size = safe_float(pos_data.get("remaining_size", pos_data.get("size", 0.0)))
            side = str(pos_data.get("side", "BUY")).upper()
            entry_price = safe_float(pos_data.get("entry_price", 0.0))

        if not dry_run and exchange:
            try:
                # Query actual exchange position size to ensure complete liquidation
                active_positions = await exchange.fetch_positions([target_symbol], params={'category': 'linear'})
                if active_positions and len(active_positions) > 0:
                    pos = active_positions[0]
                    contracts = safe_float(pos.get('contracts'), remaining_size)
                    pos_side = str(pos.get('side', '')).upper()
                    if contracts > 0:
                        opp_side = "sell" if pos_side in ["BUY", "LONG"] else "buy"
                        close_params = {'reduceOnly': True, 'category': 'linear'}
                        close_order = await exchange.create_order(
                            symbol=target_symbol,
                            type='market',
                            side=opp_side,
                            amount=contracts,
                            price=None,
                            params=close_params
                        )
                        logger.info(f"✅ [EMERGENCY MARKET CLOSE ORDER PLACED] ID: {close_order.get('id')}")
            except Exception as ex_err:
                logger.error(f"❌ Failed to place exchange emergency market close: {ex_err}")

        # 3. Synchronize State and DB
        if hasattr(bot_instance, 'fetch_ticker_safe'):
            try:
                ticker = await bot_instance.fetch_ticker_safe()
                exit_price = safe_float(ticker.get("last"), 0.0)
            except Exception:
                exit_price = 0.0

        if pos_data and remaining_size > 0 and exit_price > 0:
            pnl = remaining_size * (exit_price - entry_price) if side in ["BUY", "LONG"] else remaining_size * (entry_price - exit_price)

        if self.state_manager:
            self.state_manager.record_closed_order(target_symbol, side, pnl, reason)
            self.state_manager.clear_active_position()

        if self.db_manager and remaining_size > 0:
            opp_action = "SELL" if side in ["BUY", "LONG"] else "BUY"
            self.db_manager.log_trade(
                target_symbol,
                opp_action,
                exit_price,
                remaining_size,
                pnl,
                pos_data.get("regime", "EMERGENCY"),
                reason
            )

        # Reset bot internal trackers
        if hasattr(bot_instance, 'current_position_size'):
            bot_instance.current_position_size = 0.0
        if hasattr(bot_instance, 'bars_held'):
            bot_instance.bars_held = 0

        # 4. Telegram Emergency Broadcast
        alert_msg = (
            f"🚨 *[EMERGENCY CIRCUIT BREAKER ACTIVATED]*\n"
            f"• *Symbol:* `{target_symbol}`\n"
            f"• *Action:* Immediate Market Close (`close_all_positions`)\n"
            f"• *Reason:* `{reason}`\n"
            f"• *Exit Price:* `{exit_price:.4f}`\n"
            f"• *Realized Loss:* `${pnl:.2f}`\n"
            f"• *Protection:* Margin Shield prevented full exchange liquidation."
        )
        if hasattr(bot_instance, 'send_telegram'):
            await bot_instance.send_telegram(alert_msg)

        if hasattr(bot_instance, 'recalculate_stats'):
            bot_instance.recalculate_stats()

        return True
