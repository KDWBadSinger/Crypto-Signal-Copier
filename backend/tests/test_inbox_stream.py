import asyncio
from contextlib import asynccontextmanager

from fastapi.testclient import TestClient

from app.service import CopierService
from test_desktop_workflow import FakeTelegram, settings, event


def test_websocket_push_delivers_messages_and_signal_details(tmp_path, monkeypatch):
    monkeypatch.setenv("APP_DATABASE_PATH", str(tmp_path / "api.sqlite3"))
    from app import main
    service = CopierService(settings(tmp_path))
    service.telegram.client = FakeTelegram()
    service.telegram.connected = True
    service.set_manual_review(True)
    monkeypatch.setattr(main, "service", service)

    @asynccontextmanager
    async def isolated_lifespan(_):
        yield
        await service.bitget.close()
    monkeypatch.setattr(main.app.router, "lifespan_context", isolated_lifespan)
    with TestClient(main.app) as client:
        with client.websocket_connect("/api/telegram/stream?chat_id=-100123") as websocket:
            assert websocket.receive_json()["messages"] == []
            client.portal.call(service.telegram._handle_message, event())
            snapshot = websocket.receive_json()
            assert snapshot["messages"][0]["parsed_signal"]["symbol"] == "ETHUSDT"
            assert snapshot["messages"][0]["execution"]["status"] == "pending_review"
            signal_id = snapshot["messages"][0]["signal_id"]
            assert client.get(f"/api/signals/{signal_id}").json()["source_message_id"] == 1
            assert client.get("/api/telegram/inbox?chat_id=-999").json()["messages"] == []
    assert not service._message_subscribers
