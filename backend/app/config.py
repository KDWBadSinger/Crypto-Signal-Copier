from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ENV_PATH = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(ENV_PATH)


def update_env_file(updates: dict[str, str]) -> None:
    """Atomically update selected local .env values without touching other keys."""
    for key, value in updates.items():
        if "\n" in value or "\r" in value:
            raise ValueError(f"{key} cannot contain a newline")

    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    remaining = dict(updates)
    output: list[str] = []
    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if key in remaining:
            output.append(f"{key}={remaining.pop(key)}")
        else:
            output.append(line)
    output.extend(f"{key}={value}" for key, value in remaining.items())

    ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=".env.", suffix=".tmp", dir=ENV_PATH.parent, text=True
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write("\n".join(output) + "\n")
        os.replace(temporary_name, ENV_PATH)
    finally:
        if os.path.exists(temporary_name):
            os.unlink(temporary_name)


def _bool_env(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_set_env(name: str) -> frozenset[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return frozenset()
    return frozenset(int(item.strip()) for item in raw.split(",") if item.strip())


@dataclass(frozen=True, slots=True)
class Settings:
    environment: str
    host: str
    port: int
    database_path: Path
    seed_demo_data: bool
    telegram_api_id: int | None
    telegram_api_hash: str | None
    telegram_phone: str | None
    telegram_session_path: Path
    telegram_allowed_chat_ids: frozenset[int]
    bitget_api_key: str | None
    bitget_api_secret: str | None
    bitget_api_passphrase: str | None
    bitget_api_environment: str
    bitget_base_url: str
    bitget_product_type: str
    bitget_margin_mode: str
    bitget_margin_coin: str
    bitget_enable_demo_orders: bool
    require_manual_review: bool
    auto_demo_order_size: Decimal

    @property
    def telegram_configured(self) -> bool:
        return bool(
            self.telegram_api_id
            and self.telegram_api_hash
            and self.telegram_allowed_chat_ids
        )

    @property
    def bitget_configured(self) -> bool:
        return bool(
            self.bitget_api_key
            and self.bitget_api_secret
            and self.bitget_api_passphrase
        )

    @property
    def bitget_is_demo(self) -> bool:
        return self.bitget_api_environment == "demo"


def load_settings() -> Settings:
    api_id_raw = os.getenv("TELEGRAM_API_ID", "").strip()
    bitget_api_environment = os.getenv("BITGET_API_ENVIRONMENT", "demo").strip().lower()
    if bitget_api_environment not in {"demo", "live"}:
        raise ValueError("BITGET_API_ENVIRONMENT must be 'demo' or 'live'")
    return Settings(
        environment=os.getenv("APP_ENV", "development"),
        host=os.getenv("APP_HOST", "127.0.0.1"),
        port=int(os.getenv("APP_PORT", "8000")),
        database_path=Path(os.getenv("APP_DATABASE_PATH", "./data/mia_copier.sqlite3")),
        seed_demo_data=_bool_env("APP_SEED_DEMO_DATA", True),
        telegram_api_id=int(api_id_raw) if api_id_raw else None,
        telegram_api_hash=os.getenv("TELEGRAM_API_HASH") or None,
        telegram_phone=os.getenv("TELEGRAM_PHONE") or None,
        telegram_session_path=Path(os.getenv("TELEGRAM_SESSION_PATH", "./data/telegram")),
        telegram_allowed_chat_ids=_int_set_env("TELEGRAM_ALLOWED_CHAT_IDS"),
        bitget_api_key=os.getenv("BITGET_API_KEY") or None,
        bitget_api_secret=os.getenv("BITGET_API_SECRET") or None,
        bitget_api_passphrase=os.getenv("BITGET_API_PASSPHRASE") or None,
        bitget_api_environment=bitget_api_environment,
        bitget_base_url=os.getenv("BITGET_BASE_URL", "https://api.bitget.com").rstrip("/"),
        bitget_product_type=os.getenv("BITGET_PRODUCT_TYPE", "USDT-FUTURES").upper(),
        bitget_margin_mode=os.getenv("BITGET_MARGIN_MODE", "crossed").lower(),
        bitget_margin_coin=os.getenv("BITGET_MARGIN_COIN", "USDT").upper(),
        bitget_enable_demo_orders=_bool_env("BITGET_ENABLE_DEMO_ORDERS", False),
        require_manual_review=_bool_env("REQUIRE_MANUAL_REVIEW", True),
        auto_demo_order_size=Decimal(os.getenv("AUTO_DEMO_ORDER_SIZE", "0.01")),
    )
