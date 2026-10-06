import os
import sys
import unittest
import asyncio
from unittest.mock import MagicMock, AsyncMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from risk_manager import RiskManager
from utils.state_manager import StateManager
from utils.db_manager import DBManager
from utils.order_manager import OrderManager


class TestRiskManagerAndMarginShield(unittest.TestCase):
    def setUp(self):
        self.state_file = "test_risk_state.json"
        self.db_file = "test_risk_trades.db"
        if os.path.exists(self.state_file):
            os.remove(self.state_file)
        if os.path.exists(self.db_file):
            os.remove(self.db_file)

        self.state_manager = StateManager(self.state_file)
        self.db_manager = DBManager(self.db_file)
        self.order_manager = OrderManager(self.state_manager, self.db_manager)
        self.risk_manager = RiskManager(
            state_manager=self.state_manager,
            db_manager=self.db_manager,
            order_manager=self.order_manager
        )

    def tearDown(self):
        self.state_manager.shutdown()
        self.db_manager.shutdown()
        if os.path.exists(self.state_file):
            try:
                os.remove(self.state_file)
            except Exception:
                pass
        if os.path.exists(self.db_file):
            try:
                os.remove(self.db_file)
            except Exception:
                pass

    def test_validate_leverage_limits(self):
        """Test that leverage is strictly capped at MAX_LEVERAGE."""
        self.risk_manager.max_leverage = 5

        # Within limit
        ok, lev, msg = self.risk_manager.validate_leverage(3)
        self.assertTrue(ok)
        self.assertEqual(lev, 3)

        # Equal to limit
        ok, lev, msg = self.risk_manager.validate_leverage(5)
        self.assertTrue(ok)
        self.assertEqual(lev, 5)

        # Exceeds limit -> should clamp to MAX_LEVERAGE
        ok, lev, msg = self.risk_manager.validate_leverage(20)
        self.assertTrue(ok)
        self.assertEqual(lev, 5)

        # Invalid zero/negative
        ok, lev, msg = self.risk_manager.validate_leverage(0)
        self.assertFalse(ok)

    def test_validate_notional_exposure_limits(self):
        """Test that position notional does not exceed MAX_NOTIONAL_EXPOSURE."""
        self.risk_manager.max_notional_exposure = 50.0  # 50 USDT max

        # 3.0 XRP @ $2.50 = $7.50 notional (OK)
        ok, msg = self.risk_manager.validate_notional_exposure("XRP/USDT", 3.0, 2.50)
        self.assertTrue(ok)

        # 50.0 XRP @ $2.00 = $100.0 notional (> $50 limit -> REJECT)
        ok, msg = self.risk_manager.validate_notional_exposure("XRP/USDT", 50.0, 2.00)
        self.assertFalse(ok)
        self.assertIn("NOTIONAL LIMIT EXCEEDED", msg)

    def test_isolated_margin_calculation(self):
        """Test accurate calculation of allocated isolated margin."""
        # 10 contracts @ $100 entry price with 5x leverage -> Notional = $1000, Margin = $200
        margin = self.risk_manager.calculate_isolated_margin(size=10.0, entry_price=100.0, leverage=5)
        self.assertEqual(margin, 200.0)

        # 0.1 SOL @ $180 entry price with 5x leverage -> Notional = $18, Margin = $3.60
        sol_margin = self.risk_manager.calculate_isolated_margin(size=0.1, entry_price=180.0, leverage=5)
        self.assertAlmostEqual(sol_margin, 3.60, places=4)

    def test_margin_shield_breach_detection_long(self):
        """Test 70% isolated margin loss breach detection on Long position."""
        self.risk_manager.margin_shield_threshold_pct = 0.70  # 70%

        # Long position: Entry $100, Size 1.0, Leverage 5x -> Allocated Isolated Margin = $20.0
        # 70% loss threshold = $14.00 loss -> Mark Price <= $86.00
        
        # Scenario A: Price dropped to $90 (Loss $10 = 50% of margin) -> NO BREACH
        breached, u_pnl, margin, dd, reason = self.risk_manager.check_margin_shield_breach(
            symbol="TEST/USDT",
            size=1.0,
            entry_price=100.0,
            current_price=90.0,
            side="BUY",
            leverage=5
        )
        self.assertFalse(breached)
        self.assertEqual(margin, 20.0)
        self.assertEqual(u_pnl, -10.0)
        self.assertAlmostEqual(dd, 0.50, places=2)

        # Scenario B: Price dropped to $85 (Loss $15 = 75% of margin) -> BREACH (>70%)
        breached, u_pnl, margin, dd, reason = self.risk_manager.check_margin_shield_breach(
            symbol="TEST/USDT",
            size=1.0,
            entry_price=100.0,
            current_price=85.0,
            side="BUY",
            leverage=5
        )
        self.assertTrue(breached)
        self.assertEqual(u_pnl, -15.0)
        self.assertAlmostEqual(dd, 0.75, places=2)
        self.assertIn("MARGIN SHIELD BREACH", reason)

    def test_margin_shield_breach_detection_short(self):
        """Test 70% isolated margin loss breach detection on Short position."""
        self.risk_manager.margin_shield_threshold_pct = 0.70

        # Short position: Entry $100, Size 1.0, Leverage 5x -> Margin = $20.0
        # Price rises to $115 (Loss $15 = 75% of margin) -> BREACH (>70%)
        breached, u_pnl, margin, dd, reason = self.risk_manager.check_margin_shield_breach(
            symbol="TEST/USDT",
            size=1.0,
            entry_price=100.0,
            current_price=115.0,
            side="SELL",
            leverage=5
        )
        self.assertTrue(breached)
        self.assertEqual(u_pnl, -15.0)
        self.assertAlmostEqual(dd, 0.75, places=2)

    def test_monitor_equity_and_emergency_close_execution(self):
        """Test that monitor_equity_and_margin_shield trips circuit breaker and calls close_all_positions."""
        # Set up a mock bot instance in dry run with a simulated losing position
        mock_bot = MagicMock()
        mock_bot.dry_run = True
        mock_bot.symbol = "XRP/USDT"
        mock_bot.leverage = 5
        mock_bot.fetch_ticker_safe = AsyncMock(return_value={"last": 0.50})  # Dropped from $2.00
        mock_bot.send_telegram = AsyncMock()

        # Place losing position in state: Entry $2.00, Size 10.0, Leverage 5x -> Margin = $4.00
        # Current price $0.50 -> Loss = $15.00 (>300% loss of margin)
        self.state_manager.set("positions", {
            "XRP/USDT": {
                "symbol": "XRP/USDT",
                "side": "BUY",
                "entry_price": 2.00,
                "size": 10.0,
                "remaining_size": 10.0,
                "leverage": 5
            }
        })

        # Run equity monitor
        tripped = asyncio.run(self.risk_manager.monitor_equity_and_margin_shield(mock_bot))
        self.assertTrue(tripped)

        # Verify position is cleared from state
        positions_after = self.state_manager.get("positions", {})
        self.assertEqual(len(positions_after), 0)

        # Verify last closed order was logged as emergency circuit breaker
        last_closed = self.state_manager.get("last_closed_order", {})
        self.assertIn("EMERGENCY", last_closed.get("exit_reason", ""))


if __name__ == "__main__":
    unittest.main()
