from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import urlencode

import httpx

from .config import Settings
from .models import ExecutionMethod, ParsedSignal, SignalSide


class BitgetError(RuntimeError):
    pass


def create_signature(
    *,
    timestamp: str,
    method: str,
    request_path: str,
    query_string: str,
    body: str,
    secret: str,
) -> str:
    suffix = f"?{query_string}" if query_string else ""
    prehash = f"{timestamp}{method.upper()}{request_path}{suffix}{body}"
    digest = hmac.new(secret.encode(), prehash.encode(), hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


@dataclass(frozen=True, slots=True)
class BitgetOrderResult:
    order_id: str
    client_oid: str
    raw: dict


class BitgetDemoClient:
    """Bitget client with explicit demo/live request isolation.

    Live credentials are accepted for read-only account checks. Order submission
    remains demo-only so a configuration mistake cannot send a real-money order.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._http = httpx.AsyncClient(
            base_url=settings.bitget_base_url,
            timeout=httpx.Timeout(10.0),
            headers={"User-Agent": "MiaCryptoCopier/0.1"},
        )

    async def close(self) -> None:
        await self._http.aclose()

    def _headers(
        self,
        *,
        timestamp: str,
        method: str,
        path: str,
        query_string: str = "",
        body: str = "",
    ) -> dict[str, str]:
        if not self.settings.bitget_configured:
            raise BitgetError("Bitget demo credentials are not configured")
        signature = create_signature(
            timestamp=timestamp,
            method=method,
            request_path=path,
            query_string=query_string,
            body=body,
            secret=self.settings.bitget_api_secret or "",
        )
        headers = {
            "ACCESS-KEY": self.settings.bitget_api_key or "",
            "ACCESS-SIGN": signature,
            "ACCESS-TIMESTAMP": timestamp,
            "ACCESS-PASSPHRASE": self.settings.bitget_api_passphrase or "",
            "Content-Type": "application/json",
            "locale": "zh-CN",
        }
        if self.settings.bitget_is_demo:
            # Bitget requires this header only for demo API keys.
            headers["paptrading"] = "1"
        return headers

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict | None = None,
    ) -> dict:
        query_string = urlencode(sorted((params or {}).items()))
        body = (
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
            if payload is not None
            else ""
        )
        timestamp = str(int(time.time() * 1000))
        request_target = f"{path}?{query_string}" if query_string else path
        response = await self._http.request(
            method,
            request_target,
            content=body.encode() if body else None,
            headers=self._headers(
                timestamp=timestamp,
                method=method,
                path=path,
                query_string=query_string,
                body=body,
            ),
        )
        try:
            data = response.json()
        except ValueError as exc:
            raise BitgetError(f"Bitget returned HTTP {response.status_code} without JSON") from exc
        if response.status_code >= 400 or data.get("code") != "00000":
            raise BitgetError(
                f"Bitget error {data.get('code', response.status_code)}: {data.get('msg', 'unknown error')}"
            )
        return data

    async def account_summary(self) -> dict:
        try:
            return await self._request(
                "GET",
                "/api/v2/mix/account/accounts",
                params={"productType": self.settings.bitget_product_type},
            )
        except BitgetError as exc:
            if "40085" not in str(exc):
                raise
            # Unified Trading Accounts reject classic V2 account endpoints.
            return await self._request("GET", "/api/v3/account/assets")

    async def account_settings(self) -> dict:
        return await self._request("GET", "/api/v3/account/settings")

    async def account_info(self) -> dict:
        return await self._request("GET", "/api/v3/account/info")

    async def current_positions(self) -> dict:
        return await self._request(
            "GET",
            "/api/v3/position/current-position",
            params={"category": self.settings.bitget_product_type},
        )

    async def open_orders(self) -> dict:
        return await self._request(
            "GET",
            "/api/v3/trade/unfilled-orders",
            params={
                "category": self.settings.bitget_product_type,
                "limit": "100",
            },
        )

    async def market_price(self, symbol: str) -> Decimal:
        """Read the public production-market mark price without authentication."""
        try:
            response = await self._http.get(
                "/api/v2/mix/market/ticker",
                params={
                    "symbol": symbol.upper(),
                    "productType": self.settings.bitget_product_type,
                },
            )
            data = response.json()
        except (ValueError, httpx.HTTPError) as exc:
            raise BitgetError(f"Failed to read Bitget market price for {symbol}") from exc
        if response.status_code >= 400 or data.get("code") != "00000":
            raise BitgetError(
                f"Bitget market error {data.get('code', response.status_code)}: "
                f"{data.get('msg', 'unknown error')}"
            )
        tickers = data.get("data") or []
        ticker = tickers[0] if isinstance(tickers, list) and tickers else tickers
        raw_price = ticker.get("markPrice") or ticker.get("lastPr") if ticker else None
        if not raw_price:
            raise BitgetError(f"Bitget returned no market price for {symbol}")
        return Decimal(str(raw_price))

    async def symbol_leverage_limits(self, symbol: str) -> tuple[int, int]:
        """Return Bitget's current public leverage range for a futures symbol."""
        try:
            response = await self._http.get(
                "/api/v2/mix/market/contracts",
                params={
                    "symbol": symbol.upper(),
                    "productType": self.settings.bitget_product_type,
                },
            )
            data = response.json()
        except (ValueError, httpx.HTTPError) as exc:
            raise BitgetError(f"Failed to read leverage limits for {symbol}") from exc
        if response.status_code >= 400 or data.get("code") != "00000":
            raise BitgetError(
                f"Bitget market error {data.get('code', response.status_code)}: "
                f"{data.get('msg', 'unknown error')}"
            )
        contracts = data.get("data") or []
        contract = contracts[0] if isinstance(contracts, list) and contracts else contracts
        if not contract or not contract.get("maxLever"):
            raise BitgetError(f"Bitget returned no leverage limits for {symbol}")
        return int(contract.get("minLever") or 1), int(contract["maxLever"])

    async def set_cross_leverage(self, symbol: str, leverage: int) -> None:
        if self.settings.bitget_margin_mode != "crossed":
            raise BitgetError("Automatic execution requires crossed margin mode")
        await self._request(
            "POST",
            "/api/v2/mix/account/set-leverage",
            payload={
                "symbol": symbol.upper(),
                "productType": self.settings.bitget_product_type,
                "marginCoin": self.settings.bitget_margin_coin,
                "leverage": str(leverage),
            },
        )

    async def healthcheck(self) -> tuple[bool, str]:
        if not self.settings.bitget_configured:
            return False, "Bitget API Key 未配置"
        try:
            result = await self.account_summary()
            account_data = result.get("data") or []
            environment = "模拟盘" if self.settings.bitget_is_demo else "实盘（只读接入）"
            if isinstance(account_data, dict):
                accounts = [account_data]
                assets = account_data.get("assets") or []
                coin_key = "coin"
            else:
                accounts = account_data
                assets = account_data
                coin_key = "marginCoin"
            coins = ", ".join(
                str(asset.get(coin_key))
                for asset in assets
                if asset.get(coin_key)
            )
            suffix = f"，保证金币种 {coins}" if coins else ""
            return True, f"{environment}已连接，账户数 {len(accounts)}{suffix}"
        except (BitgetError, httpx.HTTPError) as exc:
            return False, str(exc)

    async def place_signal_order(
        self,
        signal: ParsedSignal,
        *,
        size: Decimal,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        execution_method: ExecutionMethod = ExecutionMethod.LIMIT,
    ) -> BitgetOrderResult:
        if not self.settings.bitget_is_demo:
            raise BitgetError(
                "Real-money order execution is not supported; live API access is read-only"
            )
        if not self.settings.bitget_enable_demo_orders:
            raise BitgetError("Demo order execution is disabled by configuration")
        client_oid = f"mia_{signal.id}"[:32]
        payload = {
            "symbol": signal.symbol,
            "productType": self.settings.bitget_product_type,
            "marginMode": self.settings.bitget_margin_mode,
            "marginCoin": self.settings.bitget_margin_coin,
            "size": format(size, "f"),
            "side": "buy" if signal.side == SignalSide.LONG else "sell",
            "tradeSide": "open",
            "orderType": execution_method.value,
            "clientOid": client_oid,
            "presetStopLossPrice": format(stop_loss, "f"),
            "presetStopSurplusPrice": format(take_profit, "f"),
        }
        if execution_method == ExecutionMethod.LIMIT:
            payload["price"] = format(entry_price, "f")
            payload["force"] = "gtc"
        result = await self._request(
            "POST", "/api/v2/mix/order/place-order", payload=payload
        )
        data = result["data"]
        return BitgetOrderResult(
            order_id=str(data["orderId"]),
            client_oid=str(data.get("clientOid") or client_oid),
            raw=result,
        )
