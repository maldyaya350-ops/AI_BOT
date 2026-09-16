"""
telegram_tools.py — Explicit tool definitions for Groq function calling.
Only these functions are exposed to the LLM. No arbitrary Telethon or OS access.

Safety:
  - Read-only tools execute immediately (no confirmation).
  - Write tools (send/reply) require explicit user confirmation via pending store.
  - All inputs validated, limits enforced, errors sanitized.
  - Minimal data retrieval (limited windows), no bulk operations.
  - Dangerous ops (delete, ban, leave, join, etc.) are NOT exposed.

Privacy: never logs message contents; truncates; minimizes Groq payload.
"""

import time
import json
import logging
import html as html_lib
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Inline keyboard helper (optional, requires python-telegram-bot)
try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
except ImportError:
    InlineKeyboardButton = None  # type: ignore
    InlineKeyboardMarkup = None  # type: ignore

# Import low-level API
try:
    import telegram_user
except ImportError:
    telegram_user = None  # type: ignore

# ------------------------------------------------------------------ #
# Confirmation store for write operations
# Key: bot_chat_id (int) — the Telegram bot chat where user interacts
# Value: {chat, message, reply_to, timestamp, preview}
# ------------------------------------------------------------------ #
_pending_sends: dict[int, dict[str, Any]] = {}
PENDING_TTL = 300  # seconds
CONFIRM_WORDS = {
    "yes", "yep", "yes please", "send", "confirm", "do it", "go ahead",
    "نعم", "ارسل", "أرسل", "موافق", "تأكيد", "ok", "okay"
}
CANCEL_WORDS = {"cancel", "no", "stop", "abort", "لا", "إلغاء", "الغاء"}


def _clean_expired():
    now = time.time()
    expired = [k for k, v in _pending_sends.items() if now - v.get("ts", 0) > PENDING_TTL]
    for k in expired:
        _pending_sends.pop(k, None)


def has_pending(bot_chat_id: int) -> bool:
    _clean_expired()
    return bot_chat_id in _pending_sends


def get_pending(bot_chat_id: int) -> dict | None:
    _clean_expired()
    return _pending_sends.get(bot_chat_id)


def clear_pending(bot_chat_id: int):
    _pending_sends.pop(bot_chat_id, None)


def set_pending(bot_chat_id: int, chat: str, message: str, reply_to: int | None):
    _clean_expired()
    preview = message[:150] + ("…" if len(message) > 150 else "")
    _pending_sends[bot_chat_id] = {
        "chat": chat,
        "message": message,
        "reply_to": reply_to,
        "ts": time.time(),
        "preview": preview,
    }


def is_confirmation_text(text: str) -> str | None:
    """Returns 'confirm'|'cancel'|None based on user text."""
    t = text.strip().lower()
    # Strip punctuation
    t_simple = t.strip(" .!،")
    if t_simple in CONFIRM_WORDS:
        return "confirm"
    if t_simple in CANCEL_WORDS:
        return "cancel"
    # Also handle "yes, send it" etc
    if any(w in t_simple for w in ["yes", "confirm", "send it", "go ahead"]):
        # avoid false positives for long sentences
        if len(t_simple) < 30:
            return "confirm"
    return None


def _sanitize(val: Any, max_len: int = 4000) -> str:
    s = str(val) if not isinstance(val, str) else val
    if len(s) > max_len:
        s = s[:max_len] + "…"
    return s


# ------------------------------------------------------------------ #
# Groq tool definitions (OpenAI-compatible function calling)
# ------------------------------------------------------------------ #

