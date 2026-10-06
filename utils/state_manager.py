import json
import os
import time
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict

class StateManager:
    def __init__(self, file_path: str = "state.json"):
        self.file_path = file_path
        self.lock = threading.Lock()
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.state: Dict[str, Any] = {}
        self.load()

    def load(self) -> None:
        """Loads state from disk, or initializes defaults if not present."""
        need_save = False
        with self.lock:
            if os.path.exists(self.file_path):
                loaded = False
                for i in range(5):
                    try:
                        with open(self.file_path, "r") as f:
                            self.state = json.load(f)
                        loaded = True
                        break
                    except Exception:
                        time.sleep(0.1 * (2 ** i))
                if not loaded:
                    print("Error reading state.json after retries. Reverting to defaults.")
                    self.state = self._default_state()
                    need_save = True
            else:
                self.state = self._default_state()
                need_save = True
        
        if need_save:
            self._async_save()

    def _default_state(self) -> Dict[str, Any]:
        """Provides default initial parameters for the bot."""
        return {
            "balance": 1.4252,
            "positions": {},       # e.g., {"BTC/USDT": {"size": 0.0, "entry_price": 0.0, "margin_mode": "isolated", "leverage": 5}}
            "orders": {},          # e.g., {"BTC/USDT": []}
            "bot_status": "running", # "running" or "paused"
            "dry_run": True,
            "last_update": time.time(),
            "equity": 1.4252,
            "pnl": 0.0,
            "trades_count": 0,
            "win_rate": 0.0,
            "leverage": 5,
            "last_closed_order": {} # Track timestamp, side, pnl, and exit reason of last closed trade
        }

    def get(self, key: str, default: Any = None) -> Any:
        """Retrieve key value thread-safely."""
        with self.lock:
            return self.state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        """Mutate cache value immediately, then queue disk write."""
        with self.lock:
            self.state[key] = value
            self.state["last_update"] = time.time()
        self._async_save()

    def update(self, updates: Dict[str, Any]) -> None:
        """Batch mutate cache values immediately, then queue disk write."""
        with self.lock:
            self.state.update(updates)
            self.state["last_update"] = time.time()
        self._async_save()

    def get_all(self) -> Dict[str, Any]:
        """Return a copy of the current state thread-safely."""
        with self.lock:
            return self.state.copy()

    def record_closed_order(self, symbol: str, side: str, pnl: float, exit_reason: str, timestamp: float = None) -> None:
        """
        Tracks timestamp, direction (side), PnL, and exit reason of the last closed order.
        Used by the loss cooldown mechanism to lock out false entries after a loss.
        """
        if timestamp is None:
            timestamp = time.time()
        last_order = {
            "symbol": symbol,
            "side": side,          # "BUY" (Long) or "SELL" (Short)
            "pnl": pnl,
            "exit_reason": exit_reason,
            "timestamp": timestamp
        }
        self.set("last_closed_order", last_order)

    def is_long_cooldown_active(self, cooldown_seconds: float = 900.0) -> bool:
        """
        Checks whether a Stop-Loss Lockout / Loss Cooldown period is currently active for Long signals.
        Returns True if the last closed order was in the Long direction ("BUY") and closed with a loss (pnl < 0),
        and the specified cooldown duration (5 bars / 15 mins by default) has not yet elapsed.
        """
        last_order = self.get("last_closed_order", {})
        if not last_order:
            return False
        
        last_side = last_order.get("side")
        last_pnl = last_order.get("pnl", 0.0)
        last_ts = last_order.get("timestamp", 0.0)
        
        if last_side == "BUY" and last_pnl < 0:
            elapsed = time.time() - last_ts
            if elapsed < cooldown_seconds:
                return True
        return False

    def _async_save(self) -> None:
        """Offloads I/O to background executor."""
        with self.lock:
            state_copy = self.state.copy()
        self.executor.submit(self._save_to_disk, state_copy)

    def _save_to_disk(self, state_data: Dict[str, Any]) -> None:
        """Performs atomic file write in a background thread with Windows file lock handling."""
        temp_file = f"{self.file_path}.tmp"
        
        # 1. Try to write to temp file
        written = False
        for i in range(5):
            try:
                with open(temp_file, "w") as f:
                    json.dump(state_data, f, indent=4)
                written = True
                break
            except Exception:
                time.sleep(0.1 * (2 ** i))
                
        if not written:
            print("Error: Could not write state.json.tmp after retries.")
            return

        # 2. Try to replace target file (Windows locking protection)
        for i in range(5):
            try:
                if os.path.exists(self.file_path):
                    os.remove(self.file_path)
                os.rename(temp_file, self.file_path)
                break
            except Exception as e:
                time.sleep(0.1 * (2 ** i))
            
    def shutdown(self) -> None:
        """Shutdown the executor gracefully."""
        self.executor.shutdown(wait=True)

    def clear_active_position(self) -> None:
        """Helper to clear active positions cache and persist to state.json"""
        self.set("positions", {})

    def clear_close_order_id(self, symbol: str) -> None:
        """
        Clears close_order_id from active position state and orders state thread-safely.
        Leverages background executor with 5-retry exponential backoff to avoid [WinError 32].
        """
        with self.lock:
            positions = self.state.get("positions", {})
            if symbol in positions and "close_order_id" in positions[symbol]:
                positions[symbol].pop("close_order_id", None)
            orders = self.state.get("orders", {})
            if symbol in orders:
                orders.pop(symbol, None)
            self.state["last_update"] = time.time()
        self._async_save()

    def set_close_order_id(self, symbol: str, order_id: str) -> None:
        """
        Sets close_order_id in position state and orders state thread-safely.
        Leverages background executor with 5-retry exponential backoff to avoid [WinError 32].
        """
        with self.lock:
            positions = self.state.get("positions", {})
            if symbol in positions:
                positions[symbol]["close_order_id"] = order_id
            orders = self.state.get("orders", {})
            orders[symbol] = order_id
            self.state["orders"] = orders
            self.state["last_update"] = time.time()
        self._async_save()


