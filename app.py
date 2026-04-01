import os
import json
import hashlib
import hmac
import base64
import urllib.request
import urllib.error
from datetime import datetime
from flask import Flask, request, abort

app = Flask(__name__)

# ── 設定（從環境變數讀取）────────────────────────────
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "8770f52a75aabeb6c76ef33416c0e588")
CHANNEL_ID = os.environ.get("LINE_CHANNEL_ID", "2009580026")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
OPENAI_BASE_URL = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

BASE_URL = "https://nycuemba-ftmzwgi9.manus.space"

_token_cache = None

# ── 對話記憶（每位使用者最多保留 10 輪）────────────────
_conversation_history = {}
MAX_HISTORY = 10

# ── 關鍵字對應表（更自然的回覆語氣）──────────────────
KEYWORD_REPLIES = {
    "課程": f"關於課程的資訊都在這裡喔 📖\n{BASE_URL}/courses\n\n有什麼特定課程想了解的嗎？",
    "必修": f"必修課程清單可以在這裡查到 📚\n{BASE_URL}/courses\n\n如果有疑問歡迎繼續問我！",
    "選修": f"選修課程的資訊在這裡 📚\n{BASE_URL}/courses\n\n有興趣的方向可以跟我說，我幫你找找看～",
    "行事曆": f"最新的課程行程都在行事曆上 📅\n{BASE_URL}/calendar\n\n記得定期確認，以免錯過重要課程！",
    "日曆": f"課程日曆在這裡，可以查看所有上課時間 📅\n{BASE_URL}/calendar",
    "上課": f"上課時間可以在行事曆查詢 📅\n{BASE_URL}/calendar\n\n如果有特定日期想確認，也可以直接問我！",
    "時間": f"課程時間都整理在行事曆裡了 📅\n{BASE_URL}/calendar",
    "班費": f"班費收支明細都在這裡，公開透明 💰\n{BASE_URL}/class-fund\n\n有任何疑問歡迎提出！",
    "費用": f"費用相關資訊可以在班費頁面查看 💰\n{BASE_URL}/class-fund",
    "活動": f"班級活動的精彩紀錄都在這裡 🎉\n{BASE_URL}/activities\n\n期待大家踴躍參與！",
    "聚餐": f"聚餐活動資訊在這裡 🍽️\n{BASE_URL}/activities\n\n吃飯最重要，記得報名！",
    "晚宴": f"晚宴活動詳情可以在這裡查看 🥂\n{BASE_URL}/activities",
    "通訊錄": f"同學聯絡資訊都在通訊錄裡 👥\n{BASE_URL}/directory\n\n方便大家互相聯繫！",
    "聯絡": f"同學的聯絡方式可以在通訊錄查到 👥\n{BASE_URL}/directory",
    "交通": f"各校區的交通資訊都整理好了 🚇\n{BASE_URL}/transportation\n\n第一次去的話建議提早出發！",
    "停車": f"停車資訊也在交通頁面裡 🚗\n{BASE_URL}/transportation\n\n建議先查好再出發，比較方便！",
    "怎麼去": f"交通指南在這裡，有詳細說明 🗺️\n{BASE_URL}/transportation",
    "官網": f"NYCU 115 生醫 EMBA 官網在這裡 🌐\n{BASE_URL}\n\n有什麼想找的資訊嗎？",
    "網站": f"官網連結給你 🌐\n{BASE_URL}\n\n有需要幫忙找什麼嗎？",
}

# ── 問候語偵測 ────────────────────────────────────────
GREETINGS = ["你好", "哈囉", "嗨", "hi", "hello", "早安", "午安", "晚安", "安安", "嗨嗨"]

def is_greeting(text: str) -> bool:
    text_lower = text.lower().strip()
    return any(g in text_lower for g in GREETINGS)

def get_greeting_reply() -> str:
    hour = datetime.now().hour
    if 5 <= hour < 12:
        time_greeting = "早安！☀️"
    elif 12 <= hour < 18:
        time_greeting = "午安！🌤️"
    else:
        time_greeting = "晚安！🌙"
    
    return f"{time_greeting} 我是 NYCU 115 生醫 EMBA 的班級小助理～\n\n有什麼我可以幫你的嗎？可以問我課程、行事曆、班費、活動、通訊錄、交通等資訊喔！"

# ── 取得 Channel Access Token ─────────────────────────
def get_token():
    global _token_cache
    if _token_cache:
        return _token_cache
    data = f"grant_type=client_credentials&client_id={CHANNEL_ID}&client_secret={CHANNEL_SECRET}"
    req = urllib.request.Request(
        "https://api.line.me/v2/oauth/accessToken",
        data=data.encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST"
    )
    with urllib.request.urlopen(req) as res:
        result = json.loads(res.read())
        _token_cache = result["access_token"]
        return _token_cache

# ── 驗證 LINE 簽名 ────────────────────────────────────
def verify_signature(body: bytes, signature: str) -> bool:
    hash_val = hmac.new(
        CHANNEL_SECRET.encode("utf-8"),
        body,
        hashlib.sha256
    ).digest()
    expected = base64.b64encode(hash_val).decode("utf-8")
    return hmac.compare_digest(expected, signature)

# ── 關鍵字比對 ────────────────────────────────────────
def match_keyword(text: str):
    for keyword, reply in KEYWORD_REPLIES.items():
        if keyword in text:
            return reply
    return None

