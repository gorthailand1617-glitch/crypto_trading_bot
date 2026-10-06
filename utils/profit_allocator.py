import time
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger("TradingEngine")


class ProfitAllocator:
    """
    Capital Preservation & Profit Allocator (AlphaScalp V6.0).
    Enforces the 1/3 Vault Rule:
    - 33.3% of all net positive trade profits are safely routed to Capital Vault.
    - 66.7% are compounded back into the trading pool.
    - Tracks daily drawdown and triggers Daily Circuit Breaker if loss limit is breached.
    """

    def __init__(
        self,
        state_manager: Any = None,
        db_manager: Any = None,
        vault_ratio: float = 0.3333,
        reinvest_ratio: float = 0.6667,
        daily_loss_limit_pct: float = 0.03
    ):
        self.state_manager = state_manager
        self.db_manager = db_manager
        self.vault_ratio = vault_ratio
        self.reinvest_ratio = reinvest_ratio
        self.daily_loss_limit_pct = daily_loss_limit_pct

        # Initialize or restore state from state_manager
        cached = self.state_manager.get("vault_data", {}) if self.state_manager else {}
        self.vault_balance = float(cached.get("vault_balance", 0.0))
        self.reinvest_pool = float(cached.get("reinvest_pool", 0.0))
        self.total_realized_profit = float(cached.get("total_realized_profit", 0.0))
        self.total_realized_loss = float(cached.get("total_realized_loss", 0.0))
        self.start_of_day_equity = float(cached.get("start_of_day_equity", 10.0))
        self.daily_pnl = float(cached.get("daily_pnl", 0.0))
        self.daily_loss_limit_hit = bool(cached.get("daily_loss_limit_hit", False))
        self.current_day_str = cached.get("current_day_str", self._get_utc_date_str())

        # Check if day rolled over
        self._check_and_reset_daily()

    def _get_utc_date_str(self) -> str:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")

    def _check_and_reset_daily(self, current_equity: float = 0.0) -> None:
        today_str = self._get_utc_date_str()
        if today_str != self.current_day_str:
            logger.info(f"🌅 [PROFIT ALLOCATOR] New UTC Day detected ({today_str}). Resetting daily stats.")
            self.current_day_str = today_str
            self.daily_pnl = 0.0
            self.daily_loss_limit_hit = False
            if current_equity > 0:
                self.start_of_day_equity = current_equity
            self._save_state()

    def set_start_of_day_equity(self, equity: float) -> None:
        if equity > 0 and (self.start_of_day_equity <= 0 or self.daily_pnl == 0.0):
            self.start_of_day_equity = equity
            self._save_state()

    def can_trade_today(self, current_equity: float = 0.0) -> Tuple[bool, str]:
        self._check_and_reset_daily(current_equity)

        if self.daily_loss_limit_hit:
            reason = (
                f"🛑 [DAILY CIRCUIT BREAKER ACTIVE] Daily loss limit reached "
                f"(Daily PnL: ${self.daily_pnl:.2f} / -{self.daily_loss_limit_pct * 100:.1f}% threshold). "
                f"Trading locked until next UTC day (00:00 UTC)."
            )
            return False, reason

        return True, "Trading allowed."

    def allocate_trade(
        self,
        symbol: str,
        side: str,
        exit_price: float,
        pnl: float,
        exit_type: str,
        current_equity: float = 0.0
    ) -> Dict[str, Any]:
        """
        Process completed trade PnL:
        - Splits profits: 1/3 to Vault, 2/3 to Reinvest.
        - Deducts losses from daily PnL and checks Daily Circuit Breaker.
        - Persists to DB and State.
        """
        self._check_and_reset_daily(current_equity)

        vault_cut = 0.0
        reinvest_cut = 0.0

        if pnl > 0:
            vault_cut = round(pnl * self.vault_ratio, 4)
            reinvest_cut = round(pnl * self.reinvest_ratio, 4)
            self.vault_balance = round(self.vault_balance + vault_cut, 4)
            self.reinvest_pool = round(self.reinvest_pool + reinvest_cut, 4)
            self.total_realized_profit = round(self.total_realized_profit + pnl, 4)
        else:
            self.total_realized_loss = round(self.total_realized_loss + abs(pnl), 4)

        self.daily_pnl = round(self.daily_pnl + pnl, 4)

        # Check daily loss limit
        if self.start_of_day_equity > 0:
            max_allowed_loss = self.start_of_day_equity * self.daily_loss_limit_pct
            if self.daily_pnl < 0 and abs(self.daily_pnl) >= max_allowed_loss:
                self.daily_loss_limit_hit = True
                logger.warning(
                    f"🚨 [CIRCUIT BREAKER TRIGGERED] Daily loss ${abs(self.daily_pnl):.2f} "
                    f"exceeds limit (${max_allowed_loss:.2f}). Pausing trading today."
                )

        # Save to database ledger if available
        if self.db_manager and hasattr(self.db_manager, "log_vault_allocation"):
            self.db_manager.log_vault_allocation(
                symbol=symbol,
                pnl=pnl,
                vault_amount=vault_cut,
                reinvest_amount=reinvest_cut,
                total_vault=self.vault_balance
            )

        self._save_state()

        logger.info(
            f"🏦 [PROFIT ALLOCATOR] Trade: PnL=${pnl:.4f} | "
            f"Vault Cut (1/3)=+${vault_cut:.4f} (Total Vault: ${self.vault_balance:.4f}) | "
            f"Reinvest (2/3)=+${reinvest_cut:.4f} | Daily PnL: ${self.daily_pnl:.4f}"
        )

        return {
            "pnl": pnl,
            "vault_cut": vault_cut,
            "reinvest_cut": reinvest_cut,
            "total_vault": self.vault_balance,
            "total_reinvest": self.reinvest_pool,
            "daily_pnl": self.daily_pnl,
            "daily_loss_limit_hit": self.daily_loss_limit_hit
        }

    def _save_state(self) -> None:
        if self.state_manager:
            self.state_manager.set("vault_data", {
                "vault_balance": self.vault_balance,
                "reinvest_pool": self.reinvest_pool,
                "total_realized_profit": self.total_realized_profit,
                "total_realized_loss": self.total_realized_loss,
                "start_of_day_equity": self.start_of_day_equity,
                "daily_pnl": self.daily_pnl,
                "daily_loss_limit_hit": self.daily_loss_limit_hit,
                "current_day_str": self.current_day_str
            })

    def get_summary(self) -> Dict[str, Any]:
        return {
            "vault_balance": self.vault_balance,
            "reinvest_pool": self.reinvest_pool,
            "total_realized_profit": self.total_realized_profit,
            "total_realized_loss": self.total_realized_loss,
            "daily_pnl": self.daily_pnl,
            "daily_loss_limit_hit": self.daily_loss_limit_hit,
            "start_of_day_equity": self.start_of_day_equity,
            "date": self.current_day_str
        }
