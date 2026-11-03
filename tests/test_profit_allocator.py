import unittest
from utils.profit_allocator import ProfitAllocator


class DummyStateManager:
    def __init__(self):
        self.data = {}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def set(self, key, value):
        self.data[key] = value


class DummyDBManager:
    def __init__(self):
        self.vault_logs = []

    def log_vault_allocation(self, symbol, pnl, vault_amount, reinvest_amount, total_vault):
        self.vault_logs.append({
            "symbol": symbol,
            "pnl": pnl,
            "vault": vault_amount,
            "reinvest": reinvest_amount,
            "total_vault": total_vault
        })


class TestProfitAllocator(unittest.TestCase):
    def test_profit_allocator_one_third_rule(self):
        sm = DummyStateManager()
        db = DummyDBManager()
        allocator = ProfitAllocator(sm, db, vault_ratio=0.3333, reinvest_ratio=0.6667, daily_loss_limit_pct=0.03)

        # 1. Profit Trade ($3.00 profit)
        res = allocator.allocate_trade(
            symbol="XRP/USDT",
            side="BUY",
            exit_price=0.5900,
            pnl=3.0,
            exit_type="TAKE_PROFIT_1",
            current_equity=50.0
        )

        # Expect: 1/3 (3.0 * 0.3333 = 0.9999) to vault, 2/3 (3.0 * 0.6667 = 2.0001) to reinvest
        self.assertEqual(round(res["vault_cut"], 2), 1.00)
        self.assertEqual(round(res["reinvest_cut"], 2), 2.00)
        self.assertEqual(round(res["total_vault"], 2), 1.00)
        self.assertEqual(res["daily_pnl"], 3.0)
        self.assertFalse(res["daily_loss_limit_hit"])

        # Check DB log
        self.assertEqual(len(db.vault_logs), 1)
        self.assertEqual(db.vault_logs[0]["vault"], res["vault_cut"])

    def test_daily_circuit_breaker(self):
        sm = DummyStateManager()
        db = DummyDBManager()
        allocator = ProfitAllocator(sm, db, vault_ratio=0.3333, reinvest_ratio=0.6667, daily_loss_limit_pct=0.03)
        allocator.set_start_of_day_equity(100.0) # 3% limit = $3.00 max loss

        # Loss Trade within limit (-$1.50)
        res1 = allocator.allocate_trade("XRP/USDT", "BUY", 0.58, -1.50, "STOP_LOSS", current_equity=98.5)
        self.assertFalse(res1["daily_loss_limit_hit"])
        can_trade, _ = allocator.can_trade_today(98.5)
        self.assertTrue(can_trade)

        # Another Loss Trade pushing total daily loss to -$3.50 (Breaches $3.00 threshold)
        res2 = allocator.allocate_trade("XRP/USDT", "BUY", 0.57, -2.00, "STOP_LOSS", current_equity=96.5)
        self.assertTrue(res2["daily_loss_limit_hit"])

        can_trade_after, reason = allocator.can_trade_today(96.5)
        self.assertFalse(can_trade_after)
        self.assertIn("CIRCUIT BREAKER", reason)


if __name__ == "__main__":
    unittest.main()

