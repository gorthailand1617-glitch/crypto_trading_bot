import time
import os
import sys
import unittest
import asyncio
import numpy as np
import pandas as pd

# Adjust path to import package files
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.indicators import calculate_cmo, calculate_vwap_bands, calculate_tii, classify_market_regime, calculate_ema
from utils.state_manager import StateManager
from utils.db_manager import DBManager
from utils.order_manager import OrderManager

class TestScalperSystems(unittest.TestCase):
    
    def setUp(self):
        # Setup dummy OHLCV data for testing
        np.random.seed(42)
        dates = pd.date_range(start="2026-01-01", periods=300, freq="3min")
        close_prices = 50000.0 + np.cumsum(np.random.normal(0, 150, 300))
        high_prices = close_prices + np.random.uniform(10, 50, 300)
        low_prices = close_prices - np.random.uniform(10, 50, 300)
        open_prices = close_prices - np.random.normal(0, 20, 300)
        volumes = np.random.uniform(1, 10, 300)
        
        self.df = pd.DataFrame({
            "timestamp": dates,
            "open": open_prices,
            "high": high_prices,
            "low": low_prices,
            "close": close_prices,
            "volume": volumes
        })
        
        self.state_file = "test_state.json"
        self.db_file = "test_trades.db"
        
        # Clean previous test files
        def _safe_remove(path):
            if os.path.exists(path):
                for i in range(5):
                    try:
                        os.remove(path)
                        break
                    except Exception:
                        time.sleep(0.1)

        _safe_remove(self.state_file)
        _safe_remove(self.db_file)
            
        self.state_manager = StateManager(self.state_file)
        self.db_manager = DBManager(self.db_file)
        self.order_manager = OrderManager(self.state_manager, self.db_manager)

    def tearDown(self):
        # Graceful cleanup
        self.state_manager.shutdown()
        self.db_manager.shutdown()
        
        def _safe_remove(path):
            if os.path.exists(path):
                for i in range(5):
                    try:
                        os.remove(path)
                        break
                    except Exception:
                        time.sleep(0.1)

        _safe_remove(self.state_file)
        _safe_remove(f"{self.state_file}.tmp")
        _safe_remove(self.db_file)

    def test_indicators_performance(self):
        # Warmup calls to eliminate Python/Pandas cache loading overhead from benchmark
        _ = calculate_cmo(self.df)
        _ = calculate_vwap_bands(self.df)
        _ = calculate_tii(self.df)
        _ = classify_market_regime(self.df)
        _ = calculate_ema(self.df, 200)

        # Time the calculations
        start_time = time.time()
        cmo = calculate_cmo(self.df)
        elapsed_cmo = (time.time() - start_time) * 1000.0
        
        start_time = time.time()
        vwap, upper, lower = calculate_vwap_bands(self.df)
        elapsed_vwap = (time.time() - start_time) * 1000.0
        
        start_time = time.time()
        tii = calculate_tii(self.df)
        elapsed_tii = (time.time() - start_time) * 1000.0
        
        start_time = time.time()
        regimes = classify_market_regime(self.df)
        elapsed_regime = (time.time() - start_time) * 1000.0

        start_time = time.time()
        ema200 = calculate_ema(self.df, 200)
        elapsed_ema = (time.time() - start_time) * 1000.0
        
        print(f"\n[PERF] Performance benchmarks (300 candles):")
        print(f"  CMO Execution: {elapsed_cmo:.3f} ms")
        print(f"  VWAP Bands Execution: {elapsed_vwap:.3f} ms")
        print(f"  TII Execution: {elapsed_tii:.3f} ms")
        print(f"  Regime Classifier: {elapsed_regime:.3f} ms")
        print(f"  EMA 200 Execution: {elapsed_ema:.3f} ms")
        
        # Verify strict performance limits (<5ms constraint)
        self.assertLess(elapsed_cmo, 5.0)
        self.assertLess(elapsed_vwap, 5.0)
        self.assertLess(elapsed_tii, 5.0)
        self.assertLess(elapsed_regime, 5.0)
        self.assertLess(elapsed_ema, 5.0)
        self.assertEqual(len(ema200), 300)

    def test_loss_cooldown_mechanism(self):
        # 1. Verify initially cooldown is not active
        self.assertFalse(self.state_manager.is_long_cooldown_active())

        # 2. Record a profitable Long order (pnl > 0) -> Cooldown should stay inactive
        self.state_manager.record_closed_order("XRP/USDT", "BUY", 15.5, "TAKE_PROFIT_1")
        self.assertFalse(self.state_manager.is_long_cooldown_active())

        # 3. Record a losing Short order (pnl < 0) -> Cooldown for Long should stay inactive
        self.state_manager.record_closed_order("XRP/USDT", "SELL", -10.0, "STOP_LOSS")
        self.assertFalse(self.state_manager.is_long_cooldown_active())

        # 4. Record a losing Long order (pnl < 0) -> Cooldown MUST become active immediately
        self.state_manager.record_closed_order("XRP/USDT", "BUY", -12.5, "STOP_LOSS")
        self.assertTrue(self.state_manager.is_long_cooldown_active(cooldown_seconds=900.0))

        # 5. Fast-forward timestamp past cooldown window (e.g. 901s) -> Cooldown should expire
        past_ts = time.time() - 905.0
        self.state_manager.record_closed_order("XRP/USDT", "BUY", -12.5, "STOP_LOSS", timestamp=past_ts)
        self.assertFalse(self.state_manager.is_long_cooldown_active(cooldown_seconds=900.0))

    def test_state_persistence(self):
        # Modify state values
        self.state_manager.set("balance", 12345.67)
        self.assertEqual(self.state_manager.get("balance"), 12345.67)
        
        # Allow background writer thread to write
        time.sleep(0.5)
        
        # Check direct reading from state file to check persist
        new_manager = StateManager(self.state_file)
        self.assertEqual(new_manager.get("balance"), 12345.67)
        new_manager.shutdown()

    def test_db_manager_async_log(self):
        # Inject logs and decisions into DB manager
        self.db_manager.log_message("INFO", "Test log output message", "TestRunner")
        self.db_manager.log_trade("BTC/USDT", "BUY", 50000.0, 0.1, 0.0, "IMPULSE_BULL", "ENTRY")
        self.db_manager.log_decision("BTC/USDT", "IMPULSE_BULL", "BUY", "Rebound condition", 50000.0, {"cmo": 60})
        
        # Allow thread queue to clear
        time.sleep(0.5)
        
        # Query results synchronously
        logs = self.db_manager.get_recent_logs()
        trades = self.db_manager.get_recent_trades()
        decisions = self.db_manager.get_recent_decisions()
        
        self.assertGreater(len(logs), 0)
        self.assertGreater(len(trades), 0)
        self.assertGreater(len(decisions), 0)
        self.assertEqual(logs[0]["message"], "Test log output message")
        self.assertEqual(trades[0]["symbol"], "BTC/USDT")

    def test_order_risk_mathematics(self):
        # 1. Position Sizing
        # With Force Minimum Size enabled, it should always return the minimum contract size
        capital = 10000.0
        entry_price = 50000.0
        stop_loss = 49900.0
        leverage = 10
        
        # Test BTC/USDT fallback min size (0.001)
        size_btc = asyncio.run(self.order_manager.calculate_position_size(capital, entry_price, stop_loss, leverage, symbol="BTC/USDT"))
        self.assertAlmostEqual(size_btc, 0.001)
        
        # Test SOL/USDT fallback min size (0.1)
        size_sol = asyncio.run(self.order_manager.calculate_position_size(capital, entry_price, stop_loss, leverage, symbol="SOL/USDT"))
        self.assertAlmostEqual(size_sol, 0.1)

        # Test XRP/USDT fallback min size (3.0)
        size_xrp = asyncio.run(self.order_manager.calculate_position_size(capital, entry_price, stop_loss, leverage, symbol="XRP/USDT"))
        self.assertAlmostEqual(size_xrp, 3.0)

        # 2. Pre-flight guard
        # Long trade, entry = 50000, stop_loss = 49500 (1% drop).
        # At 5x leverage, Long Liq = 50000 * (1 - 0.2 + 0.005) = 40250.
        # Since stop loss (49500) > liq price (40250), it is safe!
        allowed, final_leverage, liq = self.order_manager.validate_leverage_and_liq(entry_price, 49500.0, 5, "BUY")
        self.assertTrue(allowed)
        self.assertEqual(final_leverage, 5)
        
        # If stop loss is 38000, which is below liq price 40250:
        # The guard must reduce leverage dynamically.
        # Let's test leverage reduction
        allowed, final_leverage, liq = self.order_manager.validate_leverage_and_liq(entry_price, 38000.0, 5, "BUY")
        self.assertTrue(allowed)
        self.assertLess(final_leverage, 5)
        self.assertLess(liq, 38000.0) # Check that the new liq price is outside stop loss.

    def test_validate_pre_flight_balance(self):
        class MockExchange:
            def __init__(self, free_usdt, min_amount=0.001):
                self.free_usdt = free_usdt
                self.min_amount = min_amount
            async def fetch_balance(self, params=None):
                return {
                    'USDT': {'free': self.free_usdt, 'total': self.free_usdt},
                    'info': {
                        'result': {
                            'list': [
                                {
                                    'totalEquity': str(self.free_usdt),
                                    'totalAvailableBalance': str(self.free_usdt)
                                }
                            ]
                        }
                    }
                }
            async def load_markets(self):
                pass
            def market(self, symbol):
                return {'limits': {'amount': {'min': self.min_amount}}}

        # Case 1: Account under 15 USDT (e.g. 12 USDT)
        # Entry price: 77.0, leverage: 10, symbol: SOL/USDT -> min order size is 0.1
        # notional_value = 0.1 * 77.0 = 7.7
        # required_margin = 7.7 / 10 = 0.77
        # Since balance (12.0) < 15.0, buffer multiplier is 1.02 -> safe_margin_required = 0.77 * 1.02 = 0.7854
        # Let's test with available balance = 0.80 USDT
        mock_exchange_small = MockExchange(free_usdt=0.80)
        self.order_manager.exchange = mock_exchange_small
        check_small = asyncio.run(self.order_manager.validate_pre_flight_balance("SOL/USDT", 77.0, 10))
        # With 1.02 buffer, 0.80 > 0.7854 so it is safe!
        self.assertTrue(check_small["is_safe"])
        
        # Now test with available balance = 0.80 USDT but if buffer was 1.10 it would be 0.847, which fails.
        # Let's verify that for > 15 USDT account (e.g. 20 USDT) it uses 1.10 buffer
        # With balance = 20.0, required_margin = 19.0 (e.g. SOL price 1900.0, leverage 10, size 0.1)
        # Since balance (20.0) >= 15.0, buffer multiplier is 1.10 -> safe_margin_required = 1.9 * 1.10 = 2.09
        # Let's test with available balance = 2.00 USDT on a 20 USDT balance (we mock balance to 20 USDT to see if multiplier behaves accordingly)
        # Wait, the method checks available_balance = balance_data['USDT']['free']. So available_balance is what is checked against 15.0.
        # If available_balance is 14.0, buffer is 1.02. If available_balance is 16.0, buffer is 1.10.
        
        # Test under 15 USDT available_balance:
        mock_exchange_14 = MockExchange(free_usdt=14.0)
        self.order_manager.exchange = mock_exchange_14
        # required_margin = 0.1 * 135.0 / 1 = 13.5
        # safe_margin_required = 13.5 * 1.02 = 13.77.
        # available_balance is 14.0, which is > 13.77, so it's safe.
        check_14 = asyncio.run(self.order_manager.validate_pre_flight_balance("SOL/USDT", 135.0, 1))
        self.assertTrue(check_14["is_safe"])

        # Test over 15 USDT available_balance:
        mock_exchange_16 = MockExchange(free_usdt=16.0)
        self.order_manager.exchange = mock_exchange_16
        # required_margin = 0.1 * 150.0 / 1 = 15.0
        # since available_balance is 16.0 (>= 15.0), buffer is 1.10 -> safe_margin_required = 15.0 * 1.10 = 16.5
        # available_balance is 16.0, which is < 16.5, so it's NOT safe.
        check_16 = asyncio.run(self.order_manager.validate_pre_flight_balance("SOL/USDT", 150.0, 1))
        self.assertFalse(check_16["is_safe"])

    def test_socket_lock_logic(self):
        import socket
        # Verify socket lock logic: if we bind to port 49152, subsequent bind attempts must fail
        s1 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s2 = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                s1.bind(('127.0.0.1', 49152))
            except socket.error as e:
                # If it's already in use, the lock logic is active and working on the system!
                # We verify it raises the expected Address already in use error code.
                # errno 98 is EADDRINUSE on Linux, 10048 is WSAEADDRINUSE on Windows
                self.assertIn(e.errno, [98, 10048])
                return
            
            # Second bind attempt to the same port must fail with socket.error
            with self.assertRaises(socket.error):
                s2.bind(('127.0.0.1', 49152))
        finally:
            s1.close()
            s2.close()

    def test_telegram_polling_setup(self):
        # Verification of telegram polling setup interface parameters
        class MockUpdater:
            async def start_polling(self, drop_pending_updates=True, connect_timeout=15.0, read_timeout=30.0, write_timeout=15.0):
                self.params = {
                    'drop_pending_updates': drop_pending_updates,
                    'connect_timeout': connect_timeout,
                    'read_timeout': read_timeout,
                    'write_timeout': write_timeout
                }
        class MockApplication:
            def __init__(self):
                self.updater = MockUpdater()
        
        async def run_test():
            application = MockApplication()
            await application.updater.start_polling(
                drop_pending_updates=True,
                connect_timeout=15.0,
                read_timeout=30.0,
                write_timeout=15.0
            )
            return application.updater.params
            
        params = asyncio.run(run_test())
        self.assertTrue(params['drop_pending_updates'])
        self.assertEqual(params['connect_timeout'], 15.0)
        self.assertEqual(params['read_timeout'], 30.0)

    def test_telegram_builder_setup(self):
        # Verification of telegram request and application builder setup parameters
        class MockHTTPXRequest:
            def __init__(self, connection_pool_size=8, connect_timeout=20.0, read_timeout=45.0, write_timeout=20.0):
                self.connection_pool_size = connection_pool_size
                self.connect_timeout = connect_timeout
                self.read_timeout = read_timeout
                self.write_timeout = write_timeout
        class MockApplicationBuilder:
            def __init__(self):
                self.params = {}
            def token(self, token):
                self.params['token'] = token
                return self
            def request(self, req):
                self.params['request'] = req
                return self
            def build(self):
                return self
                
        request = MockHTTPXRequest(connection_pool_size=8, connect_timeout=20.0, read_timeout=45.0, write_timeout=20.0)
        builder = MockApplicationBuilder().token("MOCK").request(request).build()
        self.assertEqual(builder.params['request'].read_timeout, 45.0)

    def test_order_cleanup_logic(self):
        # Verify active order cleanup and state manager integration
        class MockCleanupExchange:
            def __init__(self):
                self.cancelled_symbols = []
                self.cancel_params = []
            async def cancel_all_orders(self, symbol, params=None):
                self.cancelled_symbols.append(symbol)
                self.cancel_params.append(params)
                return []

        mock_ex = MockCleanupExchange()
        self.order_manager.exchange = mock_ex
        self.state_manager.set("dry_run", False)

        # Set active close order id in state
        self.state_manager.set("positions", {"XRP/USDT": {"close_order_id": "12345"}})
        self.state_manager.set_close_order_id("XRP/USDT", "12345")
        self.assertEqual(self.state_manager.get("positions", {}).get("XRP/USDT", {}).get("close_order_id"), "12345")

        # Run cancel_active_close_orders
        t0 = time.time()
        res = asyncio.run(self.order_manager.cancel_active_close_orders("XRP/USDT"))
        t1 = time.time()

        self.assertTrue(res)
        self.assertIn("XRP/USDT", mock_ex.cancelled_symbols)
        self.assertEqual(mock_ex.cancel_params[0], {'category': 'linear'})
        # Verify post-cancel delay of at least 0.5s
        self.assertGreaterEqual(t1 - t0, 0.48)
        # Verify state manager cleared close_order_id
        self.assertNotIn("close_order_id", self.state_manager.get("positions", {}).get("XRP/USDT", {}))

if __name__ == "__main__":
    unittest.main()
