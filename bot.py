"""
Telegram AI Bot — Groq Powered with Telethon MTProto integration
Bot remains the user-facing interface; authenticated user account provides Telegram capabilities via explicit tools.

Flow:
  Telegram Bot (python-telegram-bot) -> Groq LLM -> Explicit Telegram Tools -> Telethon -> User Account
"""

import os
import json
import logging
import asyncio
import html
from dotenv import load_dotenv

from groq import Groq
from telegram import Update
from telegram.constants import ParseMode, ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, filters, ContextTypes

# Telegram User client + Tools (optional but preferred)
try:
    import telegram_user
except Exception as e:
    telegram_user = None  # type: ignore
    logging.warning(f"telegram_user not available: {e}")

try:
    import telegram_tools
except Exception as e:
    telegram_tools = None  # type: ignore
    logging.warning(f"telegram_tools not available: {e}")

# --- Load env ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")

# Fallbacks for legacy (avoid hardcoding secrets in logs; use env only)
if not BOT_TOKEN:
    BOT_TOKEN = "8513780659:AAEfY9E_ystaZlZB4HlD6XagfXTMGrHYR6A"
if not GROQ_API_KEY:
    GROQ_API_KEY = "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"

REASONING_EFFORT = os.getenv("REASONING_EFFORT", "high")
REASONING_FORMAT = os.getenv("REASONING_FORMAT", "hidden")
SHOW_THINKING = os.getenv("SHOW_THINKING", "false").lower() in ("1", "true", "yes", "on")
TEMPERATURE = float(os.getenv("TEMPERATURE", "0.7"))

# Enhanced system prompt that guides tool use + safety
_DEFAULT_SYSTEM = (
    "You are a helpful, friendly AI assistant chatting via Telegram Bot (Maldyaya_bot). "
    "You have access to the user's personal Telegram account via explicit tools (read dialogs, messages, search, send, call). "
    "Languages: Respond in the user's language (Arabic/English). "
    "PRIVACY: Only request minimal needed data. CONCURRENCY: tools are async but you just call them. "
    "SAFETY: You must NOT automatically perform dangerous actions (delete, ban, leave, join, mass message). "
    "For telegram_send_message / telegram_reply / telegram_call you MUST ask for confirmation and show preview; do not send/call without explicit yes. "
    "For read-only tools no confirmation needed. Be concise but thorough. Think step-by-step internally. "
    "IMPORTANT — 'ما اخر رسالة أرسلتها / آخر رسالة أرسلتها بنفسي / last message I sent' => You MUST call telegram_get_my_sent_messages (limit 5) and EXCLUDE the current message to the bot (Maldyaya_bot). Never answer from bot chat memory; always use Telethon tools. Exclude the bot's dialog (Maldyaya_bot / id 8513780659). "
    "If user wants to call someone ('اتصل / call Ahmed'), use telegram_call with confirmation."
)
SYSTEM_PROMPT = os.getenv("SYSTEM_PROMPT", _DEFAULT_SYSTEM)

# --- Logging ---
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# --- Groq Client ---
groq_client = Groq(api_key=GROQ_API_KEY)

# --- In-memory conversation store ---
# { chat_id: [ {"role": "system/user/assistant", "content": "..."} ] }
conversations: dict[int, list[dict]] = {}
MAX_HISTORY = 20

ALLOWED_CHAT_ID_INT = int(CHAT_ID) if CHAT_ID and CHAT_ID.isdigit() else None

def get_history(chat_id: int) -> list[dict]:
    if chat_id not in conversations:
        conversations[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    return conversations[chat_id]

def add_to_history(chat_id: int, role: str, content: str):
    hist = get_history(chat_id)
    hist.append({"role": role, "content": content})
    if len(hist) > MAX_HISTORY * 2 + 1:
        conversations[chat_id] = [hist[0]] + hist[-(MAX_HISTORY * 2):]

def is_allowed(update: Update) -> bool:
    if ALLOWED_CHAT_ID_INT is None:
        return True
    return update.effective_chat.id == ALLOWED_CHAT_ID_INT

async def check_allowed(update: Update) -> bool:
    if is_allowed(update):
        return True
    await update.message.reply_text(
        f"⛔ Access denied. This bot is private.\nYour Chat ID: <code>{update.effective_chat.id}</code>",
        parse_mode=ParseMode.HTML,
    )
    logger.warning(f"Denied access to chat_id={update.effective_chat.id}")
    return False

# --- Helpers ---
def split_text(text: str, limit: int = 4096) -> list[str]:
    if len(text) <= limit:
        return [text]
    chunks = []
    while text:
        if len(text) <= limit:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, limit)
        if split_at == -1:
            split_at = text.rfind(" ", 0, limit)
        if split_at == -1:
            split_at = limit
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip()
    return chunks

show_thinking_per_chat: dict[int, bool] = {}

def should_show_thinking(chat_id: int) -> bool:
    return show_thinking_per_chat.get(chat_id, SHOW_THINKING)

