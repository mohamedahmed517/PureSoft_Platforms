import os
import io
import time
import json
import base64
import requests
import threading
import pandas as pd
from PIL import Image
from datetime import datetime
from dotenv import load_dotenv
from flask import Flask, request
import google.generativeai as genai
from collections import defaultdict
from google.generativeai.types import HarmCategory, HarmBlockThreshold
import logging
import multiprocessing

# ==================== الأساسيات ====================
logging.basicConfig(level=logging.INFO)
load_dotenv()
app = Flask(__name__)

# ==================== المتغيرات البيئية ====================
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("PHONE_NUMBER_ID")
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "afaq_whatsapp_only_2025")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")  # حط هنا التوكن الجديد

if not GEMINI_API_KEY:
    raise ValueError("GEMINI_API_KEY مطلوب!")

# ==================== History ====================
HISTORY_FILE = "history.json"
SAVE_INTERVAL = 60

try:
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        loaded_history = json.load(f)
        conversation_history = defaultdict(list, {str(k): v for k, v in loaded_history.items()})
    print(f"تم تحميل {len(conversation_history)} محادثة")
except FileNotFoundError:
    conversation_history = defaultdict(list)

def save_history():
    while True:
        time.sleep(SAVE_INTERVAL)
        try:
            with open(HISTORY_FILE, "w", encoding="utf-8") as f:
                json.dump(dict(conversation_history), f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"خطأ حفظ التاريخ: {e}")
threading.Thread(target=save_history, daemon=True).start()

