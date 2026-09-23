from __future__ import annotations

import json
import sqlite3
import threading
from datetime import UTC, datetime
from pathlib import Path
from .database import ManagedConnection

from .models import ParsedSignal, SignalStatus


class SignalStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, factory=ManagedConnection)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS signals (
                    id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    status TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id TEXT,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS app_settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    chat_id INTEGER NOT NULL,
                    message_id INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    received_at TEXT NOT NULL,
                    PRIMARY KEY(chat_id, message_id)
                );
                CREATE TABLE IF NOT EXISTS telegram_signal_aliases (
                    chat_id INTEGER NOT NULL, root_id INTEGER NOT NULL,
                    fingerprint TEXT NOT NULL, sent_at REAL NOT NULL,
                    canonical_root INTEGER NOT NULL, canonical_signal TEXT NOT NULL,
                    PRIMARY KEY(chat_id, root_id)
                );
                CREATE INDEX IF NOT EXISTS signal_alias_fingerprint
                    ON telegram_signal_aliases(chat_id, fingerprint, sent_at);
                """
            )

    def upsert(self, signal: ParsedSignal, *, update: bool = True) -> bool:
        now = datetime.now(UTC).isoformat()
        payload = signal.model_dump_json()
        with self._lock, self._connect() as connection:
            exists = connection.execute(
                "SELECT 1 FROM signals WHERE id = ?", (signal.id,)
            ).fetchone()
            if exists and not update:
                return False
            connection.execute(
                """
                INSERT INTO signals (id, payload, status, received_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    payload=excluded.payload,
                    status=excluded.status,
                    updated_at=excluded.updated_at
                """,
                (
                    signal.id,
                    payload,
                    signal.status.value,
                    signal.received_at.isoformat(),
                    now,
                ),
            )
            if not exists:
                self.add_audit(signal.id, "signal_received", f"source={signal.source_name}", connection)
            return not bool(exists)

    def add_audit(
        self,
        signal_id: str | None,
        event: str,
        detail: str,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        owns_connection = connection is None
        active_connection = connection or self._connect()
        try:
            active_connection.execute(
                "INSERT INTO audit_log (signal_id, event, detail, created_at) VALUES (?, ?, ?, ?)",
                (signal_id, event, detail, datetime.now(UTC).isoformat()),
            )
            if owns_connection:
                active_connection.commit()
        finally:
            if owns_connection:
                active_connection.close()

    def get(self, signal_id: str) -> ParsedSignal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM signals WHERE id = ?", (signal_id,)
            ).fetchone()
        return ParsedSignal.model_validate_json(row["payload"]) if row else None

    def latest(self) -> ParsedSignal | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM signals ORDER BY received_at DESC LIMIT 1"
            ).fetchone()
        return ParsedSignal.model_validate_json(row["payload"]) if row else None

    def list(self, limit: int = 50) -> list[ParsedSignal]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM signals ORDER BY received_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [ParsedSignal.model_validate_json(row["payload"]) for row in rows]

    def list_sources(self) -> list[str]:
        return sorted({signal.source_name for signal in self.list(limit=100) if signal.source_name})

    def update_status(
        self,
        signal: ParsedSignal,
        status: SignalStatus,
        *,
        order_id: str | None = None,
        client_oid: str | None = None,
        detail: str,
    ) -> ParsedSignal:
        updated = signal.model_copy(
            update={
                "status": status,
                "bitget_order_id": order_id,
                "client_oid": client_oid,
                "execution_detail": detail,
                "execution_at": datetime.now(UTC),
            }
        )
        self.upsert(updated)
        self.add_audit(updated.id, status.value, detail)
        return updated

    def audit_for(self, signal_id: str) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event, detail, created_at
                FROM audit_log
                WHERE signal_id = ?
                ORDER BY id ASC
                """,
                (signal_id,),
            ).fetchall()
        return [dict(row) for row in rows]

    def get_message(self, chat_id: int, message_id: int) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT payload FROM telegram_messages WHERE chat_id=? AND message_id=?", (chat_id, message_id)).fetchone()
        return json.loads(row["payload"]) if row else None

    def canonicalize_signal(self,signal,message,root):
        """Exact full-parameter duplicates, same channel, root times <=120s.

        Persistent transaction makes two different Telegram IDs share one order
        identity. Never coalesce merely by symbol, or chain the time window.
        History can reserve a display identity, but never dispatch an order.
        """
        from .parser import signal_fingerprint,SignalParseError
        if message.get('origin')=='edit': return signal
        try:
            sent=datetime.fromisoformat(root['sent_at'])
            if sent.tzinfo is None: return signal
            at=sent.timestamp()
        except (KeyError,TypeError,ValueError): return signal
        chat,identifier=signal.source_chat_id,signal.source_message_id
        # Entry-only duplicates must share identity before either reply exists.
        # Full replies resolve this durable identity; they never create a second entry.
        with self._connect() as conn:
            conn.execute('CREATE TABLE IF NOT EXISTS telegram_entry_aliases (chat INTEGER, root INTEGER, fingerprint TEXT, at REAL, canonical_root INTEGER, signal TEXT, PRIMARY KEY(chat,root))')
            conn.execute('BEGIN IMMEDIATE')
            early=conn.execute('SELECT * FROM telegram_entry_aliases WHERE chat=? AND root=?',(chat,identifier)).fetchone()
            if signal.awaiting_protection and not early:
                import hashlib
                original=(signal.entry_correction or {}).get('original',str(signal.reference_entry))
                key=hashlib.sha256(f'{signal.symbol}|{signal.side}|{original}'.encode()).hexdigest()
                prior=conn.execute('SELECT * FROM telegram_entry_aliases WHERE chat=? AND fingerprint=? AND root=canonical_root AND at BETWEEN ? AND ? ORDER BY at,root LIMIT 1',(chat,key,at-120,at+120)).fetchone()
                conn.execute('INSERT INTO telegram_entry_aliases VALUES (?,?,?,?,?,?)',(chat,identifier,key,at,prior['canonical_root'] if prior else identifier,prior['signal'] if prior else signal.id))
                early=conn.execute('SELECT * FROM telegram_entry_aliases WHERE chat=? AND root=?',(chat,identifier)).fetchone()
        if early:
            if early['canonical_root']!=identifier:
                message['duplicate_of']=early['canonical_root']
                message['detail']+='；与同频道 120 秒内相同开仓意图共用订单，保护回复更新原仓位'
            return signal.model_copy(update={'id':early['signal']})
        fingerprint=signal_fingerprint(signal)
        with self._connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            alias=conn.execute('SELECT * FROM telegram_signal_aliases WHERE chat_id=? AND root_id=?',(chat,identifier)).fetchone()
            if alias and alias['fingerprint']!=fingerprint:
                raise SignalParseError('同一开单消息的保护参数发生变化，仅供核对，不作为新开单执行')
            if not alias:
                canonical=conn.execute('SELECT * FROM telegram_signal_aliases WHERE chat_id=? AND fingerprint=? AND canonical_root=root_id AND sent_at BETWEEN ? AND ? ORDER BY sent_at,root_id LIMIT 1',
                                       (chat,fingerprint,at-120,at+120)).fetchone()
                values=(chat,identifier,fingerprint,at,canonical['canonical_root'] if canonical else identifier,
                        canonical['canonical_signal'] if canonical else signal.id)
                conn.execute('INSERT INTO telegram_signal_aliases VALUES (?,?,?,?,?,?)',values)
                alias=conn.execute('SELECT * FROM telegram_signal_aliases WHERE chat_id=? AND root_id=?',(chat,identifier)).fetchone()
        if alias['canonical_root']!=identifier:
            signal=signal.model_copy(update={'id':alias['canonical_signal']})
            message['duplicate_of']=alias['canonical_root']
            message['detail']+=f"；与开单 #{alias['canonical_root']} 的完整参数一致且开单间隔不超过 120 秒，作为重复信号，不重复下单"
        return signal

    def update_message_preview(self,message):
        """Update cached analysis only. Never replace source/origin/order evidence."""
        fields={'status','detail','parsed_signal','merged_messages','duplicate_of','display_only','reparsed_at','parser_version'}
        with self._connect() as conn:
            row=conn.execute('SELECT payload FROM telegram_messages WHERE chat_id=? AND message_id=?',
                             (message['chat_id'],message['message_id'])).fetchone()
            if not row: return
            existing=json.loads(row['payload'])
            for key in fields:
                if key in message: existing[key]=message[key]
                else: existing.pop(key,None)
            # Preserve an existing executed signal's reference, including legacy
            # duplicates that may already have traded before this fix.
            if not existing.get('signal_id') or not conn.execute('SELECT 1 FROM signals WHERE id=?',(existing['signal_id'],)).fetchone():
                if message.get('signal_id'): existing['signal_id']=message['signal_id']
                else: existing.pop('signal_id',None)
            conn.execute('UPDATE telegram_messages SET payload=? WHERE chat_id=? AND message_id=?',
                         (json.dumps(existing,ensure_ascii=False),message['chat_id'],message['message_id']))

    def record_message(self, payload: dict) -> None:
        with self._connect() as connection:
            # History backfill must never replace a live processing/execution record.
            if payload.get("origin") == "history" and connection.execute(
                "SELECT 1 FROM telegram_messages WHERE chat_id=? AND message_id=? AND json_extract(payload, '$.origin') != 'history'", (payload["chat_id"], payload["message_id"])
            ).fetchone():
                return
            existing = connection.execute("SELECT payload FROM telegram_messages WHERE chat_id=? AND message_id=?",
                                          (payload["chat_id"], payload["message_id"])).fetchone()
            if existing and payload.get("origin") == "live" and json.loads(existing["payload"]).get("origin") == "edit":
                return
            connection.execute(
                """INSERT INTO telegram_messages VALUES (?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET payload=excluded.payload""",
                (payload["chat_id"], payload["message_id"], json.dumps(payload, ensure_ascii=False),
                 datetime.now(UTC).isoformat()),
            )
            connection.execute("DELETE FROM telegram_messages WHERE rowid NOT IN (SELECT rowid FROM telegram_messages ORDER BY received_at DESC LIMIT 10000)")

    def messages(self, limit: int = 50, chat_id: int | None = None) -> list[dict]:
        with self._connect() as connection:
            where, params = ("WHERE chat_id=?", (chat_id, limit)) if chat_id is not None else ("", (limit,))
            rows = connection.execute(f"SELECT payload FROM telegram_messages {where} ORDER BY COALESCE(json_extract(payload, '$.sent_at'), received_at) DESC, message_id DESC LIMIT ?", params).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def get_setting(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM app_settings WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def set_setting(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO app_settings (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
                """,
                (key, value),
            )