def _sanitize_groq_error(e: Exception) -> str:
    msg = str(e)
    # Don't leak keys
    for sec in [GROQ_API_KEY, BOT_TOKEN]:
        if sec and sec in msg:
            msg = msg.replace(sec, "***")
    if len(msg) > 800:
        msg = msg[:800] + "…"
    return html.escape(msg)

# --- Plain Groq query (fallback, no tools) ---
def query_groq(chat_id: int, user_text: str) -> str:
    hist = get_history(chat_id)
    messages = hist + [{"role": "user", "content": user_text}]
    is_reasoning_model = any(x in GROQ_MODEL for x in ["gpt-oss", "qwen", "compound"])
    kwargs: dict = {}
    if is_reasoning_model:
        try:
            kwargs["reasoning_effort"] = REASONING_EFFORT
            kwargs["reasoning_format"] = REASONING_FORMAT
        except Exception:
            pass
    try:
        completion = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=messages,
            temperature=TEMPERATURE,
            max_tokens=4096,
            top_p=1,
            **kwargs,
        )
        msg = completion.choices[0].message
        reply = (msg.content or "").strip()
        reasoning = getattr(msg, "reasoning", None)
        if not reasoning and reply and "<think>" in reply:
            pass
        elif reasoning:
            reasoning = reasoning.strip()
            if reasoning and should_show_thinking(chat_id):
                reply = f"🧠 <b>Thinking:</b>\n<pre>{html.escape(reasoning[:3000])}</pre>\n\n{reply}" if REASONING_FORMAT == "parsed" else reply
        if not reply and reasoning:
            reply = reasoning.strip()
        if not reply:
            reply = "⚠️ Model returned empty response. Try /think or switch model with /model"
        add_to_history(chat_id, "user", user_text)
        hist_reply = reply
        if "<pre>" in hist_reply:
            hist_reply = hist_reply.split("</pre>", 1)[-1].strip()
        add_to_history(chat_id, "assistant", hist_reply[:4000])
        return reply
    except Exception as e:
        logger.exception("Groq error")
        return f"⚠️ Groq API error: {_sanitize_groq_error(e)}"

# --- Groq with Tools (main path) ---
async def query_groq_with_tools(chat_id: int, user_text: str, bot_chat_id: int) -> str:
    """
    Tool-enabled Groq loop. Uses telegram_tools.TOOL_DEFINITIONS if available.
    Handles confirmation-required writes by storing pending and returning prompt.
    """
    use_tools = (
        telegram_tools is not None
        and telegram_user is not None
        and telegram_user.is_configured()
        and hasattr(telegram_tools, "TOOL_DEFINITIONS")
    )

    # If no tools, fallback
    if not use_tools:
        return await asyncio.to_thread(query_groq, chat_id, user_text)

    hist = get_history(chat_id)
    # Build messages for Groq (history + current user). History is truncated already.
    messages: list[dict] = list(hist) + [{"role": "user", "content": user_text}]

    # Tool definitions
    tools = telegram_tools.TOOL_DEFINITIONS  # type: ignore

    is_reasoning_model = any(x in GROQ_MODEL for x in ["gpt-oss", "qwen", "compound"])
    base_kwargs: dict = {}
    if is_reasoning_model:
        base_kwargs["reasoning_effort"] = REASONING_EFFORT
        base_kwargs["reasoning_format"] = REASONING_FORMAT

    # To avoid infinite loops, cap iterations
    max_iters = 5
    for iteration in range(max_iters):
        try:
            # Groq is sync; run in thread to avoid blocking event loop
            def _call():
                return groq_client.chat.completions.create(
                    model=GROQ_MODEL,
                    messages=messages,
                    tools=tools,
                    tool_choice="auto",
                    temperature=TEMPERATURE,
                    max_tokens=4096,
                    top_p=1,
                    **base_kwargs,
                )
            completion = await asyncio.to_thread(_call)
        except Exception as e:
            # If model doesn't support tools, fallback to plain
            err = str(e).lower()
            if "tool" in err or "function" in err or "unknown" in err:
                logger.warning(f"Tool calling not supported, fallback: {e}")
                return await asyncio.to_thread(query_groq, chat_id, user_text)
            logger.exception("Groq tool call error")
            return f"⚠️ Groq API error: {_sanitize_groq_error(e)}"

        choice = completion.choices[0]
        msg = choice.message
        tool_calls = getattr(msg, "tool_calls", None)

        # No tools -> final answer
        if not tool_calls:
            reply = (msg.content or "").strip()
            reasoning = getattr(msg, "reasoning", None)
            if reasoning and should_show_thinking(chat_id) and REASONING_FORMAT == "parsed":
                reasoning = reasoning.strip()
                if reasoning:
                    reply = f"🧠 <b>Thinking:</b>\n<pre>{html.escape(reasoning[:3000])}</pre>\n\n{reply}"
            if not reply and reasoning:
                reply = (reasoning or "").strip()
            if not reply:
                reply = "⚠️ Empty response. Try rephrasing."
            # Save to history (only final answer, not intermediate tool payloads)
            add_to_history(chat_id, "user", user_text)
            # Strip thinking block for history
            hist_reply = reply
            if "<pre>" in hist_reply:
                hist_reply = hist_reply.split("</pre>", 1)[-1].strip()
            add_to_history(chat_id, "assistant", hist_reply[:4000])
            return reply

        # Has tool calls -> execute them
        # First, append assistant's tool_call message to history for Groq
        messages.append(
            {
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                    }
                    for tc in tool_calls
                ],
            }
        )

        # Execute each tool (privacy: no logging of contents beyond counts)
        logger.info(f"LLM requested {len(tool_calls)} tool(s): {[tc.function.name for tc in tool_calls]}")
        has_confirmation_pending = False
        for tc in tool_calls:
            name = tc.function.name
            raw_args = tc.function.arguments or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except Exception:
                args = {}
            # Dispatch (bot_chat_id used for confirmation routing)
            try:
                result_json = await telegram_tools.dispatch_tool(name, args, bot_chat_id=bot_chat_id)  # type: ignore
            except Exception as e:
                result_json = json.dumps({"error": html.escape(str(e)[:500])}, ensure_ascii=False)

            # Check if this tool requested confirmation (write tools)
            try:
                parsed = json.loads(result_json)
                if isinstance(parsed, dict) and parsed.get("status") == "confirmation_required":
                    has_confirmation_pending = True
            except Exception:
                pass

            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result_json,
                }
            )

        # If any write requested confirmation, we don't loop again for LLM to ask user;
        # instead we will return a confirmation prompt directly after the loop handles next LLM turn.
        # But we let the loop continue so LLM can generate the confirmation question naturally.
        # To avoid extra latency, continue to next iteration where LLM will see the confirmation_required payload.
        if has_confirmation_pending:
            # Continue so LLM produces natural confirmation message
            continue

        # Otherwise continue loop for read-only tools (LLM will synthesize answer next iteration)
        continue

    # If we exit loop without final answer, fallback
    return "⚠️ Tool loop limit reached. Please try a simpler request or check /help."