# ==================== Gemini Setup ====================
genai.configure(api_key=GEMINI_API_KEY)
MODEL = genai.GenerativeModel(
    'gemini-1.5-flash',
    generation_config={"temperature": 0.9, "max_output_tokens": 2048},
    safety_settings=[
        {"category": HarmCategory.HARM_CATEGORY_HARASSMENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
        {"category": HarmCategory.HARM_CATEGORY_HATE_SPEECH, "threshold": HarmBlockThreshold.BLOCK_NONE},
        {"category": HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT, "threshold": HarmBlockThreshold.BLOCK_NONE},
        {"category": HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT, "threshold": HarmBlockThreshold.BLOCK_NONE},
    ]
)

# ==================== Load CSV ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DATA = pd.read_csv(os.path.join(BASE_DIR, 'products.csv'))

# ==================== WhatsApp Functions ====================
def download_media(media_id):
    url = f"https://graph.facebook.com/v20.0/{media_id}"
    headers = {"Authorization": f"Bearer {WHATSAPP_TOKEN}"}
    try:
        r = requests.get(url, headers=headers, timeout=15)
        media_url = r.json().get("url")
        if media_url:
            return base64.b64encode(requests.get(media_url, headers=headers, timeout=30).content).decode('utf-8')
    except: pass
    return None

def send_whatsapp_message(to, text):
    if not (WHATSAPP_TOKEN and PHONE_NUMBER_ID): return
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    payload = {"messaging_product": "whatsapp", "to": to, "type": "text", "text": {"body": text[:4000]}}
    try: requests.post(url, headers={"Authorization": f"Bearer {WHATSAPP_TOKEN}"}, json=payload, timeout=10)
    except: pass

# ==================== Gemini Chat (بوت ذكي) ====================
def gemini_chat(user_message="", image_b64=None, from_number="unknown"):
    try:
        products_text = "\n".join(
            f"• {row['product_name_ar']} | السعر: {row['sell_price']} جنيه | https://afaq-stores.com/product-details/{row['product_id']}"
            for _, row in CSV_DATA.iterrows()
        )

        history_lines = ""
        for entry in conversation_history[from_number][-40:]:
            role = "العميل" if entry["role"] == "user" else "البوت"
            history_lines += f"{entry.get('time', '')} - {role}: {entry['text']}\n"

        prompt = f"""
أنا بوت ذكي من آفاق ستورز، بتكلم عامية مصرية طبيعية وودودة.
المنتجات المتاحة:
{products_text}

آخر رسايل:
{history_lines}

العميل بيقول دلوقتي: {user_message or "بعت صورة"}
- لو صورة → ابدأ بـ "ثانية بس أشوف الصورة..."
- لو طلب منتج → رشح من القايمة بالشكل ده:
تيشيرت قطن سادة أبيض
السعر: 130 جنيه
اللينك: https://afaq-stores.com/product-details/1019
- متستخدمش إيموجي خالص
- رد بالعامية المصرية 100%
رد دلوقتي:
""".strip()

        if image_b64:
            img = Image.open(io.BytesIO(base64.b64decode(image_b64)))
            response = MODEL.generate_content([prompt, img])
        else:
            response = MODEL.generate_content(prompt)

        reply = response.text.strip() if response and response.text else "ثواني بس وأرجعلك..."

        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation_history[from_number].append({"role": "user", "text": user_message or "[صورة]", "time": now})
        conversation_history[from_number].append({"role": "assistant", "text": reply, "time": now})
        if len(conversation_history[from_number]) > 200:
            conversation_history[from_number] = conversation_history[from_number][-200:]

        return reply
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "ثواني وأرجعلك..."

def gemini_chat_audio(audio_file, from_number):
    try:
        response = MODEL.generate_content(["اسمع الريكورد ده ورد عليه بالعامية المصرية", audio_file])
        reply = response.text.strip() or "الريكورد مش واضح، ابعته تاني"
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        conversation_history[from_number].extend([
            {"role": "user", "text": "[صوت]", "time": now},
            {"role": "assistant", "text": reply, "time": now}
        ])
        return reply
    except:
        return "الريكورد مش واضح"

# ==================== WhatsApp ====================
def process_whatsapp_message(msg):
    from_number = msg["from"]
    msg_type = msg["type"]

    if msg_type == "text":
        reply = gemini_chat(msg["text"]["body"], from_number=from_number)
    elif msg_type == "image":
        image_b64 = download_media(msg["image"]["id"])
        reply = gemini_chat("بعت صورة", image_b64, from_number)
    elif msg_type in ["audio", "voice"]:
        audio_b64 = download_media(msg["audio"]["id"])
        if audio_b64:
            audio_file = io.BytesIO(base64.b64decode(audio_b64))
            audio_file.name = "voice.ogg"
            reply = gemini_chat_audio(audio_file, from_number)
        else:
            reply = "الصوت مش واصل"
    else:
        reply = gemini_chat("مش فاهم إيه ده، ابعت نص أو صورة", from_number=from_number)

    send_whatsapp_message(from_number, reply)

@app.route("/")
def home():
    return "بوت آفاق ستورز شغال 100% – واتساب + تليجرام"

@app.route("/webhook", methods=["GET"])
def verify():  
    if request.args.get("hub.mode") == "subscribe" and request.args.get("hub.verify_token") == WEBHOOK_VERIFY_TOKEN:
        return request.args.get("hub.challenge"), 200
    return "Forbidden", 403

@app.route("/webhook", methods=["POST"])
def webhook():
    try:
        data = request.get_json(force=True)
        if not data or "entry" not in data: return "OK", 200
        for entry in data["entry"]:
            for change in entry.get("changes", []):
                value = change.get("value", {})
                if "messages" in value:
                    for msg in value["messages"]:
                        process_whatsapp_message(msg)
    except Exception as e:
        logging.error(f"Error: {e}")
    return "OK", 200

# ==================== Telegram Bot (شغال 100% على Railway) ====================
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

def run_telegram_bot():
    if not TELEGRAM_TOKEN:
        print("TELEGRAM_TOKEN مش موجود!")
        return
    print("بوت تليجرام بيشتغل دلوقتي...")

    async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text("أهلًا وسهلًا! أنا البوت الذكي بتاع آفاق ستورز")

    async def handle(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = str(update.effective_user.id)
        text = update.message.text or "بعت صورة أو صوت"

        if update.message.photo:
            file = await update.message.photo[-1].get_file()
            b64 = base64.b64encode(await file.download_as_bytearray()).decode()
            reply = gemini_chat("بعت صورة", b64, user_id)
        elif update.message.voice or update.message.audio:
            file = await (update.message.voice or update.message.audio).get_file()
            audio_io = io.BytesIO(await file.download_as_bytearray())
            audio_io.name = "voice.ogg"
            reply = gemini_chat_audio(audio_io, user_id)
        else:
            reply = gemini_chat(text, from_number=user_id)

        await update.message.reply_text(reply)

    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, handle))
    app.run_polling(drop_pending_updates=True)

# ==================== تشغيل الكل ====================
if __name__ == "__main__":
    # تشغيل تليجرام في Process منفصل
    if TELEGRAM_TOKEN:
        multiprocessing.set_start_method('spawn', force=True)
        p = multiprocessing.Process(target=run_telegram_bot, daemon=True)
        p.start()
        print("بوت تليجرام شغال بنجاح!")

    # تشغيل Flask
    port = int(os.environ.get("PORT", 5000))
    print(f"Flask شغال على البورت {port}")
    app.run(host="0.0.0.0", port=port, debug=False)
