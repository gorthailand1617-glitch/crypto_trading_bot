import os
from dotenv import load_dotenv

# Force immediate load of environmental block to capture Bybit credentials
load_dotenv()

API_KEY = os.getenv('BYBIT_API_KEY')
API_SECRET = os.getenv('BYBIT_API_SECRET')

if not API_KEY or not API_SECRET:
    # Hardened fallback indicator
    print("[CRITICAL WARNING] API Credentials failed to load from OS environment matrix.")

import asyncio
import inspect
import time
import math
import traceback
import aiohttp
import logging

# Configure logger for TradingEngine to write to app.log
logger = logging.getLogger("TradingEngine")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    handler = logging.FileHandler("app.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
    logger.addHandler(handler)
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s: %(message)s'))
    logger.addHandler(console_handler)

# Monkeypatch TCPConnector to default to ThreadedResolver to bypass aiodns bugs on Windows
_orig_connector_init = aiohttp.TCPConnector.__init__
def _patched_connector_init(self, *args, **kwargs):
    if "resolver" not in kwargs or kwargs["resolver"] is None:
        kwargs["resolver"] = aiohttp.ThreadedResolver()
    _orig_connector_init(self, *args, **kwargs)
aiohttp.TCPConnector.__init__ = _patched_connector_init

from functools import wraps
from typing import Any, Dict, Optional
import dotenv
import pandas as pd
import ccxt.async_support as ccxt

# Load local environment variables
dotenv.load_dotenv()

from utils.state_manager import StateManager
from utils.db_manager import DBManager
from utils.indicators import calculate_cmo, calculate_vwap_bands, calculate_tii, classify_market_regime, calculate_ema
from utils.order_manager import OrderManager
from utils.profit_allocator import ProfitAllocator
from risk_manager import RiskManager
from config import SAFE_TRADING_CORE, RISK_CONTROL_REGIME, BOT_VERSION, PROFIT_ALLOCATION_CONFIG, BREAKEVEN_CONFIG, MICROSTRUCTURE_CONFIG
from adaptive_brain import AdaptiveBrain


# Helper function for resilient numeric parsing
def safe_float(val, default=0.0):
    try:
        if val is None or val == '':
            return default
        return float(val)
    except (ValueError, TypeError):
        return default


# Exponential Back-Off Decorator
def ccxt_retry(max_retries: int = 4, base_delay: float = 2.0):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            last_err = None
            self = args[0] if len(args) > 0 else None
            for attempt in range(max_retries + 1):
                try:
                    if inspect.iscoroutinefunction(func):
                        res = await func(*args, **kwargs)
                    else:
                        res = func(*args, **kwargs)
                    # On successful connection after a disconnect
                    if self and hasattr(self, "connection_ok") and not self.connection_ok:
                        self.connection_ok = True
                        self.db_manager.log_message("INFO", "Connection to exchange restored.", "TradingEngine")
                        msg = "✅ *Connection Restored*\nSuccessfully re-established connection to the exchange."
                        await self.send_telegram(msg)
                    return res
                except Exception as e:
                    import ccxt
                    import aiohttp
                    
                    last_err = e
                    err_msg = str(e).lower()
                    
                    # Robust network/DNS/rate-limit checks
                    is_network_err = (
                        isinstance(e, ccxt.NetworkError) or
                        isinstance(e, aiohttp.ClientError) or
                        isinstance(e, OSError) or
                        "network" in err_msg or
                        "conn" in err_msg or
                        "dns" in err_msg or
                        "timeout" in err_msg or
                        "10054" in err_msg or
                        "notavailable" in err_msg or
                        "429" in err_msg
                    )
                    
                    if is_network_err:
                        if self and hasattr(self, "connection_ok") and self.connection_ok:
                            self.connection_ok = False
                            self.db_manager.log_message("WARNING", f"Connection to exchange lost: {e}", "TradingEngine")
                            msg = f"⚠️ *Connection Lost*\nFailed to connect to exchange: {e}"
                            await self.send_telegram(msg)
                            
                        if attempt < max_retries:
                            print(f"[RETRY WARNING] CCXT call failed (network/DNS/rate-limit): {e}. Retrying {attempt+1}/{max_retries} in {delay}s...")
                            await asyncio.sleep(delay)
                            delay *= 2
                        else:
                            raise e
                    else:
                        raise e
            raise last_err
        return wrapper
    return decorator

class ScalpingBot:
    def __init__(self):
        self.start_time = time.time()
        self.state_manager = StateManager()
        self.db_manager = DBManager()
        self.connection_ok = True
        
        # Monkey patch db_manager to output log statements to stdout in addition to the SQLite DB
        orig_log_msg = self.db_manager.log_message
        def console_log_msg(level, message, module):
            orig_log_msg(level, message, module)
            t_str = time.strftime('%Y-%m-%d %H:%M:%S')
            print(f"[{t_str}] {level:7} | ({module}) - {message}")
        self.db_manager.log_message = console_log_msg

        orig_log_trade = self.db_manager.log_trade
        def console_log_trade(symbol, side, price, amount, pnl, regime, trade_type):
            orig_log_trade(symbol, side, price, amount, pnl, regime, trade_type)
            t_str = time.strftime('%Y-%m-%d %H:%M:%S')
            pnl_str = f" | PnL: ${pnl:.2f}" if pnl != 0 else ""
            print(f"[{t_str}] TRADE   | {trade_type:20} - {side} {amount:.4f} {symbol} @ {price:.2f}{pnl_str}")
        self.db_manager.log_trade = console_log_trade

        orig_log_decision = self.db_manager.log_decision
        def console_log_decision(symbol, regime, action, reason, close_price, indicators):
            orig_log_decision(symbol, regime, action, reason, close_price, indicators)
            if action != "NONE":
                t_str = time.strftime('%Y-%m-%d %H:%M:%S')
                print(f"[{t_str}] DECISION| {action:20} - {reason} (Price: {close_price:.2f})")
        self.db_manager.log_decision = console_log_decision
        
        # Pull configurations from environment/state
        self.timeframe = os.getenv("TIMEFRAME", "3m") # 3m or 5m
        self.symbol = os.getenv("SYMBOL", "BTC/USDT")
        self.max_leverage = int(os.getenv("MAX_LEVERAGE", os.getenv("LEVERAGE", "5")))
        raw_leverage = int(os.getenv("LEVERAGE", "5"))
        self.leverage = min(raw_leverage, self.max_leverage)
        self.max_notional_exposure = safe_float(os.getenv("MAX_NOTIONAL_EXPOSURE", "50.0"), 50.0)
        self.margin_shield_threshold_pct = safe_float(os.getenv("MARGIN_SHIELD_THRESHOLD_PCT", "0.70"), 0.70)
        self.telegram_token = os.getenv("TELEGRAM_TOKEN", "")
        self.telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self.telegram_enabled = bool(self.telegram_token and self.telegram_chat_id)
        self.telegram_app = None
        self.telegram_bot = None
        
        # Initialize placeholders; actual setup occurs in start() inside the event loop
        self.exchange = None
        self.order_manager = OrderManager(self.state_manager, self.db_manager, None)
        self.risk_manager = RiskManager(self.state_manager, self.db_manager, self.order_manager, None)
        self.profit_allocator = ProfitAllocator(
            self.state_manager,
            self.db_manager,
            vault_ratio=PROFIT_ALLOCATION_CONFIG.get("vault_ratio", 0.3333),
            reinvest_ratio=PROFIT_ALLOCATION_CONFIG.get("reinvest_ratio", 0.6667),
            daily_loss_limit_pct=PROFIT_ALLOCATION_CONFIG.get("daily_loss_limit_pct", 0.03)
        )
        self.breakeven_config = BREAKEVEN_CONFIG
        self.microstructure_config = MICROSTRUCTURE_CONFIG
        
        # Initialize Adaptive ML Agent and load cached checkpoint
        self.brain = AdaptiveBrain()
        self.brain.set_state(self.state_manager.get("brain_model_state", {}))
        self.last_confidence = 0.5
        
        # Set default CMO thresholds in state if they do not exist (stricter -65.0 for XRP Long)
        if self.state_manager.get("cmo_upper") is None:
            self.state_manager.set("cmo_upper", 60.0)
        if self.state_manager.get("cmo_lower") is None or self.state_manager.get("cmo_lower") > -65.0:
            self.state_manager.set("cmo_lower", -65.0)
        if self.state_manager.get("last_optimization_time") is None:
            self.state_manager.set("last_optimization_time", 0.0)
            
        # Margin Shield & Time-Based Exit Variables
        self.current_position_size = 0.0
        self.bars_held = 0


    async def send_telegram_alert_resilient(self, message: str) -> None:
        if not getattr(self, 'telegram_enabled', True):
            return

        token = getattr(self, 'telegram_token', os.getenv("TELEGRAM_TOKEN", ""))
        chat_id = getattr(self, 'telegram_chat_id', os.getenv("TELEGRAM_CHAT_ID", ""))

        for attempt in range(1, 4):
            try:
                if not hasattr(self, 'telegram_bot') or self.telegram_bot is None:
                    from telegram import Bot
                    self.telegram_bot = Bot(token=token)

                await self.telegram_bot.send_message(
                    chat_id=chat_id,
                    text=message,
                    parse_mode='Markdown',
                    read_timeout=20,
                    write_timeout=20,
                    connect_timeout=20
                )
                return
            except Exception as e:
                err_msg = str(e)
                if "429" in err_msg or "Too Many Requests" in err_msg:
                    await asyncio.sleep(10) # Respect rate limit retry
                elif "502" in err_msg or "Bad Gateway" in err_msg:
                    await asyncio.sleep(5)
                else:
                    # Reset bot instance on hard drop
                    self.telegram_bot = None
                    await asyncio.sleep(2 * attempt)

    async def send_telegram_alert_safe(self, message_text: str) -> None:
        await self.send_telegram_alert_resilient(message_text)

    async def send_telegram(self, message: str) -> None:
        """Sends non-blocking Telegram notifications without interrupting trading loops."""
        asyncio.create_task(self.send_telegram_alert_resilient(message))

    async def reply_to_telegram(self, chat_id: int, text: str) -> None:
        """Sends a response message back to the user via Telegram."""
        url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=5) as response:
                    if response.status != 200:
                        resp_text = await response.text()
                        print(f"[TELEGRAM ERROR] Failed reply: {resp_text}")
        except Exception as e:
            print(f"[TELEGRAM ERROR] Exception in reply: {e}")

    async def security_check(self, chat_id: int) -> bool:
        """Strict chat ID security filtering."""
        if str(chat_id) != self.telegram_chat_id:
            print(f"[TELEGRAM SECURITY WARNING] Unauthorized access attempt from Chat ID: {chat_id}")
            await self.reply_to_telegram(chat_id, "🔒 Unauthorized Access Denied.")
            return False
        return True

    async def telegram_polling_loop(self) -> None:
        """Asynchronous background loop that polls Telegram getUpdates for commands with error resilience."""
        if not self.telegram_token:
            print("[TELEGRAM] Telegram token not configured. Polling disabled.")
            return

        # Ensure drop_pending_updates=True remains locked on application initialization to prevent request amplification
        print("[TELEGRAM] Deleting webhook and dropping pending updates...")
        try:
            delete_webhook_url = f"https://api.telegram.org/bot{self.telegram_token}/deleteWebhook"
            async with aiohttp.ClientSession() as session:
                async with session.post(delete_webhook_url, json={"drop_pending_updates": True}, timeout=5) as response:
                    await response.read()
        except Exception as e:
            print(f"[TELEGRAM] Webhook delete/drop updates bypass: {e}")

        url = f"https://api.telegram.org/bot{self.telegram_token}/getUpdates"

        print("[TELEGRAM] Executing Backlog Purging with offset=-1...")
        offset = 0
        try:
            async with aiohttp.ClientSession() as session:
                params = {"offset": -1, "timeout": 1, "allowed_updates": '["message"]'}
                async with session.get(url, params=params, timeout=5) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get("ok"):
                            results = data.get("result", [])
                            if results:
                                offset = results[-1]["update_id"] + 1
        except Exception as e:
            print(f"[TELEGRAM] Purge bypass: {e}")

        print("[TELEGRAM] Starting interactive status bot polling loop with error resilience...")

        need_purge_offset = False
        while True:
            try:
                if need_purge_offset:
                    # Execute Backlog Purging with offset=-1 upon re-establishing connection
                    try:
                        async with aiohttp.ClientSession() as session:
                            purge_params = {"offset": -1, "timeout": 5, "allowed_updates": '["message"]'}
                            async with session.get(url, params=purge_params, timeout=aiohttp.ClientTimeout(total=10)) as response:
                                if response.status == 200:
                                    data = await response.json()
                                    if data.get("ok") and data.get("result"):
                                        offset = data.get("result")[-1]["update_id"] + 1
                                        logger.info(f"🔄 [TELEGRAM RECONNECT] Purged pending backlog. New offset: {offset}")
                                        need_purge_offset = False
                    except Exception as purge_err:
                        logger.debug(f"Purge bypass on reconnect: {purge_err}")

                async with aiohttp.ClientSession() as session:
                    # Strict Telemetry v2.0 Resilience Configuration (60s total, 30s socket limits)
                    timeout_settings = aiohttp.ClientTimeout(
                        total=60.0,
                        connect=30.0,
                        sock_read=30.0,
                        sock_connect=30.0
                    )
                    params = {"offset": offset, "timeout": 30}
                    async with session.get(url, params=params, timeout=timeout_settings) as response:
                        if response.status == 200:
                            data = await response.json()
                            if data.get("ok"):
                                updates = data.get("result", [])
                                for update in updates:
                                    offset = update["update_id"] + 1
                                    # Handle update concurrently
                                    asyncio.create_task(self.handle_telegram_update(update))
                        else:
                            resp_text = await response.text()
                            raise Exception(f"HTTP {response.status}: {resp_text}")
                await asyncio.sleep(0.5)
            except asyncio.CancelledError:
                break
            except Exception as telegram_drop_err:
                err_str = str(telegram_drop_err)
                need_purge_offset = True  # Flag to trigger offset=-1 purge on reconnect
                if "timeout" in err_str.lower() or "socket" in err_str.lower() or "getaddrinfo" in err_str.lower() or "connection" in err_str.lower():
                    logger.debug(f"🔇 [TELEGRAM SILENT RETRY] Network pause: {err_str}")
                    await asyncio.sleep(5.0)  # Explicit 5s backoff to prevent tight-loop CPU spiking
                else:
                    logger.error(f"[TELEGRAM CRITICAL ERROR]: {err_str}")
                    await asyncio.sleep(5.0)  # Explicit 5s backoff to prevent tight-loop CPU spiking

    # Harden the Telegram ApplicationBuilder and HTTPX Request setup sequence
    def configure_telegram_builder_setup(self):
        try:
            import httpx
            from telegram.request import HTTPXRequest
            from telegram.ext import ApplicationBuilder
            
            # Build an ultra-resilient network request buffer
            custom_client_pool = HTTPXRequest(
                connection_pool_size=8,
                connect_timeout=20.0,
                read_timeout=45.0,    # Expanded to tolerate heavy Telegram server latency
                write_timeout=20.0
            )

            # Apply the resilient configuration to the main telemetry engine
            application = (
                ApplicationBuilder()
                .token(self.telegram_token)
                .request(custom_client_pool)
                .build()
            )
            return application
        except Exception:
            return None

    async def handle_telegram_update(self, update: dict) -> None:
        """Parses and routes commands from updates."""
        message = update.get("message") or update.get("edited_message")
        if not message:
            return

        chat = message.get("chat")
        if not chat:
            return

        chat_id = chat.get("id")
        text = message.get("text", "").strip()

        if not text.startswith("/"):
            return

        # Security check
        if not await self.security_check(chat_id):
            return

        command = text.split()[0].lower()
        if command in ["/help", "/?"]:
            help_message = (
                "🤖 **AlphaScalp Control Center (V6.0)**\n"
                "ครูกอร์สามารถพิมพ์คำสั่งเหล่านี้เพื่อสั่งงานระบบได้ครับ:\n\n"
                "• **/status** : ตรวจเช็คสถานะระบบปฏิบัติการและสัญญาณ Heartbeat\n"
                "• **/balance** : ดึงยอดเงินสดจริงบน Bybit UTA แบบ Real-time\n"
                "• **/position** : ตรวจสอบตำแหน่งสัญญาที่เปิดค้างอยู่\n"
                "• **/vault** : 🏦 ตรวจสอบยอดเงินกองทุนสะสมรักษาทุน (1/3 Vault Rule)\n"
                "• **/stats** : สรุปจำนวนรอบและเปอร์เซ็นต์ Win Rate จากฐานข้อมูล\n"
                "• **/pause** : ⏸️ สั่งหยุดพักการเปิดออเดอร์ใหม่ชั่วคราว\n"
                "• **/resume** : ▶️ สั่งให้บอทกลับมาสแกนและเทรดต่อ\n"
                "• **/closeall** : 🚨 สั่งปิดทุกไม้ฉุกเฉินทันทีด้วย Market Order"
            )
            await self.reply_to_telegram(chat_id, help_message)
        elif command == "/status":
            await self.cmd_status(chat_id)
        elif command == "/balance":
            await self.cmd_balance(chat_id)
        elif command == "/position":
            await self.cmd_position(chat_id)
        elif command == "/stats":
            await self.cmd_stats(chat_id)
        elif command == "/vault":
            await self.cmd_vault(chat_id)
        elif command == "/pause":
            await self.cmd_pause(chat_id)
        elif command == "/resume":
            await self.cmd_resume(chat_id)
        elif command in ["/closeall", "/emergency_close"]:
            await self.cmd_closeall(chat_id)

    async def cmd_vault(self, chat_id: int) -> None:
        summary = self.profit_allocator.get_summary()
        breaker_status = "🚨 TRIGGERED (Trading Locked)" if summary["daily_loss_limit_hit"] else "🟢 Normal (Active)"
        msg = (
            f"🏦 *AlphaScalp Capital Vault (1/3 Rule)*\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"• *Safe Vault Balance (1/3):* `${summary['vault_balance']:.4f} USDT`\n"
            f"• *Reinvest Compounded (2/3):* `${summary['reinvest_pool']:.4f} USDT`\n"
            f"• *Total Realized Profit:* `${summary['total_realized_profit']:.4f} USDT`\n"
            f"• *Total Realized Loss:* `${summary['total_realized_loss']:.4f} USDT`\n"
            f"• *Daily Net PnL Today:* `${summary['daily_pnl']:.4f} USDT`\n"
            f"• *Daily Circuit Breaker:* `{breaker_status}`\n"
            f"• *Date (UTC):* `{summary['date']}`\n\n"
            f"💡 *หลักการรักษาทุน:* ทุกไม้ที่ได้กำไร จะตัด 33.3% เข้า Vault สำรองทันที กำไรส่วนนี้ไม่มีวันถูกนำไปเสี่ยงเพิ่ม"
        )
        await self.reply_to_telegram(chat_id, msg)

    async def cmd_pause(self, chat_id: int) -> None:
        self.state_manager.set("bot_status", "paused")
        self.db_manager.log_message("WARNING", "Bot execution paused via Telegram /pause command.", "Telegram")
        msg = (
            f"⏸️ *Bot Paused by User*\n"
            f"ระบบหยุดพักการเปิดออเดอร์ใหม่เรียบร้อยครับ\n"
            f"• ออเดอร์ที่ถืออยู่ (ถ้ามี) ยังคงมีระบบ Trailing Stop และ Stop Loss ดูแลตามปกติ\n"
            f"• พิมพ์ **/resume** เพื่อให้บอทกลับมาสแกนและเทรดต่อ"
        )
        await self.reply_to_telegram(chat_id, msg)

    async def cmd_resume(self, chat_id: int) -> None:
        self.state_manager.set("bot_status", "running")
        self.db_manager.log_message("INFO", "Bot execution resumed via Telegram /resume command.", "Telegram")
        msg = (
            f"▶️ *Bot Resumed by User*\n"
            f"ระบบกลับมาทำงานและเปิดการสแกนหาจังหวะเทรดตามปกติแล้วครับ\n"
            f"• Target Pair: `{self.symbol}`\n"
            f"• Timeframe: `{self.timeframe}`"
        )
        await self.reply_to_telegram(chat_id, msg)

    async def cmd_closeall(self, chat_id: int) -> None:
        await self.reply_to_telegram(chat_id, "🚨 *Executing Emergency Close All...* กำลังส่งคำสั่งปิดทุกไม้ทันที...")
        closed = await self.close_all_positions(symbol=self.symbol, reason="TELEGRAM_EMERGENCY_CLOSE")
        if closed:
            await self.reply_to_telegram(chat_id, f"✅ *Emergency Close Completed*\nปิดทุกสัญญาของ {self.symbol} และยกเลิกออเดอร์ทั้งหมดเรียบร้อยครับ")
        else:
            await self.reply_to_telegram(chat_id, f"ℹ️ *No Active Positions*\nไม่มีออเดอร์ค้างอยู่ให้ปิด หรือคำสั่งได้ถูกยกเลิกแล้วครับ")

    async def cmd_status(self, chat_id: int) -> None:
        bot_status_raw = self.state_manager.get("bot_status", "unknown").upper()
        mode_str = "Live Trading" if not self.dry_run else "Dry Run"
        status_indicator = f"🟢 Active ({mode_str})" if bot_status_raw == "RUNNING" else f"🟡 {bot_status_raw.capitalize()}"
        
        def read_logs():
            engine_logs = []
            try:
                # 1. Try reading from app.log if exists
                if os.path.exists("app.log"):
                    with open("app.log", "r") as f:
                        lines = f.readlines()
                    engine_logs = [l.strip() for l in lines if "[TradingEngine]" in l][-3:]
                
                # 2. Try reading from bot.log if app.log is not present/empty
                if not engine_logs and os.path.exists("bot.log"):
                    file_size = os.path.getsize("bot.log")
                    with open("bot.log", "r") as f:
                        if file_size > 50000:
                            f.seek(file_size - 50000)
                        lines = f.readlines()
                    engine_logs = [l.strip() for l in lines if "(TradingEngine)" in l or "[TradingEngine]" in l][-3:]
            except Exception:
                pass
            return engine_logs

        # Dynamic decision log fetcher inside async /status execution
        try:
            loop = asyncio.get_running_loop()
            engine_logs = await loop.run_in_executor(None, read_logs)
            
            # 3. Fallback to SQLite trades.db logs table if still empty
            if not engine_logs:
                def read_db():
                    rows = []
                    try:
                        import sqlite3
                        conn = sqlite3.connect("trades.db")
                        cursor = conn.cursor()
                        cursor.execute(
                            "SELECT timestamp, level, message FROM logs WHERE module = 'TradingEngine' ORDER BY timestamp DESC LIMIT 3"
                        )
                        rows = cursor.fetchall()
                        conn.close()
                    except Exception:
                        pass
                    return rows
                    
                rows = await loop.run_in_executor(None, read_db)
                for r in reversed(rows):
                    t_str = time.strftime('%H:%M:%S', time.localtime(r[0]))
                    engine_logs.append(f"[{t_str}] {r[1]} - {r[2]}")
                    
            formatted_logs = "\n".join([f"`{log}`" for log in engine_logs])
            if not formatted_logs:
                formatted_logs = "`No active logs found.`"
        except Exception:
            formatted_logs = "`No active logs found.`"

        status_message = (
            f"🤖 **AlphaScalp Core Heartbeat ({BOT_VERSION})**\n"
            f"• **System Status:** {status_indicator}\n"
            f"• **Target Pair:** {self.symbol} ({self.timeframe})\n"
            f"• **AI Confidence Score:** `{self.last_confidence * 100:.1f}%`\n"
            f"• **Active CMO Upper/Lower Thresholds:** `{self.state_manager.get('cmo_upper', 60.0)}` / `{self.state_manager.get('cmo_lower', -60.0)}`\n\n"
            "📜 **ล่าสุด DECISION LOGS:**\n"
            f"{formatted_logs}"
        )
        await self.reply_to_telegram(chat_id, status_message)


    async def cmd_balance(self, chat_id: int) -> None:
        if self.dry_run or not self.exchange:
            val = self.state_manager.get("balance", 10000.0)
            await self.reply_to_telegram(chat_id, f"💵 *Wallet Balance (Dry Run)*\n• *Estimated Balance:* `${val:,.2f}`")
            return
            
        try:
            balance_data = await self.exchange.fetch_balance({'type': 'unified'})
            total_wallet_balance = 0.0
            total_equity = 0.0
            
            # Extract totalWalletBalance and totalEquity
            if 'info' in balance_data and 'result' in balance_data['info'] and 'list' in balance_data['info']['result'] and len(balance_data['info']['result']['list']) > 0:
                list_entry = balance_data['info']['result']['list'][0]
                total_wallet_balance = float(list_entry.get('totalWalletBalance', 0.0) or 0.0)
                total_equity = float(list_entry.get('totalEquity', 0.0) or 0.0)
            
            # Available margin (USDT free balance or totalAvailableBalance)
            available_margin = balance_data.get('USDT', {}).get('free', 0.0)
            
            balance_msg = (
                f"💰 *Bybit UTA Balance*\n"
                f"• *Total Wallet Balance:* `${total_wallet_balance:,.2f}`\n"
                f"• *Total Equity:* `${total_equity:,.2f}`\n"
                f"• *Available Margin (USDT):* `${available_margin:,.2f}`"
            )
            await self.reply_to_telegram(chat_id, balance_msg)
        except Exception as e:
            await self.reply_to_telegram(chat_id, f"❌ *Failed to fetch balance:* {e}")

    async def cmd_position(self, chat_id: int) -> None:
        # Check local position
        positions = self.state_manager.get("positions", {})
        pos_data = positions.get(self.symbol)
        
        has_local = pos_data and pos_data.get("size", 0.0) > 0
        
        # Also query from exchange if live
        exchange_pos = None
        if not self.dry_run and self.exchange:
            try:
                positions_info = await self.exchange.fetch_positions(symbols=['XRP/USDT'], params={'category': 'linear'})
                for p in positions_info:
                    p_symbol = p.get('symbol', '')
                    if p_symbol == self.symbol or p_symbol.split(':')[0] == self.symbol or p_symbol.split(':')[0].replace('/', '') == self.symbol.replace('/', ''):
                        contracts = float(p.get('contracts', 0.0) or p.get('size', 0.0) or 0.0)
                        if contracts > 0:
                            exchange_pos = p
                            break
            except Exception as e:
                print(f"[TELEGRAM POSITION ERROR] Failed to fetch from Bybit: {e}")
                
        # Fetch current price
        try:
            ticker = await self.fetch_ticker_safe()
            current_price = ticker["last"]
        except Exception:
            current_price = 0.0
            
        if not has_local and not exchange_pos:
            await self.reply_to_telegram(chat_id, f"📊 *Active Positions ({self.symbol})*\n• *Status:* No active positions.")
            return
            
        # Extract variables
        if exchange_pos:
            entry_price = float(exchange_pos.get('entryPrice') or 0.0)
            size = float(exchange_pos.get('contracts', 0.0) or exchange_pos.get('size', 0.0) or 0.0)
            side = exchange_pos.get('side', '').upper()
            pnl = float(exchange_pos.get('unrealizedPnl', 0.0) or 0.0)
        else:
            entry_price = pos_data["entry_price"]
            size = pos_data["size"]
            side = pos_data["side"]
            pnl = size * (current_price - entry_price) if side == "BUY" else size * (entry_price - current_price)
            
        # Target/SL from local pos_data or fallback
        if has_local:
            stop_loss = pos_data["stop_loss"]
            target1 = pos_data["target1"]
            target1_hit = pos_data.get("target1_hit", False)
            
            dist_sl = abs(stop_loss - current_price)
            dist_t1 = abs(target1 - current_price)
            
            t1_status = "Hit ✅" if target1_hit else f"${target1:.2f} (Dist: ${dist_t1:.2f})"
            sl_status = f"${stop_loss:.2f} (Dist: ${dist_sl:.2f})"
        else:
            t1_status = "Not set locally"
            sl_status = "Not set locally"
            
        pos_msg = (
            f"📊 *Active Position: {side} {self.symbol}*\n"
            f"• *Size:* `{size:.4f}`\n"
            f"• *Entry Price:* `${entry_price:.2f}`\n"
            f"• *Current Price:* `${current_price:.2f}`\n"
            f"• *Unrealized PnL:* `${pnl:.2f}`\n"
            f"• *Stop Loss (SL):* `{sl_status}`\n"
            f"• *Target 1 (TP1):* `{t1_status}`"
        )
        await self.reply_to_telegram(chat_id, pos_msg)

    async def cmd_stats(self, chat_id: int) -> None:
        try:
            import sqlite3
            conn = sqlite3.connect("trades.db")
            cursor = conn.cursor()
            cursor.execute("SELECT pnl FROM trades WHERE pnl != 0.0")
            pnls = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            total = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            losses = sum(1 for p in pnls if p < 0)
            win_rate = (wins / total * 100) if total > 0 else 0.0
            
            stats_msg = (
                f"📈 *Trade Statistics (SQLite)*\n"
                f"• *Total Trades:* `{total}`\n"
                f"• *Wins (Profit > 0):* `{wins}`\n"
                f"• *Losses (Profit < 0):* `{losses}`\n"
                f"• *Win Rate:* `{win_rate:.1f}%`"
            )
            await self.reply_to_telegram(chat_id, stats_msg)
        except Exception as e:
            await self.reply_to_telegram(chat_id, f"❌ *Failed to query stats:* {e}")

    # Advanced Resilient Network Request Wrapper for Windows 11
    async def execute_safe_ccxt_call(self, coro_func, *args, **kwargs):
        max_retries = 5
        for attempt in range(1, max_retries + 1):
            try:
                return await coro_func(*args, **kwargs)
            except (ccxt.ExchangeNotAvailable, ccxt.RequestTimeout, Exception) as err:
                err_str = str(err)
                if attempt == max_retries:
                    logger.error(f"❌ [CRITICAL NETWORK DROP] CCXT call failed after {max_retries} attempts: {err_str}")
                    raise err
                logger.warning(f"⚠️ [NETWORK RETRY {attempt}/{max_retries}] Retrying in {attempt * 2}s due to: {err_str}")
                await asyncio.sleep(attempt * 2)

    async def place_native_maker_tp_with_retry(self, symbol, side, amount, entry_price):
        tp_pct = 0.004 if 'XRP' in symbol else 0.005
        tp_side = 'sell' if side.upper() == 'BUY' else 'buy'
        tp_price = round(entry_price * (1.0 + tp_pct), 4) if side.upper() == 'BUY' else round(entry_price * (1.0 - tp_pct), 4)

        # Active Order Cleanup before placing new TP limit order
        await self.order_manager.cancel_active_close_orders(symbol)

        # Retry loop to allow Bybit exchange state to sync position
        for attempt in range(1, 4):
            try:
                await asyncio.sleep(0.5 * attempt) # Progressive backoff
                # Verify position exists before sending reduceOnly
                positions = await self.exchange.fetch_positions([symbol], params={'category': 'linear'})
                pos_size = float(positions[0].get('contracts', 0) if positions else 0)

                if pos_size > 0:
                    tp_params = {'category': 'linear', 'reduceOnly': True, 'postOnly': True}
                    order = await self.exchange.create_order(
                        symbol=symbol,
                        type='limit',
                        side=tp_side,
                        amount=amount,
                        price=tp_price,
                        params=tp_params
                    )
                    logger.info(f"🎯 [NATIVE MAKER TP SUCCESS] Placed {tp_side} at {tp_price}")
                    if order and isinstance(order, dict) and 'id' in order:
                        self.state_manager.set_close_order_id(symbol, order['id'])
                    return order
            except Exception as tp_err:
                logger.warning(f"⚠️ TP placement attempt {attempt} failed: {str(tp_err)}")
        logger.error("❌ Failed to place Native Maker TP after 3 sync attempts.")

    def recalculate_stats(self):
        try:
            if hasattr(self.db_manager, 'queue'):
                self.db_manager.queue.join()
            import sqlite3
            conn = sqlite3.connect("trades.db")
            cursor = conn.cursor()
            cursor.execute("SELECT pnl FROM trades WHERE pnl != 0.0")
            pnls = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            total = len(pnls)
            wins = sum(1 for p in pnls if p > 0)
            win_rate = (wins / total * 100.0) if total > 0 else 0.0
            
            self.state_manager.update({
                "trades_count": total,
                "win_rate": win_rate
            })
            logger.info(f"📊 [STATS SYNC] Recalculated stats: Total Trades = {total}, Win Rate = {win_rate:.1f}%")
        except Exception as e:
            logger.error(f"❌ Failed to recalculate stats: {e}")

    async def get_available_balance(self) -> float:
        """Fetch available margin balance from exchange. Fallback to state manager if dry run or fails."""
        if self.dry_run or not self.exchange:
            val = self.state_manager.get("balance", 10000.0)
            self.available_capital = val
            self.wallet_free_margin = val
            self.wallet_total_equity = val
            return val
        
        # Upgrade Bybit UTA Balance Retrieval Logic inside TradingEngine
        try:
            uta_response = await self.execute_safe_ccxt_call(self.exchange.fetch_balance, params={'type': 'unified'})

            # Parse using multiple fallback keys safely
            result_list = uta_response.get('info', {}).get('result', {}).get('list', [{}])[0]

            total_eq = safe_float(result_list.get('totalEquity'), 0.0)
            avail_bal = safe_float(result_list.get('totalAvailableBalance'), 0.0)

            # Fallback to CCXT standardized array if native API info structure is empty
            if avail_bal == 0.0 and 'USDT' in uta_response:
                avail_bal = safe_float(uta_response['USDT'].get('free'), 0.0)
                total_eq = safe_float(uta_response['USDT'].get('total'), 0.0)

            self.wallet_total_equity = total_eq
            self.available_capital = avail_bal
            self.wallet_free_margin = avail_bal
            logger.info(f"🔍 [UTA ONLINE SYNC] Verified Equity: {total_eq} USD | Free Margin: {avail_bal} USDT")
            self.state_manager.set("balance", avail_bal)
            return avail_bal
        except Exception as uta_err:
            logger.error(f"❌ Failed to query clean Bybit UTA margins: {str(uta_err)}")
            self.available_capital = 0.0
            self.wallet_free_margin = 0.0
            self.wallet_total_equity = 0.0
            return self.state_manager.get("balance", 10000.0)

    def run_walk_forward_optimization(self) -> None:
        """Walk-Forward Optimizer executing every 24 hours to tune CMO thresholds based on 7-day rolling win rate."""
        current_time = time.time()
        last_opt = self.state_manager.get("last_optimization_time", 0.0)
        
        if current_time - last_opt < 86400.0:
            return
            
        logger.info("⚙️ [OPTIMIZER] Starting Walk-Forward Parameter Auto-Tuning...")
        import sqlite3
        seven_days_ago = current_time - (7 * 86400)
        try:
            conn = sqlite3.connect("trades.db")
            cursor = conn.cursor()
            cursor.execute("SELECT pnl FROM trades WHERE timestamp >= ? AND pnl != 0.0", (seven_days_ago,))
            pnls = [row[0] for row in cursor.fetchall()]
            conn.close()
            
            total = len(pnls)
            if total > 0:
                wins = sum(1 for p in pnls if p > 0)
                win_rate = (wins / total) * 100.0
            else:
                win_rate = 50.0  # Default to 50% if no trades
                
            # Dynamically adjust CMO Thresholds (stricter -65.0 minimum for XRP Long)
            if win_rate >= 70.0:
                cmo_upper = 55.0
                cmo_lower = -65.0
            elif win_rate >= 50.0:
                cmo_upper = 60.0
                cmo_lower = -65.0
            elif win_rate >= 40.0:
                cmo_upper = 68.0
                cmo_lower = -68.0
            else:
                cmo_upper = 75.0
                cmo_lower = -75.0
                
            self.state_manager.update({
                "cmo_upper": cmo_upper,
                "cmo_lower": cmo_lower,
                "last_optimization_time": current_time
            })
            
            self.db_manager.log_message(
                "INFO",
                f"⚙️ [OPTIMIZER SUCCESS] Walk-Forward Optimization completed. 7d Win Rate: {win_rate:.1f}% | New CMO limits: {cmo_lower} / {cmo_upper}",
                "TradingEngine"
            )
            
            # Send Telegram alert for self-learning
            opt_msg = f"⚙️ *[OPTIMIZER SELF-LEARNING]*\nWalk-Forward Optimization completed.\n• *7d Win Rate:* `{win_rate:.1f}%`\n• *New CMO thresholds:* `{cmo_lower}` / `{cmo_upper}`"
            try:
                loop = asyncio.get_running_loop()
                if loop.is_running():
                    loop.create_task(self.send_telegram_alert_safe(opt_msg))
            except RuntimeError:
                pass
        except Exception as e:
            logger.error(f"❌ Walk-Forward Optimization failed: {e}")

    @ccxt_retry()
    async def fetch_ohlcv_safe(self, limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV candle data safely using aggressive 5-attempt retry/backoff wrappers."""
        # Strict Bybit V5 Linear Derivative Kline Override
        formatted_symbol = self.symbol.replace('/', '') # Converts XRP/USDT to XRPUSDT
        params = {
            'category': 'linear' # Forces linear futures market stream
        }
        candles = []
        max_retries = 5
        delay = 2.0
        for attempt in range(1, max_retries + 1):
            try:
                candles = await self.exchange.fetch_ohlcv(
                    symbol=formatted_symbol,
                    timeframe=self.timeframe,
                    limit=limit,
                    params=params
                )
                if candles and len(candles) > 0:
                    break
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RequestTimeout, aiohttp.ClientError, OSError, Exception) as fetch_err:
                logger.warning(f"⚠️ [KLINE RETRIEVAL RETRY {attempt}/{max_retries}] Exchange fetch delayed by network drop: {fetch_err}")
                if attempt < max_retries:
                    await asyncio.sleep(delay)
                    delay *= 2
                else:
                    logger.error(f"❌ [KLINE RETRIEVAL FAILED] Failed to fetch OHLCV candles after {max_retries} attempts: {fetch_err}")
                    candles = []

        df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
        if not df.empty:
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms")
        return df

    @ccxt_retry()
    async def fetch_ticker_safe(self) -> Dict[str, Any]:
        """Fetch real-time ticker data safely using retry/backoff wrappers."""
        return await self.exchange.fetch_ticker(self.symbol, params={'category': 'linear'})

    async def run_strategy_tick(self) -> None:
        """The main analysis evaluation logic executed at candle closes."""
        self.db_manager.log_message("INFO", f"Evaluating strategy tick for {self.symbol}...", "TradingEngine")
        
        # Hard Cap Pyramiding Protection
        MAX_LAYERS = 3
        base_unit = 3.0 if 'XRP' in self.symbol else 0.1
        max_allowable_size = base_unit * MAX_LAYERS

        if abs(self.current_position_size) >= max_allowable_size:
            logger.info(f"🛡️ [MARGIN CAP REACHED] Position size {self.current_position_size} hits ceiling. Blocking further entries.")
            self.db_manager.log_message("INFO", f"🛡️ [MARGIN CAP REACHED] Position size {self.current_position_size} hits ceiling. Blocking further entries.", "TradingEngine")
            return
            
        # Time-Based Emergency Release (18 Minutes / 6 Klines)
        if self.current_position_size != 0:
            self.bars_held += 1
            if self.bars_held >= 6:
                self.db_manager.log_message("WARNING", f"⏱️ [TIME-BASED EXIT] Position held for {self.bars_held} bars (18 mins). Closing position to liberate UTA Free Margin.", "TradingEngine")
                if hasattr(self, 'close_position_emergency'):
                    await self.close_position_emergency()
                self.bars_held = 0
        else:
            self.bars_held = 0
        
        try:
            df = await self.fetch_ohlcv_safe(limit=300)
            if len(df) < 50:
                self.db_manager.log_message("WARNING", "Insufficient historical candles to run strategy.", "TradingEngine")
                return
                
            # Compute Indicators
            cmo = calculate_cmo(df)
            vwap, vwap_upper, vwap_lower = calculate_vwap_bands(df)
            tii = calculate_tii(df)
            ema200 = calculate_ema(df, period=200)
            regimes = classify_market_regime(df)
            
            latest_idx = len(df) - 1
            close_price = df.iloc[latest_idx]["close"]
            latest_cmo = cmo.iloc[latest_idx]
            latest_tii = tii.iloc[latest_idx]
            latest_ema200 = float(ema200.iloc[latest_idx]) if not pd.isna(ema200.iloc[latest_idx]) else 0.0
            latest_regime = regimes.iloc[latest_idx]
            
            # Compute current ATR for risk targets
            # True range is already computed inside VWAP bands, let's pull it directly:
            high = df["high"]
            low = df["low"]
            close_prev = df["close"].shift(1)
            tr = pd.concat([high - low, (high - close_prev).abs(), (low - close_prev).abs()], axis=1).max(axis=1)
            latest_atr = tr.rolling(window=14).mean().iloc[latest_idx]
            
            indicators_data = {
                "cmo": float(latest_cmo),
                "tii": float(latest_tii),
                "vwap": float(vwap.iloc[latest_idx]),
                "vwap_upper": float(vwap_upper.iloc[latest_idx]),
                "vwap_lower": float(vwap_lower.iloc[latest_idx]),
                "ema200": latest_ema200,
                "atr": float(latest_atr)
            }
            
            self.db_manager.log_decision(self.symbol, latest_regime, "NONE", "Periodic evaluation tick", close_price, indicators_data)
            
            # Feature extraction for Adaptive ML model
            vol_sma = df['volume'].rolling(window=14).mean().iloc[latest_idx]
            volume_delta = float(df.iloc[latest_idx]["volume"] / (vol_sma + 1e-9))
            vwap_val = float(vwap.iloc[latest_idx])
            vwap_dist = float((close_price - vwap_val) / (latest_atr + 1e-9))
            
            brain_features = {
                "cmo": float(latest_cmo),
                "vwap_dist": vwap_dist,
                "volume_delta": volume_delta,
                "volatility": float(latest_atr / (close_price + 1e-9))
            }
            confidence = self.brain.predict_proba_one(brain_features)
            self.last_confidence = confidence
            
            # Check for entries and Pyramiding risk limits
            active_position = self.state_manager.get("positions", {}).get(self.symbol)
            is_pyramiding = False
            
            if active_position and active_position.get("size", 0.0) > 0:
                layers_count = active_position.get("layers_count", 1)
                last_entry_idx = active_position.get("last_entry_bar_index", 0)
                last_entry_price = active_position.get("last_entry_price", active_position.get("entry_price", 0.0))
                
                # Strict maximum layer limits: Max 5 layers per asset direction
                if layers_count >= 5:
                    return
                
                # Entry cooldown logic: Minimum 3 candle bars OR minimum price spacing of 1.5 * ATR(14)
                bars_since_last = latest_idx - last_entry_idx
                price_dist = abs(close_price - last_entry_price)
                if bars_since_last < 3 and price_dist < (1.5 * latest_atr):
                    return
                
                is_pyramiding = True
                
            # Entry logic
            decision_action = None
            entry_side = None
            stop_loss = 0.0
            target1 = 0.0
            
            # Dynamic CMO thresholds updated by Walk-Forward Optimizer (stricter -65.0 default for XRP Long)
            cmo_upper_thresh = self.state_manager.get("cmo_upper", 60.0)
            cmo_lower_thresh = self.state_manager.get("cmo_lower", -65.0)
            
            if "IMPULSE" in latest_regime:
                # Breakout entry logic
                if latest_regime == "IMPULSE_BULL":
                    # Buy momentum breakout (Allowed during validated IMPULSE_BULL regime)
                    decision_action = "BUY_BREAKOUT"
                    entry_side = "BUY"
                    stop_loss = close_price - (2.5 * latest_atr)
                    target1 = vwap_val  # Dynamic TP target (VWAP Median Line)
                elif latest_regime == "IMPULSE_BEAR":
                    # Short momentum breakout
                    decision_action = "SELL_BREAKOUT"
                    entry_side = "SELL"
                    stop_loss = close_price + (2.5 * latest_atr)
                    target1 = vwap_val  # Dynamic TP target (VWAP Median Line)
            elif latest_regime == "MEAN_REVERSION":
                # Rebound trade logic (Tightened Filter with EMA 200 trend filter)
                if close_price <= vwap_lower.iloc[latest_idx] and latest_cmo < cmo_lower_thresh:
                    # STRICT RULE: Do NOT generate Long MEAN_REVERSION signals if current close price < EMA 200.
                    # Allow Longs ONLY when price > EMA 200 OR during validated IMPULSE_BULL regime.
                    if close_price > latest_ema200:
                        decision_action = "BUY_REVERSION"
                        entry_side = "BUY"
                        stop_loss = close_price - (2.5 * latest_atr)
                        target1 = vwap_val  # Dynamic TP target (VWAP Median Line)
                    else:
                        logger.info(
                            f"🚫 [EMA 200 TREND FILTER] Blocked Long MEAN_REVERSION: Close price ({close_price:.4f}) < EMA 200 ({latest_ema200:.4f})"
                        )
                elif close_price >= vwap_upper.iloc[latest_idx] and latest_cmo > cmo_upper_thresh:
                    decision_action = "SELL_REVERSION"
                    entry_side = "SELL"
                    stop_loss = close_price + (2.5 * latest_atr)
                    target1 = vwap_val  # Dynamic TP target (VWAP Median Line)
                    
            # Loss Cooldown Mechanism (Stop-Loss Lockout): 5 bars (15 minutes on 3m timeframe)
            tf_mins = int(self.timeframe.replace("m", "")) if "m" in self.timeframe else 3
            cooldown_seconds = 5 * tf_mins * 60
            if entry_side == "BUY" and self.state_manager.is_long_cooldown_active(cooldown_seconds=cooldown_seconds):
                logger.info(f"⏳ [LOSS COOLDOWN LOCKOUT] Long signal ({decision_action}) blocked: 5-bar ({tf_mins * 5} min) lockout active after previous Long loss.")
                self.db_manager.log_message("WARNING", f"⏳ [LOSS COOLDOWN LOCKOUT] Long entry blocked for 5 bars ({tf_mins * 5} mins) following a loss.", "TradingEngine")
                decision_action = None
                entry_side = None
                    
            # Disable counter-trend entries (or opposite pyramiding direction)
            if is_pyramiding and decision_action:
                if entry_side != active_position.get("side"):
                    decision_action = None  # Block opposite direction entries if currently in position
                    
            if decision_action and entry_side:
                # 1. Enforce Daily Circuit Breaker (Capital Preservation)
                equity_now = getattr(self, "wallet_total_equity", 0.0) or getattr(self, "available_capital", 10.0)
                can_trade, breaker_reason = self.profit_allocator.can_trade_today(equity_now)
                if not can_trade:
                    logger.info(f"⏳ {breaker_reason}")
                    self.db_manager.log_message("WARNING", breaker_reason, "TradingEngine")
                    return

                # 2. Microstructure Edge: Orderbook Imbalance (OBI) Filter
                if self.microstructure_config.get("orderbook_imbalance_check", True):
                    obi_ok, obi_val, obi_msg = await self.order_manager.check_orderbook_imbalance(
                        self.symbol,
                        entry_side,
                        depth=self.microstructure_config.get("depth_levels", 10),
                        threshold=self.microstructure_config.get("imbalance_threshold", 0.35)
                    )
                    if not obi_ok:
                        self.db_manager.log_message("WARNING", f"Trade blocked by Orderbook Imbalance Guard: {obi_msg}", "TradingEngine")
                        return

                # Enforce strict maximum leverage constraints from environment
                lev_allowed, safe_leverage, lev_reason = self.risk_manager.validate_leverage(self.leverage)
                if not lev_allowed:
                    self.db_manager.log_message("WARNING", f"Trade rejected by RiskManager leverage policy: {lev_reason}", "TradingEngine")
                    return

                # Run Pre-flight liquidation check
                allowed, final_leverage, liq_price = self.order_manager.validate_leverage_and_liq(
                    close_price, stop_loss, safe_leverage, entry_side
                )
                
                if not allowed:
                    self.db_manager.log_message("WARNING", f"Trade rejected by Pre-flight Liquidation Guard for {self.symbol}.", "TradingEngine")
                    return
                    
                # Configure margin mode and leverage
                setup_ok = await self.order_manager.setup_isolated_margin(self.symbol, final_leverage)
                if not setup_ok:
                    self.db_manager.log_message("ERROR", f"Failed to configure Isolated margin mode. Aborting entry.", "TradingEngine")
                    return

                # Pre-flight balance check
                guard = await self.order_manager.validate_pre_flight_balance(self.symbol, close_price, final_leverage)
                if not guard["is_safe"]:
                    self.db_manager.log_message("WARNING", f"❌ [ORDER REJECTED] {guard['reason']}", "TradingEngine")
                    return  # ระงับการเทรดไม้นั้นทันทีอย่างปลอดภัย
                    
                # Calculate size based on 1% equity risk (dynamically fetched from exchange if live)
                capital = await self.get_available_balance()
                position_size = await self.order_manager.calculate_position_size(capital, close_price, stop_loss, final_leverage, self.symbol)
                
                if position_size <= 0:
                    self.db_manager.log_message("ERROR", "Calculated position size is 0. Aborting entry.", "TradingEngine")
                    return

                # Enforce strict notional exposure limits via RiskManager
                notional_ok, notional_msg = self.risk_manager.validate_notional_exposure(
                    self.symbol, position_size, close_price
                )
                if not notional_ok:
                    self.db_manager.log_message("WARNING", f"Trade aborted by RiskManager: {notional_msg}", "TradingEngine")
                    return
                    
                # Execute Trade
                self.db_manager.log_message("INFO", f"Executing Entry {entry_side}: Size={position_size:.4f}, Price={close_price:.2f}, SL={stop_loss:.2f}, Target1={target1:.2f}", "TradingEngine")
                
                if not self.dry_run:
                    # Live Order execution via CCXT
                    try:
                        # Force strict integration with Bybit Unified Derivative infrastructure
                        regime_cfg = RISK_CONTROL_REGIME['XRP_USDT']
                        symbol_futures = "XRPUSDT" # Standardize Bybit V5 string structure

                        order_params = {
                            'category': 'linear',
                            'timeInForce': 'PostOnly'
                        }
                        order_type = 'limit'

                        # Execute order natively through unified ccxt framework
                        response = await self.exchange.create_order(
                            symbol=symbol_futures,
                            type=order_type,
                            side=entry_side.lower(),
                            amount=position_size,
                            price=close_price,
                            params=order_params
                        )
                        self.db_manager.log_message("INFO", f"Live entry order placed successfully. ID: {response['id']}", "TradingEngine")
                        
                        # Attach Immediate Maker Limit TP Order on Fill
                        try:
                            await self.place_native_maker_tp_with_retry(symbol_futures, entry_side, position_size, close_price)
                        except Exception as tp_err:
                            self.db_manager.log_message("WARNING", f"⚠️ Failed to place Native Maker TP: {tp_err}", "TradingEngine")
                    except Exception as e:
                        self.db_manager.log_message("ERROR", f"Failed to place live order: {e}", "TradingEngine")
                        return
                
                # Save position states (Support Pyramiding)
                positions = self.state_manager.get("positions", {})
                if is_pyramiding:
                    pos_data = positions.get(self.symbol)
                    old_size = pos_data["size"]
                    old_entry_price = pos_data["entry_price"]
                    new_size = old_size + position_size
                    new_entry_price = ((old_entry_price * old_size) + (close_price * position_size)) / new_size
                    
                    pos_data["size"] = new_size
                    pos_data["remaining_size"] += position_size
                    pos_data["entry_price"] = new_entry_price
                    pos_data["layers_count"] = pos_data.get("layers_count", 1) + 1
                    pos_data["last_entry_bar_index"] = latest_idx
                    pos_data["last_entry_price"] = close_price
                    # Keep SL/TP from the first entry to ensure strategy consistency
                else:
                    pos_data = {
                        "symbol": self.symbol,
                        "side": entry_side,
                        "entry_price": close_price,
                        "size": position_size,
                        "remaining_size": position_size,
                        "stop_loss": stop_loss,
                        "target1": target1,
                        "target1_hit": False,
                        "trailing_stop": stop_loss,
                        "highest_price": close_price,
                        "lowest_price": close_price,
                        "atr": latest_atr,
                        "leverage": final_leverage,
                        "liq_price": liq_price,
                        "regime": latest_regime,
                        "timestamp": time.time(),
                        "layers_count": 1,
                        "last_entry_bar_index": latest_idx,
                        "last_entry_price": close_price,
                        "entry_features": brain_features,  # Save features for ML training
                        "breakeven_locked": False
                    }
                
                positions[self.symbol] = pos_data
                self.state_manager.set("positions", positions)
                
                self.db_manager.log_trade(self.symbol, entry_side, close_price, position_size, 0.0, latest_regime, f"ENTRY_{decision_action}")
                
                # Rich Telegram Alert Card
                side_emoji = "🟢" if entry_side == "BUY" else "🔴"
                msg = (
                    f"{side_emoji} *[SCALPING ENTRY - {entry_side}]*\n"
                    f"━━━━━━━━━━━━━━━━━━\n"
                    f"• *Symbol:* `{self.symbol}` ({self.timeframe})\n"
                    f"• *Entry Price:* `${close_price:.4f}`\n"
                    f"• *Position Size:* `{position_size:.2f} {self.symbol.split('/')[0]}` ({final_leverage}x)\n"
                    f"• *Take Profit 1:* `${target1:.4f}`\n"
                    f"• *Stop Loss:* `${stop_loss:.4f}`\n"
                    f"• *AI Confidence:* `{self.last_confidence * 100:.1f}%`\n"
                    f"• *Market Regime:* `{latest_regime}`\n"
                    f"• *CMO Value:* `{latest_cmo:.1f}`\n"
                    f"🛡️ *Breakeven Guard:* Active (+0.25% or 50% TP distance)"
                )
                await self.send_telegram(msg)
                
        except Exception as e:
            self.db_manager.log_message("ERROR", f"Error in strategy tick execution: {e}\n{traceback.format_exc()}", "TradingEngine")

    async def send_exit_alert_card(
        self,
        symbol: str,
        side: str,
        exit_price: float,
        pnl: float,
        exit_type: str,
        alloc: dict,
        win_rate: float
    ) -> None:
        """Sends rich telegram exit cards with 1/3 vault breakdown and circuit breaker alerts."""
        if pnl > 0:
            vault_msg = (
                f"🎯 *[PROFIT REALIZED - {exit_type}]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *Symbol:* `{symbol}` ({side})\n"
                f"• *Exit Price:* `${exit_price:.4f}`\n"
                f"• *Net Profit:* `+${pnl:.4f} USDT`\n"
                f"──────────────────\n"
                f"🏦 *Capital Vault (1/3 เก็บปลอดภัย):* `+${alloc.get('vault_cut', 0.0):.4f} USDT`\n"
                f"*(สะสมใน Vault รวม: ${alloc.get('total_vault', 0.0):.4f} USDT)*\n"
                f"📈 *Reinvested (2/3 ทบต้นลุยต่อ):* `+${alloc.get('reinvest_cut', 0.0):.4f} USDT`\n"
                f"• *Daily PnL วันนี้:* `${alloc.get('daily_pnl', 0.0):.4f} USDT`\n"
                f"• *Win Rate:* `{win_rate:.1f}%`"
            )
            await self.send_telegram(vault_msg)
        else:
            loss_msg = (
                f"🛑 *[POSITION CLOSED - {exit_type}]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"• *Symbol:* `{symbol}` ({side})\n"
                f"• *Exit Price:* `${exit_price:.4f}`\n"
                f"• *PnL:* `${pnl:.4f} USDT`\n"
                f"• *Daily PnL วันนี้:* `${alloc.get('daily_pnl', 0.0):.4f} USDT`\n"
                f"• *Win Rate:* `{win_rate:.1f}%`"
            )
            await self.send_telegram(loss_msg)

        if alloc.get("daily_loss_limit_hit", False):
            breaker_msg = (
                f"🚨 *[DAILY CIRCUIT BREAKER TRIGGERED]*\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"ยอดขาดทุนสะสมวันนี้ (${alloc.get('daily_pnl', 0.0):.2f}) ถึงเกณฑ์ความเสี่ยงสูงสุด (-{PROFIT_ALLOCATION_CONFIG.get('daily_loss_limit_pct', 0.03)*100:.1f}%)!\n"
                f"ระบบหยุดการเปิดออเดอร์ใหม่เพื่อรักษาทุนตลอดทั้งวันที่เหลือจนถึง 00:00 UTC"
            )
            await self.send_telegram(breaker_msg)

    async def close_all_positions(self, symbol: Optional[str] = None, reason: str = "EMERGENCY_CIRCUIT_BREAKER") -> bool:
        """
        Emergency circuit breaker to hard-cut positions immediately and independently of exchange liquidations.
        Cancels active open orders, executes market reduce-only orders, syncs DB/state, and alerts via Telegram.
        """
        return await self.risk_manager.close_all_positions(self, symbol=symbol, reason=reason)

    async def close_position_emergency(self) -> None:
        """Emergency Time-Based ReduceOnly Market Close."""
        await self.close_all_positions(symbol=self.symbol, reason="TIME_BASED_EMERGENCY_RELEASE")

    async def check_active_positions(self) -> None:
        """Real-time high frequency tracker logic evaluating targets and trailing stops."""
        positions = self.state_manager.get("positions", {})
        pos_data = positions.get(self.symbol)
        
        if not pos_data or pos_data.get("size", 0.0) == 0:
            return
            
        if not self.dry_run:
            # Advanced Resilient Position Monitor Override
            try:
                active_positions = await self.execute_safe_ccxt_call(
                    self.exchange.fetch_positions,
                    symbols=[self.symbol],
                    params={'category': 'linear'}
                )

                exchange_size = 0.0
                if active_positions and len(active_positions) > 0:
                    current_pos = active_positions[0]
                    pos_size = float(current_pos['contracts'])
                    entry_px = float(current_pos['entryPrice'])
                    unrealized_pnl = float(current_pos['unrealizedPnl'])

                    # Standardize dynamic internal state sync
                    self.current_position_size = pos_size
                    self.position_entry_price = entry_px
                    
                    exchange_size = pos_size

                if exchange_size == 0.0:
                    self.db_manager.log_message("WARNING", f"Position for {self.symbol} is already closed on the exchange. Syncing local state.", "TradingEngine")
                    if pos_data and pos_data.get("size", 0.0) > 0:
                        try:
                            ticker = await self.fetch_ticker_safe()
                            current_price = ticker["last"]
                        except Exception:
                            current_price = 0.0
                            
                        side = pos_data["side"]
                        entry_price = pos_data["entry_price"]
                        remaining_size = pos_data["remaining_size"]
                        target1 = pos_data["target1"]
                        stop_loss = pos_data["stop_loss"]
                        regime = pos_data.get("regime", "UNKNOWN")
                        
                        exit_price = current_price
                        exit_type = "EXCHANGE_CLOSE"
                        
                        try:
                            my_trades = await self.exchange.fetch_my_trades(self.symbol, limit=5)
                            if my_trades:
                                exit_side_expected = "sell" if side == "BUY" else "buy"
                                for t in reversed(my_trades):
                                    if t.get('side', '').lower() == exit_side_expected:
                                        exit_price = float(t.get('price', current_price))
                                        break
                        except Exception as trade_fetch_err:
                            logger.warning(f"⚠️ Could not fetch actual exit trade details: {trade_fetch_err}")
                            if side == "BUY":
                                if current_price > 0.0 and abs(current_price - target1) < abs(current_price - stop_loss):
                                    exit_price = target1
                                    exit_type = "TAKE_PROFIT_1"
                                else:
                                    exit_price = stop_loss
                                    exit_type = "STOP_LOSS"
                            else:
                                if current_price > 0.0 and abs(current_price - target1) < abs(current_price - stop_loss):
                                    exit_price = target1
                                    exit_type = "TAKE_PROFIT_1"
                                else:
                                    exit_price = stop_loss
                                    exit_type = "STOP_LOSS"
                                    
                        pnl = remaining_size * (exit_price - entry_price) if side == "BUY" else remaining_size * (entry_price - exit_price)
                        self.db_manager.log_trade(self.symbol, "SELL" if side == "BUY" else "BUY", exit_price, remaining_size, pnl, regime, exit_type)
                        self.state_manager.record_closed_order(self.symbol, side, pnl, exit_type)
                        logger.info(f"📊 [AUTO SYNC EXIT] Synced exit trade: {exit_type} at {exit_price:.4f}, PnL: {pnl:.4f}")
                        
                        # Capital Vault Profit Allocation (1/3 Vault, 2/3 Reinvestment)
                        alloc = self.profit_allocator.allocate_trade(
                            self.symbol, side, exit_price, pnl, exit_type,
                            current_equity=getattr(self, 'wallet_total_equity', 0.0)
                        )
                        self.recalculate_stats()
                        win_rate = self.state_manager.get("win_rate", 0.0)
                        await self.send_exit_alert_card(self.symbol, side, exit_price, pnl, exit_type, alloc, win_rate)
                        
                        # Train Adaptive ML Agent & Notify
                        features = pos_data.get("entry_features")
                        if features:
                            label = 1 if (pos_data.get("target1_hit", False) or pnl > 0) else 0
                            self.brain.learn_one(features, label)
                            self.state_manager.set("brain_model_state", self.brain.get_state())
                            
                            label_desc = "PROFITABLE (1)" if label == 1 else "UNPROFITABLE (0)"
                            learn_msg = (
                                f"🧠 *[AI BRAIN SELF-LEARNING]*\n"
                                f"Learned from Bybit closed trade:\n"
                                f"• *Features:* CMO={features.get('cmo'):.2f}, VWAP Dist={features.get('vwap_dist'):.2f}, Volatility={features.get('volatility'):.4f}\n"
                                f"• *Outcome:* `{label_desc}`\n"
                                f"• *Action:* Model updated."
                            )
                            await self.send_telegram(learn_msg)
                            
                    await self.order_manager.cancel_active_close_orders(self.symbol)
                    self.state_manager.clear_active_position()
                    self.recalculate_stats()
                    return
            except Exception as pos_monitor_err:
                logger.warning(f"⚠️ Dynamic position tracking buffer sync delayed: {str(pos_monitor_err)}")
            
        try:
            ticker = await self.fetch_ticker_safe()
            current_price = ticker["last"]
            current_high = ticker.get("high", current_price) or current_price
            current_low = ticker.get("low", current_price) or current_price
            
            side = pos_data["side"]
            entry_price = pos_data["entry_price"]
            size = pos_data["size"]
            remaining_size = pos_data["remaining_size"]
            stop_loss = pos_data["stop_loss"]
            target1 = pos_data["target1"]
            target1_hit = pos_data["target1_hit"]
            trailing_stop = pos_data["trailing_stop"]
            atr = pos_data["atr"]
            regime = pos_data["regime"]
            
            # Early Breakeven Protection (Locks Stop Loss to Breakeven + fee buffer before Target 1 is hit)
            breakeven_locked = pos_data.get("breakeven_locked", False)
            if not breakeven_locked and self.breakeven_config.get("enabled", True):
                min_gain_pct = self.breakeven_config.get("min_profit_pct", 0.0025)
                buffer_pct = self.breakeven_config.get("fee_buffer_pct", 0.0006)
                trigger_ratio = self.breakeven_config.get("trigger_ratio", 0.50)

                if side == "BUY":
                    gain_pct = (current_high - entry_price) / (entry_price + 1e-9)
                    tp_dist = target1 - entry_price
                    should_lock = (tp_dist > 0 and (current_high - entry_price) >= tp_dist * trigger_ratio) or (gain_pct >= min_gain_pct)
                    if should_lock:
                        be_stop = round(entry_price * (1.0 + buffer_pct), 4)
                        if be_stop > pos_data["stop_loss"]:
                            pos_data["stop_loss"] = be_stop
                            pos_data["breakeven_locked"] = True
                            positions[self.symbol] = pos_data
                            self.state_manager.set("positions", positions)
                            self.db_manager.log_message("INFO", f"🛡️ [BREAKEVEN GUARD] Long SL moved to {be_stop:.4f} to lock in risk-free position.", "TradingEngine")
                            be_msg = (
                                f"🛡️ *[BREAKEVEN PROTECTION ACTIVATED]*\n"
                                f"━━━━━━━━━━━━━━━━━━\n"
                                f"• *Symbol:* `{self.symbol}` (LONG)\n"
                                f"• *Entry Price:* `${entry_price:.4f}`\n"
                                f"• *Current High:* `${current_high:.4f}` (+{gain_pct * 100:.2f}%)\n"
                                f"• *New Protected SL:* `${be_stop:.4f}` (Above Entry!)\n"
                                f"🎉 *Status:* Risk-free trade locked in. ทุนปลอดภัย 100%!"
                            )
                            await self.send_telegram(be_msg)
                else: # SELL
                    gain_pct = (entry_price - current_low) / (entry_price + 1e-9)
                    tp_dist = entry_price - target1
                    should_lock = (tp_dist > 0 and (entry_price - current_low) >= tp_dist * trigger_ratio) or (gain_pct >= min_gain_pct)
                    if should_lock:
                        be_stop = round(entry_price * (1.0 - buffer_pct), 4)
                        if be_stop < pos_data["stop_loss"]:
                            pos_data["stop_loss"] = be_stop
                            pos_data["breakeven_locked"] = True
                            positions[self.symbol] = pos_data
                            self.state_manager.set("positions", positions)
                            self.db_manager.log_message("INFO", f"🛡️ [BREAKEVEN GUARD] Short SL moved to {be_stop:.4f} to lock in risk-free position.", "TradingEngine")
                            be_msg = (
                                f"🛡️ *[BREAKEVEN PROTECTION ACTIVATED]*\n"
                                f"━━━━━━━━━━━━━━━━━━\n"
                                f"• *Symbol:* `{self.symbol}` (SHORT)\n"
                                f"• *Entry Price:* `${entry_price:.4f}`\n"
                                f"• *Current Low:* `${current_low:.4f}` (+{gain_pct * 100:.2f}%)\n"
                                f"• *New Protected SL:* `${be_stop:.4f}` (Below Entry!)\n"
                                f"🎉 *Status:* Risk-free trade locked in. ทุนปลอดภัย 100%!"
                            )
                            await self.send_telegram(be_msg)

            # Check target 1 (50% scale-out)
            if not target1_hit:
                hit = False
                if side == "BUY" and current_high >= target1:
                    hit = True
                elif side == "SELL" and current_low <= target1:
                    hit = True
                    
                if hit:
                    close_size = size * 0.5
                    new_remaining = remaining_size - close_size
                    pnl = close_size * (target1 - entry_price) if side == "BUY" else close_size * (entry_price - target1)
                    
                    self.db_manager.log_message("INFO", f"Target 1 Hit at {target1:.2f}. Scaling out 50% ({close_size:.4f})", "TradingEngine")
                    
                    if not self.dry_run:
                        try:
                            # Active Order Cleanup before market scale-out order
                            await self.order_manager.cancel_active_close_orders(self.symbol)
                            # Place market close order for half size with reduceOnly=True
                            opp_side = "sell" if side == "BUY" else "buy"
                            params = {'reduceOnly': True, 'category': 'linear'}
                            await self.exchange.create_order(
                                symbol=self.symbol,
                                type='market',
                                side=opp_side,
                                amount=close_size,
                                price=None,
                                params=params
                            )
                        except Exception as e:
                            self.db_manager.log_message("ERROR", f"Failed to place live scale-out order: {e}", "TradingEngine")
                            # Break loop/prevent infinite retry by clearing positions
                            self.state_manager.clear_active_position()
                            return
                            
                    # Update state variables
                    pos_data["remaining_size"] = new_remaining
                    pos_data["target1_hit"] = True
                    
                    # Update trailing stop lock-in at entry price to guarantee no losses on Target 2
                    pos_data["trailing_stop"] = entry_price
                    positions[self.symbol] = pos_data
                    self.state_manager.set("positions", positions)
                    
                    # Log to DB
                    self.db_manager.log_trade(self.symbol, "SELL" if side == "BUY" else "BUY", target1, close_size, pnl, regime, "TAKE_PROFIT_1")
                    
                    # Update running balance
                    if self.dry_run:
                        balance = self.state_manager.get("balance", 10000.0)
                        self.state_manager.set("balance", balance + pnl)
                    else:
                        await self.get_available_balance()
                    
                    # Capital Vault Profit Allocation (1/3 Vault, 2/3 Reinvestment)
                    alloc = self.profit_allocator.allocate_trade(
                        self.symbol, side, target1, pnl, "TAKE_PROFIT_1",
                        current_equity=getattr(self, 'wallet_total_equity', 0.0)
                    )
                    self.recalculate_stats()
                    win_rate = self.state_manager.get("win_rate", 0.0)
                    await self.send_exit_alert_card(self.symbol, side, target1, pnl, "TAKE_PROFIT_1 (Scale-Out 50%)", alloc, win_rate)
                    return # Exit tick to avoid double matching in same tick
            
            # Update Trailing Stop (ATR trailing for Target 2)
            if side == "BUY":
                if current_high > pos_data["highest_price"]:
                    pos_data["highest_price"] = current_high
                    # If target 1 is already hit, trail based on high. Otherwise trailing stop is hard stop loss.
                    if target1_hit:
                        new_ts = current_high - (2.5 * atr)
                        # Trailing stop only moves up
                        if new_ts > trailing_stop:
                            pos_data["trailing_stop"] = new_ts
                            
                trailing_stop = pos_data["trailing_stop"]
                
                # Check stop conditions
                is_sl_hit = current_low <= stop_loss and not target1_hit
                is_ts_hit = current_low <= trailing_stop and target1_hit
                
                if is_sl_hit or is_ts_hit:
                    exit_price = stop_loss if is_sl_hit else trailing_stop
                    exit_type = "STOP_LOSS" if is_sl_hit else "TRAILING_STOP"
                    pnl = remaining_size * (exit_price - entry_price)
                    
                    self.db_manager.log_message("INFO", f"Long Position Closed via {exit_type} at {exit_price:.2f}", "TradingEngine")
                    
                    if not self.dry_run:
                        try:
                            # Active Order Cleanup before market close
                            await self.order_manager.cancel_active_close_orders(self.symbol)
                            params = {'reduceOnly': True, 'category': 'linear'}
                            await self.exchange.create_order(
                                symbol=self.symbol,
                                type='market',
                                side='sell',
                                amount=remaining_size,
                                price=None,
                                params=params
                            )
                        except Exception as e:
                            self.db_manager.log_message("ERROR", f"Failed to execute live close order: {e}", "TradingEngine")
                            self.state_manager.clear_active_position()
                            return
                            
                    # Update balances
                    if self.dry_run:
                        balance = self.state_manager.get("balance", 10000.0)
                        new_balance = balance + pnl
                        self.state_manager.update({
                            "balance": new_balance,
                            "positions": {}
                        })
                    else:
                        await self.get_available_balance()
                        self.state_manager.set("positions", {})
                    
                    self.db_manager.log_trade(self.symbol, "SELL", exit_price, remaining_size, pnl, regime, exit_type)
                    self.state_manager.record_closed_order(self.symbol, "BUY", pnl, exit_type)
                    
                    # Update Win Rate and Stats
                    self.recalculate_stats()
                    win_rate = self.state_manager.get("win_rate", 0.0)
                    
                    # Capital Vault Profit Allocation (1/3 Vault, 2/3 Reinvestment)
                    alloc = self.profit_allocator.allocate_trade(
                        self.symbol, "BUY", exit_price, pnl, exit_type,
                        current_equity=getattr(self, 'wallet_total_equity', 0.0)
                    )
                    
                    # Train Adaptive ML Agent & Notify
                    features = pos_data.get("entry_features")
                    if features:
                        # Label 1 if target hit or trade ended in profit
                        label = 1 if (target1_hit or pnl > 0) else 0
                        self.brain.learn_one(features, label)
                        self.state_manager.set("brain_model_state", self.brain.get_state())
                        
                        label_desc = "PROFITABLE (1)" if label == 1 else "UNPROFITABLE (0)"
                        learn_msg = (
                            f"🧠 *[AI BRAIN SELF-LEARNING]*\n"
                            f"Learned from closed Long trade:\n"
                            f"• *Features:* CMO={features.get('cmo'):.2f}, VWAP Dist={features.get('vwap_dist'):.2f}, Volatility={features.get('volatility'):.4f}\n"
                            f"• *Outcome:* `{label_desc}`\n"
                            f"• *Action:* Model updated."
                        )
                        await self.send_telegram(learn_msg)
                        
                    await self.send_exit_alert_card(self.symbol, "BUY", exit_price, pnl, exit_type, alloc, win_rate)
                    
            else: # SHORT position
                if current_low < pos_data["lowest_price"]:
                    pos_data["lowest_price"] = current_low
                    if target1_hit:
                        new_ts = current_low + (2.5 * atr)
                        # Trailing stop only moves down
                        if new_ts < trailing_stop:
                            pos_data["trailing_stop"] = new_ts
                            
                trailing_stop = pos_data["trailing_stop"]
                
                # Check stop conditions
                is_sl_hit = current_high >= stop_loss and not target1_hit
                is_ts_hit = current_high >= trailing_stop and target1_hit
                
                if is_sl_hit or is_ts_hit:
                    exit_price = stop_loss if is_sl_hit else trailing_stop
                    exit_type = "STOP_LOSS" if is_sl_hit else "TRAILING_STOP"
                    pnl = remaining_size * (entry_price - exit_price)
                    
                    self.db_manager.log_message("INFO", f"Short Position Closed via {exit_type} at {exit_price:.2f}", "TradingEngine")
                    
                    if not self.dry_run:
                        try:
                            # Active Order Cleanup before market close
                            await self.order_manager.cancel_active_close_orders(self.symbol)
                            params = {'reduceOnly': True, 'category': 'linear'}
                            await self.exchange.create_order(
                                symbol=self.symbol,
                                type='market',
                                side='buy',
                                amount=remaining_size,
                                price=None,
                                params=params
                            )
                        except Exception as e:
                            self.db_manager.log_message("ERROR", f"Failed to execute live close order: {e}", "TradingEngine")
                            self.state_manager.clear_active_position()
                            return
                            
                    # Update balances
                    if self.dry_run:
                        balance = self.state_manager.get("balance", 10000.0)
                        new_balance = balance + pnl
                        self.state_manager.update({
                            "balance": new_balance,
                            "positions": {}
                        })
                    else:
                        await self.get_available_balance()
                        self.state_manager.set("positions", {})
                    
                    self.db_manager.log_trade(self.symbol, "BUY", exit_price, remaining_size, pnl, regime, exit_type)
                    self.state_manager.record_closed_order(self.symbol, "SELL", pnl, exit_type)
                    
                    # Update Win Rate and Stats
                    self.recalculate_stats()
                    win_rate = self.state_manager.get("win_rate", 0.0)
                    
                    # Capital Vault Profit Allocation (1/3 Vault, 2/3 Reinvestment)
                    alloc = self.profit_allocator.allocate_trade(
                        self.symbol, "SELL", exit_price, pnl, exit_type,
                        current_equity=getattr(self, 'wallet_total_equity', 0.0)
                    )
                    
                    # Train Adaptive ML Agent & Notify
                    features = pos_data.get("entry_features")
                    if features:
                        label = 1 if (target1_hit or pnl > 0) else 0
                        self.brain.learn_one(features, label)
                        self.state_manager.set("brain_model_state", self.brain.get_state())
                        
                        label_desc = "PROFITABLE (1)" if label == 1 else "UNPROFITABLE (0)"
                        learn_msg = (
                            f"🧠 *[AI BRAIN SELF-LEARNING]*\n"
                            f"Learned from closed Short trade:\n"
                            f"• *Features:* CMO={features.get('cmo'):.2f}, VWAP Dist={features.get('vwap_dist'):.2f}, Volatility={features.get('volatility'):.4f}\n"
                            f"• *Outcome:* `{label_desc}`\n"
                            f"• *Action:* Model updated."
                        )
                        await self.send_telegram(learn_msg)
                        
                    await self.send_exit_alert_card(self.symbol, "SELL", exit_price, pnl, exit_type, alloc, win_rate)
            
            # Save any updates to positions
            if self.symbol in self.state_manager.get("positions", {}):
                positions[self.symbol] = pos_data
                self.state_manager.set("positions", positions)
                
        except Exception as e:
            self.db_manager.log_message("ERROR", f"Error in position tracker: {e}", "TradingEngine")

    async def trading_loop(self) -> None:
        """Loop tracking precise candle closures dynamically."""
        tf_mins = int(self.timeframe.replace("m", ""))
        period_seconds = tf_mins * 60
        
        while True:
            # If bot is paused, idle sleep for 1.0s and check again
            if self.state_manager.get("bot_status") != "running":
                await asyncio.sleep(1.0)
                continue
                
            now = time.time()
            # Calculate seconds remaining to next candle close
            next_close = (math.floor(now / period_seconds) + 1) * period_seconds
            sleep_time = next_close - now
            
            # Grace buffer of 1.5 seconds to ensure candle completion on exchange
            sleep_time += 1.5
            
            self.db_manager.log_message("INFO", f"Sleeping {sleep_time:.2f}s until next close...", "TradingEngine")
            
            # Sleep in 1-second chunks so that if the bot is paused during sleep, it reacts instantly!
            elapsed = 0.0
            is_interrupted = False
            while elapsed < sleep_time:
                if self.state_manager.get("bot_status") != "running":
                    is_interrupted = True
                    break
                await asyncio.sleep(1.0)
                elapsed += 1.0
                
            if is_interrupted or self.state_manager.get("bot_status") != "running":
                continue
                
            await self.run_strategy_tick()

    async def tracker_loop(self) -> None:
        """Loop checking active orders, trailing stops, and margin shield equity monitor every 1 second."""
        while True:
            if self.state_manager.get("bot_status") == "running":
                # Active Equity & Margin Shield Monitor (70% drawdown circuit breaker)
                circuit_breaker_tripped = await self.risk_manager.monitor_equity_and_margin_shield(self)
                if not circuit_breaker_tripped:
                    await self.check_active_positions()
            await asyncio.sleep(1.0)

    async def start(self) -> None:
        """Run engine loops concurrently."""
        # Setup exchange client inside the running event loop
        api_key = os.getenv("EXCHANGE_API_KEY") or os.getenv("BYBIT_API_KEY") or ""
        api_secret = os.getenv("EXCHANGE_API_SECRET") or os.getenv("BYBIT_API_SECRET") or ""
        exchange_id = os.getenv("EXCHANGE_ID", "bybit")
        
        resolver = aiohttp.ThreadedResolver()
        connector = aiohttp.TCPConnector(resolver=resolver, ttl_dns_cache=300)
        
        exchange_config = {
            **SAFE_TRADING_CORE,
            "aiohttp_proxy": None,
            "connector": connector
        }
        if exchange_id == "bybit":
            exchange_config["hostname"] = "bytick.com"
            
        if not api_key or not api_secret:
            self.db_manager.log_message("WARNING", "API Keys missing. Forcing DRY RUN mode.", "TradingEngine")
            self.state_manager.set("dry_run", True)
            self.dry_run = True
            exchange_class = getattr(ccxt, exchange_id)
            self.exchange = exchange_class(exchange_config)
        else:
            self.dry_run = os.getenv("DRY_RUN", "true").lower() == "true"
            self.state_manager.set("dry_run", self.dry_run)
            exchange_class = getattr(ccxt, exchange_id)
            exchange_config["apiKey"] = api_key
            exchange_config["secret"] = api_secret
            self.exchange = exchange_class(exchange_config)
            
        self.order_manager.exchange = self.exchange
        self.risk_manager.exchange = self.exchange
        await self.get_available_balance()
        self.recalculate_stats()
        self.db_manager.log_message("INFO", f"Scalping Bot {BOT_VERSION} initialized. Symbol: {self.symbol}, Timeframe: {self.timeframe}, Dry Run: {self.dry_run}", "TradingEngine")

        self.state_manager.set("bot_status", "running")
        
        # Send Telegram alert on start
        start_msg = (
            f"🚀 *Scalping Bot Started ({BOT_VERSION})*\n"
            f"• *Symbol:* `{self.symbol}`\n"
            f"• *Timeframe:* `{self.timeframe}`\n"
            f"• *Dry Run:* `{self.dry_run}`\n"
            f"• *Leverage:* `{self.leverage}x`"
        )
        await self.send_telegram(start_msg)
        # Spawn the Telegram Polling Loop as a background task
        telegram_task = asyncio.create_task(self.telegram_polling_loop())

        # Start Healthcheck Web Server for Cloud / Render if PORT is provided
        http_runner = None
        port_env = os.getenv("PORT")
        if port_env:
            try:
                from aiohttp import web
                async def handle_health(request):
                    status = self.state_manager.get("bot_status", "running")
                    price = self.state_manager.get("last_price", "N/A")
                    body = (
                        f"AlphaScalp Bot V{BOT_VERSION} is ONLINE\n"
                        f"Status: {status}\n"
                        f"Symbol: {self.symbol}\n"
                        f"Dry Run: {self.dry_run}\n"
                        f"Last Price: {price}\n"
                    )
                    return web.Response(text=body, content_type="text/plain")

                app = web.Application()
                app.router.add_get("/", handle_health)
                app.router.add_get("/health", handle_health)
                http_runner = web.AppRunner(app)
                await http_runner.setup()
                site = web.TCPSite(http_runner, "0.0.0.0", int(port_env))
                await site.start()
                self.db_manager.log_message("INFO", f"Render Health Check HTTP server running on 0.0.0.0:{port_env}", "TradingEngine")
                print(f"[INFO] Cloud Healthcheck HTTP server listening on port {port_env}")
            except Exception as e:
                self.db_manager.log_message("WARNING", f"Failed to start healthcheck server: {e}", "TradingEngine")

        try:
            # Perform initial evaluation immediately
            await self.run_strategy_tick()
            
            # Spawn both loops
            await asyncio.gather(
                self.trading_loop(),
                self.tracker_loop()
            )
        except asyncio.CancelledError:
            self.db_manager.log_message("INFO", "Trading loops execution cancelled.", "TradingEngine")
        finally:
            if http_runner:
                await http_runner.cleanup()
            # Gracefully cancel Telegram task on shutdown
            telegram_task.cancel()
            await asyncio.gather(telegram_task, return_exceptions=True)
            if self.exchange:
                await self.exchange.close()
            self.db_manager.log_message("INFO", "Scalping Bot stopped.", "TradingEngine")

if __name__ == "__main__":
    import socket
    import sys

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

    print("==================================================")
    print(f"   🤖 AlphaScalp Trading Engine - Version {BOT_VERSION}")
    print("==================================================")

    # Create a process-global socket lock
    try:
        lock_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        lock_socket.bind(('127.0.0.1', 49152)) # Lock this internal port
    except socket.error:
        print("❌ [CRITICAL] AlphaScalp is already running in another terminal! Exiting to prevent multi-spawning.")
        sys.exit(1)

    bot = ScalpingBot()
    try:
        asyncio.run(bot.start())
    except KeyboardInterrupt:
        print("Interrupted by user. Shutting down...")
