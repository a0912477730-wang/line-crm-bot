import os
import json
import re
from datetime import datetime
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration, ApiClient, MessagingApi,
    ReplyMessageRequest, TextMessage, FlexMessage, FlexContainer
)
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from google import genai
import gspread
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# ── 環境變數 ──────────────────────────────────────────
LINE_TOKEN    = os.environ["LINE_CHANNEL_ACCESS_TOKEN"]
LINE_SECRET   = os.environ["LINE_CHANNEL_SECRET"]
GEMINI_KEY    = os.environ["GEMINI_API_KEY"]
SHEET_ID      = os.environ["GOOGLE_SHEET_ID"]
GOOGLE_CREDS  = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

# ── LINE / Gemini 初始化 ──────────────────────────────
configuration = Configuration(access_token=LINE_TOKEN)
handler = WebhookHandler(LINE_SECRET)
gemini = genai.Client(api_key=GEMINI_KEY)

# ── Google Sheets 連線 ────────────────────────────────
def get_sheet():
    creds_dict = json.loads(GOOGLE_CREDS)
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=["https://www.googleapis.com/auth/spreadsheets"]
    )
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(SHEET_ID)
    month_tab = datetime.now().strftime("%Y-%m")
    try:
        ws = sh.worksheet(month_tab)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=month_tab, rows=500, cols=7)
        ws.append_row(["日期時間", "客戶名稱", "工作性質", "摘要", "對話內容", "跟進狀態", "置信度"])
        ws.freeze(rows=1)
    return ws

# ── AI 工作性質判別（Gemini）────────────────────────────
PROMPT_TEMPLATE = """你是一個業務CRM助理，請根據以下工作性質分類，判別對話內容。

【工作性質分類】
1. 內部作業：開會、處理人事、活動舉辦、教育訓練、算圖、預估損益表製作、陪同拜訪
2. 專案任務：發送shaw國產樣本(S+P)&(F+V)等專案任務
3. 外出介紹樣冊：外出送新樣冊並介紹
4. 工地巡查：看工地（含工地開會）並拍照
5. 參觀展間：帶客戶或自行參觀展間
6. 新案件報備：新案件報備；後續追蹤歸「內部作業」
7. 拜訪新客戶：第一次拜訪客戶並介紹產品
8. 外出簡報：對客戶進行簡報或銷售講習
9. 送禮（年節）：年節送禮活動
10. 報價/議價：提供報價或價格協商
11. 案件討論：針對案件內容討論
12. 餐敘/聯誼：與客戶餐敘或聯誼
13. 客戶來訪：客戶來公司參觀或取樣
14. 其它：上述沒有的內容

請判別以下對話內容，只回傳 JSON，不要其他文字：
{
  "category": "工作性質名稱",
  "summary": "摘要（15字以內）",
  "status": "待跟進或跟進中或已成交或已結案",
  "confidence": "高或中或低",
  "reason": "判別理由（20字以內）",
  "contact": "推測的客戶名稱（無法判斷填空字串）"
}

對話內容：
"""

def ai_classify(text: str) -> dict:
    response = gemini.models.generate_content(model="gemini-2.0-flash-lite", contents=PROMPT_TEMPLATE + text)
raw = response.text.strip()
    clean = re.sub(r"```json|```", "", raw).strip()
    return json.loads(clean)

# ── 寫入 Google Sheets ────────────────────────────────
def append_to_sheet(contact, category, summary, content, status, confidence):
    ws = get_sheet()
    now = datetime.now().strftime("%Y/%m/%d %H:%M")
    ws.append_row([now, contact, category, summary, content, status, confidence])

