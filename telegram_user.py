"""
Telegram User Client — MTProto via Telethon
Provides authenticated access to the user's personal Telegram account.
Bot API cannot see private chats; this client can (via MTProto user session).

Separation:
  - This module = low-level Telethon I/O, session management, auth.
  - telegram_tools.py = validated tool wrappers exposed to the LLM.

Privacy: never logs message contents, never prints secrets.
Security: session file is gitignored, credentials only from env.
"""

import os
import asyncio
import logging
import html as html_lib
from typing import Any

from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# --- Configuration (from env, never hardcoded) ---
API_ID: str = os.getenv("TELEGRAM_API_ID", "").strip()
API_HASH: str = os.getenv("TELEGRAM_API_HASH", "").strip()
PHONE: str = os.getenv("TELEGRAM_PHONE", "").strip()
SESSION: str = os.getenv("TELEGRAM_SESSION", "telegram_ai_session").strip() or "telegram_ai_session"
# Bot identity to exclude when querying "my last sent message" (so we don't count the message sent to the bot itself)
BOT_USERNAME: str = os.getenv("BOT_USERNAME", "Maldyaya_bot").strip().lstrip("@")
BOT_ID: int | None = None
_bot_id_raw = os.getenv("BOT_ID", "8513780659").strip()
try:
    BOT_ID = int(_bot_id_raw) if _bot_id_raw.isdigit() else None
except Exception:
    BOT_ID = None
if BOT_ID is None:
    BOT_ID = 8513780659  # fallback for this project

# Lazy import telethon
try:
    from telethon import TelegramClient
    from telethon.tl.types import User, Chat, Channel  # noqa: F401
    from telethon.errors import (
        SessionPasswordNeededError,
        FloodWaitError,
        UsernameNotOccupiedError,
        UsernameInvalidError,
        ChannelPrivateError,
        ChatAdminRequiredError,
    )
except ImportError:
    TelegramClient = None  # type: ignore

_client: Any = None
_client_lock = asyncio.Lock()
# For interactive auth we need to avoid concurrent connects


def is_configured() -> bool:
    """True if API credentials are present and look valid."""
    return bool(API_ID and API_HASH and API_ID.isdigit())


def _sanitize_error(e: Exception) -> str:
    """Return safe error message without leaking secrets."""
    msg = str(e)
    # Strip any accidental secret leakage
    for secret in [API_HASH, API_ID, PHONE]:
        if secret and secret in msg:
            msg = msg.replace(secret, "***")
    # Generic sanitization for common sensitive patterns
    # Remove telethon internal paths etc if needed - keep short
    if len(msg) > 500:
        msg = msg[:500] + "…"
    return msg


async def _get_connected_client() -> Any:
    """Internal: get or create connected client without auth prompts (for bot)."""
    global _client
    if TelegramClient is None:
        logger.warning("Telethon not installed. Run: pip install -r requirements.txt")
        return None
    if not is_configured():
        logger.warning("Telegram MTProto not configured (TELEGRAM_API_ID/HASH missing)")
        return None

    async with _client_lock:
        if _client and _client.is_connected():
            return _client
        # Create new client
        # Telethon will create SESSION.session file on disk
        _client = TelegramClient(SESSION, int(API_ID), API_HASH)
        try:
            await _client.connect()
        except Exception as e:
            logger.error(f"Telethon connect failed: {_sanitize_error(e)}")
            _client = None
            return None
        return _client