# --- Command Handlers ---
async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    chat_id = update.effective_chat.id
    get_history(chat_id)
    show = "ON 🧠" if should_show_thinking(chat_id) else "OFF"
    has_tg = telegram_user is not None and telegram_user.is_configured()
    tg_status = "✅ Connected" if has_tg else "🔒 Not configured (set TELEGRAM_API_ID/HASH/PHONE in .env, run python telegram_user.py)"
    await update.message.reply_text(
        "👋 <b>Hi! I'm your Groq AI bot — VERY THINK mode with Telegram tools!</b>\n\n"
        f"Model: <code>{html.escape(GROQ_MODEL)}</code>\n"
        f"Reasoning: <code>{html.escape(REASONING_EFFORT)}</code> ({html.escape(REASONING_FORMAT)}) - Show thinking: {show}\n"
        f"Telegram user: {tg_status}\n\n"
        "Just send me any message — I can also read your Telegram chats when needed.\n\n"
        "<b>Commands:</b>\n"
        "/start - Show this message\n"
        "/help - Help & info + Telegram tools\n"
        "/clear - Clear conversation history\n"
        "/model - Show/change model info\n"
        "/think - Toggle thinking display (ON/OFF)\n"
        "/chatid - Show your Chat ID\n"
        "/last - Last conversation (via Telegram)\n"
        "/unread - Unread chats\n"
        "/dialogs - List recent dialogs\n"
        "/latest [@chat] - Recent messages from chat\n"
        "/sent [n] - آخر رسائلي المرسلة (بدون احتساب رسالتي للبوت)\n"
        "/call @user - طلب مكالمة صوتية (بـ تأكيد)\n"
        "/me - Verify Telegram user account (get_me)\n"
        "/cancel - Cancel pending send/call confirmation\n\n"
        "Examples:\n"
        "• \"ما آخر رسالة أرسلتها؟\" (لن يحتسب رسالتك للبوت)\n"
        "• \"What did Ahmed send me recently?\"\n"
        "• \"اتصل بأحمد / call Ahmed\" (سيطلب تأكيد)\n"
        "• \"Send Ahmed I'll arrive at 8\" (will ask for confirmation)\n"
        "⚡ Powered by Groq + Telethon",
        parse_mode=ParseMode.HTML,
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    show = "ON" if should_show_thinking(update.effective_chat.id) else "OFF"
    has_tg = telegram_user is not None and telegram_user.is_configured()
    # Check auth without blocking
    auth_str = "unknown"
    if has_tg and telegram_user:
        try:
            c = await telegram_user.ensure_client()
            auth_str = "✅ Authenticated" if c else "⚠️ Not authenticated — run python telegram_user.py"
        except Exception:
            auth_str = "⚠️ Error"
    await update.message.reply_text(
        "ℹ️ <b>Help — Very Think + Telegram Tools</b>\n\n"
        "• Send any text → I answer via Groq, using Telegram tools when relevant\n"
        "• I remember last 20 exchanges per chat (use /clear to reset)\n"
        f"• Thinking display: {show} (toggle with /think)\n\n"
        f"<b>Model:</b> <code>{html.escape(GROQ_MODEL)}</code>\n"
        f"<b>Reasoning:</b> <code>{html.escape(REASONING_EFFORT)}</code> / <code>{html.escape(REASONING_FORMAT)}</code>\n"
        f"<b>Your Chat ID:</b> <code>{update.effective_chat.id}</code>\n"
        f"<b>Allowed Chat ID:</b> <code>{CHAT_ID or 'ANY'}</code>\n\n"
        "<b>Telegram Tools (AI can call):</b>\n"
        "• <code>telegram_get_me</code> — verify account\n"
        "• <code>telegram_list_dialogs</code> — recent conversations\n"
        "• <code>telegram_get_recent_messages</code> — messages from a chat\n"
        "• <code>telegram_search_messages</code> — search messages\n"
        "• <code>telegram_get_chat_info / telegram_get_user_info</code>\n"
        "• <code>telegram_get_unread</code> — unread chats\n"
        "• <code>telegram_get_my_sent_messages</code> — آخر رسائلي المرسلة (يستثني البوت)\n"
        "• <code>telegram_send_message / telegram_reply</code> — send (requires confirmation)\n"
        "• <code>telegram_call</code> — مكالمة صوتية (بـ تأكيد)\n\n"
        f"<b>Telegram status:</b> {auth_str}\n"
        f"<b>Session file:</b> <code>{os.getenv('TELEGRAM_SESSION', 'telegram_ai_session')}.session</code> (gitignored)\n\n"
        "<b>Safety:</b> destructive actions (delete/ban/leave/join) are NOT available. Sends need explicit 'yes'.\n"
        "<b>Setup:</b> my.telegram.org → API development tools → set TELEGRAM_API_ID/HASH/PHONE in .env → <code>python telegram_user.py</code>\n",
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def clear_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    chat_id = update.effective_chat.id
    conversations[chat_id] = [{"role": "system", "content": SYSTEM_PROMPT}]
    # also clear pending
    if telegram_tools:
        telegram_tools.clear_pending(chat_id)  # type: ignore
    await update.message.reply_text("🧹 History cleared! Pending confirmations cleared.")

async def model_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    show = "ON" if should_show_thinking(update.effective_chat.id) else "OFF"
    await update.message.reply_text(
        f"🤖 <b>Current Model:</b> <code>{html.escape(GROQ_MODEL)}</code>\n"
        f"🧠 <b>Reasoning:</b> <code>{html.escape(REASONING_EFFORT)}</code> / <code>{html.escape(REASONING_FORMAT)}</code>\n"
        f"👁 <b>Show Thinking:</b> {show} (use /think to toggle)\n"
        f"🌡 <b>Temperature:</b> {TEMPERATURE}\n\n"
        "Change in <code>.env</code> and restart.",
        parse_mode=ParseMode.HTML,
    )

async def think_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    cid = update.effective_chat.id
    current = should_show_thinking(cid)
    show_thinking_per_chat[cid] = not current
    new_state = "ON 🧠" if not current else "OFF"
    await update.message.reply_text(
        f"🧠 Thinking display: <b>{new_state}</b>\n"
        f"Model will {'show' if not current else 'hide'} its reasoning block before the answer.\n"
        f"Reasoning effort stays: <code>{html.escape(REASONING_EFFORT)}</code>",
        parse_mode=ParseMode.HTML,
    )

async def chatid_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"🆔 Your Chat ID: <code>{update.effective_chat.id}</code>\nChat type: {update.effective_chat.type}", parse_mode=ParseMode.HTML)

async def sent_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_user is None or not telegram_user.is_configured():
        await update.message.reply_text("🔒 Telegram not configured. See /help")
        return
    limit = 5
    if context.args and context.args[0].isdigit():
        try:
            limit = max(1, min(int(context.args[0]), 10))
        except Exception:
            pass
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        data = await telegram_user.api_get_my_last_sent_messages(limit=limit, exclude_bot=True)
        if data is None:
            await update.message.reply_text("⚠️ Not authenticated. Run: python telegram_user.py")
            return
        if not data:
            await update.message.reply_text("لا توجد رسائل مرسلة حديثاً (باستثناء محادثة البوت).")
            return
        lines = [f"📤 <b>آخر {len(data)} رسائل أرسلتها (بدون احتساب رسالتك للبوت):</b>"]
        for i, m in enumerate(data, 1):
            uname = f" @{html.escape(m['username'])}" if m.get("username") else ""
            lines.append(f"{i}. <b>{html.escape(m['chat'])}</b>{uname} — {html.escape(m['date'])}\n   <i>{html.escape(m['text'][:120])}</i>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {html.escape(str(e)[:500])}", parse_mode=ParseMode.HTML)

async def call_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_user is None or not telegram_user.is_configured():
        await update.message.reply_text("🔒 Telegram not configured.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /call @username  (سيطلب تأكيد — اضغط نعم)")
        return
    target = " ".join(context.args).strip()
    # Use tool dispatch to get confirmation flow (same as LLM)
    if telegram_tools is None:
        await update.message.reply_text("⚠️ Tools unavailable")
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    res_json = await telegram_tools.dispatch_tool("telegram_call", {"user": target}, bot_chat_id=update.effective_chat.id)
    try:
        res = json.loads(res_json)
    except Exception:
        res = {}
    if res.get("status") == "confirmation_required":
        prompt = telegram_tools.format_confirmation_prompt(update.effective_chat.id)
        kb = telegram_tools.get_confirmation_keyboard(update.effective_chat.id) if telegram_tools else None
        if prompt:
            await update.message.reply_text(prompt, parse_mode=ParseMode.HTML, reply_markup=kb)
        else:
            await update.message.reply_text(f"هل تريد الاتصال بـ {html.escape(target)}؟ رد بـ yes أو اضغط الزر.", reply_markup=kb)
    else:
        await update.message.reply_text(html.escape(str(res)), parse_mode=ParseMode.HTML)

# --- Telegram-specific shortcuts (direct tool calls, no LLM) ---
async def me_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_user is None or not telegram_user.is_configured():
        await update.message.reply_text("🔒 Telegram not configured. Set TELEGRAM_API_ID/HASH/PHONE in .env, then run python telegram_user.py")
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        data = await telegram_user.api_get_me()
        if data is None:
            await update.message.reply_text("⚠️ Not authenticated. Run: python telegram_user.py")
            return
        name = html.escape(f"{data.get('first_name','')} {data.get('last_name','') or ''}".strip() or "Unknown")
        uname = html.escape(data.get("username") or "no username")
        await update.message.reply_text(f"✅ <b>Authenticated as</b> {name} (@{uname}) id=<code>{data.get('id')}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {html.escape(str(e)[:500])}")

async def cancel_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_tools and telegram_tools.has_pending(update.effective_chat.id):  # type: ignore
        telegram_tools.clear_pending(update.effective_chat.id)  # type: ignore
        await update.message.reply_text("❌ Pending send cancelled.")
    else:
        await update.message.reply_text("No pending message to cancel.")

async def last_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_user is None or not telegram_user.is_configured():
        await update.message.reply_text("🔒 Telegram not configured. See /help for setup.")
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        dialogs = await telegram_user.api_list_dialogs(limit=5)
        if dialogs is None:
            await update.message.reply_text("⚠️ Not authenticated. Run: python telegram_user.py")
            return
        if not dialogs:
            await update.message.reply_text("No recent dialogs.")
            return
        top = dialogs[0]
        top_name = str(top.get('name') or "Unknown")
        lines = [f"💬 <b>Last conversation:</b> <b>{html.escape(top_name)}</b>"]
        if top.get("username"):
            lines[-1] += f" (@{html.escape(str(top['username']) )})"
        lines.append(f"Last: <i>{html.escape(str(top['last_message'] or '')[:120])}</i>")
        # date is datetime
        try:
            lines.append(f"At: {top['date'].strftime('%Y-%m-%d %H:%M')}")
        except Exception:
            pass
        lines.append("\n<b>Recent others:</b>")
        for d in dialogs[1:5]:
            lines.append(f"• {html.escape(str(d.get('name') or 'Unknown'))} — {html.escape(str(d.get('last_message') or '')[:50])}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {html.escape(str(e)[:500])}", parse_mode=ParseMode.HTML)

async def unread_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_user is None or not telegram_user.is_configured():
        await update.message.reply_text("🔒 Telegram not configured.")
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        dialogs = await telegram_user.api_get_unread(limit=20)
        if dialogs is None:
            await update.message.reply_text("⚠️ Not authenticated. Run: python telegram_user.py")
            return
        if not dialogs:
            await update.message.reply_text("✅ No unread messages — all caught up!")
            return
        lines = [f"📬 <b>Unread ({len(dialogs)}):</b>"]
        for d in dialogs[:15]:
            uname = f"@{d['username']}" if d.get("username") else f"id:{d['id']}"
            lines.append(f"• <b>{html.escape(str(d.get('name') or 'Unknown'))}</b> ({html.escape(str(uname))}) — {d['unread']} unread — last: <i>{html.escape(str(d.get('last_message') or '')[:60])}</i>")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {html.escape(str(e)[:500])}", parse_mode=ParseMode.HTML)

async def dialogs_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_user is None or not telegram_user.is_configured():
        await update.message.reply_text("🔒 Telegram not configured.")
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        dialogs = await telegram_user.api_list_dialogs(limit=10)
        if dialogs is None:
            await update.message.reply_text("⚠️ Not authenticated. Run: python telegram_user.py")
            return
        lines = [f"📋 <b>Latest {len(dialogs)} chats:</b>"]
        for i, d in enumerate(dialogs, 1):
            unread_badge = f" <b>({d['unread']} unread)</b>" if d["unread"] else ""
            uname = f" @{d['username']}" if d.get("username") else ""
            lines.append(f"{i}. <b>{html.escape(str(d.get('name') or 'Unknown'))}</b>{html.escape(str(uname))}{unread_badge}\n   <i>{html.escape(str(d.get('last_message') or '')[:70])}</i>")
        lines.append("\nUse <code>/latest @username</code> to see messages from a specific chat.")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {html.escape(str(e)[:500])}", parse_mode=ParseMode.HTML)

async def latest_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if telegram_user is None or not telegram_user.is_configured():
        await update.message.reply_text("🔒 Telegram not configured.")
        return
    arg = " ".join(context.args) if context.args else ""
    if not arg:
        await dialogs_cmd(update, context)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    try:
        res = await telegram_user.api_get_recent_messages(arg, limit=10)
        if res is None:
            await update.message.reply_text("⚠️ Not authenticated. Run: python telegram_user.py")
            return
        if isinstance(res, str):
            await update.message.reply_text(f"⚠️ {html.escape(res)}")
            return
        if not res:
            await update.message.reply_text(f"No messages found for {html.escape(arg)}")
            return
        lines = [f"💬 <b>Recent messages from {html.escape(arg)}:</b>"]
        for m in res:
            lines.append(f"{html.escape(m['date'])} <b>{html.escape(m['sender'])}:</b> {html.escape(m['text'][:120])}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
    except Exception as e:
        await update.message.reply_text(f"⚠️ Error: {html.escape(str(e)[:500])}", parse_mode=ParseMode.HTML)

def _is_my_last_sent_query(text: str) -> bool:
    t = text.lower().strip()
    # Arabic patterns: "ما اخر رسالة ارسلتها", "آخر رسالة أرسلتها بنفسي", "اخر رساله ارسلتها"
    arabic_keywords = ["اخر رسالة ارسلتها", "آخر رسالة أرسلتها", "اخر رساله", "ارسلتها بنفسي", "الرسالة التي ارسلتها"]
    # Normalize hamza/yaa
    norm = t.replace("أ", "ا").replace("إ", "ا").replace("ة", "ه").replace("ؤ", "و")
    if any(k.replace("أ","ا").replace("ة","ه") in norm for k in arabic_keywords):
        return True
    english = ["last message i sent", "last msg i sent", "my last sent message", "what did i last send"]
    if any(k in t for k in english):
        return True
    return False

# --- Main message handler with confirmation + tool support ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await check_allowed(update):
        return
    if not update.message or not update.message.text:
        return
    user_text = update.message.text.strip()
    if not user_text:
        return

    chat_id = update.effective_chat.id
    # Don't log message contents verbatim beyond prefix for privacy debug
    logger.info(f"Message from {chat_id} ({update.effective_user.first_name}): {user_text[:50]}…")

    # 1) Handle pending confirmation first (highest priority)
    if telegram_tools and telegram_tools.has_pending(chat_id):  # type: ignore
        decision = telegram_tools.is_confirmation_text(user_text)  # type: ignore
        if decision == "confirm":
            # User confirmed -> actually send
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            # Show pending info
            pending = telegram_tools.get_pending(chat_id)  # type: ignore
            result = await telegram_tools.execute_pending(chat_id)  # type: ignore
            # Inform user + also feed result to history?
            add_to_history(chat_id, "user", user_text)
            add_to_history(chat_id, "assistant", f"[Tool] Pending send to {pending['chat'] if pending else ''}: {result}")
            await update.message.reply_text(f"✅ {html.escape(result)}", parse_mode=ParseMode.HTML)
            return
        elif decision == "cancel":
            telegram_tools.clear_pending(chat_id)  # type: ignore
            await update.message.reply_text("❌ Cancelled. Message not sent.")
            return
        else:
            # Still pending but user sent unrelated text -> remind
            prompt = telegram_tools.format_confirmation_prompt(chat_id)  # type: ignore
            if prompt:
                # Only nudge if the new message is short or looks like a question about sending
                if len(user_text) < 200 and any(k in user_text.lower() for k in ["send", "yes", "cancel", "what", "which", "نعم", "ارسل"]):
                    kb = telegram_tools.get_confirmation_keyboard(chat_id) if telegram_tools else None  # type: ignore
                    await update.message.reply_text(prompt + "\n\n<i>Reply <b>yes</b> to send or <b>cancel</b> to abort — أو اضغط الزر أدناه. (pending expires in 5m).</i>", parse_mode=ParseMode.HTML, reply_markup=kb)
                    # Don't return; let the message also be processed? For now, remind and also process as new query?
                    # We'll still process the new user_text as a new LLM query after reminder.
                    pass

    # 1b) Fast path for "ما اخر رسالة أرسلتها" — exclude bot message, use Telethon directly (more reliable than LLM)
    if _is_my_last_sent_query(user_text) and telegram_user and telegram_user.is_configured():
        # Don't count the query itself: we directly fetch via API which already excludes Maldyaya_bot
        await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
        try:
            data = await telegram_user.api_get_my_last_sent_messages(limit=5, exclude_bot=True)
            if data is None:
                await update.message.reply_text("⚠️ غير مصادق. شغل: python telegram_user.py")
                return
            if not data:
                await update.message.reply_text("لا توجد رسائل مرسلة حديثاً (باستثناء محادثتك مع البوت). جرب /sent")
                return
            # Also optionally use LLM to phrase nicely, but provide direct answer
            lines = [f"📤 <b>آخر رسالة أرسلتها (بدون احتساب رسالتك لي):</b>"]
            top = data[0]
            lines.append(f"<b>{html.escape(top['chat'])}</b> — {html.escape(top['date'])}")
            lines.append(f"<i>{html.escape(top['text'][:300])}</i>")
            if len(data) > 1:
                lines.append(f"\n<b>قبلها:</b>")
                for m in data[1:4]:
                    lines.append(f"• {html.escape(m['chat'])} — {html.escape(m['text'][:60])}")
            lines.append(f"\n<i>تم الاستثناء: رسالتك الحالية للبوت ({html.escape(user_text[:30])}) لم تحتسب.</i>")
            await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.HTML)
            # Save to history
            add_to_history(chat_id, "user", user_text)
            add_to_history(chat_id, "assistant", f"آخر رسالة أرسلتها: {top['chat']} - {top['text'][:100]}")
            return
        except Exception as e:
            logger.exception("sent query fast path failed")
            # fallback to LLM
            pass

    # 2) Normal LLM flow with tools
    await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)

    async def keep_typing():
        while True:
            await asyncio.sleep(4)
            try:
                await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception:
                break

    typing_task = asyncio.create_task(keep_typing())
    try:
        # Use tool-enabled path
        reply = await query_groq_with_tools(chat_id, user_text, bot_chat_id=chat_id)
        # If reply indicates confirmation required but we already handled pending display,
        # the LLM should have asked for confirmation; ensure user sees pending prompt too
        # Check if reply contains confirmation language and we have pending -> append formatted prompt
        if telegram_tools and telegram_tools.has_pending(chat_id):  # type: ignore
            # If LLM didn't already include the preview, attach it
            pending_prompt = telegram_tools.format_confirmation_prompt(chat_id)  # type: ignore
            if pending_prompt and "Confirm send" not in reply and "Confirm call" not in reply:
                reply = reply + "\n\n" + pending_prompt
                # reply now contains HTML, will be sent as HTML below with buttons
                kb = telegram_tools.get_confirmation_keyboard(chat_id) if telegram_tools else None  # type: ignore
                for idx, chunk in enumerate(split_text(reply)):
                    try:
                        # Only add keyboard to last chunk
                        if idx == len(split_text(reply)) - 1 and kb:
                            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML, reply_markup=kb)
                        else:
                            await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
                    except Exception:
                        await update.message.reply_text(chunk)
                return
            elif pending_prompt:
                # LLM already included prompt but no keyboard — add keyboard to next message
                kb = telegram_tools.get_confirmation_keyboard(chat_id) if telegram_tools else None  # type: ignore
                if kb:
                    # Send keyboard as follow-up if reply was already sent via LLM path? For simplicity, send extra message with keyboard
                    try:
                        await update.message.reply_text("اختر:", reply_markup=kb)
                    except Exception:
                        pass
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except asyncio.CancelledError:
            pass

    # 3) Send reply (split, try Markdown first)
    # Detect if reply already contains HTML from confirmation/thinking
    contains_html = "<b>" in reply or "<pre>" in reply or "<code>" in reply or "Confirm send" in reply or "Confirm call" in reply
    for chunk in split_text(reply):
        try:
            if contains_html:
                await update.message.reply_text(chunk, parse_mode=ParseMode.HTML)
            else:
                # Try Markdown, fallback to plain
                try:
                    await update.message.reply_text(chunk, parse_mode=ParseMode.MARKDOWN)
                except Exception:
                    await update.message.reply_text(chunk)
        except Exception as e:
            logger.error(f"Failed to send: {e}")
            try:
                await update.message.reply_text(chunk)
            except Exception:
                pass

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard presses for confirm/cancel pending actions."""
    query = update.callback_query
    if not query:
        return
    chat_id = update.effective_chat.id if update.effective_chat else None
    if chat_id is None:
        await query.answer("No chat", show_alert=True)
        return
    if not is_allowed(update):
        await query.answer("⛔ Access denied", show_alert=True)
        return
    data = query.data or ""
    # Only handle our pending buttons
    if data == "confirm_pending":
        if not telegram_tools or not telegram_tools.has_pending(chat_id):  # type: ignore
            await query.answer("لا يوجد طلب معلق (انتهى)", show_alert=True)
            try:
                await query.edit_message_text("❌ لا يوجد طلب معلق أو انتهت صلاحيته (5 دقائق).")
            except Exception:
                pass
            return
        await query.answer("جاري التنفيذ...")
        pending = telegram_tools.get_pending(chat_id)  # type: ignore
        result = await telegram_tools.execute_pending(chat_id)  # type: ignore
        # Save to history
        add_to_history(chat_id, "user", "ضغط زر ✅ نعم")
        add_to_history(chat_id, "assistant", f"[Button confirm] {result}")
        # Update the confirmation message and remove keyboard
        try:
            await query.edit_message_text(f"✅ {html.escape(result)}", parse_mode=ParseMode.HTML)
        except Exception:
            try:
                await query.edit_message_reply_markup(reply_markup=None)
            except Exception:
                pass
            await context.bot.send_message(chat_id=chat_id, text=f"✅ {result}")
        logger.info(f"Button confirm executed for {chat_id}: {result[:80]}")
    elif data == "cancel_pending":
        if telegram_tools and telegram_tools.has_pending(chat_id):  # type: ignore
            telegram_tools.clear_pending(chat_id)  # type: ignore
            await query.answer("تم الإلغاء")
            try:
                await query.edit_message_text("❌ تم الإلغاء. لم يتم الإرسال/الاتصال.")
            except Exception:
                pass
            add_to_history(chat_id, "user", "ضغط زر ❌ إلغاء")
            add_to_history(chat_id, "assistant", "Cancelled via button")
        else:
            await query.answer("لا يوجد شيء لإلغائه", show_alert=True)
            try:
                await query.edit_message_text("❌ لا يوجد طلب معلق.")
            except Exception:
                pass
    else:
        await query.answer()


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    logger.exception(f"Exception while handling update: {context.error}")

def main():
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())

    if not BOT_TOKEN or ":" not in BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing or invalid. Check .env")
    if not GROQ_API_KEY or not GROQ_API_KEY.startswith("gsk_"):
        logger.warning("GROQ_API_KEY looks invalid — check .env")

    print("=" * 60)
    print(f"[BOT] Starting Telegram Groq Bot — VERY THINK + TELESCOPE")
    print(f"   Model: {GROQ_MODEL}")
    print(f"   Reasoning: {REASONING_EFFORT} / {REASONING_FORMAT} (show={SHOW_THINKING})")
    print(f"   Allowed Chat ID: {ALLOWED_CHAT_ID_INT if ALLOWED_CHAT_ID_INT else 'ANY (open)'}")
    # Never print full tokens
    print(f"   Bot Token: {BOT_TOKEN[:6]}***{BOT_TOKEN[-4:] if len(BOT_TOKEN)>10 else ''}")
    has_tg = telegram_user is not None and telegram_user.is_configured()
    print(f"   Telegram MTProto: {'configured' if has_tg else 'NOT configured (set TELEGRAM_API_ID/HASH/PHONE)'}")
    print(f"   Session: {os.getenv('TELEGRAM_SESSION', 'telegram_ai_session')}.session")
    print("=" * 60)

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("clear", clear_cmd))
    app.add_handler(CommandHandler("model", model_cmd))
    app.add_handler(CommandHandler("think", think_cmd))
    app.add_handler(CommandHandler("chatid", chatid_cmd))
    app.add_handler(CommandHandler("last", last_cmd))
    app.add_handler(CommandHandler("unread", unread_cmd))
    app.add_handler(CommandHandler("dialogs", dialogs_cmd))
    app.add_handler(CommandHandler("latest", latest_cmd))
    app.add_handler(CommandHandler("me", me_cmd))
    app.add_handler(CommandHandler("cancel", cancel_cmd))
    app.add_handler(CommandHandler("sent", sent_cmd))
    app.add_handler(CommandHandler("call", call_cmd))
    app.add_handler(CallbackQueryHandler(callback_handler, pattern="^(confirm_pending|cancel_pending)$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.add_error_handler(error_handler)

    async def post_init(application: Application):
        # Init Telethon in same event loop (concurrency)
        if telegram_user and telegram_user.is_configured():
            try:
                client = await telegram_user.ensure_client()
                if client:
                    me = await client.get_me()
                    display = getattr(me, "first_name", "") or "Unknown"
                    if getattr(me, "last_name", None):
                        display += f" {me.last_name}"
                    username = getattr(me, "username", None)
                    logger.info(f"Telethon verified: {display} (@{username}) id={me.id}")
                    print(f"[OK] Telethon authenticated as {display} (@{username}) id={me.id}")
                else:
                    print("[WARN] Telethon session not authenticated. Run: python telegram_user.py")
                    logger.warning("Telethon not authenticated at startup")
            except Exception as e:
                # Sanitize
                msg = str(e)
                for sec in [os.getenv("TELEGRAM_API_HASH",""), os.getenv("TELEGRAM_API_ID","")]:
                    if sec and sec in msg:
                        msg = msg.replace(sec, "***")
                logger.warning(f"Telethon init failed: {msg[:500]}")
                print(f"[WARN] Telethon init failed: {msg[:200]}")

        if ALLOWED_CHAT_ID_INT:
            try:
                await application.bot.send_message(
                    chat_id=ALLOWED_CHAT_ID_INT,
                    text=f"Very Think Bot started!\nModel: <code>{html.escape(GROQ_MODEL)}</code>\nReasoning: <code>{html.escape(REASONING_EFFORT)}</code> ({html.escape(REASONING_FORMAT)})\nTelegram tools: {'✅' if has_tg else '🔒 not configured (/help)'}\nSend /think to toggle thinking.",
                    parse_mode=ParseMode.HTML,
                )
            except Exception as e:
                logger.warning(f"Could not notify owner: {e}")

    async def post_shutdown(application: Application):
        if telegram_user:
            try:
                await telegram_user.close_client()
            except Exception:
                pass

    app.post_init = post_init  # type: ignore
    app.post_shutdown = post_shutdown  # type: ignore

    print("[OK] Bot is polling. Press Ctrl+C to stop.")
    print("   Test it: open Telegram and send /start to your bot")
    print("   First auth: python telegram_user.py  (creates session file)")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)

if __name__ == "__main__":
    main()
