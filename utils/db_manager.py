import sqlite3
import queue
import threading
import json
import time
import os
from typing import Any, Dict, List, Tuple

class DBManager:
    def __init__(self, db_path: str = "trades.db"):
        self.db_path = db_path
        self.queue: queue.Queue[Tuple[str, tuple] | None] = queue.Queue()
        self._init_db()
        self.worker_thread = threading.Thread(target=self._db_worker, daemon=True)
        self.worker_thread.start()

    def _init_db(self) -> None:
        """Create tables if they do not exist."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        # Trades Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                symbol TEXT,
                side TEXT,
                price REAL,
                amount REAL,
                pnl REAL,
                regime TEXT,
                type TEXT
            )
        """)
        
        # Decisions Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS decisions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                symbol TEXT,
                regime TEXT,
                action TEXT,
                reason TEXT,
                close_price REAL,
                indicators TEXT
            )
        """)
        
        # Logs Table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                level TEXT,
                message TEXT,
                module TEXT
            )
        """)
        
        # Capital Vault Ledger Table (AlphaScalp V6.0 1/3 Vault Rule)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS vault_ledger (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp REAL,
                symbol TEXT,
                pnl REAL,
                vault_amount REAL,
                reinvest_amount REAL,
                total_vault REAL
            )
        """)
        
        conn.commit()
        conn.close()

    def _db_worker(self) -> None:
        """Background thread logic for processing DB writes sequentially."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        
        while True:
            try:
                task = self.queue.get()
                if task is None:
                    break
                
                query, params = task
                cursor.execute(query, params)
                conn.commit()
                self.queue.task_done()
            except Exception as e:
                print(f"Database error in background thread: {e}")
                time.sleep(1)  # Throttle on error to prevent database lock issues
                
        conn.close()

    def log_trade(self, symbol: str, side: str, price: float, amount: float, pnl: float, regime: str, trade_type: str) -> None:
        """Pushes a trade entry/exit record to the queue."""
        query = """
            INSERT INTO trades (timestamp, symbol, side, price, amount, pnl, regime, type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (time.time(), symbol, side, price, amount, pnl, regime, trade_type)
        self.queue.put((query, params))

    def log_decision(self, symbol: str, regime: str, action: str, reason: str, close_price: float, indicators: Dict[str, Any]) -> None:
        """Pushes a strategic decision point to the queue."""
        query = """
            INSERT INTO decisions (timestamp, symbol, regime, action, reason, close_price, indicators)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """
        params = (time.time(), symbol, regime, action, reason, close_price, json.dumps(indicators))
        self.queue.put((query, params))

    def log_message(self, level: str, message: str, module: str) -> None:
        """Pushes general diagnostic logs to the database queue."""
        query = """
            INSERT INTO logs (timestamp, level, message, module)
            VALUES (?, ?, ?, ?)
        """
        params = (time.time(), level, message, module)
        self.queue.put((query, params))

    def log_vault_allocation(self, symbol: str, pnl: float, vault_amount: float, reinvest_amount: float, total_vault: float) -> None:
        """Pushes capital vault allocation record to the queue."""
        query = """
            INSERT INTO vault_ledger (timestamp, symbol, pnl, vault_amount, reinvest_amount, total_vault)
            VALUES (?, ?, ?, ?, ?, ?)
        """
        params = (time.time(), symbol, pnl, vault_amount, reinvest_amount, total_vault)
        self.queue.put((query, params))

    def clear_logs(self) -> None:
        """Pushes a clear logs command to the database queue."""
        query = "DELETE FROM logs"
        self.queue.put((query, ()))

    def get_recent_trades(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Synchronous query helper for dashboard (read operations)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        trades = [dict(row) for row in rows]
        conn.close()
        return trades

    def get_recent_vault_allocations(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Synchronous query helper for vault ledger records."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM vault_ledger ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        allocations = [dict(row) for row in rows]
        conn.close()
        return allocations

    def get_recent_decisions(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Synchronous query helper for decisions (read operations)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decisions ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        decisions = [dict(row) for row in rows]
        conn.close()
        return decisions

    def get_recent_logs(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Synchronous query helper for diagnostics (read operations)."""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM logs ORDER BY timestamp DESC LIMIT ?", (limit,))
        rows = cursor.fetchall()
        logs = [dict(row) for row in rows]
        conn.close()
        return logs

    def shutdown(self) -> None:
        """Close DB queue and wait for the worker to terminate."""
        self.queue.put(None)
        self.worker_thread.join()