async def get_client():
    """
    Get authenticated Telethon client.
    - If session already authenticated, returns client.
    - If not authenticated and PHONE is set, attempts interactive flow ONLY if called
      from a TTY context (telegram_user.py standalone). When called from bot, it
      returns None and logs guidance (no blocking input()).
    For bot usage, prefer is_user_authorized() check and ensure_client_async().
    """
    client = await _get_connected_client()
    if not client:
        return None
    try:
        authorized = await client.is_user_authorized()
    except Exception as e:
        logger.error(f"Auth check failed: {_sanitize_error(e)}")
        return None

    if authorized:
        return client

    # Not authorized - only do interactive prompt if stdin is a TTY
    # This prevents blocking the bot event loop on headless servers.
    import sys

    if not sys.stdin.isatty():
        logger.warning(
            "Telethon session not authenticated. Run 'python telegram_user.py' interactively to login. "
            "Session file: %s.session",
            SESSION,
        )
        return None

    # Interactive auth path (only when run via telegram_user.py)
    if not PHONE:
        logger.warning("TELEGRAM_PHONE not set - cannot request login code. Set in .env")
        return None
    logger.info("Session not authorized - requesting login code (interactive)")
    try:
        await client.send_code_request(PHONE)
    except FloodWaitError as e:
        logger.error(f"FloodWait: retry after {e.seconds}s")
        return None
    except Exception as e:
        logger.error(f"send_code_request failed: {_sanitize_error(e)}")
        return None

    # Secure code input - never log the code
    try:
        code = input(f"Enter the Telegram login code sent to {PHONE}: ").strip()
    except Exception:
        logger.error("Failed to read login code")
        return None
    if not code:
        logger.error("Empty code")
        return None
    try:
        await client.sign_in(PHONE, code)
    except SessionPasswordNeededError:
        # 2FA required - ask securely without echo
        import getpass

        try:
            pwd = getpass.getpass("Telegram 2FA password (input hidden): ").strip()
        except Exception:
            logger.error("Failed to read 2FA password")
            return None
        if not pwd:
            logger.error("Empty 2FA password")
            return None
        try:
            await client.sign_in(password=pwd)
            # Explicitly clear pwd ref
            pwd = ""  # noqa: S105
        except Exception as e:
            logger.error(f"2FA sign-in failed: {_sanitize_error(e)}")
            return None
    except Exception as e:
        logger.error(f"sign_in failed: {_sanitize_error(e)}")
        return None

    # Verify
    try:
        me = await client.get_me()
        # Log only safe info
        display = getattr(me, "first_name", "") or "Unknown"
        username = getattr(me, "username", None)
        logger.info(f"Telethon authenticated as {display} (@{username}) id={me.id}")
        print(f"[OK] Authenticated as {display} (@{username}) id={me.id}")
        print(f"[OK] Session saved to {SESSION}.session — keep private, gitignored.")
    except Exception as e:
        logger.error(f"get_me failed after auth: {_sanitize_error(e)}")
        return None
    return client


async def ensure_client() -> Any:
    """
    Bot-friendly get: returns authenticated client or None without prompting.
    Used by the bot's background task to test auth at startup.
    """
    client = await _get_connected_client()
    if not client:
        return None
    try:
        if await client.is_user_authorized():
            return client
    except Exception:
        return None
    # Not authorized -> don't prompt inside bot loop
    logger.warning(
        "Telethon session not authorized. Run: python telegram_user.py  (TELEGRAM_PHONE must be set)"
    )
    return None


async def close_client():
    global _client
    if _client:
        try:
            await _client.disconnect()
        except Exception:
            pass
        _client = None


# ------------------------------------------------------------------ #
# Low-level Telegram operations (validated, limited, privacy-aware)
# ------------------------------------------------------------------ #

