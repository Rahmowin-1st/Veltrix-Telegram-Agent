# Veltrix Telegram Agent

Personal Telegram AI agent with two access layers:
- Telegram Business Bot for connected business chats.
- MTProto user session for owner-only account controls that Bot API cannot perform.

## Core
- Gemini 3.8 Flash agent + function calling
- Live public web research with Google Search grounding
- Public URL reading and public image/file download
- Telegram image/document/audio/voice/video understanding
- Recent chats/messages, Telegram search, bounded history
- Contacts: list, add/import, protected delete
- Send/edit/forward/pin/read/archive/mute
- Protected block/unblock and owner profile changes
- Per-chat wallpaper through MTProto where the Telegram schema supports it
- Per-chat memory with /memory and /forget
- High-impact actions protected by /confirm

## Access matrix
| Capability | Bot | Business | MTProto |
|---|---:|---:|---:|
| Direct Veltrix AI chat | ✅ | — | — |
| Connected-business replies | — | ✅ | — |
| Owner dialogs/history/search | ❌ | limited | ✅ |
| Send/edit/forward as owner | ❌ | limited | ✅ |
| Contacts/importContacts | ❌ | ❌ | ✅ |
| Archive/mute/block | ❌ | ❌ | ✅ |
| Owner profile changes | ❌ | ❌ | ✅ |
| Chat wallpaper | ❌ | ❌ | ✅ |
| Live public web search | ✅ | ✅ | ✅ |

Telegram permissions, privacy rules, flood limits and the active MTProto schema still apply.

## Security
Secrets are environment variables only. Never commit GEMINI_API_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_API_HASH or TELEGRAM_SESSION.
Never send Telegram OTP/login codes or 2FA passwords to an AI. Generate the MTProto session only on a trusted local device.
Read-only actions run immediately. Destructive/account-wide actions require a second explicit confirmation.
No spam, unsolicited mass messaging, stealth behavior, access-control bypass or private-account scraping.

## Required environment
AI: GEMINI_API_KEY, GEMINI_MODEL=gemini-3.8-flash.
Direct bot: TELEGRAM_BOT_TOKEN and OWNER_TELEGRAM_ID.
Full account tools: TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_SESSION.
Optional durable memory: DATABASE_URL (Postgres). Without it, local SQLite is used.

## Telegram setup
1. Create a bot with @BotFather and store the token only as TELEGRAM_BOT_TOKEN.
2. Connect it from Telegram Business settings and grant only the permissions you want.
3. AUTO_REPLY_BUSINESS=false is the safe default. Change to true only when automatic business replies are desired.
4. Set OWNER_TELEGRAM_ID to the owner's numeric Telegram user ID. Only that direct bot user gets MTProto account tools.
5. Get TELEGRAM_API_ID and TELEGRAM_API_HASH from my.telegram.org.
6. Run: python scripts/create_telegram_session.py on a trusted local device.
7. Store the produced StringSession only as TELEGRAM_SESSION in deployment secrets.

## Commands
/start, /help, /status, /tools, /memory on|off|status, /forget, /confirm TOKEN, /cancel

## Local run
python -m venv .venv
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 10000

Health endpoints: /healthz and /setup-status.
The HTTP service stays up in setup mode even when Telegram credentials are missing.

## Render
The repository includes render.yaml configured for the free plan. Free Render is useful for validation but is not a true always-on guarantee and may spin down.
Build: pip install -r requirements.txt
Start: uvicorn app.main:app --host 0.0.0.0 --port $PORT
Keep all secrets in Render environment variables, never in render.yaml.

## Tests
python -m pytest -q
ruff check .

## License
MIT