TOOL_DEFINITIONS: list[dict] = [
    {
        "type": "function",
        "function": {
            "name": "telegram_get_me",
            "description": "Get info about the authenticated Telegram user account (your own account). Use to verify login or get own username/id.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_list_dialogs",
            "description": "List recent Telegram conversations/dialogs (chats, groups, channels, private chats). Shows titles, usernames, unread counts, last message preview. Use to find who you talked to recently.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of dialogs to return (1-20, default 10). Keep low.",
                        "minimum": 1,
                        "maximum": 20,
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_get_recent_messages",
            "description": "Get recent messages from a specific chat. Chat can be username (@username or username), phone, or numeric ID. Returns limited window of messages.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {
                        "type": "string",
                        "description": "Chat identifier: username, @username, t.me link, numeric ID, or title if unambiguous.",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Number of messages to fetch (1-20, default 10). Keep minimal.",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["chat"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_search_messages",
            "description": "Search for messages containing a query string. Optionally restrict to a specific chat. Use to find messages about a topic.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query (1-200 chars).",
                    },
                    "chat": {
                        "type": "string",
                        "description": "Optional: chat to search inside. If omitted, searches globally (still limited).",
                    },
                    "limit": {
                        "type": "integer",
                        "description": "Max results (1-20, default 10).",
                        "minimum": 1,
                        "maximum": 20,
                    },
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_get_chat_info",
            "description": "Get basic info about a chat/channel/group by username or ID (title, type, member count if available).",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {
                        "type": "string",
                        "description": "Chat identifier (username, @username, ID)",
                    }
                },
                "required": ["chat"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_get_user_info",
            "description": "Get basic info about a Telegram user by username, phone, or ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "User identifier (username, @username, phone, or numeric ID)",
                    }
                },
                "required": ["user"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_get_unread",
            "description": "Get dialogs with unread messages (where API permits). Returns chats with unread counts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Max unread dialogs to return (1-20, default 10)",
                        "minimum": 1,
                        "maximum": 20,
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_send_message",
            "description": "Request to send a Telegram message to a chat. IMPORTANT: This does NOT send immediately — it asks the user for confirmation first. Only call when you have a clear reason and the destination is unambiguous. Do NOT call for mass messaging.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {
                        "type": "string",
                        "description": "Destination chat: username, @username, phone, or ID. Must be a known/verified chat.",
                    },
                    "message": {
                        "type": "string",
                        "description": "Message text to send (1-4096 chars). Be concise and helpful.",
                    },
                    "reply_to": {
                        "type": "integer",
                        "description": "Optional message ID to reply to. Use telegram_get_recent_messages to get IDs if needed.",
                    },
                },
                "required": ["chat", "message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_reply",
            "description": "Request to reply to a specific message in a chat. Same confirmation behavior as telegram_send_message — will ask user to confirm before sending.",
            "parameters": {
                "type": "object",
                "properties": {
                    "chat": {
                        "type": "string",
                        "description": "Chat identifier",
                    },
                    "reply_to": {
                        "type": "integer",
                        "description": "Message ID to reply to (required)",
                    },
                    "message": {
                        "type": "string",
                        "description": "Reply text (1-4096 chars)",
                    },
                },
                "required": ["chat", "reply_to", "message"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_get_my_sent_messages",
            "description": "Get your most recent OUTGOING messages (you sent) across all chats, EXCLUDING the bot's own chat (Maldyaya_bot) and excluding the current query message. Use this when user asks in Arabic 'ما اخر رسالة أرسلتها / آخر رسالة أرسلتها بنفسي / last message I sent'. Returns last sent texts with chat name and date, most recent first.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "Number of last sent messages to return (1-10, default 5).",
                        "minimum": 1,
                        "maximum": 10,
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "telegram_call",
            "description": "Request a Telegram voice call to a user via your authenticated account (MTProto). IMPORTANT: Requires user confirmation like send_message. VoIP may not be supported on all clients; if fails will explain. Use when user explicitly says 'اتصل بـ / call Ahmed'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user": {
                        "type": "string",
                        "description": "User to call: username, @username, phone, or numeric ID.",
                    }
                },
                "required": ["user"],
                "additionalProperties": False,
            },
        },
    },
]

# Set of read-only tools (no confirmation)
READ_ONLY_TOOLS = {
    "telegram_get_me",
    "telegram_list_dialogs",
    "telegram_get_recent_messages",
    "telegram_search_messages",
    "telegram_get_chat_info",
    "telegram_get_user_info",
    "telegram_get_unread",
    "telegram_get_my_sent_messages",
}

WRITE_TOOLS = {"telegram_send_message", "telegram_reply", "telegram_call"}