async def _resolve_entity(client, chat: str | int):
    """Resolve chat identifier to Telethon entity. Accepts username, phone, id, title. Supports display names with spaces."""
    if isinstance(chat, int):
        return await client.get_entity(chat)
    s = str(chat).strip()
    if not s:
        raise ValueError("Empty chat identifier")
    # Normalize: allow passing "@username", "username", "https://t.me/username"
    if s.startswith("https://t.me/"):
        s = s.split("https://t.me/")[-1].split("/")[0].split("?")[0]
    if s.startswith("t.me/"):
        s = s[4:]
    # Handle display names with spaces like "Omar Ahmed" or "@Omar Ahmed"
    # Try direct first (without space handling)
    try:
        # If contains space and starts with @, strip @ for search
        raw_for_direct = s
        # Telethon cannot handle spaces, so only try direct if no space
        if " " not in s:
            return await client.get_entity(raw_for_direct)
        # If has space, skip direct and go to dialog search
        raise ValueError("contains space, try dialog search")
    except (UsernameNotOccupiedError, UsernameInvalidError, ValueError, ChannelPrivateError) as e:
        # Try as int id (e.g. "-100123..." for channels, or numeric user id)
        if s.lstrip("-@").replace(" ", "").isdigit() and " " not in s:
            # only for pure numeric
            try:
                return await client.get_entity(int(s.lstrip("@")))
            except Exception:
                pass
        # Fallback: search in dialogs by title/first_name (for contacts without username)
        # Strip leading @ and normalize
        search_name = s.lstrip("@").strip().lower()
        # Also handle "@Omar Ahmed" -> "omar ahmed"
        try:
            dialogs = await client.get_dialogs(limit=50)
            for d in dialogs:
                entity = d.entity
                name = (getattr(entity, "title", None) or getattr(entity, "first_name", "") or "").strip()
                if hasattr(entity, "last_name") and entity.last_name:
                    name += f" {entity.last_name}"
                uname = (getattr(entity, "username", None) or "").strip()
                # Exact match
                if name and name.lower() == search_name:
                    return entity
                if uname and uname.lower() == search_name.lstrip("@").lower():
                    return entity
                # Flexible: if search is "Omar Ahmed" but dialog is "Omar" (last_name missing), match on first word or containment
                # This handles UI showing "Omar Ahmed" while entity is just "Omar"
                if name and search_name:
                    # If either contains the other (case-insensitive)
                    if name.lower() in search_name or search_name in name.lower():
                        return entity
                    # Also handle first name match when search has two words but dialog has one
                    search_first = search_name.split()[0] if search_name.split() else ""
                    if search_first and search_first == name.lower().split()[0]:
                        return entity
        except Exception:
            pass
        # If still not found, raise original
        raise ValueError(f"Cannot find any entity corresponding to \"{chat}\" — جرب /dialogs لمعرفة الاسم الصحيح أو استخدم رقم الهاتف. إذا كان الاسم به مسافة مثل 'Omar Ahmed'، اكتب بدون @: Omar Ahmed")


def _is_bot_dialog(d) -> bool:
    """Check if dialog is the bot itself (to exclude from 'my last sent' queries)."""
    try:
        entity = d.entity
        if BOT_ID is not None and getattr(d, "id", None) == BOT_ID:
            return True
        # also check username case-insensitive
        uname = (getattr(entity, "username", None) or "").lower()
        if uname and uname.lower() == BOT_USERNAME.lower():
            return True
        # Bot token's bot user id check via entity id for User
        if hasattr(entity, "id") and entity.id == BOT_ID:
            return True
    except Exception:
        pass
    return False


def _format_dialog(d, entity) -> dict:
    from telethon.tl.types import User as TUser

    name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or "Unknown"
    if not name:
        name = "Unknown"
    if hasattr(entity, "last_name") and entity.last_name:
        name += f" {entity.last_name}"
    if not name or name.strip() == "":
        name = "Unknown"
    username = getattr(entity, "username", None)
    # Truncate last_message to avoid sending excessive data to LLM
    last_msg = ""
    if d.message:
        raw = getattr(d.message, "message", None) or ""
        # Don't log; truncate
        last_msg = raw[:120]
        if d.message.out:
            last_msg = f"You: {last_msg}"
    return {
        "name": name,
        "username": username,
        "id": d.id,
        "unread": d.unread_count,
        "is_user": isinstance(entity, TUser),
        "last_message": last_msg,
        "date": d.date,
        "entity_type": type(entity).__name__,
        "is_bot_dialog": _is_bot_dialog(d),
    }


# --- Public async helpers (called by telegram_tools wrappers) ---

async def api_get_me() -> dict | None:
    client = await ensure_client()
    if not client:
        return None
    try:
        me = await client.get_me()
        return {
            "id": me.id,
            "first_name": getattr(me, "first_name", ""),
            "last_name": getattr(me, "last_name", ""),
            "username": getattr(me, "username", None),
            "phone": getattr(me, "phone", None),  # safe? we return but caller must not log full
            "is_bot": getattr(me, "bot", False),
        }
    except FloodWaitError as e:
        raise RuntimeError(f"Flood wait: retry after {e.seconds}s") from e
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e


