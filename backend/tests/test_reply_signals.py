import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.parser import parse_signal, merge_reply
from app.service import CopierService
from test_desktop_workflow import FakeTelegram, settings, event

ENTRY = "#UAI 市價多 0.45091"
PROTECTION = "止盈：0.479-0.511-0.551\n止损：0.425"


def reply(text=PROTECTION, identifier=7608, parent=7606, age=0):
    value = event(text=text, message_id=identifier, age=age)
    value.message.reply_to_msg_id = parent
    return value


@pytest.mark.parametrize("opening,protection,symbol", [
    (ENTRY, PROTECTION, "UAIUSDT"),
    ("#ETH 市價空 進場2659.26", "止盈：2608.17\n止损：2709.47", "ETHUSDT"),
    ("BTC 市价多 80000", "止盈：82000\n止损：79000", "BTCUSDT"),
])
def test_blogger_formats(opening, protection, symbol):
    signal = parse_signal(merge_reply(opening, protection), source_name="test")
    assert signal.symbol == symbol
    if symbol == "UAIUSDT":
        assert signal.take_profits == [Decimal("0.479"), Decimal("0.511"), Decimal("0.551")]


def test_live_reply_links_entry_and_executes_only_once_after_restart(tmp_path):
    async def run():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.set_manual_review(True)
        try:
            await service.telegram._handle_message(event(ENTRY, message_id=7606, age=180))
            assert not service.store.list()
            assert service.store.get_message(-100123, 7606)["status"] == "waiting"
            await service.telegram._handle_message(reply())
            assert len(service.store.list()) == 1
            signal = service.store.latest()
            assert signal.source_message_id == 7606
            assert "#7608" in signal.raw_text
            assert service.store.get_message(-100123, 7606)["signal_id"] == signal.id
            assert service.store.get_message(-100123, 7608)["signal_id"] == signal.id
            # A fresh service uses persisted identities; a different repeated reply cannot create another order.
            other = CopierService(settings(tmp_path))
            other.telegram.client = FakeTelegram()
            other.set_manual_review(True)
            try:
                await other.telegram._handle_message(reply(identifier=7610))
                assert len(other.store.list()) == 1
            finally:
                await other.bitget.close()
        finally:
            await service.bitget.close()
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["missing_parent", "wrong_symbol", "old_entry", "old_reply", "history_parent", "edited_reply", "no_reply", "bad_geometry", "cross_channel"])
def test_unsafe_correlations_do_not_trade(tmp_path, kind):
    async def run():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.set_manual_review(True)
        try:
            if kind != "missing_parent":
                await service.telegram._handle_message(event(ENTRY, message_id=7606, age=1000 if kind == "old_entry" else 30))
            if kind == "history_parent":
                parent = service.store.get_message(-100123, 7606)
                # Use another identity to model a history-only root without overwriting live evidence.
                parent.update(message_id=7605, origin="history")
                service.record_telegram_message(parent)
            incoming = reply(parent=7605 if kind == "history_parent" else 7606, age=180 if kind == "old_reply" else 0)
            if kind == "wrong_symbol": incoming.raw_text = "#BTC\n" + PROTECTION
            if kind == "bad_geometry": incoming.raw_text = PROTECTION.replace("0.425", "0.6")
            if kind == "no_reply": incoming.message.reply_to_msg_id = None
            if kind == "cross_channel":
                from telethon.tl.types import PeerChannel
                incoming.message.reply_to = SimpleNamespace(reply_to_peer_id=PeerChannel(999))
            await service.telegram._handle_message(incoming, edited=kind == "edited_reply")
            assert not service.store.list()
        finally:
            await service.bitget.close()
    asyncio.run(run())


def test_history_merges_reverse_order_without_execution(tmp_path):
    async def run():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.telegram.connected = True
        async def history(chat_id, limit):
            yield SimpleNamespace(id=7608, message=PROTECTION, reply_to_msg_id=7606, date=datetime.now(UTC))
            yield SimpleNamespace(id=7606, message=ENTRY, date=datetime.now(UTC)-timedelta(seconds=78))
        service.telegram.client.iter_messages = history
        try:
            await service.telegram.sync_history(-100123)
            result = service.store.get_message(-100123, 7608)
            assert result["parsed_signal"]["symbol"] == "UAIUSDT"
            assert result["merged_messages"][0]["message_id"] == 7606
            assert not service.store.list()
        finally:
            await service.bitget.close()
    asyncio.run(run())