# ------------------------------------------------------------------ #
# Dispatcher: called by bot.py when Groq returns tool_calls
# ------------------------------------------------------------------ #
async def dispatch_tool(name: str, arguments: dict, bot_chat_id: int | None = None) -> str:
    """
    Execute a tool and return JSON string result for LLM.
    For write tools, returns a confirmation prompt instead of sending immediately.
    bot_chat_id is the bot interaction chat (for confirmation routing).
    """
    if telegram_user is None:
        return json.dumps({"error": "Telegram user client not available. Install telethon and configure .env"}, ensure_ascii=False)

    if not telegram_user.is_configured():
        return json.dumps({"error": "Telegram API not configured. Set TELEGRAM_API_ID/HASH/PHONE in .env and run: python telegram_user.py"}, ensure_ascii=False)

    # Check auth without prompting
    client = await telegram_user.ensure_client()
    if client is None:
        return json.dumps({"error": "Telegram user not authenticated. Run 'python telegram_user.py' to login (creates session file)."}, ensure_ascii=False)

    try:
        if name == "telegram_get_me":
            data = await telegram_user.api_get_me()
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            # Sanitize phone for LLM (partial)
            if data.get("phone"):
                p = str(data["phone"])
                data["phone"] = p[:4] + "***" + p[-3:] if len(p) > 5 else "***"
            return json.dumps({"result": data}, ensure_ascii=False)

        elif name == "telegram_list_dialogs":
            limit = int(arguments.get("limit", 10))
            limit = max(1, min(limit, 20))
            data = await telegram_user.api_list_dialogs(limit=limit)
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            # Return minimal for LLM
            return json.dumps({"result": data}, ensure_ascii=False, default=str)

        elif name == "telegram_get_recent_messages":
            chat = str(arguments.get("chat", "")).strip()
            limit = int(arguments.get("limit", 10))
            limit = max(1, min(limit, 20))
            if not chat:
                return json.dumps({"error": "chat is required"}, ensure_ascii=False)
            data = await telegram_user.api_get_recent_messages(chat, limit=limit)
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            if isinstance(data, str):
                return json.dumps({"error": data}, ensure_ascii=False)
            return json.dumps({"result": data}, ensure_ascii=False, default=str)

        elif name == "telegram_search_messages":
            query = str(arguments.get("query", "")).strip()
            chat = arguments.get("chat")
            if chat is not None:
                chat = str(chat).strip() or None
            limit = int(arguments.get("limit", 10))
            limit = max(1, min(limit, 20))
            data = await telegram_user.api_search_messages(query, chat=chat, limit=limit)
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            if isinstance(data, str):
                return json.dumps({"error": data}, ensure_ascii=False)
            return json.dumps({"result": data}, ensure_ascii=False, default=str)

        elif name == "telegram_get_chat_info":
            chat = str(arguments.get("chat", "")).strip()
            data = await telegram_user.api_get_chat_info(chat)
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            if isinstance(data, str):
                return json.dumps({"error": data}, ensure_ascii=False)
            return json.dumps({"result": data}, ensure_ascii=False)

        elif name == "telegram_get_user_info":
            user = str(arguments.get("user", "")).strip()
            data = await telegram_user.api_get_user_info(user)
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            if isinstance(data, str):
                return json.dumps({"error": data}, ensure_ascii=False)
            if data.get("phone"):
                p = str(data["phone"])
                data["phone"] = p[:4] + "***" + p[-3:] if len(p) > 5 else "***"
            return json.dumps({"result": data}, ensure_ascii=False)

        elif name == "telegram_get_unread":
            limit = int(arguments.get("limit", 10))
            limit = max(1, min(limit, 20))
            data = await telegram_user.api_get_unread(limit=limit)
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            return json.dumps({"result": data}, ensure_ascii=False, default=str)

        elif name == "telegram_get_my_sent_messages":
            limit = int(arguments.get("limit", 5))
            limit = max(1, min(limit, 10))
            data = await telegram_user.api_get_my_last_sent_messages(limit=limit, exclude_bot=True)
            if data is None:
                return json.dumps({"error": "Not authenticated"}, ensure_ascii=False)
            return json.dumps({"result": data}, ensure_ascii=False, default=str)

        elif name == "telegram_call":
            user = str(arguments.get("user", "")).strip()
            if not user:
                return json.dumps({"error": "user is required"}, ensure_ascii=False)
            if bot_chat_id is None:
                return json.dumps({"error": "Missing confirmation context"}, ensure_ascii=False)
            # Similar to send: store pending call request for confirmation
            # Reuse pending store with special key
            set_pending(bot_chat_id, f"CALL:{user}", f"Voice call to {user}", None)
            return json.dumps(
                {
                    "status": "confirmation_required",
                    "user": user,
                    "instruction": "You MUST ask the user for explicit confirmation before calling. Show the user and ask 'Do you want me to call X? (yes/cancel)'. Do NOT call again until user confirms.",
                },
                ensure_ascii=False,
            )

        elif name in WRITE_TOOLS:
            # Confirmation mechanism: don't send yet
            chat = str(arguments.get("chat", "")).strip()
            message = str(arguments.get("message", "")).strip()
            reply_to = arguments.get("reply_to")
            if reply_to is not None:
                try:
                    reply_to = int(reply_to)
                except Exception:
                    return json.dumps({"error": "reply_to must be integer message ID"}, ensure_ascii=False)
            if not chat or not message:
                return json.dumps({"error": "chat and message are required"}, ensure_ascii=False)
            if len(message) > 4096:
                return json.dumps({"error": "message too long (max 4096)"}, ensure_ascii=False)
            # Prevent mass/bulk: single message only, rate not enforced here but via FloodWait later
            # Store pending
            if bot_chat_id is None:
                # Fallback: execute directly if no bot context (should not happen)
                return json.dumps({"error": "Missing confirmation context"}, ensure_ascii=False)
            set_pending(bot_chat_id, chat, message, reply_to)
            # Return to LLM instructing it to ask user
            return json.dumps(
                {
                    "status": "confirmation_required",
                    "chat": chat,
                    "message_preview": message[:200] + ("…" if len(message) > 200 else ""),
                    "reply_to": reply_to,
                    "instruction": "You MUST ask the user for explicit confirmation before sending. Show the message and destination and ask 'Do you want me to send it? (yes/cancel)'. Do NOT call the tool again until user confirms.",
                },
                ensure_ascii=False,
            )

        else:
            return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)

    except ValueError as e:
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except RuntimeError as e:
        # Sanitized already
        return json.dumps({"error": str(e)}, ensure_ascii=False)
    except Exception as e:
        logger.exception("Tool %s failed", name)
        # Never leak raw trace to LLM; give concise safe error
        return json.dumps({"error": "Tool failed: " + _sanitize(str(e), 300)}, ensure_ascii=False)