async def api_list_dialogs(limit: int = 10) -> list[dict] | None:
    if limit < 1 or limit > 50:
        raise ValueError("limit must be 1-50")
    client = await ensure_client()
    if not client:
        return None
    try:
        dialogs = await client.get_dialogs(limit=limit)
    except FloodWaitError as e:
        raise RuntimeError(f"Flood wait: retry after {e.seconds}s") from e
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e
    results = []
    for d in dialogs:
        try:
            results.append(_format_dialog(d, d.entity))
        except Exception:
            continue
    return results


async def api_get_recent_messages(chat: str | int, limit: int = 10) -> list[dict] | str | None:
    if limit < 1 or limit > 50:
        raise ValueError("limit must be 1-50")
    if not str(chat).strip():
        raise ValueError("chat is required")
    client = await ensure_client()
    if not client:
        return None
    try:
        entity = await _resolve_entity(client, chat)
    except Exception as e:
        return f"Could not find chat '{chat}': {_sanitize_error(e)}"
    try:
        msgs = await client.get_messages(entity, limit=limit)
    except FloodWaitError as e:
        raise RuntimeError(f"Flood wait: retry after {e.seconds}s") from e
    except ChannelPrivateError:
        return f"Cannot access chat '{chat}': private or not a member"
    except ChatAdminRequiredError:
        return f"Cannot access chat '{chat}': admin required"
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e
    out = []
    for m in reversed(msgs or []):
        # sender name without leaking too much
        sender = "You" if getattr(m, "out", False) else "Them"
        try:
            if getattr(m, "sender", None) and getattr(m.sender, "first_name", None):
                sender = m.sender.first_name if not m.out else "You"
        except Exception:
            pass
        text = getattr(m, "message", None) or ("[media]" if getattr(m, "media", None) else "")
        # Privacy: truncate, don't store
        text = text[:500]
        date = m.date.strftime("%Y-%m-%d %H:%M") if getattr(m, "date", None) else ""
        out.append(
            {
                "date": date,
                "sender": sender,
                "text": text,
                "id": getattr(m, "id", None),
                "out": bool(getattr(m, "out", False)),
            }
        )
    return out


async def api_search_messages(query: str, chat: str | int | None = None, limit: int = 10) -> list[dict] | str | None:
    if not query or not query.strip():
        raise ValueError("query is required")
    if limit < 1 or limit > 50:
        raise ValueError("limit must be 1-50")
    if len(query) > 200:
        raise ValueError("query too long (max 200)")
    client = await ensure_client()
    if not client:
        return None
    entity = None
    if chat is not None and str(chat).strip():
        try:
            entity = await _resolve_entity(client, chat)
        except Exception as e:
            return f"Could not find chat '{chat}': {_sanitize_error(e)}"
    try:
        msgs = await client.get_messages(entity, limit=limit, search=query.strip())
    except FloodWaitError as e:
        raise RuntimeError(f"Flood wait: retry after {e.seconds}s") from e
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e
    out = []
    for m in msgs or []:
        sender = "You" if getattr(m, "out", False) else "Them"
        try:
            if getattr(m, "sender", None) and getattr(m.sender, "first_name", None):
                sender = m.sender.first_name if not m.out else "You"
        except Exception:
            pass
        text = getattr(m, "message", None) or ""
        text = text[:500]
        # Include chat title if global search
        chat_name = ""
        try:
            if getattr(m, "chat", None) and getattr(m.chat, "title", None):
                chat_name = m.chat.title
            elif getattr(m, "chat", None) and getattr(m.chat, "first_name", None):
                chat_name = m.chat.first_name
        except Exception:
            pass
        out.append(
            {
                "date": m.date.strftime("%Y-%m-%d %H:%M") if getattr(m, "date", None) else "",
                "sender": sender,
                "text": text,
                "chat": chat_name,
                "id": getattr(m, "id", None),
            }
        )
    return out


