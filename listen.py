#!/usr/bin/env python3
"""
Ashum V2 - Telegram Command Listener
Handles user commands every 5 minutes
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from urllib import request as urllib_request
import urllib.error

CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '').strip()
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
SYMBOLS = os.environ.get('SYMBOLS', 'TSLA,NVDA,AAPL,META,GOOGL,MSFT,AMD').split(',')
SYMBOLS = [s.strip().upper() for s in SYMBOLS if s.strip()]

# Import from scan.py
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from scan import (
    fetch_candles, aggregate_4h, claude_analyze,
    tg_send, format_new_signal, http_get, http_post,
    fetch_news, fetch_earnings_date, fetch_52w_data,
    calculate_win_rate, load_positions, load_closed,
    create_position, add_position, has_open_position
)

HISTORY_FILE = 'last_offset.json'

def get_last_offset():
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
    return """🌟 <b>أهلاً بك في أَسْهُم V2</b>

<b>📋 الأوامر المتاحة:</b>

🔍 <code>/analyze AAPL</code> أو <code>تحليل AAPL</code>
تحليل عميق بـ Claude AI

📊 <code>/scan</code> أو <code>فحص</code>
فحص فوري لكل القائمة

📋 <code>/list</code> أو <code>قائمة</code>
عرض الأسهم المتابعة

📈 <code>/quote AAPL</code> أو <code>سعر AAPL</code>
السعر الحالي

🎯 <code>/open</code> أو <code>مفتوحة</code>
عرض الصفقات المفتوحة الآن

📜 <code>/history</code> أو <code>سجل</code>
آخر 10 صفقات مغلقة

📊 <code>/stats</code> أو <code>إحصائيات</code>
نسبة النجاح والأداء

💡 <code>/help</code> أو <code>مساعدة</code>
هذه الرسالة

<i>البوت يفحص السوق تلقائياً 8 مرات يومياً</i>
<i>التتبع كل 15 دقيقة خلال السوق</i>"""

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
    w52_high = meta.get('fiftyTwoWeekHigh', 0)
    w52_low = meta.get('fiftyTwoWeekLow', 0)

    emoji = '🟢' if change >= 0 else '🔴'
    return f"""{emoji} <b>{symbol}</b>

💰 <b>السعر:</b> <code>${price:.2f}</code>
📊 <b>التغير:</b> {change:+.2f} ({change_pct:+.2f}%)
📈 <b>الأعلى/الأدنى اليوم:</b> ${high:.2f} / ${low:.2f}
🎯 <b>52W:</b> ${w52_low:.2f} → ${w52_high:.2f}
🔊 <b>الحجم:</b> {vol/1e6:.2f}M

⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"""

def cmd_open():
    """عرض الصفقات المفتوحة"""
    data = load_positions()
    positions = [p for p in data.get('positions', []) if p.get('status') != 'CLOSED']

    if not positions:
        return "📭 لا توجد صفقات مفتوحة حالياً"

    lines = [f"🎯 <b>الصفقات المفتوحة ({len(positions)})</b>\n"]

    for i, p in enumerate(positions, 1):
        direction = 'كول' if p['direction'] == 'LONG' else 'بوت'
        emoji = '🟢' if p['direction'] == 'LONG' else '🔴'
        pnl = p.get('currentPnL', 0)
        pnl_emoji = '📈' if pnl >= 0 else '📉'
        tp_done = len(p.get('targetsHit', []))
        tp_total = len(p.get('targets', []))

        trail_stage = p.get('trailingStage', 'INITIAL')
        trail_text = ''
        if trail_stage == 'BREAK_EVEN':
            trail_text = ' 🛡️BE'
        elif trail_stage == 'AT_TP1':
            trail_text = ' 🛡️TP1'

        lines.append(f"""{i}. {emoji} <b>{p['symbol']}</b> · {direction}
   💰 دخول: ${p['entry']:.2f} | حالي: ${p.get('currentPrice', 0):.2f}
   {pnl_emoji} PnL: {pnl:+.2f}% | TPs: {tp_done}/{tp_total}{trail_text}
   🛑 SL: ${p['currentStopLoss']:.2f}""")

    return '\n\n'.join(lines)

def cmd_history():
    data = load_closed()
    closed_list = data.get('closed', [])
    if not closed_list:
        return "📭 السجل فارغ"

    recent = closed_list[-10:][::-1]
    lines = [f"📜 <b>آخر {len(recent)} صفقة</b>\n"]

    for i, p in enumerate(recent, 1):
        pnl = p.get('finalPnL', 0)
        result = p.get('result', '')
        result_emoji = '✅' if result == 'WIN' else ('❌' if result == 'LOSS' else '⏰')
        closed_at = p.get('closedAt', '')[:10]
        lines.append(f"{i}. {result_emoji} <b>{p['symbol']}</b> · {pnl:+.2f}% · {closed_at}")

    return '\n'.join(lines)

def cmd_stats():
    data = load_closed()
    stats = data.get('stats', {})

    if not stats or stats.get('total', 0) == 0:
        return "📊 لا توجد إحصائيات بعد. ابدأ بتنفيذ بعض الصفقات."

    return f"""📊 <b>ملخص الأداء</b>

