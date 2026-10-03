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

## Natural-language account agent

Normal use is natural speech, not `/do` or JSON. The model performs function calls internally and
returns only a concise result. Examples (not automatic background tasks):

- “Admin menga yuborgan oxirgi musiqani Savedga tashla va music deb tag qil.”
- “Shu xabarimga bot sifatida 👍 reaction bos.”
- “Falona kanaldagi oxirgi postga mening akkauntimdan ❤️ bos.”
- “Bu stiker packni qo‘shib qo‘y.”
- “Kanaldagi huquqlarimni tekshir, tavsifini mana bunday yangila.”
- “Savedga Markdown formatida haftalik kreativ kontent reja yubor.”

The agent resolves local contact/dialog names, filters incoming music separately from voice notes,
forwards media to Saved Messages, and uses **native Premium reaction tags**, not fake hashtag labels.
It will ask if multiple contacts match. Partial success (saved but tagging failed) is reported without
re-forwarding. Bot reactions are bound to the current user message server-side; user-account
reactions use MTProto message IDs. Paid reactions are not supported or purchased.

Channel tools include join/leave/create, information/members, title/about, admin rights and ban/unban,
subject to existing Telegram permissions. Sticker tools include installed packs, inspect/install/remove,
send/favorite, and create/add to personal packs **from existing sticker messages**. Creative UTF-8
txt/md/csv/json/svg/html files can be sent; arbitrary code execution and arbitrary server file reads are
not exposed. This is a broad supported toolset, **not a claim that every Telegram API method is available**.

Protected actions show human-readable confirmation buttons; “tasdiqlayman” works only when exactly one
pending action exists. Tokens remain private, chat-bound, expiring and single-use. Other users, business
chats and groups cannot access the owner's account. High-impact confirmation cannot be disabled by
setting REQUIRE_CONFIRMATION=false. Gemini thought signatures/call IDs are retained internally; thinking
text/intermediate tools are not sent to the user. Permanent provider errors are not repeatedly retried.

The user client identifies itself as `Veltrix Telegram Agent`; the local login helper as
`Veltrix Session Setup`. Render deployments in Frankfurt can appear as Germany in Telegram devices.
Location alone does not prove a session is trusted. Revoked sessions are not silently logged in again.

## Optional diagnostic commands
/start, /help, /status, /whoami, /tools, /memory on|off|status, /forget, /confirm TOKEN, /cancel

Account commands work only in the configured owner's **private bot chat**, without needing Gemini:
- `/account`: actual connected account identity (phone hidden).
- `/chats [limit]`: recent chats (1–100).
- `/messages PEER [limit]`: recent messages; `me`, `@username`, or numeric chat ID.
- `/search QUERY`: global account message search (20 results).
- `/contacts [limit]`: contacts (1–100).
- `/actions`: available mutation names and examples.
- `/do ACTION JSON`: preview a change; **all direct mutations** need a separate `/confirm TOKEN`.

Example: `/do send_message {"peer":"me","text":"Hello"}` previews a Saved Messages send.
Confirmation tokens expire after five minutes, are chat-bound and single-use. Restart clears pending tokens.
`/status` distinguishes configured credentials from an actual MTProto connection and reports owner/account
alignment only to the owner. It does not test AI provider quota/availability. Group/business chats cannot
invoke account tools, and model tool dispatch enforces this independently of tool declarations.

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