# ── 管理對話歷史 ──────────────────────────────────────
def get_history(user_id: str) -> list:
    return _conversation_history.get(user_id, [])

def add_to_history(user_id: str, role: str, content: str):
    if user_id not in _conversation_history:
        _conversation_history[user_id] = []
    _conversation_history[user_id].append({"role": role, "content": content})
    # 只保留最近 MAX_HISTORY 輪對話
    if len(_conversation_history[user_id]) > MAX_HISTORY * 2:
        _conversation_history[user_id] = _conversation_history[user_id][-MAX_HISTORY * 2:]

# ── AI 回覆（OpenAI，帶對話記憶）────────────────────────
def ai_reply(user_id: str, user_message: str) -> str:
    if not OPENAI_API_KEY:
        return f"感謝你的訊息！如需查詢相關資訊，可以前往官網看看：{BASE_URL} 😊"

    system_prompt = f"""你是「NYCU 115 生醫 EMBA」班級的小助理，名字叫做「小陽」。
你的個性親切、有點活潑，說話像朋友一樣自然，不會太正式。
你熟悉班級的所有事務，包括課程、班費、活動、通訊錄、交通等。

班級官網：{BASE_URL}
- 課程資訊：{BASE_URL}/courses
- 課程行事曆：{BASE_URL}/calendar
- 班費明細：{BASE_URL}/class-fund
- 班級活動：{BASE_URL}/activities
- 同學通訊錄：{BASE_URL}/directory
- 交通資訊：{BASE_URL}/transportation

回覆原則：
1. 用繁體中文，語氣像朋友一樣自然，可以適度使用 emoji
2. 回答要具體有幫助，不要只說「請去官網查」
3. 如果問題超出你知道的範圍，誠實說不確定，並提供官網連結
4. 回覆長度適中，不要太長也不要太短，大約 50-120 字
5. 可以在回覆末尾加一個小問題或鼓勵，讓對話更有溫度
6. 不要每次都重複介紹自己是誰"""

    # 取得對話歷史
    history = get_history(user_id)
    
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_message})

    payload = json.dumps({
        "model": "gpt-4.1-mini",
        "messages": messages,
        "max_tokens": 400,
        "temperature": 0.85,
        "presence_penalty": 0.3,
        "frequency_penalty": 0.3
    }).encode()

    req = urllib.request.Request(
        f"{OPENAI_BASE_URL}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as res:
            result = json.loads(res.read())
            reply_text = result["choices"][0]["message"]["content"].strip()
            # 儲存對話歷史
            add_to_history(user_id, "user", user_message)
            add_to_history(user_id, "assistant", reply_text)
            return reply_text
    except Exception as e:
        print(f"AI error: {e}")
        return f"哎呀，我剛才有點當機 😅 可以再說一次嗎？或是直接去官網查看：{BASE_URL}"

# ── 回覆 LINE 訊息 ────────────────────────────────────
def reply_message(reply_token: str, text: str):
    global _token_cache
    token = get_token()
    payload = json.dumps({
        "replyToken": reply_token,
        "messages": [{"type": "text", "text": text}]
    }).encode()
    req = urllib.request.Request(
        "https://api.line.me/v2/bot/message/reply",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {token}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as res:
            return res.status
    except urllib.error.HTTPError as e:
        if e.status == 401:
            # Token 過期，清除快取重試
            _token_cache = None
            token = get_token()
            payload_obj = json.loads(payload.decode())
            new_payload = json.dumps(payload_obj).encode()
            new_req = urllib.request.Request(
                "https://api.line.me/v2/bot/message/reply",
                data=new_payload,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {token}"
                },
                method="POST"
            )
            try:
                with urllib.request.urlopen(new_req, timeout=10) as res2:
                    return res2.status
            except Exception:
                pass
        print(f"Reply error: {e.status} {e.read().decode()}")
        return None

# ── Webhook 端點 ──────────────────────────────────────
@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body = request.get_data()

    if not verify_signature(body, signature):
        abort(400)

    events = json.loads(body).get("events", [])

    for event in events:
        if event.get("type") != "message":
            continue
        if event["message"].get("type") != "text":
            continue

        reply_token = event["replyToken"]
        user_id = event["source"].get("userId", "unknown")
        user_text = event["message"]["text"].strip()

        print(f"[收到訊息] user={user_id[:8]}... text={user_text}")

        # 1. 問候語偵測
        if is_greeting(user_text):
            print(f"[問候回覆]")
            reply_message(reply_token, get_greeting_reply())
            continue

        # 2. 關鍵字比對
        keyword_response = match_keyword(user_text)
        if keyword_response:
            print(f"[關鍵字回覆] {user_text[:15]}...")
            reply_message(reply_token, keyword_response)
            # 也記錄到對話歷史，讓 AI 知道之前聊過什麼
            add_to_history(user_id, "user", user_text)
            add_to_history(user_id, "assistant", keyword_response)
        else:
            # 3. 交給 AI 回覆（帶對話記憶）
            print(f"[AI 回覆] {user_text[:15]}...")
            ai_response = ai_reply(user_id, user_text)
            reply_message(reply_token, ai_response)

    return "OK", 200

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return json.dumps({
        "status": "ok",
        "service": "NYCU 115 生醫 EMBA LINE Bot",
        "version": "2.0",
        "features": ["keyword_reply", "ai_reply", "conversation_memory", "greeting_detection"],
        "keywords": len(KEYWORD_REPLIES)
    }, ensure_ascii=False), 200, {"Content-Type": "application/json"}

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