async def api_get_chat_info(chat: str | int) -> dict | str | None:
    if not str(chat).strip():
        raise ValueError("chat is required")
    client = await ensure_client()
    if not client:
        return None
    try:
        entity = await _resolve_entity(client, chat)
    except Exception as e:
        return f"Could not find chat '{chat}': {_sanitize_error(e)}"
    try:
        # Use get_entity + basic info; avoid extra requests
        return {
            "id": getattr(entity, "id", None),
            "title": getattr(entity, "title", None) or getattr(entity, "first_name", None),
            "username": getattr(entity, "username", None),
            "type": type(entity).__name__,
            "participants_count": getattr(entity, "participants_count", None),
            "is_group": getattr(entity, "megagroup", False) or isinstance(entity, Chat),
            "is_channel": isinstance(entity, Channel),
            "is_user": isinstance(entity, User),
        }
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e


async def api_get_user_info(user: str | int) -> dict | str | None:
    if not str(user).strip():
        raise ValueError("user is required")
    client = await ensure_client()
    if not client:
        return None
    try:
        entity = await _resolve_entity(client, user)
    except Exception as e:
        return f"Could not find user '{user}': {_sanitize_error(e)}"
    if not isinstance(entity, User):
        return f"'{user}' is not a user (found {type(entity).__name__})"
    try:
        return {
            "id": entity.id,
            "first_name": getattr(entity, "first_name", ""),
            "last_name": getattr(entity, "last_name", ""),
            "username": getattr(entity, "username", None),
            "phone": getattr(entity, "phone", None),
            "is_bot": getattr(entity, "bot", False),
            "is_contact": getattr(entity, "contact", False),
        }
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e


async def api_send_message(chat: str | int, message: str, reply_to: int | None = None) -> dict | str | None:
    if not str(chat).strip():
        raise ValueError("chat is required")
    if not message or not message.strip():
        raise ValueError("message is required")
    if len(message) > 4096:
        raise ValueError("message too long (max 4096)")
    if reply_to is not None and (not isinstance(reply_to, int) or reply_to <= 0):
        raise ValueError("reply_to must be a positive message ID")
    client = await ensure_client()
    if not client:
        return None
    try:
        entity = await _resolve_entity(client, chat)
    except Exception as e:
        return f"Could not find chat '{chat}': {_sanitize_error(e)}"
    try:
        sent = await client.send_message(entity, message.strip(), reply_to=reply_to)
        return {
            "status": "sent",
            "message_id": getattr(sent, "id", None),
            "chat": getattr(entity, "title", None) or getattr(entity, "first_name", str(chat)),
            "date": sent.date.strftime("%Y-%m-%d %H:%M") if getattr(sent, "date", None) else "",
        }
    except FloodWaitError as e:
        raise RuntimeError(f"Flood wait: Telegram limits sending. Retry after {e.seconds}s") from e
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e


async def api_get_unread(limit: int = 20) -> list[dict] | None:
    if limit < 1 or limit > 50:
        raise ValueError("limit must be 1-50")
    dialogs = await api_list_dialogs(limit=50)
    if dialogs is None:
        return None
    unread = [d for d in dialogs if d.get("unread", 0) > 0]
    return unread[:limit]


