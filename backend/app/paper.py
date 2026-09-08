from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN
from pathlib import Path

from .models import (
    PaperAccount,
    PaperTrade,
    PaperTradeStatus,
    ParsedSignal,
    SignalSide,
)


ZERO = Decimal("0")
MONEY = Decimal("0.00000001")


class PaperTradingError(RuntimeError):
    pass


class PaperTradingStore:
    """Persistent local ledger. It never sends an order to an exchange."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_account (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    initial_balance TEXT NOT NULL,
                    leverage INTEGER NOT NULL,
                    fee_rate TEXT NOT NULL,
                    auto_execute INTEGER NOT NULL DEFAULT 1,
                    selected_sources TEXT NOT NULL DEFAULT '[]',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS paper_trades (
                    id TEXT PRIMARY KEY,
                    signal_id TEXT NOT NULL UNIQUE,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    status TEXT NOT NULL,
                    entry_low TEXT NOT NULL,
                    entry_high TEXT NOT NULL,
                    entry_price TEXT,
                    last_price TEXT,
                    size TEXT,
                    remaining_size TEXT,
                    stop_loss TEXT NOT NULL,
                    take_profits TEXT NOT NULL,
                    next_take_profit INTEGER NOT NULL DEFAULT 0,
                    leverage INTEGER NOT NULL,
                    risk_percent TEXT NOT NULL,
                    realized_pnl TEXT NOT NULL DEFAULT '0',
                    fees TEXT NOT NULL DEFAULT '0',
                    close_reason TEXT,
                    created_at TEXT NOT NULL,
                    opened_at TEXT,
                    closed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                """
            )

            columns = {
                row["name"]
                for row in connection.execute("PRAGMA table_info(paper_account)").fetchall()
            }
            if "selected_sources" not in columns:
                connection.execute(
                    "ALTER TABLE paper_account ADD COLUMN selected_sources TEXT NOT NULL DEFAULT '[]'"
                )

    def reset(
        self,
        initial_balance: Decimal,
        leverage: int,
        fee_rate: Decimal,
        selected_sources: list[str] | None = None,
    ) -> None:
        now = datetime.now(UTC).isoformat()
        sources_json = json.dumps(sorted(set(selected_sources or [])), ensure_ascii=False)
        with self._lock, self._connect() as connection:
            connection.execute("DELETE FROM paper_trades")
            connection.execute(
                """
                INSERT INTO paper_account
                    (id, initial_balance, leverage, fee_rate, auto_execute, selected_sources, updated_at)
                VALUES (1, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    initial_balance=excluded.initial_balance,
                    leverage=excluded.leverage,
                    fee_rate=excluded.fee_rate,
                    auto_execute=1,
                    selected_sources=excluded.selected_sources,
                    updated_at=excluded.updated_at
                """,
                (str(initial_balance), leverage, str(fee_rate), sources_json, now),
            )

    def is_initialized(self) -> bool:
        with self._connect() as connection:
            return connection.execute("SELECT 1 FROM paper_account WHERE id=1").fetchone() is not None

    def auto_execute(self) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT auto_execute FROM paper_account WHERE id=1").fetchone()
        return bool(row and row["auto_execute"])

    def should_auto_execute(self, source_name: str) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT auto_execute, selected_sources FROM paper_account WHERE id=1"
            ).fetchone()
        if not row or not row["auto_execute"]:
            return False
        return source_name in json.loads(row["selected_sources"] or "[]")

    def set_auto_execute(self, enabled: bool) -> None:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE paper_account SET auto_execute=?, updated_at=? WHERE id=1",
                (int(enabled), datetime.now(UTC).isoformat()),
            )
            if cursor.rowcount != 1:
                raise PaperTradingError("请先初始化程序内模拟账户")

    def set_strategy(self, selected_sources: list[str]) -> None:
        normalized = sorted({source.strip() for source in selected_sources if source.strip()})
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE paper_account SET selected_sources=?, updated_at=? WHERE id=1",
                (json.dumps(normalized, ensure_ascii=False), datetime.now(UTC).isoformat()),
            )
            if cursor.rowcount != 1:
                raise PaperTradingError("请先初始化程序内模拟账户")

    def enqueue(self, signal: ParsedSignal) -> bool:
        if not self.is_initialized():
            raise PaperTradingError("请先初始化程序内模拟账户")
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM paper_trades WHERE signal_id=?", (signal.id,)
            ).fetchone()
            if exists:
                return False
            account = connection.execute(
                "SELECT leverage FROM paper_account WHERE id=1"
            ).fetchone()
            connection.execute(
                """
                INSERT INTO paper_trades (
                    id, signal_id, symbol, side, status, entry_low, entry_high,
                    stop_loss, take_profits, leverage, risk_percent,
                    created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"paper_{signal.id}", signal.id, signal.symbol, signal.side.value,
                    PaperTradeStatus.PENDING.value, str(signal.entry_low), str(signal.entry_high),
                    str(signal.stop_loss), json.dumps([str(x) for x in signal.take_profits]),
                    int(account["leverage"]), str(signal.risk_percent or Decimal("1")), now, now,
                ),
            )
        return True

    def active_symbols(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT symbol FROM paper_trades WHERE status IN ('pending','open')"
            ).fetchall()
        return [str(row["symbol"]) for row in rows]

    @staticmethod
    def _pnl(side: str, entry: Decimal, exit_price: Decimal, size: Decimal) -> Decimal:
        direction = Decimal("1") if side == SignalSide.LONG.value else Decimal("-1")
        return (exit_price - entry) * size * direction

    def mark(self, symbol: str, price: Decimal) -> None:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            account = connection.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
            if not account:
                return
            rows = connection.execute(
                "SELECT * FROM paper_trades WHERE symbol=? AND status IN ('pending','open')",
                (symbol,),
            ).fetchall()
            for row in rows:
                connection.execute(
                    "UPDATE paper_trades SET last_price=?, updated_at=? WHERE id=?",
                    (str(price), now, row["id"]),
                )
                if row["status"] == PaperTradeStatus.PENDING.value:
                    self._try_fill(connection, row, account, price, now)
                else:
                    self._apply_exit_rules(connection, row, price, now)

    def _try_fill(self, connection, row, account, price: Decimal, now: str) -> None:
        low, high = Decimal(row["entry_low"]), Decimal(row["entry_high"])
        if not low <= price <= high:
            return
        snapshot = self._snapshot(connection)
        risk_fraction = Decimal(row["risk_percent"]) / Decimal("100")
        risk_budget = max(snapshot.equity, ZERO) * risk_fraction
        stop_distance = abs(price - Decimal(row["stop_loss"]))
        if stop_distance <= ZERO:
            self._reject(connection, row["id"], "止损距离无效", now)
            return
        risk_size = risk_budget / stop_distance
        leverage = Decimal(str(row["leverage"]))
        capacity_size = max(snapshot.available_balance, ZERO) * leverage * Decimal("0.98") / price
        size = min(risk_size, capacity_size).quantize(MONEY, rounding=ROUND_DOWN)
        if size <= ZERO:
            self._reject(connection, row["id"], "可用本金不足", now)
            return
        opening_fee = price * size * Decimal(account["fee_rate"])
        connection.execute(
            """
            UPDATE paper_trades SET status='open', entry_price=?, size=?, remaining_size=?,
                last_price=?, fees=?, opened_at=?, updated_at=? WHERE id=?
            """,
            (str(price), str(size), str(size), str(price), str(opening_fee), now, now, row["id"]),
        )

    def _reject(self, connection, trade_id: str, reason: str, now: str) -> None:
        connection.execute(
            "UPDATE paper_trades SET status='rejected', close_reason=?, closed_at=?, updated_at=? WHERE id=?",
            (reason, now, now, trade_id),
        )

    def _apply_exit_rules(self, connection, row, price: Decimal, now: str) -> None:
        side = row["side"]
        stop = Decimal(row["stop_loss"])
        stop_hit = price <= stop if side == SignalSide.LONG.value else price >= stop
        if stop_hit:
            self._close_size(connection, row, price, Decimal(row["remaining_size"]), "stop_loss", now)
            return

        targets = [Decimal(x) for x in json.loads(row["take_profits"])]
        next_index = int(row["next_take_profit"])
        remaining = Decimal(row["remaining_size"])
        while next_index < len(targets):
            hit = price >= targets[next_index] if side == SignalSide.LONG.value else price <= targets[next_index]
            if not hit:
                break
            targets_left = len(targets) - next_index
            close_size = remaining if targets_left == 1 else remaining / Decimal(targets_left)
            row = self._close_size(
                connection, row, price, close_size, f"take_profit_{next_index + 1}", now,
                next_take_profit=next_index + 1,
            )
            remaining = Decimal(row["remaining_size"])
            next_index += 1
            if remaining <= ZERO:
                break

    def _close_size(
        self, connection, row, price: Decimal, close_size: Decimal, reason: str,
        now: str, next_take_profit: int | None = None,
    ) -> sqlite3.Row:
        entry = Decimal(row["entry_price"])
        remaining = max(Decimal(row["remaining_size"]) - close_size, ZERO)
        realized = Decimal(row["realized_pnl"]) + self._pnl(row["side"], entry, price, close_size)
        account = connection.execute("SELECT fee_rate FROM paper_account WHERE id=1").fetchone()
        fees = Decimal(row["fees"]) + price * close_size * Decimal(account["fee_rate"])
        closed = remaining <= MONEY
        connection.execute(
            """
            UPDATE paper_trades SET remaining_size=?, last_price=?, realized_pnl=?, fees=?,
                next_take_profit=?, status=?, close_reason=?, closed_at=?, updated_at=? WHERE id=?
            """,
            (
                "0" if closed else str(remaining), str(price), str(realized), str(fees),
                next_take_profit if next_take_profit is not None else row["next_take_profit"],
                "closed" if closed else "open", reason,
                now if closed else None, now, row["id"],
            ),
        )
        return connection.execute("SELECT * FROM paper_trades WHERE id=?", (row["id"],)).fetchone()

    def _snapshot(self, connection: sqlite3.Connection) -> PaperAccount:
        account = connection.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
        if not account:
            return PaperAccount(initialized=False)
        rows = connection.execute("SELECT * FROM paper_trades ORDER BY created_at DESC").fetchall()
        trades = [self._trade_from_row(row) for row in rows]
        realized = sum((trade.realized_pnl for trade in trades), ZERO)
        fees = sum((trade.fees for trade in trades), ZERO)
        unrealized = sum((trade.unrealized_pnl for trade in trades), ZERO)
        used_margin = sum((trade.margin for trade in trades), ZERO)
        initial = Decimal(account["initial_balance"])
        equity = initial + realized + unrealized - fees
        available = initial + realized - fees - used_margin
        return PaperAccount(
            initialized=True,
            initial_balance=initial,
            equity=equity,
            available_balance=available,
            used_margin=used_margin,
            realized_pnl=realized,
            unrealized_pnl=unrealized,
            fees_paid=fees,
            return_percent=((equity - initial) / initial * Decimal("100")) if initial else ZERO,
            leverage=int(account["leverage"]),
            fee_rate=Decimal(account["fee_rate"]),
            auto_execute=bool(account["auto_execute"]),
            selected_sources=json.loads(account["selected_sources"] or "[]"),
            updated_at=datetime.fromisoformat(account["updated_at"]),
            trades=trades,
        )

    def snapshot(self) -> PaperAccount:
        with self._connect() as connection:
            return self._snapshot(connection)

    @staticmethod
    def _trade_from_row(row: sqlite3.Row) -> PaperTrade:
        entry = Decimal(row["entry_price"]) if row["entry_price"] else None
        last = Decimal(row["last_price"]) if row["last_price"] else None
        remaining = Decimal(row["remaining_size"]) if row["remaining_size"] else None
        unrealized = ZERO
        margin = ZERO
        if row["status"] == PaperTradeStatus.OPEN.value and entry and last and remaining:
            unrealized = PaperTradingStore._pnl(row["side"], entry, last, remaining)
            margin = entry * remaining / Decimal(str(row["leverage"]))
        return PaperTrade(
            id=row["id"], signal_id=row["signal_id"], symbol=row["symbol"], side=row["side"],
            status=row["status"], entry_low=Decimal(row["entry_low"]), entry_high=Decimal(row["entry_high"]),
            entry_price=entry, last_price=last, size=Decimal(row["size"]) if row["size"] else None,
            remaining_size=remaining, stop_loss=Decimal(row["stop_loss"]),
            take_profits=[Decimal(x) for x in json.loads(row["take_profits"])],
            next_take_profit=int(row["next_take_profit"]), leverage=int(row["leverage"]),
            risk_percent=Decimal(row["risk_percent"]), margin=margin, unrealized_pnl=unrealized,
            realized_pnl=Decimal(row["realized_pnl"]), fees=Decimal(row["fees"]),
            close_reason=row["close_reason"], created_at=datetime.fromisoformat(row["created_at"]),
            opened_at=datetime.fromisoformat(row["opened_at"]) if row["opened_at"] else None,
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
