#!/usr/bin/env python3
"""
Ashum Telegram Command Handler
Polls Telegram for new commands and responds.
Runs frequently from GitHub Actions.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from urllib import request as urllib_request
from urllib.parse import quote
import urllib.error

CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '').strip()
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
SYMBOLS = os.environ.get('SYMBOLS', 'TSLA,NVDA,AAPL,META,GOOGL,MSFT,AMD').split(',')
SYMBOLS = [s.strip().upper() for s in SYMBOLS if s.strip()]

# Import functions from scan.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import (
    fetch_candles, aggregate_4h, claude_analyze,
    tg_send, format_signal, http_get, http_post
)

OFFSET_FILE = '/tmp/tg_offset.txt'
HISTORY_FILE = 'last_offset.json'  # persisted to repo

def get_last_offset():
    # Try persisted file
    try:
        with open(HISTORY_FILE, 'r') as f:
            return json.load(f).get('offset', 0)
    except: pass
    return 0

def save_offset(offset):
    try:
        with open(HISTORY_FILE, 'w') as f:
            json.dump({'offset': offset, 'updated': datetime.now(timezone.utc).isoformat()}, f)
    except Exception as e:
        print(f'Failed to save offset: {e}')

def get_updates(offset):
    url = f'https://api.telegram.org/bot{TG_TOKEN}/getUpdates?offset={offset}&timeout=0&limit=30'
    try:
        return http_get(url).get('result', [])
    except Exception as e:
        print(f'getUpdates error: {e}')
        return []

# ============ COMMANDS ============
def cmd_help():
    return """🌟 <b>أهلاً بك في بوت أَسْهُم</b>

<b>الأوامر:</b>

🔍 <code>/analyze AAPL</code> أو <code>تحليل AAPL</code>
تحليل عميق لسهم بـ Claude AI

📊 <code>/scan</code> أو <code>فحص</code>
فحص فوري لكل أسهم القائمة

📋 <code>/list</code> أو <code>قائمة</code>
عرض الأسهم المتابعة

📈 <code>/quote AAPL</code> أو <code>سعر AAPL</code>
السعر الحالي + تغير اليوم

💡 <code>/help</code> أو <code>مساعدة</code>
هذه الرسالة

<i>البوت يفحص السوق تلقائياً 8 مرات يومياً ويرسل الفرص.</i>"""

def cmd_list():
    text = f"📊 <b>قائمة المتابعة ({len(SYMBOLS)})</b>\n\n"
    text += '\n'.join(f"{i+1}. <b>{s}</b>" for i, s in enumerate(SYMBOLS))
    text += "\n\n<i>للتعديل: عدّل الـ secret SYMBOLS في GitHub</i>"
    return text

def cmd_quote(symbol):
    if not symbol:
        return "❌ استخدم: <code>/quote AAPL</code>"

    candles, meta = fetch_candles(symbol, '5d', '1h')
    if not candles or not meta:
        return f"❌ تعذر جلب بيانات <b>{symbol}</b>"

    price = meta.get('regularMarketPrice', candles[-1]['close'])
    prev = meta.get('previousClose', candles[-2]['close'])
    change = price - prev
    change_pct = (change / prev * 100) if prev else 0
    high = meta.get('regularMarketDayHigh', 0)
    low = meta.get('regularMarketDayLow', 0)
    vol = meta.get('regularMarketVolume', 0)

    emoji = '🟢' if change >= 0 else '🔴'
    return f"""{emoji} <b>{symbol}</b>

💰 <b>السعر:</b> ${price:.2f}
📊 <b>التغير:</b> {change:+.2f} ({change_pct:+.2f}%)
📈 <b>الأعلى:</b> ${high:.2f}
📉 <b>الأدنى:</b> ${low:.2f}
🔊 <b>الحجم:</b> {vol/1e6:.2f}M

⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"""

def cmd_analyze(symbol):
    if not symbol:
        return "❌ استخدم: <code>/analyze AAPL</code>"
    if not CLAUDE_KEY:
        return "❌ Claude API key غير مُعد"

    tg_send(f"🔍 جاري تحليل <b>{symbol}</b> بـ Claude AI...")

    c15m, _ = fetch_candles(symbol, '5d', '15m')
    c1h, meta = fetch_candles(symbol, '1mo', '1h')
    c4h_raw, _ = fetch_candles(symbol, '3mo', '1h')
    c1d, _ = fetch_candles(symbol, '6mo', '1d')

    if not all([c15m, c1h, c4h_raw, c1d]):
        return f"❌ بيانات غير كافية لـ <b>{symbol}</b>"

    c4h = aggregate_4h(c4h_raw)
    price = meta.get('regularMarketPrice') if meta else c15m[-1]['close']
    prev = meta.get('previousClose', c1d[-2]['close'] if len(c1d) > 1 else price)
    change_pct = ((price - prev) / prev * 100) if prev else 0

    data = {
        'c15m': c15m, 'c1h': c1h, 'c4h': c4h, 'c1d': c1d,
        'price': price, 'change_pct': change_pct
    }

    result = claude_analyze(symbol, data)

    if result.get('decision') == 'SKIP':
        return f"""📊 <b>{symbol}</b> — لا توصية حالياً

💰 السعر: ${price:.2f} ({change_pct:+.2f}%)

📝 <b>التحليل:</b>
{result.get('reasoning', 'الشروط غير محققة')}

<i>الجودة قبل الكمية. سنواصل المراقبة.</i>"""

    return format_signal(symbol, result)

def cmd_scan():
    # Trigger a scan now (uses scan.py logic)
    tg_send(f"⚙️ بدء مسح فوري لـ {len(SYMBOLS)} أسهم...")

    found = 0
    for symbol in SYMBOLS:
        try:
            c15m, _ = fetch_candles(symbol, '5d', '15m')
            c1h, meta = fetch_candles(symbol, '1mo', '1h')
            c4h_raw, _ = fetch_candles(symbol, '3mo', '1h')
            c1d, _ = fetch_candles(symbol, '6mo', '1d')

            if not all([c15m, c1h, c4h_raw, c1d]): continue
            c4h = aggregate_4h(c4h_raw)
            price = meta.get('regularMarketPrice') if meta else c15m[-1]['close']
            prev = meta.get('previousClose', c1d[-2]['close'])
            change_pct = ((price - prev) / prev * 100) if prev else 0

            data = {'c15m': c15m, 'c1h': c1h, 'c4h': c4h, 'c1d': c1d, 'price': price, 'change_pct': change_pct}
            result = claude_analyze(symbol, data)

            if result.get('decision') != 'SKIP' and result.get('confidence', 0) >= 70:
                tg_send(format_signal(symbol, result))
                found += 1

            time.sleep(2)
        except Exception as e:
            print(f'scan error {symbol}: {e}')

    return f"✅ <b>اكتمل المسح</b>\n\n🔍 فحص: {len(SYMBOLS)}\n🎯 فرص: {found}"

# ============ COMMAND ROUTER ============
def handle_command(text):
    text = text.strip()
    parts = text.split()
    if not parts: return None

    cmd = parts[0].lower().lstrip('/')
    args = parts[1:]
    arg = args[0].upper() if args else ''

    if cmd in ('start', 'help', 'مساعدة'):
        return cmd_help()
    if cmd in ('list', 'قائمة'):
        return cmd_list()
    if cmd in ('quote', 'سعر'):
        return cmd_quote(arg)
    if cmd in ('analyze', 'تحليل', 'check'):
        return cmd_analyze(arg)
    if cmd in ('scan', 'فحص'):
        return cmd_scan()

    return "❓ أمر غير معروف. استخدم <code>/help</code> لرؤية الأوامر"

# ============ MAIN ============
def main():
    if not TG_TOKEN or not TG_CHAT:
        print('Telegram not configured')
        return

    offset = get_last_offset()
    print(f'Polling Telegram with offset {offset}...')

    updates = get_updates(offset)
    print(f'Got {len(updates)} updates')

    new_offset = offset
    processed_chats = set()

    for update in updates:
        update_id = update.get('update_id', 0)
        if update_id >= new_offset:
            new_offset = update_id + 1

        msg = update.get('message') or update.get('edited_message')
        if not msg: continue

        chat_id = str(msg.get('chat', {}).get('id', ''))
        # Only respond to configured chat
        if chat_id != TG_CHAT:
            print(f'Ignoring chat {chat_id}')
            continue

        text = msg.get('text', '').strip()
        if not text:
            continue

        print(f'  Command: {text}')
        try:
            reply = handle_command(text)
            if reply:
                tg_send(reply)
        except Exception as e:
            print(f'Command error: {e}')
            tg_send(f'❌ خطأ في تنفيذ الأمر: {e}')

    save_offset(new_offset)
    print(f'Updated offset to {new_offset}')

if __name__ == '__main__':
    main()
