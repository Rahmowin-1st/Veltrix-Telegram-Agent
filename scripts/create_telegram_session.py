from __future__ import annotations

import asyncio
import getpass
import os

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


async def main() -> None:
    print("Veltrix Telegram Session Creator")
    print("Run this only on a trusted local device. Never paste login codes or 2FA into an AI chat.")
    api_id = int(os.getenv("TELEGRAM_API_ID") or input("TELEGRAM_API_ID: ").strip())
    api_hash = os.getenv("TELEGRAM_API_HASH") or getpass.getpass("TELEGRAM_API_HASH: ")
    phone = input("Telegram phone (+country...): ").strip()

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        code = getpass.getpass("Telegram login code (hidden): ")
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
        except SessionPasswordNeededError:
            password = getpass.getpass("Telegram 2FA password (hidden): ")
            await client.sign_in(password=password)
        session = client.session.save()
        print("\nTELEGRAM_SESSION created. Store this ONLY as a deployment secret env variable:")
        print(session)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
