import math
import logging
import traceback
import asyncio
from typing import Any, Dict, Tuple, Optional
import ccxt

logger = logging.getLogger("TradingEngine")

# Helper function for resilient numeric parsing
def safe_float(val, default=0.0):
    try:
        if val is None or val == '':
            return default
        return float(val)
    except (ValueError, TypeError):
        return default

class OrderManager:
    def __init__(self, state_manager: Any, db_manager: Any, exchange: Optional[ccxt.Exchange] = None, risk_pct: float = 0.01, default_mmr: float = 0.005):
        self.state_manager = state_manager
        self.db_manager = db_manager
        self.exchange = exchange
        self.risk_pct = risk_pct          # กฎเหล็ก 1% ของ Richard Dennis
        self.mmr = default_mmr            # Maintenance Margin Ratio (0.5% สำหรับ Bybit BTC)

    async def cancel_active_close_orders(self, symbol: str) -> bool:
        """
        [HOTFIX AlphaScalp v4.2 & v4.3] Active Order Cleanup Logic.
        Before the bot places ANY new Dynamic Take Profit, Stop Loss, or Close position order,
        it checks for and cancels existing open orders for that specific pair on Bybit V5.
        Clears old close_order_id from state.json and enforces a post-cancel delay.
        """
        dry_run = self.state_manager.get("dry_run", True) if self.state_manager else True
        if dry_run or not self.exchange:
            if self.state_manager and hasattr(self.state_manager, 'clear_close_order_id'):
                self.state_manager.clear_close_order_id(symbol)
            return True

        try:
            logger.info(f"🧹 [ORDER CLEANUP] Cancelling active open orders for {symbol}...")
            # Bybit V5 CCXT cancel_all_orders with params={'category': 'linear'}
            await self.exchange.cancel_all_orders(symbol, params={'category': 'linear'})
            logger.info(f"✅ Successfully cancelled active open orders for {symbol}")
        except Exception as e:
            err_msg = str(e).lower()
            if "order not found" in err_msg or "30086" in err_msg or "110001" in err_msg or "no active orders" in err_msg:
                logger.debug(f"ℹ️ No active open orders found for {symbol} to cancel.")
            else:
                logger.warning(f"⚠️ Warning while cancelling active orders for {symbol}: {e}")

        # State Manager Integrity: clear old close_order_id from state.json
        if self.state_manager and hasattr(self.state_manager, 'clear_close_order_id'):
            self.state_manager.clear_close_order_id(symbol)

        # Enforce Post-Cancel Delay (0.5s) to allow Bybit matching engine to clear cache
        await asyncio.sleep(0.5)
        return True

    async def check_orderbook_imbalance(
        self,
        symbol: str,
        side: str,
        depth: int = 10,
        threshold: float = 0.35
    ) -> Tuple[bool, float, str]:
        """
        Microstructure Edge: Orderbook Imbalance (OBI).
        Calculates imbalance ratio between bid and ask depth:
        imbalance = (sum(bids) - sum(asks)) / (sum(bids) + sum(asks))
        Range: [-1.0, 1.0]
        - For BUY: Reject if imbalance < -threshold (heavy ask wall right above).
        - For SELL: Reject if imbalance > threshold (heavy bid wall right below).
        """
        dry_run = self.state_manager.get("dry_run", True) if self.state_manager else True
        if dry_run or not self.exchange:
            return True, 0.0, "Dry run / exchange bypass."

        try:
            orderbook = await self.exchange.fetch_order_book(symbol, limit=depth, params={'category': 'linear'})
            bids = orderbook.get('bids', [])[:depth]
            asks = orderbook.get('asks', [])[:depth]

            total_bid_vol = sum([b[1] for b in bids]) if bids else 0.0
            total_ask_vol = sum([a[1] for a in asks]) if asks else 0.0

            total_vol = total_bid_vol + total_ask_vol
            if total_vol <= 0:
                return True, 0.0, "Orderbook depth empty; bypass."

            imbalance = (total_bid_vol - total_ask_vol) / total_vol

            side_upper = side.upper()
            if side_upper in ["BUY", "LONG"] and imbalance < -threshold:
                reason = (
                    f"⚠️ [OBI FILTER REJECT] Heavy ask wall detected (Imbalance: {imbalance:.2f} < -{threshold:.2f}). "
                    f"Ask Vol: {total_ask_vol:.1f} vs Bid Vol: {total_bid_vol:.1f}. Skipping Long entry."
                )
                self.db_manager.log_message("WARNING", reason, "OrderManager")
                return False, imbalance, reason

            if side_upper in ["SELL", "SHORT"] and imbalance > threshold:
                reason = (
                    f"⚠️ [OBI FILTER REJECT] Heavy bid wall detected (Imbalance: {imbalance:.2f} > +{threshold:.2f}). "
                    f"Bid Vol: {total_bid_vol:.1f} vs Ask Vol: {total_ask_vol:.1f}. Skipping Short entry."
                )
                self.db_manager.log_message("WARNING", reason, "OrderManager")
                return False, imbalance, reason

            return True, imbalance, f"Orderbook imbalance {imbalance:.2f} acceptable for {side_upper}."
        except Exception as e:
            logger.debug(f"Orderbook imbalance check exception: {e}")
            return True, 0.0, f"Exception during OBI fetch: {e}"


    async def configure_leverage_safely(self, symbol: str, leverage: int):
        """
        [FIXED] ระบบตั้งค่ามาร์จิ้นและเลเวอเรจแบบ Asynchronous เต็มรูปแบบ 
        แก้ปัญหา 'coroutine was never awaited' เพื่อส่งคำสั่งเข้ากระดานจริง 100%
        """
        try:
            logger.info(f"⚙️ [ASYNC RPC] Enforcing Isolated Margin for {symbol}...")
            await self.exchange.set_margin_mode("ISOLATED", symbol)
            logger.info(f"✅ Margin mode successfully locked to ISOLATED for {symbol}")
        except Exception as e:
            err_msg = str(e).lower()
            if "marginmode must be either" in err_msg or "margin_mode_invalid" in err_msg or "marginmode" in err_msg or "requests must be" in err_msg:
                logger.info("⚠️ [INFO] Account is running on Bybit UTA. Relying on manual UI margin settings.")
            elif "already" in err_msg or "margin_mode_no_change" in err_msg:
                logger.info(f"✅ Margin mode is already ISOLATED for {symbol}")
            else:
                logger.warning(f"⚠️ Could not set margin mode (Asset might already be ISOLATED): {e}")

        try:
            # Fix Leverage RPC Payload - format symbol to linear (remove slash) and set category linear
            formatted_symbol = symbol.replace('/', '')
            
            # Active Position Guard
            active_position = self.state_manager.get("positions", {}).get(symbol, {})
            current_position_size = float(active_position.get("size", 0.0))
            
            if current_position_size == 0.0:
                await self.exchange.set_leverage(
                    leverage=leverage,
                    symbol=formatted_symbol,
                    params={'category': 'linear'}
                )
                logger.info(f"✅ Leverage successfully set to {leverage}x for {symbol}")
            else:
                logger.debug(f"⏩ Position active for {symbol}; skipping redundant setLeverage RPC call.")
                
        except Exception as lev_err:
            err_text = str(lev_err)
            if "110043" in err_text or "not modified" in err_text.lower() or "110012" in err_text or "not changed" in err_text.lower():
                logger.debug(f"ℹ️ Leverage {leverage}x already active. Skipping.")
            else:
                logger.warning(f"⚠️ Leverage warning: {err_text}")

    async def setup_isolated_margin(self, symbol: str, leverage: int) -> bool:
        """
        Compatibility wrapper for bot.py to use configure_leverage_safely.
        """
        dry_run = self.state_manager.get("dry_run", True)
        if dry_run:
            self.db_manager.log_message("INFO", f"[DRY RUN] Margin set to ISOLATED and leverage to {leverage}x for {symbol}", "OrderManager")
            return True
            
        if not self.exchange:
            self.db_manager.log_message("ERROR", "Exchange connection not configured", "OrderManager")
            return False
            
        await self.configure_leverage_safely(symbol, leverage)
        return True

    async def validate_pre_flight_balance(self, symbol: str, entry_price: float, leverage: int) -> dict:
        """
        [NEW] ระบบคำนวณความสัมพันธ์ของขนาดพอร์ตและสัญญาขั้นต่ำ (Exchange Min Size Guard)
        ตรวจสอบยอดเงินคงเหลือจริงและสเปกขั้นต่ำ เพื่อป้องกันการเกิด 'Insufficient balance (170131)'
        """
        # 1. ดึงยอดเงินสดคงเหลือจริงในกระปุก Futures (USDT)
        try:
            balance_data = await self.exchange.fetch_balance(params={'type': 'unified'})
            result_list = balance_data.get('info', {}).get('result', {}).get('list', [{}])[0]
            available_balance = safe_float(result_list.get('totalAvailableBalance'), 0.0)
            wallet_total_equity = safe_float(result_list.get('totalEquity'), 0.0)
            
            # Fallback to CCXT standardized array if native API info structure is empty
            if available_balance == 0.0 and 'USDT' in balance_data:
                available_balance = safe_float(balance_data['USDT'].get('free'), 0.0)
                wallet_total_equity = safe_float(balance_data['USDT'].get('total'), 0.0)
        except Exception as e:
            logger.error(f"Failed to query clean Bybit UTA margins: {e}")
            available_balance = 0.0
            wallet_total_equity = 0.0
        
        # 2. ดึงสเปกสัญญาขั้นต่ำของเหรียญนั้นๆ จาก CCXT Markets Data (Default BTC = 0.001)
        await self.exchange.load_markets()
        market = self.exchange.market(symbol)
        
        # Correct enforcement for small accounts on SOL/USDT
        if symbol.startswith("SOL"):
            min_order_size = 0.1  # Absolute safe execution limit for Bybit SOL Futures (~7.7 USDT value)
        elif symbol.startswith("XRP"):
            min_order_size = 3.0  # Absolute safe execution limit for Bybit XRP Futures (> $3 USDT value)
        else:
            min_order_size = float(market['limits']['amount']['min']) if market else 0.001
        
        # 3. คำนวณมูลค่าสัญญาดิบดิบ (Notional Value) และมาร์จิ้นขั้นต่ำที่ต้องใช้ค้ำประกัน
        notional_value = min_order_size * entry_price
        required_margin = notional_value / leverage
        
        # เผื่อเศษเงินสแตนด์บายสำหรับค่าธรรมเนียม Taker Fee และ Buffer ป้องกันราคาขยับ (10% หรือ 2% สำหรับบัญชีเล็กกว่า 15 USDT)
        buffer_multiplier = 1.02 if available_balance < 15.0 else 1.10
        safe_margin_required = required_margin * buffer_multiplier
        
        logger.info(f"🔍 [UTA ONLINE SYNC] Total Portfolio Equity: {wallet_total_equity} USD | Free Margin: {available_balance} USDT")
        
        if available_balance < safe_margin_required:
            return {
                "is_safe": False,
                "reason": f"Insufficient Capital. Your wallet has {available_balance:.2f} USDT, but the absolute minimum order size ({min_order_size} {symbol.split('/')[0]}) at {leverage}x requires at least {safe_margin_required:.2f} USDT (including fee buffers).",
                "calculated_size": 0.0
            }
            
        return {
            "is_safe": True,
            "reason": "Capital verification passed.",
            "calculated_size": min_order_size
        }

    async def calculate_position_size(self, capital: float, entry_price: float, stop_loss_price: float, leverage: int, symbol: str = "BTC/USDT", risk_pct: float = 0.01) -> float:
        """
        [MODIFIED] Force Minimum Size: Returns the minimum contract size allowed by exchange/spec
        to accommodate small accounts (e.g. 16 USDT) instead of standard 1% risk allocation.
        """
        dry_run = self.state_manager.get("dry_run", True)
        if not dry_run and self.exchange:
            # Enforce pre-flight balance safety check
            check = await self.validate_pre_flight_balance(symbol, entry_price, leverage)
            if not check["is_safe"]:
                self.db_manager.log_message("WARNING", f"Pre-flight balance check failed: {check['reason']}", "OrderManager")
                return 0.0
            min_size = check["calculated_size"]
        else:
            # Fallback values for dry run based on symbol
            if "BTC" in symbol:
                min_size = 0.001
            elif "SOL" in symbol:
                min_size = 0.1
            elif "XRP" in symbol:
                min_size = 3.0
            else:
                min_size = 0.01

        self.db_manager.log_message(
            "INFO",
            f"[FORCE MINIMUM SIZE] Using minimum contract size {min_size} for {symbol} due to small account size.",
            "OrderManager"
        )
        return min_size

    def calculate_pre_flight_liquidation(self, side: str, entry_price: float, leverage: int) -> float:
        """
        ระบบคำนวณจุดล้างพอร์ตล่วงหน้าทางคณิตศาสตร์แบบไร้ความหน่วง
        Long Liq = Entry * (1 - 1/Leverage + MMR)
        Short Liq = Entry * (1 + 1/Leverage - MMR)
        """
        if side.upper() == "LONG" or side.upper() == "BUY":
            return entry_price * (1.0 - (1.0 / leverage) + self.mmr)
        else: # SHORT / SELL
            return entry_price * (1.0 + (1.0 / leverage) - self.mmr)

    def calculate_liquidation_price(self, entry_price: float, leverage: int, side: str, mmr: float = 0.005) -> float:
        """
        Compatibility wrapper for calculate_pre_flight_liquidation.
        """
        return self.calculate_pre_flight_liquidation(side, entry_price, leverage)

    def validate_leverage_and_liq(self, entry_price: float, stop_loss_price: float, leverage: int, side: str, mmr: float = 0.005) -> Tuple[bool, int, float]:
        """
        Pre-Flight Liquidation Guard.
        Verifies if the liquidation price sits inside the Stop Loss boundary.
        If it does, it dynamically scales down leverage until the liquidation point is outside.
        Also clamps leverage to MAX_LEVERAGE configured in environment.
        Returns: (is_allowed, approved_leverage, liquidation_price)
        """
        import os
        max_lev = int(os.getenv("MAX_LEVERAGE", os.getenv("LEVERAGE", "5")))
        current_leverage = min(leverage, max_lev)
        side = side.upper()
        
        while current_leverage >= 1:
            liq_price = self.calculate_liquidation_price(entry_price, current_leverage, side, mmr)
            
            if side == "BUY" or side == "LONG":
                # For long, Stop Loss must be HIGHER than liquidation price (SL hits first)
                is_safe = stop_loss_price > liq_price
            else:
                # For short, Stop Loss must be LOWER than liquidation price (SL hits first)
                is_safe = stop_loss_price < liq_price
                
            if is_safe:
                if current_leverage != leverage:
                    self.db_manager.log_message(
                        "WARNING", 
                        f"Liquidation price at {leverage}x was inside Stop Loss. Leverage reduced to {current_leverage}x for safety.", 
                        "OrderManager"
                    )
                return True, current_leverage, liq_price
                
            current_leverage -= 1
            
        self.db_manager.log_message("ERROR", f"Pre-flight guard rejected trade. Stop loss {stop_loss_price} too wide.", "OrderManager")
        return False, 0, 0.0
