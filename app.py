import os
import json
import hashlib
import hmac
import base64
import urllib.request
import urllib.error
from flask import Flask, request, abort

app = Flask(__name__)

# ── 設定（從環境變數讀取）────────────────────────────
CHANNEL_SECRET = os.environ.get("LINE_CHANNEL_SECRET", "8770f52a75aabeb6c76ef33416c0e588")
CHANNEL_ID = os.environ.get("LINE_CHANNEL_ID", "2009580026")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")

BASE_URL = "https://nycuemba-ftmzwgi9.manus.space"

_token_cache = None

# ── 關鍵字對應表 ───────────────────────────────────────
KEYWORD_REPLIES = {
    "課程": f"📖 課程資訊\n請點擊以下連結查看完整課程資訊：\n{BASE_URL}/courses",
    "必修": f"📖 必修課程\n請點擊以下連結查看必修課程清單：\n{BASE_URL}/courses",
    "選修": f"📖 選修課程\n請點擊以下連結查看選修課程清單：\n{BASE_URL}/courses",
    "行事曆": f"📅 課程日曆\n請點擊以下連結查看最新課程行程：\n{BASE_URL}/calendar",
    "日曆": f"📅 課程日曆\n請點擊以下連結查看最新課程行程：\n{BASE_URL}/calendar",
    "上課": f"📅 上課時間\n請點擊以下連結查看最新上課行程：\n{BASE_URL}/calendar",
    "時間": f"📅 課程時間\n請點擊以下連結查看課程日曆：\n{BASE_URL}/calendar",
    "班費": f"💰 班費資訊\n請點擊以下連結查看班費收支明細：\n{BASE_URL}/class-fund",
    "費用": f"💰 班費資訊\n請點擊以下連結查看班費收支明細：\n{BASE_URL}/class-fund",
    "活動": f"🎉 班級活動\n請點擊以下連結查看班級活動紀錄：\n{BASE_URL}/activities",
    "聚餐": f"🎉 班級活動\n請點擊以下連結查看班級活動資訊：\n{BASE_URL}/activities",
    "晚宴": f"🎉 班級活動\n請點擊以下連結查看班級活動資訊：\n{BASE_URL}/activities",
    "通訊錄": f"👥 同學通訊錄\n請點擊以下連結查看同學聯絡資訊：\n{BASE_URL}/directory",
    "聯絡": f"👥 同學通訊錄\n請點擊以下連結查看同學聯絡資訊：\n{BASE_URL}/directory",
    "交通": f"🚇 交通資訊\n請點擊以下連結查看各校區交通指南：\n{BASE_URL}/transportation",
    "停車": f"🚇 交通資訊\n請點擊以下連結查看各校區交通與停車資訊：\n{BASE_URL}/transportation",
    "怎麼去": f"🚇 交通資訊\n請點擊以下連結查看各校區交通指南：\n{BASE_URL}/transportation",
    "官網": f"🌐 NYCU 115 生醫 EMBA 官網\n{BASE_URL}",
    "網站": f"🌐 NYCU 115 生醫 EMBA 官網\n{BASE_URL}",
}

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

# ── AI 回覆（OpenAI）────────────────────────────────────
def ai_reply(user_message: str) -> str:
    if not OPENAI_API_KEY:
        return f"感謝您的訊息！如需查詢相關資訊，請前往官網：{BASE_URL}"

    system_prompt = f"""你是「NYCU 115 生醫 EMBA 課程平台」的智慧助理，
負責回答同學關於課程、班費、活動、通訊錄、交通等問題。
請用親切、簡潔的繁體中文回答。
如果問題超出你的知識範圍，請引導用戶前往官網 {BASE_URL} 查詢。
回答請控制在 150 字以內。"""

    payload = json.dumps({
        "model": "gpt-4.1-mini",
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message}
        ],
        "max_tokens": 300,
        "temperature": 0.7
    }).encode()

    req = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {OPENAI_API_KEY}"
        },
        method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            result = json.loads(res.read())
            return result["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"AI error: {e}")
        return f"感謝您的訊息！目前系統繁忙，請稍後再試，或前往官網查詢：{BASE_URL}"

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
            req.headers["Authorization"] = f"Bearer {token}"
            try:
                with urllib.request.urlopen(req, timeout=10) as res2:
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
        user_text = event["message"]["text"].strip()

        print(f"[收到訊息] {user_text}")

        # 1. 先嘗試關鍵字比對
        keyword_response = match_keyword(user_text)
        if keyword_response:
            print(f"[關鍵字回覆] {user_text[:10]}...")
            reply_message(reply_token, keyword_response)
        else:
            # 2. 交給 AI 回覆
            print(f"[AI 回覆] {user_text[:10]}...")
            ai_response = ai_reply(user_text)
            reply_message(reply_token, ai_response)

    return "OK", 200

@app.route("/", methods=["GET"])
@app.route("/health", methods=["GET"])
def health():
    return json.dumps({
        "status": "ok",
        "service": "NYCU 115 生醫 EMBA LINE Bot",
        "keywords": len(KEYWORD_REPLIES)
    }), 200

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
