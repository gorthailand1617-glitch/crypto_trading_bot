import asyncio
from utils.order_manager import OrderManager


class DummyExchange:
    def __init__(self, bids, asks):
        self._bids = bids
        self._asks = asks

    async def fetch_order_book(self, symbol, limit=10, params=None):
        return {
            "bids": self._bids,
            "asks": self._asks
        }


class DummyState:
    def get(self, key, default=None):
        if key == "dry_run":
            return False
        return default


class DummyDB:
    def __init__(self):
        self.logs = []

    def log_message(self, level, message, module):
        self.logs.append((level, message, module))


def test_orderbook_imbalance_buy_rejected_on_heavy_ask_wall():
    # Heavy Ask Wall: Bids = 10, Asks = 100 -> Imbalance = (10 - 100) / 110 = -0.818
    bids = [[0.58, 10.0]]
    asks = [[0.59, 100.0]]
    exch = DummyExchange(bids, asks)
    om = OrderManager(DummyState(), DummyDB(), exchange=exch)

    async def _run():
        return await om.check_orderbook_imbalance("XRP/USDT", "BUY", depth=10, threshold=0.35)

    is_safe, imb, reason = asyncio.run(_run())
    assert is_safe is False
    assert imb < -0.35
    assert "Heavy ask wall detected" in reason


def test_orderbook_imbalance_sell_rejected_on_heavy_bid_wall():
    # Heavy Bid Wall: Bids = 100, Asks = 10 -> Imbalance = (100 - 10) / 110 = +0.818
    bids = [[0.58, 100.0]]
    asks = [[0.59, 10.0]]
    exch = DummyExchange(bids, asks)
    om = OrderManager(DummyState(), DummyDB(), exchange=exch)

    async def _run():
        return await om.check_orderbook_imbalance("XRP/USDT", "SELL", depth=10, threshold=0.35)

    is_safe, imb, reason = asyncio.run(_run())
    assert is_safe is False
    assert imb > 0.35
    assert "Heavy bid wall detected" in reason


def test_orderbook_imbalance_balanced_is_allowed():
    # Balanced: Bids = 50, Asks = 50 -> Imbalance = 0.0
    bids = [[0.58, 50.0]]
    asks = [[0.59, 50.0]]
    exch = DummyExchange(bids, asks)
    om = OrderManager(DummyState(), DummyDB(), exchange=exch)

    async def _run():
        res_buy = await om.check_orderbook_imbalance("XRP/USDT", "BUY", depth=10, threshold=0.35)
        res_sell = await om.check_orderbook_imbalance("XRP/USDT", "SELL", depth=10, threshold=0.35)
        return res_buy, res_sell

    (is_safe_buy, imb_buy, _), (is_safe_sell, imb_sell, _) = asyncio.run(_run())
    assert is_safe_buy is True
    assert abs(imb_buy) < 0.01
    assert is_safe_sell is True