# ── Flex Message 回覆卡片 ─────────────────────────────
def build_flex(result: dict, original: str) -> dict:
    confidence_color = {"高": "#0F6E56", "中": "#854F0B", "低": "#A32D2D"}.get(result.get("confidence", "中"), "#888780")
    status_color = {"待跟進": "#854F0B", "跟進中": "#3C3489", "已成交": "#0F6E56", "已結案": "#5F5E5A"}.get(result.get("status", "待跟進"), "#854F0B")
    preview = original[:60] + ("…" if len(original) > 60 else "")

    return {
        "type": "bubble",
        "size": "kilo",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#2A7F5F",
            "paddingAll": "14px",
            "contents": [
                {"type": "text", "text": "📋 已儲存到 CRM", "color": "#FFFFFF", "size": "sm", "weight": "bold"},
                {"type": "text", "text": result.get("summary", ""), "color": "#C8EBE0", "size": "xs", "margin": "sm"}
            ]
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "paddingAll": "14px",
            "contents": [
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "工作性質", "size": "xs", "color": "#888780", "flex": 3},
                    {"type": "text", "text": result.get("category", "其它"), "size": "xs", "weight": "bold", "color": "#2C2C2A", "flex": 5}
                ]},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "客戶", "size": "xs", "color": "#888780", "flex": 3},
                    {"type": "text", "text": result.get("contact", "—") or "—", "size": "xs", "weight": "bold", "color": "#2A7F5F", "flex": 5}
                ]},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "狀態", "size": "xs", "color": "#888780", "flex": 3},
                    {"type": "text", "text": result.get("status", "待跟進"), "size": "xs", "color": status_color, "weight": "bold", "flex": 5}
                ]},
                {"type": "box", "layout": "horizontal", "contents": [
                    {"type": "text", "text": "置信度", "size": "xs", "color": "#888780", "flex": 3},
                    {"type": "text", "text": result.get("confidence", "中"), "size": "xs", "color": confidence_color, "flex": 5}
                ]},
                {"type": "separator", "margin": "sm"},
                {"type": "text", "text": preview, "size": "xxs", "color": "#AAAAAA", "wrap": True, "margin": "sm"}
            ]
        },
        "footer": {
            "type": "box", "layout": "vertical", "paddingAll": "10px",
            "contents": [
                {"type": "text", "text": f"判別理由：{result.get('reason', '')}", "size": "xxs", "color": "#BBBBBB", "wrap": True}
            ]
        }
    }

# ── Webhook 入口 ──────────────────────────────────────
@app.route("/callback", methods=["POST"])
def callback():
    signature = request.headers["X-Line-Signature"]
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return "OK"

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()

    if text in ["選單", "help", "Help", "？", "?"]:
        reply_help(event)
        return

    try:
        result = ai_classify(text)
    except Exception as e:
        reply_text(event, f"⚠️ AI 判別失敗：{str(e)[:80]}")
        return

    contact = result.get("contact", "") or "未知"

    try:
        append_to_sheet(
            contact=contact,
            category=result.get("category", "其它"),
            summary=result.get("summary", ""),
            content=text,
            status=result.get("status", "待跟進"),
            confidence=result.get("confidence", "中")
        )
    except Exception as e:
        reply_text(event, f"⚠️ 寫入 Sheets 失敗：{str(e)[:80]}")
        return

    flex_content = build_flex(result, text)
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[FlexMessage(
                alt_text=f"✅ 已記錄：{result.get('category')} — {result.get('summary')}",
                contents=FlexContainer.from_dict(flex_content)
            )]
        ))

def reply_text(event, msg):
    with ApiClient(configuration) as api_client:
        MessagingApi(api_client).reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=msg)]
        ))

def reply_help(event):
    reply_text(event,
        "📋 CRM小當家 使用說明\n\n"
        "【方式 A】轉傳訊息\n"
        "長按客戶訊息 → 轉傳給我\n→ 自動判別工作性質並儲存\n\n"
        "【方式 B】快速輸入\n"
        "直接輸入客戶名稱 + 內容\n"
        "例：王大明 今天去台北展間確認美綻報價\n\n"
        "所有紀錄自動存入 Google Sheets 本月分頁\n"
        "月底下載 Excel 即可上傳"
    )

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)
