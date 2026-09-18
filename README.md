# Telegram Groq AI Bot: Python Telegram Chatbot Powered by Groq

![Python](https://img.shields.io/badge/python-3.9%2B-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Telegram Bot](https://img.shields.io/badge/Telegram-bot-26A5E4)
![Groq](https://img.shields.io/badge/LLM-Groq-orange)

A fast **Telegram AI chatbot written in Python** and powered by the [Groq API](https://console.groq.com/). Chat with reasoning models such as `qwen/qwen3.8-27b`, `openai/gpt-oss-20b` or `groq/compound-mini` straight from Telegram. It uses `python-telegram-bot` with async long polling, so you can self-host it on your own computer or server without a public URL. Optionally, it can connect to your own Telegram account so you can ask about recent chats and unread messages.

<!-- TODO: add a screenshot or GIF of the bot replying, e.g. docs/demo.gif, then embed it: ![Telegram Groq bot demo](docs/demo.gif) -->

## Table of contents

- [Features](#features)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Configuration reference (.env template)](#configuration-reference-env-template)
- [Setup guide: get every .env value](#setup-guide-get-every-env-value)
- [Bot commands](#bot-commands)
- [Choosing a Groq model](#choosing-a-groq-model)
- [Telegram account access (optional)](#telegram-account-access-optional)
- [Public vs. private bot](#public-vs-private-bot)
- [Running in the background](#running-in-the-background)
- [Project structure](#project-structure)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Security](#security)
- [Tech stack](#tech-stack)
- [Contributing](#contributing)
- [License](#license)

## Features

- **Fast replies** from Groq's LLM inference API
- **Reasoning model support**: control whether the model's thinking is hidden or shown (`REASONING_FORMAT`, `SHOW_THINKING`)
- **Any Groq model**: switch by changing one line in `.env`
- **Custom personality**: set your own `SYSTEM_PROMPT` and `TEMPERATURE`
- **Per-chat conversation memory**: the last 20 exchanges are kept for each chat, with the system prompt preserved
- **Optional Telegram account access**: ask about your recent chats and unread messages
- **Access control**: restrict the bot to your own Telegram chat ID
- **Async** implementation using `python-telegram-bot` and long polling (no webhook or public server needed)
- **Two versions**: a full-featured bot (`bot.py`) and a lightweight one (`bot_simple.py`) that uses only `requests` and `groq`

## How it works

1. The bot polls Telegram for new messages.
2. It checks the sender against `CHAT_ID`.
3. It adds your message to that chat's history and sends the conversation, with your system prompt, to the Groq chat completions API.
4. It sends the answer back to you in Telegram. If enabled, reasoning text is hidden.

## Quick start

### 1. Get your credentials

You need a Telegram bot token, a Groq API key and your Telegram chat ID. Optionally you also need a Telegram API ID, API hash and phone number for account features. Every value is explained step by step in the [setup guide](#setup-guide-get-every-env-value) below.

### 2. Install

```bash
git clone https://github.com/maldyaya350-ops/AI_BOT.git
cd AI_BOT
pip install -r requirements.txt
```

Requires Python 3.9 or newer.

### 3. Configure

Save the [`.env` template](#configuration-reference-env-template) as `.env.example` in the project folder, then copy it:

```bash
cp .env.example .env      # Windows: copy .env.example .env
```

Open `.env` and replace each `YOUR_...` placeholder with your own value.

> `.env` holds secrets. It is listed in `.gitignore` and must never be committed.

### 4. Check your setup (optional)

The [verification script](#step-7-verify-everything) confirms your bot token, Groq key, model and chat ID all work.

### 5. Run

```bash
python bot.py
```

On Windows you can also double-click `run.bat`. Then open your bot in Telegram and send `/start`.

## Configuration reference (.env template)

Save this block as `.env.example` (it contains placeholders only, so it is safe to commit), then copy it to `.env` and fill in your own values:

```env
# Copy this file to .env and fill in your own values. Never commit .env.

# ---------- Telegram bot (required) ----------
# Token from @BotFather
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN

# Your numeric Telegram chat ID. Only this chat can use the bot.
# Strongly recommended: leave it empty and anyone can talk to the bot.
CHAT_ID=YOUR_TELEGRAM_CHAT_ID

# ---------- Telegram account access (optional) ----------
# Needed only for features that read your own chats / unread messages.
# Get API ID and hash at https://my.telegram.org -> API development tools
TELEGRAM_API_ID=YOUR_TELEGRAM_API_ID
TELEGRAM_API_HASH=YOUR_TELEGRAM_API_HASH
# Your phone number in international format, e.g. +201234567890
TELEGRAM_PHONE=YOUR_TELEGRAM_PHONE

# ---------- Groq (required) ----------
# Key from https://console.groq.com/keys
GROQ_API_KEY=YOUR_GROQ_API_KEY

# Model ID. Check the Groq console for current availability, pricing and limits.
GROQ_MODEL=qwen/qwen3.8-27b
# Alternatives:
# GROQ_MODEL=openai/gpt-oss-20b
# GROQ_MODEL=groq/compound-mini

# ---------- Reasoning and reply style ----------
# How reasoning output is handled (hidden = don't return the thinking)
REASONING_FORMAT=hidden
# true = show the model's thinking in replies, false = final answer only
SHOW_THINKING=false
# 0.0 = focused/deterministic, higher = more creative
TEMPERATURE=0.7
SYSTEM_PROMPT=You are a helpful, friendly AI assistant. Think step-by-step internally but only show the final answer. Be concise but thorough. You are chatting via Telegram. You have access to the user's Telegram account to answer about recent chats and unread messages.
```

Every variable at a glance:

| Variable | Where it comes from | Required |
|---|---|---|
| `BOT_TOKEN` | @BotFather on Telegram ([Step 2](#step-2-get-bot_token-telegram-bot-token)) | Yes |
| `CHAT_ID` | @userinfobot, `/chatid`, or the Telegram Bot API ([Step 5](#step-5-get-chat_id)) | Strongly recommended. If empty, anyone can use the bot |
| `GROQ_API_KEY` | Groq console ([Step 3](#step-3-get-groq_api_key)) | Yes |
| `GROQ_MODEL` | Groq model list ([Step 4](#step-4-choose-groq_model-and-the-reply-style-settings)) | No |
| `TELEGRAM_API_ID` | my.telegram.org ([Step 6](#step-6-get-the-telegram-api-id-api-hash-and-phone)) | Account features only |
| `TELEGRAM_API_HASH` | my.telegram.org ([Step 6](#step-6-get-the-telegram-api-id-api-hash-and-phone)) | Account features only |
| `TELEGRAM_PHONE` | Your own phone number, international format | Account features only |
| `REASONING_FORMAT` | You choose. `hidden` keeps thinking out of replies | No |
| `SHOW_THINKING` | You choose. `true` shows the thinking in Telegram, `false` shows only the final answer | No |
| `TEMPERATURE` | You choose. Lower is more focused, higher is more creative (default `0.7`) | No |
| `SYSTEM_PROMPT` | You write it. Sets the bot's behavior and personality | No |

Each line in `.env` must be on its own line. Two settings joined on one line will not be read.

## Setup guide: get every .env value

This guide explains where each value comes from and how to check that everything works.

**Never paste real values into the README, an issue, a screenshot, or a commit.** The `.env` file and any Telegram session file must stay out of git.

### Step 1: Create the .env file safely

1. Copy the template:
   ```bash
   cp .env.example .env
   ```
   On Windows: `copy .env.example .env`
2. Make sure `.gitignore` contains these lines:
   ```
   .env
   *.session
   *.session-journal
   ```
   A Telegram session file gives full access to your account, so treat it like a password.
3. If `.env` was ever committed, stop tracking it:
   ```bash
   git rm --cached .env
   git commit -m "Stop tracking .env"
   ```
   Any key that was ever committed or posted publicly must be revoked and replaced (see [Step 8](#step-8-if-a-key-was-ever-exposed)).

### Step 2: Get BOT_TOKEN (Telegram bot token)

1. Open Telegram and search for [@BotFather](https://t.me/BotFather). Check for the verified badge.
2. Send `/newbot`.
3. Enter a display name (for example `My Groq Bot`).
4. Enter a unique username that ends in `bot` (for example `my_groq_helper_bot`).
5. BotFather replies with the **HTTP API token**. It looks like `123456789:ABC...`.
6. Put it in `.env`:
   ```env
   BOT_TOKEN=paste-the-token-here
   ```

**Already have a bot?** Send `/mybots` to BotFather, choose your bot, then **API Token**.

### Step 3: Get GROQ_API_KEY

1. Sign in at the [Groq console](https://console.groq.com/).
2. Open **API Keys** ([console.groq.com/keys](https://console.groq.com/keys)).
3. Click **Create API Key**, name it (for example `telegram-bot`), and submit.
4. **Copy the key immediately.** It is shown only once. Keys start with `gsk_`.
5. Put it in `.env`:
   ```env
   GROQ_API_KEY=paste-the-key-here
   ```

### Step 4: Choose GROQ_MODEL and the reply-style settings

List the models your key can use:

```bash
python -c "from dotenv import load_dotenv; load_dotenv(); from groq import Groq; print([m.id for m in Groq().models.list().data])"
```

Then set one:

```env
GROQ_MODEL=qwen/qwen3.8-27b
# alternatives:
# GROQ_MODEL=openai/gpt-oss-20b
# GROQ_MODEL=groq/compound-mini
```

Check the Groq console for each model's current pricing and limits. Free access to a model can change, so don't rely on a "free" label written months ago.

Reasoning models can think for a long time before answering, which makes replies slower and uses more of your quota. If the bot feels slow, try a smaller or non-reasoning model.

`REASONING_FORMAT`, `SHOW_THINKING`, `TEMPERATURE` and `SYSTEM_PROMPT` are your own choices. See the [variable table](#configuration-reference-env-template) for what each one does.

### Step 5: Get CHAT_ID

`CHAT_ID` locks the bot to one Telegram chat, which is you. This matters even more if you enable [Telegram account access](#telegram-account-access-optional), because an open bot could then be asked about your private chats by anyone.

Pick one method:

**Method A: @userinfobot (quickest).** Open [@userinfobot](https://t.me/userinfobot), press Start, and copy the numeric **Id**.

**Method B: the bot's `/chatid` command.**
1. Temporarily leave `CHAT_ID` empty and run `python bot.py`.
2. Send `/chatid` to your bot and copy the number.
3. Stop the bot and put the number in `.env`.

**Method C: the Telegram Bot API.**
1. Stop the bot (two programs can't poll the same bot at once).
2. Send any message to your bot.
3. Open this address in a browser, replacing `<TOKEN>` with your bot token:
   ```
   https://api.telegram.org/bot<TOKEN>/getUpdates
   ```
4. Copy the number from `"chat":{"id":123456789,...}`.
5. Don't share that URL, because it contains your token.

```env
CHAT_ID=123456789
```

Group chats have negative IDs (for example `-1001234567890`). That is normal.

### Step 6: Get the Telegram API ID, API hash and phone

These three are only needed for features that act as **your own Telegram account** (for example reading your recent chats and unread messages). They are separate from the bot token.

1. Go to [my.telegram.org](https://my.telegram.org) and log in with your phone number in international format (for example `+201234567890`). Telegram sends a login code to your Telegram app.
2. Click **API development tools**.
3. If you have no app yet, fill in the form: **App title** and **Short name** can be anything (for example `groq-bot`), and the platform can be `Other`. URL and description can be left blank or filled with placeholders.
4. Click **Create application**.
5. Copy **App api_id** (a number) and **App api_hash** (a long string of letters and digits).
6. Put them in `.env` together with your own phone number:
   ```env
   TELEGRAM_API_ID=1234567
   TELEGRAM_API_HASH=paste-the-hash-here
   TELEGRAM_PHONE=+201234567890
   ```

On the first run, the program will likely ask for a login code that Telegram sends to your Telegram app (and your two-step verification password if you have one), and then save a session file so you don't have to log in every time. Keep that session file private and out of git.

**Important:** these credentials give the program access to everything in your account. Only enable this if you understand that, keep `CHAT_ID` set, and never share the values. If Telegram shows a warning or blocks the login, don't try to work around it. Wait and try again later.

### Step 7: Verify everything

Save this as `check_env.py` and run `python check_env.py`:

```python
import os
import requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# Telegram bot token check
token = os.environ["BOT_TOKEN"]
r = requests.get(f"https://api.telegram.org/bot{token}/getMe", timeout=15).json()
print("Telegram OK:" if r.get("ok") else "Telegram FAILED:", r.get("result", r).get("username", r))

# Groq key and model check
model = os.getenv("GROQ_MODEL", "groq/compound-mini")
reply = Groq().chat.completions.create(
    model=model,
    messages=[{"role": "user", "content": "Say hi in three words."}],
    max_tokens=200,
)
print("Groq OK:", model, "->", reply.choices[0].message.content)

# Chat ID check
chat_id = os.getenv("CHAT_ID")
if chat_id:
    r = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": chat_id, "text": "Setup check passed."},
        timeout=15,
    ).json()
    print("CHAT_ID OK: message sent" if r.get("ok") else f"CHAT_ID FAILED: {r}")

# Telegram account variables (only checks they are filled in)
for name in ("TELEGRAM_API_ID", "TELEGRAM_API_HASH", "TELEGRAM_PHONE"):
    value = os.getenv(name, "")
    print(name, "set" if value and not value.startswith("YOUR_") else "MISSING (only needed for account features)")
```

Expected result: "Telegram OK", "Groq OK", "CHAT_ID OK", and a "Setup check passed." message in Telegram.

### Step 8: If a key was ever exposed

Treat any key that appeared in a public repo, README, screenshot, or chat as compromised.

- **Telegram bot:** BotFather, `/mybots`, your bot, **API Token**, **Revoke current token**.
- **Groq:** Groq console, **API Keys**, delete the exposed key and create a new one.
- **Telegram account:** if your API hash or session file leaked, go to Telegram **Settings, Devices** and terminate unknown sessions, and consider creating a new app at my.telegram.org.

Removing a secret from the latest commit does not remove it from git history, so revoking is what actually protects you.

## Bot commands

| Command | What it does |
|---|---|
| `/start` | Welcome message |
| `/help` | Help and your IDs |
| `/clear` | Clear the conversation memory for this chat |
| `/model` | Show the model currently in use |
| `/chatid` | Show your Telegram chat ID |

## Choosing a Groq model

Set `GROQ_MODEL` in `.env` and restart the bot.

```env
GROQ_MODEL=qwen/qwen3.8-27b      # 27B reasoning model with vision support
# GROQ_MODEL=openai/gpt-oss-20b  # fast 20B reasoning model
# GROQ_MODEL=groq/compound-mini  # agentic model with built-in tools
```

Model availability, pricing and free-tier limits change over time, so check the [Groq console](https://console.groq.com/) before you rely on one. Reasoning models may think for a while before answering, which can make replies slower and use more quota. To list the models your key can use, see [Step 4](#step-4-choose-groq_model-and-the-reply-style-settings).

## Telegram account access (optional)

With `TELEGRAM_API_ID`, `TELEGRAM_API_HASH` and `TELEGRAM_PHONE` set, the project can log in as your own Telegram account so the AI can answer questions about your recent chats and unread messages. See [Step 6](#step-6-get-the-telegram-api-id-api-hash-and-phone) for how to get the values.

Things to know before enabling it:

- It gives the program access to your private messages. Keep `CHAT_ID` set so nobody else can query them through the bot.
- Your message content is sent to Groq to generate answers. Read Groq's data policy first.
- The first login asks for a code sent to your Telegram app and creates a session file. Keep the session file private (`*.session` is in `.gitignore`).
- Leave these three variables out if you only want a normal chatbot.

## Public vs. private bot

- `CHAT_ID=<your id>`: only you can use the bot. **Recommended.**
- `CHAT_ID=` (empty): anyone who finds the bot's username can chat with it, and every message spends **your** Groq quota. Never do this together with Telegram account access.

## Running in the background

- **Windows:** run `run.bat`, or use Task Scheduler to start it at login.
- **Linux or macOS:** run it in `tmux` or `screen`, or:
  ```bash
  nohup python bot.py > bot.log 2>&1 &
  ```

Run only one copy of the bot at a time. Two copies polling the same token cause a `Conflict` error.

## Project structure

| File | Purpose |
|---|---|
| `bot.py` | Main bot (async, full features). **Use this one.** |
| `bot_simple.py` | Lightweight alternative using only `requests` and `groq` |
| `telegram_user.py` | Telegram account (user session) client for reading your chats |
| `telegram_tools.py` | Helper functions that fetch Telegram data for the AI |
| `requirements.txt` | Python dependencies |
| `run.bat` | Windows launcher |
| `.env.example` | Configuration template (safe to commit) |
| `.env` | Your real secrets (never commit) |

## Troubleshooting

| Problem | Cause and fix |
|---|---|
| `Unauthorized` / `Telegram FAILED` | `BOT_TOKEN` is wrong or was revoked. Redo [Step 2](#step-2-get-bot_token-telegram-bot-token) |
| `401` or `invalid_api_key` from Groq | `GROQ_API_KEY` is wrong or deleted. Redo [Step 3](#step-3-get-groq_api_key) |
| `model_not_found` (404) | The model was retired or isn't available to your key. Redo [Step 4](#step-4-choose-groq_model-and-the-reply-style-settings) |
| `Access denied` reply | Your chat ID doesn't match `CHAT_ID`. Redo [Step 5](#step-5-get-chat_id) |
| `chat not found` | You haven't messaged the bot yet, or `CHAT_ID` is wrong |
| `Conflict: terminated by other getUpdates request` | Another copy of the bot is running. Stop it |
| `BOT_TOKEN` not found although it is in `.env` | Two lines ran together with no line break. Put each `KEY=value` on its own line |
| Slow replies | Reasoning models think first. Try a smaller model |
| Telegram login code never arrives | Use international phone format with `+` and the country code |
| No reply at all | Check the console logs and confirm your Groq key is valid |

## FAQ

**How do I make a Telegram bot with Groq?**
Create a bot with @BotFather, create a Groq API key, put both in `.env`, install the requirements, and run `python bot.py`. Full instructions are in the [setup guide](#setup-guide-get-every-env-value).

**Is the Groq API free?**
Groq has offered free access with rate limits, but which models are free changes. Check the [Groq console](https://console.groq.com/) for current pricing and limits.

**Can it read my Telegram messages?**
Only if you set the Telegram account variables and log in. Without them it is a normal chatbot.

**Can I use a different model?**
Yes. Set `GROQ_MODEL` in `.env` to any model your Groq account can access.

**Does the bot remember the conversation?**
Yes, the last 20 exchanges per chat. Send `/clear` to reset.

**Do I need a server or a public URL?**
No. The bot uses long polling, so it works from any computer with internet access.

## Security

- Keep `BOT_TOKEN`, `GROQ_API_KEY`, `TELEGRAM_API_HASH` and any `.session` file out of your code, README, screenshots, issues and commit history.
- Commit `.env.example` with placeholders, never `.env`.
- If a key is exposed, revoke it immediately ([Step 8](#step-8-if-a-key-was-ever-exposed)), then create a new one.
- Always set `CHAT_ID`, especially if Telegram account access is enabled.

## Tech stack

- [`python-telegram-bot`](https://python-telegram-bot.org/) 21.9 (async application, long polling)
- [`groq`](https://github.com/groq/groq-python) Python SDK (OpenAI-compatible chat completions)
- [`python-dotenv`](https://pypi.org/project/python-dotenv/) for configuration

## Contributing

Issues and pull requests are welcome. If you have an idea (voice messages, streaming replies, image input, Docker support), open an issue to discuss it first.

## License

Released under the MIT License. See [LICENSE](LICENSE).
