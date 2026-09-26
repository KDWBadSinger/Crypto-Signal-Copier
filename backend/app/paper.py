from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from pathlib import Path
from .database import ManagedConnection
from .follow_policy import target_sizes, temporary_stop, PROTECTION_WAIT_SECONDS

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
        self._runtime_tick = None
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, factory=ManagedConnection)
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
            for name, declaration in {
                "sizing_mode": "TEXT NOT NULL DEFAULT 'risk'", "fixed_usdt": "TEXT NOT NULL DEFAULT '100'",
                "position_percent": "TEXT NOT NULL DEFAULT '5'",
                "simulation_id": "TEXT", "lifecycle": "TEXT NOT NULL DEFAULT 'running'",
                "started_at": "TEXT", "stopped_at": "TEXT", "active_seconds": "REAL NOT NULL DEFAULT 0",
                "market_updated_at": "TEXT", "market_error": "TEXT",
            }.items():
                if name not in columns:
                    connection.execute(f"ALTER TABLE paper_account ADD COLUMN {name} {declaration}")
            connection.execute("UPDATE paper_account SET simulation_id=COALESCE(simulation_id, 'legacy-simulation'), started_at=COALESCE(started_at, updated_at)")
            trade_columns = {r['name'] for r in connection.execute('PRAGMA table_info(paper_trades)')}
            if 'market_entry' not in trade_columns:
                connection.execute("ALTER TABLE paper_trades ADD COLUMN market_entry INTEGER NOT NULL DEFAULT 0")
            if 'tp_percentages' not in trade_columns:
                connection.execute("ALTER TABLE paper_trades ADD COLUMN tp_percentages TEXT NOT NULL DEFAULT '[40,40,20]'")
            for name, declaration in {'source_chat_id': 'INTEGER', 'source_message_id': 'INTEGER',
                                      'management_flags': "TEXT NOT NULL DEFAULT '[]'",
                                      'sizing_mode': "TEXT NOT NULL DEFAULT 'risk'",
                                      'sizing_value': "TEXT NOT NULL DEFAULT '0'",
                                      'awaiting_protection': 'INTEGER NOT NULL DEFAULT 0',
                                      'entry_deviation': "TEXT NOT NULL DEFAULT '0.02'",
                                      'protection_deadline': 'TEXT'}.items():
                if name not in trade_columns:
                    connection.execute(f'ALTER TABLE paper_trades ADD COLUMN {name} {declaration}')
            connection.execute('CREATE TABLE IF NOT EXISTS paper_commands (simulation_id TEXT NOT NULL, chat_id INTEGER NOT NULL, message_id INTEGER NOT NULL, result TEXT NOT NULL, PRIMARY KEY(simulation_id, chat_id, message_id))')
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='signals'").fetchone():
                legacy = connection.execute('SELECT t.id, s.payload FROM paper_trades t JOIN signals s ON s.id=t.signal_id WHERE t.source_chat_id IS NULL').fetchall()
                for old in legacy:
                    payload = json.loads(old['payload'])
                    connection.execute('UPDATE paper_trades SET source_chat_id=?, source_message_id=? WHERE id=?',
                                       (payload.get('source_chat_id'), payload.get('source_message_id'), old['id']))
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS paper_daily (day TEXT PRIMARY KEY, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_reports (simulation_id TEXT PRIMARY KEY, payload TEXT NOT NULL);
            """)

    def reset(
        self,
        initial_balance: Decimal,
        leverage: int,
        fee_rate: Decimal,
        selected_sources: list[str] | None = None,
        simulation_id: str = "",
    ) -> None:
        now = datetime.now(UTC).isoformat()
        sources_json = json.dumps(sorted(set(selected_sources or [])), ensure_ascii=False)
        with self._lock, self._connect() as connection:
            previous = self._snapshot(connection)
            if previous.initialized and previous.lifecycle != "stopped":
                raise PaperTradingError("请先停止并结算当前模拟，再创建新 ID；不会覆盖运行中的账本")
            identifier = simulation_id.strip() or f"SIM-{datetime.now(UTC):%Y%m%d-%H%M%S-%f}"
            if connection.execute("SELECT 1 FROM paper_reports WHERE simulation_id=?", (identifier,)).fetchone():
                raise PaperTradingError("该模拟 ID 已存在，请使用新的 ID；历史结算不会覆盖")
            connection.execute("DELETE FROM paper_trades")
            connection.execute("DELETE FROM paper_daily")
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
            connection.execute("UPDATE paper_account SET simulation_id=?, lifecycle='running', started_at=?, stopped_at=NULL, active_seconds=0, market_updated_at=NULL, market_error=NULL WHERE id=1", (identifier, now))
            self._record_day(connection, now)

    def runtime_start(self) -> None:
        self._runtime_tick = datetime.now(UTC)

    def heartbeat(self, *, market_ok: bool = False, error: str | None = None) -> None:
        now = datetime.now(UTC)
        seconds = max(0, (now - self._runtime_tick).total_seconds()) if self._runtime_tick else 0
        self._runtime_tick = now
        with self._lock, self._connect() as connection:
            account = self._snapshot(connection)
            if not account.initialized or account.lifecycle != "running":
                return
            connection.execute("UPDATE paper_account SET active_seconds=active_seconds+?, updated_at=? WHERE id=1", (min(seconds, 30), now.isoformat()))
            if market_ok:
                connection.execute("UPDATE paper_account SET market_updated_at=?, market_error=NULL WHERE id=1", (now.isoformat(),))
                self._record_day(connection, now.isoformat())
            elif error:
                connection.execute("UPDATE paper_account SET market_error=? WHERE id=1", (error,))

    def pause_entries_for_close_all(self):
        with self._lock, self._connect() as connection:
            connection.execute('UPDATE paper_account SET auto_execute=0 WHERE id=1')
            connection.execute("UPDATE paper_trades SET status='rejected', close_reason='manual_close_all_cancelled_pending', updated_at=? WHERE status='pending'", (datetime.now(UTC).isoformat(),))

    def close_positions(self, prices, trade_id=None):
        """Close only this local ledger, atomically; no exchange writes."""
        from .follow_policy import positive
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            if trade_id is None:
                connection.execute('UPDATE paper_account SET auto_execute=0 WHERE id=1')
                connection.execute("UPDATE paper_trades SET status='rejected', close_reason='manual_close_all_cancelled_pending', updated_at=? WHERE status='pending'", (now,))
            rows=connection.execute("SELECT * FROM paper_trades WHERE status='open'").fetchall()
            if trade_id is not None:
                exists=connection.execute('SELECT id FROM paper_trades WHERE id=?',(trade_id,)).fetchone()
                if not exists: raise PaperTradingError('模拟订单不存在')
                rows=[r for r in rows if r['id']==trade_id]
            for row in rows:
                price=positive(prices[row['symbol']])
                self._close_size(connection,row,price,Decimal(row['remaining_size']),
                                 'manual_close_all' if trade_id is None else 'manual_close',now)
            self._record_day(connection,now)
            return self._snapshot(connection)

    def runtime_stop(self) -> None:
        self.heartbeat()
        self._runtime_tick = None

    def _record_day(self, connection, now: str) -> None:
        account = self._snapshot(connection)
        balance = account.initial_balance + account.realized_pnl - account.fees_paid
        point = {"day": now[:10], "observed_at": now, "balance": str(balance),
                 "equity": str(account.equity), "profit": str(account.equity-account.initial_balance)}
        connection.execute("INSERT INTO paper_daily VALUES (?,?) ON CONFLICT(day) DO UPDATE SET payload=excluded.payload", (now[:10], json.dumps(point)))

    def _report(self, connection) -> dict:
        account = self._snapshot(connection)
        daily = {r['day']: json.loads(r['payload']) for r in connection.execute('SELECT * FROM paper_daily ORDER BY day')}
        points = []
        if account.started_at:
            day = account.started_at.date()
            end = (account.stopped_at or datetime.now(UTC)).date()
            previous = account.initial_balance
            while day <= end:
                point = daily.get(day.isoformat())
                if point:
                    point['daily_profit'] = str(Decimal(point['equity']) - previous) if previous is not None else None
                    previous = Decimal(point['equity'])
                else:
                    previous = None
                points.append(point or {"day": day.isoformat(), "equity": None, "balance": None, "profit": None, "daily_profit": None})
                day += timedelta(days=1)
        return {"account": account.model_dump(mode="json"), "daily": points, "timezone": "UTC"}

    def report(self, simulation_id: str | None = None) -> dict:
        with self._connect() as connection:
            if simulation_id:
                row = connection.execute('SELECT payload FROM paper_reports WHERE simulation_id=?', (simulation_id,)).fetchone()
                if not row:
                    raise PaperTradingError("找不到该模拟的结算记录")
                return json.loads(row['payload'])
            return self._report(connection)

    def reports(self) -> list[dict]:
        with self._connect() as connection:
            return [json.loads(row['payload'])['account'] for row in connection.execute('SELECT payload FROM paper_reports ORDER BY rowid DESC')]

    def settle(self, prices: dict[str, Decimal]) -> PaperAccount:
        self.heartbeat()
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            account = self._snapshot(connection)
            if not account.initialized:
                raise PaperTradingError("请先创建模拟")
            if account.lifecycle == "stopped":
                return account
            rows = connection.execute("SELECT * FROM paper_trades WHERE status IN ('pending','open')").fetchall()
            for row in rows:
                if row['status'] == 'open':
                    price = prices.get(row['symbol'])
                    if price is None or not price.is_finite() or price <= 0:
                        raise PaperTradingError("缺少有效的最新公开行情，结算未完成，请重试")
                    self._close_size(connection, row, price, Decimal(row['remaining_size']), 'simulation_stopped', now)
                else:
                    self._reject(connection, row['id'], '停止跟单，取消未成交订单', now)
            connection.execute("UPDATE paper_account SET lifecycle='stopped', stopped_at=?, auto_execute=0, updated_at=?, market_updated_at=?, market_error=NULL WHERE id=1", (now, now, now))
            self._record_day(connection, now)
            report = self._report(connection)
            connection.execute('INSERT INTO paper_reports VALUES (?,?)', (account.simulation_id, json.dumps(report)))
            return self._snapshot(connection)

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
            if self._snapshot(connection).lifecycle == 'stopped':
                raise PaperTradingError("已结算的模拟不可重新启用，请创建新 ID")
            cursor = connection.execute(
                "UPDATE paper_account SET auto_execute=?, updated_at=? WHERE id=1",
                (int(enabled), datetime.now(UTC).isoformat()),
            )
            if cursor.rowcount != 1:
                raise PaperTradingError("请先初始化程序内模拟账户")

    def set_strategy(self, selected_sources: list[str]) -> None:
        normalized = sorted({source.strip() for source in selected_sources if source.strip()})
        with self._lock, self._connect() as connection:
            if self._snapshot(connection).lifecycle == 'stopped':
                raise PaperTradingError("已结算的模拟不可修改")
            cursor = connection.execute(
                "UPDATE paper_account SET selected_sources=?, updated_at=? WHERE id=1",
                (json.dumps(normalized, ensure_ascii=False), datetime.now(UTC).isoformat()),
            )
            if cursor.rowcount != 1:
                raise PaperTradingError("请先初始化程序内模拟账户")

    def enqueue(self, signal: ParsedSignal, *, leverage=None, tp_percentages=None) -> bool:
        from .follow_policy import validate_tp_percentages
        allocation = validate_tp_percentages(tp_percentages if tp_percentages is not None else [40,40,20])
        if not self.is_initialized():
            raise PaperTradingError("请先初始化程序内模拟账户")
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            if self._snapshot(connection).lifecycle == 'stopped':
                raise PaperTradingError("当前模拟已停止，不能加入新信号")
            exists = connection.execute(
                "SELECT 1 FROM paper_trades WHERE signal_id=?", (signal.id,)
            ).fetchone()
            if exists:
                return False
            account = connection.execute(
                "SELECT * FROM paper_account WHERE id=1"
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
                    int(leverage if leverage is not None else account["leverage"]), str(signal.risk_percent or Decimal("1")), now, now,
                ),
            )
            connection.execute("UPDATE paper_trades SET market_entry=? WHERE signal_id=?", (int(signal.market_entry), signal.id))
            connection.execute('UPDATE paper_trades SET tp_percentages=? WHERE signal_id=?', (json.dumps(allocation), signal.id))
            connection.execute('UPDATE paper_trades SET awaiting_protection=?, entry_deviation=? WHERE signal_id=?',
                               (int(signal.awaiting_protection),'0.10' if signal.entry_correction else '0.02',signal.id))
            connection.execute('UPDATE paper_trades SET source_chat_id=?, source_message_id=? WHERE signal_id=?',
                               (signal.source_chat_id, signal.source_message_id, signal.id))
            connection.execute('UPDATE paper_trades SET sizing_mode=?, sizing_value=? WHERE signal_id=?',
                               (account['sizing_mode'], account['fixed_usdt'] if account['sizing_mode'] == 'fixed_usdt' else account['position_percent'], signal.id))
        return True

    def management_target(self, chat_id, symbol=None, root_id=None, signal_id=None):
        with self._connect() as connection:
            rows = connection.execute("SELECT * FROM paper_trades WHERE source_chat_id=? AND status='open'", (chat_id,)).fetchall()
            rows = [r for r in rows if (not symbol or r['symbol'] == symbol)
                    and (root_id is None or r['source_message_id'] == root_id)
                    and (signal_id is None or r['signal_id'] == signal_id)]
            if len(rows) != 1:
                raise PaperTradingError('未找到唯一的同频道持仓，不猜测管理对象（可能未开仓、已平仓或存在多单）')
            return dict(rows[0])

    def manage(self, chat_id, message_id, trade_id, actions, price):
        """Atomic, restart-safe paper-only management; caller supplies a fresh price."""
        if not price.is_finite() or price <= 0:
            raise PaperTradingError('管理指令需要有效实时价格')
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            account = connection.execute('SELECT * FROM paper_account WHERE id=1').fetchone()
            if not account or account['lifecycle'] != 'running' or not account['auto_execute']:
                raise PaperTradingError('模拟未运行或已暂停自动跟单')
            prior = connection.execute('SELECT result FROM paper_commands WHERE simulation_id=? AND chat_id=? AND message_id=?',
                                       (account['simulation_id'], chat_id, message_id)).fetchone()
            if prior:
                return '重复消息：' + prior['result']
            row = connection.execute("SELECT * FROM paper_trades WHERE id=? AND source_chat_id=? AND status='open'", (trade_id, chat_id)).fetchone()
            if not row:
                raise PaperTradingError('目标持仓已关闭或频道不匹配')
            flags = set(json.loads(row['management_flags']))
            entry, fee = Decimal(row['entry_price']), Decimal(account['fee_rate'])
            long = row['side'] == 'long'
            stop = Decimal(row['stop_loss'])
            if (long and price <= stop) or (not long and price >= stop):
                raise PaperTradingError('当前价格已触发原止损，不覆盖止损规则')
            details = []
            # A combined TP1 + runner message reduces once to the agreed runner size.
            if 'breakeven' in actions:
                cost = entry * (1+fee)/(1-fee) if long else entry*(1-fee)/(1+fee)
                if (long and price <= cost) or (not long and price >= cost):
                    raise PaperTradingError('当前价格尚不满足含双边手续费的保本条件')
                cost = max(cost, stop) if long else min(cost, stop)
                connection.execute('UPDATE paper_trades SET stop_loss=?, updated_at=? WHERE id=?', (str(cost), now, trade_id))
                details.append(f'含双边手续费成本损 {cost:.8f}（只收紧，不放宽）')
            if 'runner' in actions and 'runner' not in flags:
                target = entry*(1+Decimal(5)/row['leverage']) if long else entry*(1-Decimal(5)/row['leverage'])
                if target <= 0:
                    raise PaperTradingError('当前杠杆下空单 500% 保证金收益目标无法达到正价格，不执行减仓')
                remaining = Decimal(row['remaining_size'])
                keep = (remaining*Decimal('0.1')).quantize(MONEY, rounding=ROUND_DOWN)
                if keep <= MONEY:
                    raise PaperTradingError('尾仓数量过小，未执行减仓')
                self._close_size(connection, row, price, remaining-keep, 'runner_reduction', now)
                connection.execute('UPDATE paper_trades SET take_profits=?, next_take_profit=0 WHERE id=?', (json.dumps([str(target)]), trade_id))
                flags.update(['runner', 'tp1'])
                details.append(f'保留指令执行前剩余仓位的 10%；500% 保证金毛收益目标价 {target}；保留止损')
            elif 'tp1' in actions and 'tp1' not in flags and row['next_take_profit'] == 0:
                count = len(json.loads(row['take_profits']))
                first=target_sizes(Decimal(row['size']),count,MONEY,json.loads(row['tp_percentages']))[0]
                self._close_size(connection, row, price, min(Decimal(row['remaining_size']),first), 'manual_tp1', now, next_take_profit=1)
                flags.add('tp1')
                details.append(f'按实时价格 {price} 执行第一档止盈（三档按开仓时保存比例 {row["tp_percentages"]}，其他档数等分）')
            connection.execute('UPDATE paper_trades SET management_flags=? WHERE id=?', (json.dumps(sorted(flags)), trade_id))
            detail = '；'.join(details) or '该减仓阶段已完成，不重复执行'
            connection.execute('INSERT INTO paper_commands VALUES (?, ?, ?, ?)', (account['simulation_id'], chat_id, message_id, detail))
            self._record_day(connection, now)
            return detail

    def active_symbols(self) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT DISTINCT symbol FROM paper_trades WHERE status IN ('pending','open')"
            ).fetchall()
        return [str(row["symbol"]) for row in rows]

    def set_sizing(self, request):
        with self._lock, self._connect() as connection:
            account = self._snapshot(connection)
            if not account.initialized or account.lifecycle != 'running':
                raise PaperTradingError('请先创建运行中的模拟；已结算账户不能修改')
            connection.execute('UPDATE paper_account SET leverage=?, sizing_mode=?, fixed_usdt=?, position_percent=? WHERE id=1',
                               (request.leverage, request.sizing_mode, str(request.fixed_usdt), str(request.position_percent)))

    @staticmethod
    def _pnl(side: str, entry: Decimal, exit_price: Decimal, size: Decimal) -> Decimal:
        direction = Decimal("1") if side == SignalSide.LONG.value else Decimal("-1")
        return (exit_price - entry) * size * direction

    def mark(self, symbol: str, price: Decimal) -> None:
        if not price.is_finite() or price <= 0:
            raise PaperTradingError("公开行情价格无效")
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            account = connection.execute("SELECT * FROM paper_account WHERE id=1").fetchone()
            if not account or account['lifecycle'] == 'stopped':
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
        if row['market_entry']:
            if (datetime.fromisoformat(now)-datetime.fromisoformat(row['created_at'])).total_seconds() > 120:
                self._reject(connection, row['id'], '市价单等待行情超过 120 秒，不追补过期信号', now)
                return
            reference = (low + high) / 2
            if abs(price-reference)/reference > Decimal(row['entry_deviation']):
                self._reject(connection, row['id'], '市价偏离校验参考价超过允许范围', now)
                return
            targets = [Decimal(x) for x in json.loads(row['take_profits'])]
            if not row['awaiting_protection'] and ((row['side'] == 'long' and (price <= Decimal(row['stop_loss']) or price >= min(targets))) or (row['side'] == 'short' and (price >= Decimal(row['stop_loss']) or price <= max(targets)))):
                self._reject(connection, row['id'], '最新价格已越过止盈或止损，不追单', now)
                return
        elif not low <= price <= high:
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
        if row['awaiting_protection'] and row['sizing_mode']=='risk':
            # With a 100%-margin stop, the legacy risk budget equals initial margin.
            risk_size=risk_budget*leverage/price
        if row['sizing_mode'] != 'risk':
            requested_margin = Decimal(row['sizing_value']) if row['sizing_mode'] == 'fixed_usdt' else max(snapshot.equity, ZERO)*Decimal(row['sizing_value'])/100
            risk_size = requested_margin*leverage/price
            required = requested_margin + risk_size*price*Decimal(account['fee_rate'])
            if required > snapshot.available_balance:
                self._reject(connection, row['id'], '可用余额不足以支付设定保证金及开仓手续费，不缩小订单冒充足额跟单', now)
                return
        capacity_size = max(snapshot.available_balance, ZERO) * leverage * Decimal("0.98") / price
        size = (min(risk_size, capacity_size) if row['sizing_mode'] == 'risk' else risk_size).quantize(MONEY, rounding=ROUND_DOWN)
        if size <= ZERO:
            self._reject(connection, row["id"], "可用本金不足", now)
            return
        opening_fee = price * size * Decimal(account["fee_rate"])
        if row['awaiting_protection']:
            stop=temporary_stop(price,size,price*size/leverage,row['side'],account['fee_rate'],MONEY)
            deadline=(datetime.fromisoformat(now)+timedelta(seconds=PROTECTION_WAIT_SECONDS)).isoformat()
            connection.execute('UPDATE paper_trades SET stop_loss=?,protection_deadline=? WHERE id=?',(str(stop),deadline,row['id']))
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
        if row['awaiting_protection'] and datetime.fromisoformat(now)>=datetime.fromisoformat(row['protection_deadline']):
            self._close_size(connection,row,price,Decimal(row['remaining_size']),'protection_timeout',now)
            return
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
            allocation = target_sizes(Decimal(row['size']),len(targets),MONEY,json.loads(row['tp_percentages']))
            if allocation[next_index] == 0:
                next_index += 1
                connection.execute('UPDATE paper_trades SET next_take_profit=? WHERE id=?', (next_index,row['id']))
                continue
            hit = price >= targets[next_index] if side == SignalSide.LONG.value else price <= targets[next_index]
            if not hit:
                break
            targets_left = len(targets) - next_index
            close_size = min(remaining,allocation[next_index])
            row = self._close_size(
                connection, row, price, close_size, f"take_profit_{next_index + 1}", now,
                next_take_profit=next_index + 1,
            )
            remaining = Decimal(row["remaining_size"])
            next_index += 1
            if remaining <= ZERO:
                break

    def receive_protection(self,signal,price):
        now=datetime.now(UTC).isoformat()
        with self._lock,self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row=connection.execute('SELECT * FROM paper_trades WHERE signal_id=?',(signal.id,)).fetchone()
            if not row or row['status']!='open' or not row['awaiting_protection']: return False
            if signal.source_chat_id!=row['source_chat_id'] or signal.symbol!=row['symbol'] or signal.side!=row['side']:
                raise PaperTradingError('保护回复与模拟仓位归属不符')
            if datetime.fromisoformat(now)>=datetime.fromisoformat(row['protection_deadline']):
                self._close_size(connection,row,price,Decimal(row['remaining_size']),'protection_timeout',now)
                return False
            ParsedSignal.model_validate({**signal.model_dump(),'entry_low':price,'entry_high':price})
            connection.execute('UPDATE paper_trades SET stop_loss=?,take_profits=?,awaiting_protection=0,updated_at=? WHERE id=?',
                               (str(signal.stop_loss),json.dumps([str(t) for t in signal.take_profits]),now,row['id']))
            return True

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
            sizing_mode=account['sizing_mode'], fixed_usdt=Decimal(account['fixed_usdt']), position_percent=Decimal(account['position_percent']),
            simulation_id=account['simulation_id'], lifecycle=account['lifecycle'],
            started_at=account['started_at'], stopped_at=account['stopped_at'], active_seconds=account['active_seconds'],
            elapsed_seconds=max(0, ((datetime.fromisoformat(account['stopped_at']) if account['stopped_at'] else datetime.now(UTC)) - datetime.fromisoformat(account['started_at'])).total_seconds()),
            market_updated_at=account['market_updated_at'], market_error=account['market_error'],
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
            tp_percentages=json.loads(row['tp_percentages']),
            next_take_profit=int(row["next_take_profit"]), leverage=int(row["leverage"]),
            awaiting_protection=bool(row['awaiting_protection']),protection_deadline=row['protection_deadline'],
            risk_percent=Decimal(row["risk_percent"]), margin=margin, unrealized_pnl=unrealized,
            realized_pnl=Decimal(row["realized_pnl"]), fees=Decimal(row["fees"]),
            close_reason=row["close_reason"], created_at=datetime.fromisoformat(row["created_at"]),
            opened_at=datetime.fromisoformat(row["opened_at"]) if row["opened_at"] else None,
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
