# Telegram Groq AI Bot

Easy Telegram AI powered by Groq. Chat with `groq/compound-mini` (or any Groq model) directly in Telegram.

Bot: **@Maldyaya_bot** (ID: 8513780659)

## Files
- `bot.py` — main bot (python-telegram-bot, async, full features) **← use this**
- `bot_simple.py` — lightweight alternative using only `requests` + `groq`
- `.env` — credentials (BOT_TOKEN, CHAT_ID, GROQ_API_KEY)
- `requirements.txt` — deps
- `run.bat` — double-click to start

## Quick Start
```bash
# 1. Install deps (already done in this setup)
pip install -r requirements.txt

# 2. Configure .env (already filled)
# BOT_TOKEN=8513780659:AAEfY9E_ystaZlZB4HlD6XagfXTMGrHYR6A
# CHAT_ID=8166156987  # restrict to you, or empty for public
# GROQ_API_KEY=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
# GROQ_MODEL=groq/compound-mini

# 3. Run
python bot.py
# or
./run.bat
```

Bot will:
- Poll Telegram via long polling
- Send startup notice to CHAT_ID
- Reply to any message using Groq

## Commands in Telegram
- `/start` — welcome
- `/help` — help + IDs
- `/clear` — clear memory
- `/model` — show model
- `/chatid` — show your chat ID

Memory: keeps last 20 exchanges per chat (system prompt preserved). Use `/clear` to reset.

## Change Model
Edit `.env`:
```
GROQ_MODEL=groq/compound
# alternatives: openai/gpt-oss-20b, openai/gpt-oss-120b, qwen/qwen3.8-27b, allam-2-7b
```
List available: `python -c "from groq import Groq; print([m.id for m in Groq(api_key='...').models.list().data])"`

Restart bot after change.

## Make Public / Private
- `CHAT_ID=8166156987` → only you can use it (your ID)
- `CHAT_ID=` (empty) → anyone who knows bot username can chat

Find your ID: send `/chatid` to bot, or check @userinfobot

## Verify Setup (already tested)
```bash
# Telegram
curl https://api.telegram.org/bot$BOT_TOKEN/getMe

# Groq
python -c "from groq import Groq; c=Groq(api_key='...').chat.completions.create(model='groq/compound-mini', messages=[{'role':'user','content':'hi'}], max_tokens=20); print(c.choices[0].message.content)"

# Send test message
python -c "import requests; requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage', json={'chat_id':CHAT_ID,'text':'hello'})"
```
Both APIs verified working on 2026-09-16.

## Troubleshooting
- `Unauthorized` → BOT_TOKEN wrong
- `Groq 404 model_not_found` → model decommissioned, switch to `groq/compound-mini` (check models.list())
- `Access denied` → you're not CHAT_ID; set CHAT_ID empty to allow all
- No reply → check logs, ensure Groq key valid

## Security
- `.env` is gitignored ideally — don't commit keys
- Prefer env vars over hardcoded tokens in code

## Tech
- `python-telegram-bot==21.9` (async Application + polling)
- `groq==1.7.0` (OpenAI-compatible)
- `python-dotenv` for config