async def execute_pending(bot_chat_id: int) -> str:
    """Actually send the pending message/call after user confirms. Returns result string."""
    pending = get_pending(bot_chat_id)
    if not pending:
        return "No pending action."
    chat = pending["chat"]
    message = pending["message"]
    reply_to = pending["reply_to"]
    clear_pending(bot_chat_id)
    if telegram_user is None:
        return "Telegram client unavailable."
    # Handle call pending (stored as CALL:username)
    if isinstance(chat, str) and chat.startswith("CALL:"):
        user = chat[5:]
        try:
            res = await telegram_user.api_request_call(user)
            if res is None:
                return "Not authenticated. Run python telegram_user.py to login."
            if isinstance(res, str):
                return res
            if isinstance(res, dict) and res.get("status") == "call_requested":
                return f"Call requested to {res.get('user')} (@{res.get('username')}) — check Telegram app to continue. Note: {res.get('note')}"
            return str(res)
        except Exception as e:
            return f"Call failed: {_sanitize(str(e), 300)}"
    try:
        res = await telegram_user.api_send_message(chat, message, reply_to=reply_to)
        if res is None:
            return "Not authenticated. Run python telegram_user.py to login."
        if isinstance(res, str):
            return f"Failed: {res}"
        if isinstance(res, dict) and res.get("status") == "sent":
            return f"Sent to {res.get('chat')} (id {res.get('message_id')}) at {res.get('date')}"
        return str(res)
    except Exception as e:
        return f"Send failed: {_sanitize(str(e), 300)}"


def format_confirmation_prompt(bot_chat_id: int) -> str | None:
    pending = get_pending(bot_chat_id)
    if not pending:
        return None
    chat = pending["chat"]
    # Call pending special formatting
    if isinstance(chat, str) and chat.startswith("CALL:"):
        user = html_lib.escape(chat[5:])
        return (
            f"📞 <b>Confirm call?</b>\n\n"
            f"Call: <code>{user}</code>\n\n"
            f"Reply <b>yes</b> to call or <b>cancel</b> to abort — أو اضغط الزر أدناه. (expires in 5m)"
        )
    chat_e = html_lib.escape(chat)
    preview = html_lib.escape(pending["preview"])
    reply = f" (reply to {pending['reply_to']})" if pending["reply_to"] else ""
    return (
        f"📨 <b>Confirm send?</b>\n\n"
        f"To: <code>{chat_e}</code>{reply}\n"
        f"Message:\n<pre>{preview}</pre>\n\n"
        f"Reply <b>yes</b> to send or <b>cancel</b> to abort — أو اضغط الزر أدناه. (expires in 5m)"
    )


def get_confirmation_keyboard(bot_chat_id: int | None = None):
    """Return InlineKeyboardMarkup with نعم/إلغاء buttons for pending action. Works for both send and call."""
    if InlineKeyboardButton is None or InlineKeyboardMarkup is None:
        return None
    # Use callback_data with chat_id to handle race; but we also fallback to generic
    # Data format: confirm:CHATID or cancel:CHATID — handler will use effective_chat.id anyway
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("✅ نعم / Yes", callback_data="confirm_pending"),
                InlineKeyboardButton("❌ إلغاء / Cancel", callback_data="cancel_pending"),
            ]
        ]
    )
