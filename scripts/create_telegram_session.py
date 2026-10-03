from __future__ import annotations

import argparse
import asyncio
import getpass
import os
from pathlib import Path

from telethon import TelegramClient
from telethon.errors import SessionPasswordNeededError
from telethon.sessions import StringSession


async def main(output_env: Path | None = None) -> None:
    print("Veltrix Telegram Session Creator")
    print(
        "Run this only on a trusted local device. Never paste login codes or 2FA into an AI chat."
    )
    api_id = int(os.getenv("TELEGRAM_API_ID") or input("TELEGRAM_API_ID: ").strip())
    api_hash = os.getenv("TELEGRAM_API_HASH") or getpass.getpass("TELEGRAM_API_HASH: ")
    phone = input("Telegram phone (+country...): ").strip()

    if output_env is not None:
        if not output_env.parent.is_dir():
            raise ValueError("Create a private output directory before running this command.")
        if output_env.exists():
            raise FileExistsError("Output already exists; refusing to overwrite a saved session.")

    client = TelegramClient(
        StringSession(), api_id, api_hash, device_model="Veltrix Session Setup", app_version="1.2.0"
    )
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
        if output_env is None:
            print(
                "\nTELEGRAM_SESSION created. Store this ONLY as a deployment secret env variable:"
            )
            print(session)
        else:
            # The local launcher restricts this directory to the current Windows user.
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            fd = os.open(output_env, flags, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as output:
                output.write(f"TELEGRAM_API_ID={api_id}\n")
                output.write(f"TELEGRAM_API_HASH={api_hash}\n")
                output.write(f"TELEGRAM_SESSION={session}\n")
            print("\nSUCCESS: Telegram session created and saved privately for Render.")
            print("No session secret was printed. Return to Codex and say: tayyor.")
    finally:
        await client.disconnect()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Create a Telegram session on a trusted device.")
    parser.add_argument(
        "--output-env",
        type=Path,
        help="Save deployment credentials to a new private .env file instead of printing them.",
    )
    args = parser.parse_args()
    try:
        asyncio.run(main(args.output_env))
    except (KeyboardInterrupt, EOFError):
        print("\nCancelled. No login credentials were displayed.")
        raise SystemExit(1) from None
    except Exception as exc:
        # Telegram errors can carry request details; show only the error type here.
        print(f"\nSession creation failed ({type(exc).__name__}). Retry locally.")
        raise SystemExit(1) from None