async def api_get_my_last_sent_messages(limit: int = 5, exclude_bot: bool = True) -> list[dict] | None:
    """
    Return the user's most recent OUTGOING messages across all dialogs,
    excluding the bot's own dialog (so 'ما اخر رسالة أرسلتها' doesn't count the query to the bot).
    Privacy: minimal window, truncated.
    """
    if limit < 1 or limit > 20:
        raise ValueError("limit must be 1-20")
    client = await ensure_client()
    if not client:
        return None
    try:
        # Get enough dialogs to find outgoing messages (skip bot)
        dialogs = await client.get_dialogs(limit=50)
    except FloodWaitError as e:
        raise RuntimeError(f"Flood wait: retry after {e.seconds}s") from e
    except Exception as e:
        raise RuntimeError(_sanitize_error(e)) from e

    # Filter dialogs: optionally exclude bot, and skip empty
    filtered = []
    for d in dialogs:
        if exclude_bot and _is_bot_dialog(d):
            continue
        filtered.append(d)

    # For each dialog, fetch last few messages and collect where out == True
    candidates: list[dict] = []
    for d in filtered[:30]:  # cap to 30 dialogs for performance
        try:
            # fetch small window; we need only check outgoing
            msgs = await client.get_messages(d.entity, limit=10)
        except Exception:
            continue
        for m in msgs or []:
            if not getattr(m, "out", False):
                continue
            text = getattr(m, "message", None) or ("[media]" if getattr(m, "media", None) else "")
            if not text or not text.strip():
                continue
            # Exclude very recent message that is the bot query itself if it matches bot dialog (already excluded)
            # But also exclude if message was just sent to bot within last 60s and text equals query - handled by caller
            text = text[:500]
            chat_name = getattr(d.entity, "title", None) or getattr(d.entity, "first_name", "Unknown")
            if hasattr(d.entity, "last_name") and d.entity.last_name:
                chat_name += f" {d.entity.last_name}"
            candidates.append(
                {
                    "chat": chat_name,
                    "username": getattr(d.entity, "username", None),
                    "chat_id": getattr(d, "id", None),
                    "text": text,
                    "date": m.date.strftime("%Y-%m-%d %H:%M") if getattr(m, "date", None) else "",
                    "timestamp": m.date.timestamp() if getattr(m, "date", None) else 0,
                    "message_id": getattr(m, "id", None),
                }
            )
            # Only take most recent outgoing per dialog to avoid flooding
            break

    # Sort globally by timestamp descending (most recent first)
    candidates.sort(key=lambda x: x["timestamp"], reverse=True)
    # Drop timestamp before returning
    for c in candidates:
        c.pop("timestamp", None)
    return candidates[:limit]


async def api_request_call(user: str | int) -> dict | str | None:
    """
    Initiate a Telegram voice call via user account (MTProto).
    Requires confirmation in telegram_tools layer.
    Note: Telegram calls need VoIP handling; Telethon's RequestCall is low-level and may fail if peer doesn't support.
    We attempt the MTProto phone.RequestCall and return result, or a helpful message.
    """
    raw = str(user).strip()
    if not raw:
        raise ValueError("user is required")
    # Handle Arabic placeholders like "رقمي" / "@رقمي" / "رقمى" / "my number" — user meant own number; explain
    aliases_self = {"رقمي", "@رقمي", "رقمى", "@رقمى", "my number", "my phone", "myself", "نفسي"}
    # normalize without @ and lower
    norm = raw.lstrip("@").strip().lower()
    if norm in aliases_self or raw.strip() in aliases_self:
        return "لا يمكنك الاتصال بنفسك — حدد جهة اتصال أخرى مثل @Ahmed أو رقم كامل +2015...  مثال: /call @Ahmed ثم أكد بـ نعم"
    # Also treat "رقمي" inside phrase like "Call: @رقمي"
    if "رقمي" in raw or "رقمى" in raw:
        return f"الاسم '{raw}' غير صالح — استخدم @username أو رقم هاتف. لا تستخدم '@رقمي'. مثال: /call @username"
    client = await ensure_client()
    if not client:
        return None
    try:
        entity = await _resolve_entity(client, user)
    except Exception as e:
        return f"Could not find user '{user}': {_sanitize_error(e)}"
    from telethon.tl.types import User as TUser

    if not isinstance(entity, TUser):
        return f"'{user}' is not a user (cannot call a channel/group)"

    # Attempt to request call - this is the MTProto way
    try:
        try:
            from telethon.tl.functions.phone import RequestCallRequest as RequestCall
        except ImportError:
            from telethon.tl.functions.phone import RequestCall  # fallback for older versions
        from telethon.tl.types import PhoneCallProtocol

        # Minimal protocol
        # We need g_a_hash - Telethon handles DH; we can pass random bytes
        import os as _os

        g_a_hash = _os.urandom(256)  # placeholder; Telethon will handle actual DH if needed
        # Use 256 random bytes; library_versions required in newer Telethon
        try:
            protocol = PhoneCallProtocol(
                min_layer=65,
                max_layer=92,
                library_versions=["11.0.0"],
                udp_p2p=True,
                udp_reflector=True,
            )
        except TypeError:
            # Fallback for older versions without library_versions
            protocol = PhoneCallProtocol(udp_p2p=True, udp_reflector=True, min_layer=65, max_layer=92)  # type: ignore
        result = await client(RequestCall(
            user_id=entity,
            g_a_hash=g_a_hash,
            protocol=protocol
        ))
        return {
            "status": "call_requested",
            "user": getattr(entity, "first_name", str(user)),
            "username": getattr(entity, "username", None),
            "phone_call_id": getattr(result, "phone_call", None) and getattr(result.phone_call, "id", None),
            "note": "Call request sent via MTProto. Answer on Telegram app. VoIP handling depends on client."
        }
    except FloodWaitError as e:
        raise RuntimeError(f"Flood wait: retry after {e.seconds}s") from e
    except Exception as e:
        # Provide sanitized but helpful error
        err = _sanitize_error(e)
        # If calls not supported, suggest alternative: voice message
        if "CALL" in err.upper() or "PHONE" in err.upper():
            return f"Call failed (may not be supported on this account/client): {err}. Alternative: send a voice message via telegram_send_message."
        return f"Call failed: {err}"


