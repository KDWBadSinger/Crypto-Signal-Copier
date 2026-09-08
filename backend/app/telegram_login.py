from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError

from .config import load_settings


async def login() -> None:
    settings = load_settings()
    if not settings.telegram_api_id or not settings.telegram_api_hash:
        raise SystemExit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH first.")
    phone = settings.telegram_phone or input("Telegram phone number: ").strip()
    settings.telegram_session_path.parent.mkdir(parents=True, exist_ok=True)
    client = TelegramClient(
        str(settings.telegram_session_path),
        settings.telegram_api_id,
        settings.telegram_api_hash,
    )
    await client.connect()
    try:
        if await client.is_user_authorized():
            me = await client.get_me()
            print(f"Already authorized as user {me.id}.")
            return
        sent = await client.send_code_request(phone)
        code = input("Telegram login code: ").strip()
        try:
            await client.sign_in(phone, code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            password = getpass.getpass("Telegram 2FA password: ")
            await client.sign_in(password=password)
        me = await client.get_me()
        print(f"Authorized as user {me.id}. Session saved locally.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(login())
