import asyncio
from dataclasses import replace
from decimal import Decimal

import pytest

from app.bitget import BitgetDemoClient, BitgetError, create_signature
from app.bitget_ws import create_websocket_signature
from app.config import load_settings
from app.models import ExecutionMethod
from app.parser import parse_signal


def test_signature_vector() -> None:
    signature = create_signature(
        timestamp="1685013478665",
        method="POST",
        request_path="/api/v2/mix/order/place-order",
        query_string="",
        body='{"symbol":"BTCUSDT","size":"0.1"}',
        secret="test-secret",
    )
    assert signature == "fzttmRGpqSCV67t/4PXwZyIqwzqlORQLM8w/UnaS4R4="


def test_signature_includes_sorted_query_string() -> None:
    signature = create_signature(
        timestamp="1685013478665",
        method="GET",
        request_path="/api/v2/mix/account/accounts",
        query_string="productType=USDT-FUTURES",
        body="",
        secret="test-secret",
    )
    assert signature == "i4KqIs8A64Dutx5cdQN8UiLaN2PvU2nb4DyZhyzpORU="


def test_websocket_login_signature() -> None:
    assert create_websocket_signature("1538054050", "test-secret") == (
        "2FD4UoGa/tQ8jgXnZzuSt9hpq4EWUe7BiZnqNJabjHs="
    )


def test_paper_trading_header_is_environment_specific() -> None:
    async def scenario() -> None:
        demo_client = BitgetDemoClient(
            replace(load_settings(), bitget_api_environment="demo")
        )
        live_client = BitgetDemoClient(
            replace(load_settings(), bitget_api_environment="live")
        )
        try:
            request = {"timestamp": "1", "method": "GET", "path": "/test"}
            assert demo_client._headers(**request)["paptrading"] == "1"
            assert "paptrading" not in live_client._headers(**request)
        finally:
            await demo_client.close()
            await live_client.close()

    asyncio.run(scenario())


def test_live_order_submission_is_blocked_before_network_access() -> None:
    async def scenario() -> None:
        client = BitgetDemoClient(
            replace(
                load_settings(),
                bitget_api_environment="live",
                bitget_enable_demo_orders=True,
            )
        )
        signal = parse_signal(
            "ETHUSDT SHORT\nEntry: 3680 - 3720\nSL: 3785\nTP1: 3560",
            source_name="test",
        )
        try:
            with pytest.raises(BitgetError, match="Real-money"):
                await client.place_signal_order(
                    signal,
                    size=Decimal("0.01"),
                    entry_price=signal.reference_entry,
                    stop_loss=signal.stop_loss,
                    take_profit=signal.take_profits[0],
                )
        finally:
            await client.close()

    asyncio.run(scenario())


def test_market_contract_order_omits_limit_only_fields(monkeypatch) -> None:
    async def scenario() -> None:
        client = BitgetDemoClient(
            replace(
                load_settings(),
                bitget_api_environment="demo",
                bitget_enable_demo_orders=True,
            )
        )
        signal = parse_signal(
            "ETHUSDT SHORT\nEntry: 3680 - 3720\nSL: 3785\nTP1: 3560",
            source_name="test",
        )
        captured = {}

        async def request(method, path, *, payload=None, params=None):
            captured.update(payload or {})
            return {"data": {"orderId": "demo-order", "clientOid": "demo-client"}}

        monkeypatch.setattr(client, "_request", request)
        try:
            await client.place_signal_order(
                signal,
                size=Decimal("0.05"),
                entry_price=signal.reference_entry,
                stop_loss=signal.stop_loss,
                take_profit=signal.take_profits[0],
                execution_method=ExecutionMethod.MARKET,
            )
            assert captured["orderType"] == "market"
            assert "price" not in captured
            assert "force" not in captured
        finally:
            await client.close()

    asyncio.run(scenario())
