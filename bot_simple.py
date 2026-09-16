"""
Lightweight alternative — no python-telegram-bot needed.
Uses only `requests` + `groq` via long polling. Good for minimal installs.
Run: python bot_simple.py
"""
import os, time, html, requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN", "8513780659:AAEfY9E_ystaZlZB4HlD6XagfXTMGrHYR6A")
CHAT_ID = os.getenv("CHAT_ID", "8166156987")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "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")
GROQ_MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
REASONING_EFFORT = os.getenv("REASONING_EFFORT", "high")
REASONING_FORMAT = os.getenv("REASONING_FORMAT", "parsed")
SHOW_THINKING = os.getenv("SHOW_THINKING", "true").lower() in ("1","true","yes","on")

ALLOWED = int(CHAT_ID) if CHAT_ID and CHAT_ID.isdigit() else None
groq_client = Groq(api_key=GROQ_API_KEY)
conversations = {}
BASE = f"https://api.telegram.org/bot{BOT_TOKEN}"

def get_history(cid):
    if cid not in conversations:
        conversations[cid] = [{"role":"system","content":"You are a helpful Telegram AI assistant. Think step-by-step."}]
    return conversations[cid]

def query(cid, text):
    hist = get_history(cid)
    msgs = hist + [{"role":"user","content":text}]
    kwargs = {}
    if any(x in GROQ_MODEL for x in ["gpt-oss","qwen","compound"]):
        kwargs["reasoning_effort"] = REASONING_EFFORT
        kwargs["reasoning_format"] = REASONING_FORMAT
    resp = groq_client.chat.completions.create(model=GROQ_MODEL, messages=msgs, temperature=0.7, max_tokens=4096, **kwargs)
    msg = resp.choices[0].message
    reply = (msg.content or "").strip()
    reasoning = getattr(msg, "reasoning", None)
    if reasoning and SHOW_THINKING and REASONING_FORMAT=="parsed":
        reasoning=reasoning.strip()
        if reasoning:
            reply = f"[Thinking]\n{reasoning[:2000]}\n\n{reply}"
    if not reply and reasoning:
        reply = reasoning.strip()
    hist.append({"role":"user","content":text})
    hist.append({"role":"assistant","content":reply[:4000]})
    if len(hist) > 41: conversations[cid] = [hist[0]] + hist[-40:]
    return reply

def send(chat_id, text):
    for i in range(0, len(text), 4096):
        requests.post(f"{BASE}/sendMessage", json={"chat_id": chat_id, "text": text[i:i+4096]})

def main():
    print(f"Simple bot polling with model {GROQ_MODEL} — allowed: {ALLOWED or 'any'}")
    offset = 0
    # notify
    if ALLOWED:
        try: send(ALLOWED, f"✅ Simple bot started! Model: {GROQ_MODEL}")
        except: pass
    while True:
        try:
            r = requests.get(f"{BASE}/getUpdates", params={"offset": offset, "timeout": 30}, timeout=35).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message")
                if not msg or "text" not in msg: continue
                cid = msg["chat"]["id"]
                if ALLOWED and cid != ALLOWED:
                    send(cid, f" Private bot. Your ID: {cid}")
                    continue
                text = msg["text"].strip()
                if text == "/start":
                    send(cid, "👋 Hi! I'm Groq AI. Send any message.")
                elif text == "/clear":
                    conversations[cid] = [{"role":"system","content":"You are a helpful Telegram AI assistant."}]
                    send(cid, "🧹 Cleared!")
                elif text == "/model":
                    send(cid, f"Model: {GROQ_MODEL}")
                else:
                    requests.post(f"{BASE}/sendChatAction", json={"chat_id": cid, "action":"typing"})
                    reply = query(cid, text)
                    send(cid, reply)
        except KeyboardInterrupt:
            break
        except Exception as e:
            print("Error:", e)
            time.sleep(3)

if __name__ == "__main__":
    main()