📈 <b>إجمالي:</b> {stats.get('total', 0)} صفقة
✅ <b>رابحة:</b> {stats.get('wins', 0)}
❌ <b>خاسرة:</b> {stats.get('losses', 0)}
⏰ <b>منتهية:</b> {stats.get('expired', 0)}

🎯 <b>نسبة النجاح:</b> {stats.get('winRate', 0)}%
💰 <b>متوسط الربح:</b> +{stats.get('avgWin', 0):.2f}%
📉 <b>متوسط الخسارة:</b> {stats.get('avgLoss', 0):.2f}%

🏆 <b>أفضل صفقة:</b> +{stats.get('bestTrade', 0):.2f}%
💔 <b>أسوأ صفقة:</b> {stats.get('worstTrade', 0):.2f}%
💵 <b>صافي PnL:</b> {stats.get('totalPnL', 0):+.2f}%"""

def cmd_analyze(symbol):
    if not symbol:
        return "❌ استخدم: <code>/analyze AAPL</code>"
    if not CLAUDE_KEY:
        return "❌ Claude API key غير مُعد"
    if has_open_position(symbol):
        return f"⚠️ <b>{symbol}</b> له صفقة مفتوحة بالفعل\nاستخدم /open لعرضها"

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

    # جلب إضافات
    news = fetch_news(symbol)
    earnings = fetch_earnings_date(symbol)
    w52 = fetch_52w_data(symbol)

    data = {
        'c15m': c15m, 'c1h': c1h, 'c4h': c4h, 'c1d': c1d,
        'price': price, 'change_pct': change_pct,
        'news': news, 'earnings_date': earnings, 'w52': w52
    }

    result = claude_analyze(symbol, data)
    win_stats = calculate_win_rate()

    if result.get('decision') == 'SKIP':
        return f"""📊 <b>{symbol}</b> — لا توصية حالياً

💰 السعر: ${price:.2f} ({change_pct:+.2f}%)

📝 <b>التحليل:</b>
{result.get('reasoning', 'الشروط غير محققة')}

<i>الجودة قبل الكمية</i>"""

    return format_new_signal(symbol, result, win_stats)

def cmd_scan():
    tg_send(f"⚙️ بدء مسح فوري لـ {len(SYMBOLS)} أسهم...")

    found = 0
    for symbol in SYMBOLS:
        if has_open_position(symbol):
            continue
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

            news = fetch_news(symbol)
            earnings = fetch_earnings_date(symbol)
            w52 = fetch_52w_data(symbol)

            data = {'c15m': c15m, 'c1h': c1h, 'c4h': c4h, 'c1d': c1d,
                    'price': price, 'change_pct': change_pct,
                    'news': news, 'earnings_date': earnings, 'w52': w52}
            result = claude_analyze(symbol, data)

            if result.get('decision') != 'SKIP' and result.get('confidence', 0) >= 75:
                win_stats = calculate_win_rate()
                position = create_position(symbol, result, price)
                add_position(position)
                tg_send(format_new_signal(symbol, result, win_stats))
                found += 1
            time.sleep(2)
        except Exception as e:
            print(f'scan error {symbol}: {e}')

    return f"✅ <b>اكتمل المسح</b>\n\n🔍 فحص: {len(SYMBOLS)}\n🎯 فرص جديدة: {found}"

# ============ ROUTER ============
def handle_command(text):
    text = text.strip()
    parts = text.split()
    if not parts: return None

    cmd = parts[0].lower().lstrip('/')
    args = parts[1:]
    arg = args[0].upper() if args else ''

    if cmd in ('start', 'help', 'مساعدة'): return cmd_help()
    if cmd in ('list', 'قائمة'): return cmd_list()
    if cmd in ('quote', 'سعر'): return cmd_quote(arg)
    if cmd in ('analyze', 'تحليل', 'check'): return cmd_analyze(arg)
    if cmd in ('scan', 'فحص'): return cmd_scan()
    if cmd in ('open', 'مفتوحة', 'positions'): return cmd_open()
    if cmd in ('history', 'سجل', 'closed'): return cmd_history()
    if cmd in ('stats', 'إحصائيات', 'احصائيات'): return cmd_stats()

    return "❓ أمر غير معروف. استخدم <code>/help</code>"

def main():
    if not TG_TOKEN or not TG_CHAT:
        print('Telegram not configured')
        return

    offset = get_last_offset()
    print(f'Polling Telegram (offset={offset})...')

    updates = get_updates(offset)
    print(f'Got {len(updates)} updates')

    new_offset = offset
    for update in updates:
        update_id = update.get('update_id', 0)
        if update_id >= new_offset:
            new_offset = update_id + 1

        msg = update.get('message') or update.get('edited_message')
        if not msg: continue

        chat_id = str(msg.get('chat', {}).get('id', ''))
        if chat_id != TG_CHAT:
            continue

        text = msg.get('text', '').strip()
        if not text: continue

        print(f'  Command: {text}')
        try:
            reply = handle_command(text)
            if reply:
                tg_send(reply)
        except Exception as e:
            print(f'Command error: {e}')
            tg_send(f'❌ خطأ: {e}')

    save_offset(new_offset)
    print(f'Updated offset to {new_offset}')

if __name__ == '__main__':
    main()
