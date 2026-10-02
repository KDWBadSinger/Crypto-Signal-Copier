import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.service import CopierService
from test_desktop_workflow import FakeTelegram, settings, event, TEXT


def test_receipt_is_published_before_slow_order_and_other_messages_continue(tmp_path):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        gate = asyncio.Event()
        started = asyncio.Event()
        async def slow_order(signal):
            started.set()
            await gate.wait()
        service.telegram.on_signal = slow_order
        queue = asyncio.Queue(maxsize=1)
        service._message_subscribers.add(queue)
        try:
            await service.telegram._on_new_message(event())
            await asyncio.wait_for(started.wait(), 1)
            assert queue.qsize() == 1
            message = service.telegram_inbox()["messages"][0]
            assert message["parsed_signal"]["symbol"] == "ETHUSDT"
            await service.telegram._on_new_message(event(text="今天市场波动大", message_id=2))
            await asyncio.sleep(0.01)
            assert service.store.get_message(-100123, 2)["status"] == "unparsed"
            gate.set()
            await asyncio.gather(*service.telegram._processing_tasks)
        finally:
            await service.telegram.stop()
            await service.bitget.close()
    asyncio.run(scenario())


def test_photo_only_is_recorded_not_dropped(tmp_path):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        received = event(text="", message_id=2)
        received.message.photo = object()
        try:
            await service.telegram._handle_message(received)
            message = service.telegram_inbox()["messages"][0]
            assert message["media_kind"] == "图片"
            assert message["status"] == "media"
            assert not service.store.list()
        finally:
            await service.bitget.close()
    asyncio.run(scenario())


def test_history_never_trades_and_cannot_overwrite_live_records(tmp_path):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.telegram.connected = True
        async def historical(chat_id, limit):
            yield SimpleNamespace(id=1, message=TEXT, date=datetime.now(UTC))
            yield SimpleNamespace(id=2, message="频道历史文字", date=datetime.now(UTC))
        service.telegram.client.iter_messages = historical
        async def forbidden(*args): pytest.fail("History triggered trading")
        service.telegram.on_signal = forbidden
        try:
            count = await service.telegram.sync_history(-100123)
            assert count == 2
            assert len(service.store.messages()) == 2
            assert not service.store.list()
            assert service.store.get_message(-100123, 1)["parsed_signal"]["symbol"] == "ETHUSDT"
            await service.telegram._handle_message(event())
            assert service.store.get_message(-100123, 1)["origin"] == "history"
            live = service.store.get_message(-100123, 2) | {"origin": "live", "text": "实时消息"}
            service.record_telegram_message(live)
            await service.telegram.sync_history(-100123)
            assert service.store.get_message(-100123, 2)["text"] == "实时消息"
            with pytest.raises(ValueError):
                await service.telegram.sync_history(-999)
        finally:
            await service.bitget.close()
    asyncio.run(scenario())


def test_edit_updates_preview_without_changing_original_signal_or_reexecuting(tmp_path):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.set_manual_review(True)
        try:
            await service.telegram._handle_message(event())
            original = service.store.latest()
            await service.telegram._handle_message(event(text=TEXT.replace("3785", "3800")), edited=True)
            message = service.telegram_inbox()["messages"][0]
            assert message["origin"] == "edit"
            assert message["parsed_signal"]["stop_loss"] == "3800"
            assert message["execution"]["stop_loss"] == "3785"
            assert service.store.latest() == original
            service.record_telegram_message({**message, "origin": "live", "text": TEXT})
            assert service.store.get_message(-100123, 1)["origin"] == "edit"
        finally:
            await service.bitget.close()
    asyncio.run(scenario())


def test_inbox_only_contains_selected_channels_and_explains_rejections(tmp_path):
    async def scenario():
        service = CopierService(settings(tmp_path))
        service.telegram.client = FakeTelegram()
        service.telegram.connected = True
        await service.telegram_channels()
        try:
            await service.telegram._handle_message(event(text="ETHUSDT LONG\nEntry: 3700\nSL: 4000\nTP1: 3900"))
            state = service.telegram_inbox()
            assert state["channels"][0]["title"] == "Test channel"
            assert state["messages"][0]["detail"] == "多单参数冲突：止损 4000 必须低于入场 3700"
            assert state["messages"][0]["execution"] is None
            assert service.telegram_inbox(-999)["messages"] == []
            system = await service.status()
            assert system.telegram_selected_channels[0]["title"] == "Test channel"
        finally:
            await service.bitget.close()
    asyncio.run(scenario())
