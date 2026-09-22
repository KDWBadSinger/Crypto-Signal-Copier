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