# ------------------------------------------------------------------ #
# Legacy helpers kept for backward compat (bot.py old calls)
# ------------------------------------------------------------------ #

async def get_recent_dialogs(limit=10):
    return await api_list_dialogs(limit=limit)


async def get_unread_dialogs():
    return await api_get_unread(limit=20)


async def get_latest_messages(dialog_id_or_username, limit=10):
    res = await api_get_recent_messages(dialog_id_or_username, limit=limit)
    if isinstance(res, str):
        return res
    if res is None:
        return None
    # Legacy format: list of strings
    out = []
    for m in res:
        out.append(f"{m['date']} {m['sender']}: {m['text'][:120]}")
    return out


# ------------------------------------------------------------------ #
# Interactive login entrypoint
# ------------------------------------------------------------------ #

async def login_interactive():
    """Run once to create/verify session file. Safe logging."""
    if TelegramClient is None:
        print("[ERROR] Telethon not installed. Run: pip install telethon")
        return
    if not is_configured():
        print("[ERROR] Set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE in .env first")
        print("  Get them from https://my.telegram.org -> API development tools")
        print("  Also set TELEGRAM_SESSION=telegram_ai_session (optional)")
        return
    print("=" * 60)
    print(f"[INFO] Session file: {SESSION}.session")
    # Mask secrets in log
    print(f"[INFO] API_ID: {API_ID[:2]}***  API_HASH: ***  PHONE: {PHONE[:4]}***")
    print("=" * 60)
    client = await get_client()
    if client:
        try:
            me = await client.get_me()
            # Only safe info
            display = getattr(me, "first_name", "") or "Unknown"
            if getattr(me, "last_name", None):
                display += f" {me.last_name}"
            username = getattr(me, "username", None)
            print(f"[OK] Verified as {display} (@{username}) id={me.id}")
            dialogs = await api_list_dialogs(limit=3)
            if dialogs:
                print("Recent chats:")
                for d in dialogs:
                    # Don't log message contents verbatim beyond 40 chars truncated
                    preview = d["last_message"][:40].replace("\n", " ")
                    print(f"  - {d['name']} (@{d['username']}) unread={d['unread']} last: {preview}")
            await close_client()
            print("[OK] Done. Now run: python bot.py")
        except Exception as e:
            print(f"[ERROR] Verification failed: {_sanitize_error(e)}")
    else:
        print("[ERROR] Login failed. Check credentials / code / 2FA and retry.")


if __name__ == "__main__":
    # Setup minimal logging for interactive run
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
    asyncio.run(login_interactive())
