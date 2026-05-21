#!/usr/bin/env python3
"""
Ashum Trading Bot V2 - Smart Market Scanner
- Wall Street SMC methodology
- Yahoo News integration
- Top 3 opportunities filtering
- Auto-adds to position tracking
"""

import os
import sys
import json
import time
import re
from datetime import datetime, timezone, timedelta
from urllib import request as urllib_request
from urllib.parse import quote
import urllib.error

# ============ CONFIG ============
CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '').strip()
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
SYMBOLS = os.environ.get('SYMBOLS', 'TSLA,NVDA,AAPL,META,GOOGL,MSFT,AMD').split(',')
MIN_CONFIDENCE = int(os.environ.get('MIN_CONFIDENCE', '75'))
SCAN_MODE = os.environ.get('SCAN_MODE', 'swing')
TOP_N = int(os.environ.get('TOP_N', '3'))
CLAUDE_MODEL = 'claude-opus-4-5'

SYMBOLS = [s.strip().upper() for s in SYMBOLS if s.strip()]

POSITIONS_FILE = 'positions.json'
CLOSED_FILE = 'closed.json'

# ============ HTTP HELPERS ============
def http_get(url, headers=None, timeout=20):
    req = urllib_request.Request(url, headers=headers or {})
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def http_post(url, body, headers=None, timeout=60):
    data = json.dumps(body).encode('utf-8')
    h = {'Content-Type': 'application/json'}
    if headers: h.update(headers)
    req = urllib_request.Request(url, data=data, headers=h, method='POST')
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def http_get_text(url, headers=None, timeout=15):
    req = urllib_request.Request(url, headers=headers or {})
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='ignore')

# ============ FILE OPERATIONS ============
def load_positions():
    try:
        with open(POSITIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {'positions': [], 'lastUpdated': datetime.now(timezone.utc).isoformat(), 'version': '2.0'}

def save_positions(data):
    data['lastUpdated'] = datetime.now(timezone.utc).isoformat()
    with open(POSITIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def load_closed():
    try:
        with open(CLOSED_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {'closed': [], 'stats': {}, 'version': '2.0'}

def has_open_position(symbol):
    data = load_positions()
    return any(p['symbol'] == symbol.upper() and p['status'] != 'CLOSED' for p in data['positions'])

def calculate_win_rate():
    """نسبة النجاح من السجل التاريخي"""
    data = load_closed()
    closed_list = data.get('closed', [])
    if not closed_list: return None
    wins = sum(1 for p in closed_list if p.get('result') == 'WIN')
    total = len(closed_list)
    return {
        'rate': round((wins / total) * 100, 1) if total else 0,
        'wins': wins,
        'total': total
    }

# ============ YAHOO FINANCE ============
def fetch_candles(symbol, range_, interval):
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range={range_}&interval={interval}'
    try:
        data = http_get(url, headers={'User-Agent': 'Mozilla/5.0'})
        result = data.get('chart', {}).get('result', [None])[0]
        if not result: return None, None

        ts = result.get('timestamp', [])
        q = result.get('indicators', {}).get('quote', [{}])[0]
        candles = []
        for i, t in enumerate(ts):
            if q.get('close', [None])[i] is None: continue
            candles.append({
                'time': t * 1000,
                'open': q['open'][i],
                'high': q['high'][i],
                'low': q['low'][i],
                'close': q['close'][i],
                'volume': q.get('volume', [0])[i] or 0,
            })
        meta = result.get('meta', {})
        return candles, meta
    except Exception as e:
        print(f'  ⚠️  {symbol} fetch failed: {e}')
        return None, None

def fetch_news(symbol, max_items=5):
    """جلب آخر الأخبار من Yahoo Finance"""
    try:
        url = f'https://query1.finance.yahoo.com/v1/finance/search?q={symbol}&newsCount={max_items}&quotesCount=0'
        data = http_get(url, headers={'User-Agent': 'Mozilla/5.0'})
        news = data.get('news', [])
        recent_news = []
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=48)).timestamp()
        for n in news[:max_items]:
            pub_time = n.get('providerPublishTime', 0)
            if pub_time < cutoff: continue
            recent_news.append({
                'title': n.get('title', ''),
                'publisher': n.get('publisher', ''),
                'time': datetime.fromtimestamp(pub_time, tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
                'hours_ago': round((datetime.now(timezone.utc).timestamp() - pub_time) / 3600, 1)
            })
        return recent_news
    except Exception as e:
        print(f'  ⚠️  {symbol} news fetch failed: {e}')
        return []

def fetch_earnings_date(symbol):
    """تاريخ Earnings القادم"""
    try:
        url = f'https://query1.finance.yahoo.com/v10/finance/quoteSummary/{symbol}?modules=calendarEvents'
        data = http_get(url, headers={'User-Agent': 'Mozilla/5.0'})
        cal = data.get('quoteSummary', {}).get('result', [{}])[0].get('calendarEvents', {})
        earnings = cal.get('earnings', {}).get('earningsDate', [])
        if not earnings: return None
        ts = earnings[0].get('raw', 0)
        if not ts: return None
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except:
        return None

def fetch_52w_data(symbol):
    """52-week high/low"""
    try:
        url = f'https://query1.finance.yahoo.com/v6/finance/quote?symbols={symbol}'
        data = http_get(url, headers={'User-Agent': 'Mozilla/5.0'})
        result = data.get('quoteResponse', {}).get('result', [{}])
        if result:
            r = result[0]
            return {
                'high_52w': r.get('fiftyTwoWeekHigh'),
                'low_52w': r.get('fiftyTwoWeekLow'),
                'avg_volume': r.get('averageDailyVolume3Month'),
            }
    except: pass
    return {}

def aggregate_4h(candles_1h):
    out = []
    for i in range(0, len(candles_1h), 4):
        chunk = candles_1h[i:i+4]
        if not chunk: continue
        out.append({
            'time': chunk[0]['time'],
            'open': chunk[0]['open'],
            'high': max(c['high'] for c in chunk),
            'low': min(c['low'] for c in chunk),
            'close': chunk[-1]['close'],
            'volume': sum(c['volume'] for c in chunk),
        })
    return out

# ============ TECHNICAL INDICATORS ============
def ema(values, period):
    if not values: return []
    k = 2 / (period + 1)
    out = [values[0]]
    for i in range(1, len(values)):
        out.append(values[i] * k + out[-1] * (1 - k))
    return out

def rsi(values, period=14):
    if len(values) < period + 1: return 50
    gains = losses = 0
    for i in range(1, period + 1):
        d = values[i] - values[i-1]
        if d > 0: gains += d
        else: losses -= d
    avg_g = gains / period
    avg_l = losses / period
    for i in range(period + 1, len(values)):
        d = values[i] - values[i-1]
        g = max(d, 0); l = max(-d, 0)
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l == 0: return 100
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)

def atr(candles, period=14):
    if len(candles) < 2: return 0
    trs = []
    for i in range(1, len(candles)):
        c = candles[i]; p = candles[i-1]
        trs.append(max(c['high'] - c['low'], abs(c['high'] - p['close']), abs(c['low'] - p['close'])))
    last = trs[-period:]
    return sum(last) / len(last)

def vwap(candles):
    """Volume Weighted Average Price"""
    if not candles: return 0
    cum_pv = sum(((c['high'] + c['low'] + c['close']) / 3) * c['volume'] for c in candles)
    cum_v = sum(c['volume'] for c in candles)
    return cum_pv / cum_v if cum_v > 0 else 0

def summarize(candles):
    closes = [c['close'] for c in candles]
    if not closes: return None
    last = len(closes) - 1
    e9 = ema(closes, 9)[last]
    e21 = ema(closes, 21)[last]
    e50 = ema(closes, 50)[last] if len(closes) >= 50 else closes[last]
    rsi_v = rsi(closes, 14)
    atr_v = atr(candles, 14)
    recent = candles[-20:]
    vols = [c['volume'] for c in recent]
    avg_vol = sum(vols) / len(vols) if vols else 0
    last_vol = candles[last]['volume']
    vwap_v = vwap(candles[-26:])
    return {
        'price': closes[last],
        'ema9': e9, 'ema21': e21, 'ema50': e50,
        'rsi': rsi_v, 'atr': atr_v,
        'recent_high': max(c['high'] for c in recent),
        'recent_low': min(c['low'] for c in recent),
        'vol_ratio': last_vol / avg_vol if avg_vol > 0 else 1,
        'vwap': vwap_v,
    }

# ============ CLAUDE AI - WALL STREET PROMPT ============
def claude_analyze(symbol, data):
    s15 = summarize(data['c15m'][-50:])
    s1h = summarize(data['c1h'][-50:])
    s4h = summarize(data['c4h'][-50:])
    s1d = summarize(data['c1d'][-50:])

    if not all([s15, s1h, s4h, s1d]):
        return {'decision': 'SKIP', 'reasoning': 'بيانات غير كافية'}

    def fmt_c(c):
        ts = datetime.fromtimestamp(c['time']/1000, tz=timezone.utc).strftime('%m-%d %H:%M')
        return f"{ts} O:{c['open']:.2f} H:{c['high']:.2f} L:{c['low']:.2f} C:{c['close']:.2f} V:{c['volume']/1e6:.2f}M"

    candles_1h_str = '\n'.join(fmt_c(c) for c in data['c1h'][-10:])
    candles_15m_str = '\n'.join(fmt_c(c) for c in data['c15m'][-10:])

    news_str = "لا توجد أخبار حديثة"
    if data.get('news'):
        news_str = '\n'.join(f"- [{n['hours_ago']}h ago] {n['title']} ({n['publisher']})" for n in data['news'][:5])

    earnings_str = "لا توجد earnings قريبة"
    earnings_warning = ""
    if data.get('earnings_date'):
        ed = data['earnings_date']
        hours_to_earnings = (ed - datetime.now(timezone.utc)).total_seconds() / 3600
        if 0 < hours_to_earnings < 48:
            earnings_warning = f"\n\n⚠️ تحذير حرج: Earnings خلال {hours_to_earnings:.1f} ساعة فقط! IV crush متوقع - تجنب الأوبشنز العادية، يمكن اقتراح Iron Condor أو Straddle"
            earnings_str = f"⚠️ Earnings في {ed.strftime('%Y-%m-%d %H:%M UTC')} ({hours_to_earnings:.1f} ساعة)"
        else:
            earnings_str = f"Earnings في {ed.strftime('%Y-%m-%d')} ({hours_to_earnings/24:.1f} يوم)"

    w52 = data.get('w52', {})
    w52_str = ""
    if w52.get('high_52w'):
        dist_high = ((w52['high_52w'] - data['price']) / data['price'] * 100)
        dist_low = ((data['price'] - w52['low_52w']) / data['price'] * 100)
        w52_str = f"\n- 52W High: ${w52['high_52w']:.2f} (على بُعد {dist_high:.1f}%)\n- 52W Low: ${w52['low_52w']:.2f} (على بُعد {dist_low:.1f}%)"

    win_stats = calculate_win_rate()
    win_rate_str = f"{win_stats['rate']}% ({win_stats['wins']}/{win_stats['total']})" if win_stats else "لا توجد بيانات سابقة"

    prompt = f"""أنت محلل تداول أسطوري في Wall Street، خبرة 20+ سنة، متخصص في:
- 0DTE & Weekly Options Flow
- Smart Money Concepts (SMC) - Order Blocks, Liquidity Hunts, BOS, CHoCH
- Volume Profile & VWAP Analysis
- Multi-timeframe Confluence (Daily → 4H → 1H → 15M)
- Price Action (Pin bars, Engulfing, Inside bars, Liquidity sweeps)

# 🧠 العقلية الأساسية (لا تتجاوزها أبداً):
قبل أي إشارة، اسأل نفسك: "لو كنت أتداول حساب $1M، هل سأدخل هذا الترييد؟"
إذا الإجابة "لا" أو "ربما" → اختر SKIP فوراً وبدون تردد.
المتداول الأسطوري ينتظر فرصة A+ فقط، ولا يضيع رأسماله على setups B أو C.

═══════════════════════════════════════
# 📊 بيانات السهم: {symbol}
═══════════════════════════════════════

السعر الحالي: ${data['price']:.2f}
تغير اليوم: {data['change_pct']:+.2f}%
نسبة نجاح إشاراتك السابقة: {win_rate_str}{w52_str}

## 📅 Earnings:
{earnings_str}{earnings_warning}

## 📰 آخر الأخبار (48 ساعة):
{news_str}

═══════════════════════════════════════
# 📈 التحليل الفني متعدد الإطارات
═══════════════════════════════════════

## Daily (الإطار الرئيسي):
- Price: ${s1d['price']:.2f}
- EMA9/21/50: ${s1d['ema9']:.2f} / ${s1d['ema21']:.2f} / ${s1d['ema50']:.2f}
- RSI: {s1d['rsi']:.1f} | ATR: ${s1d['atr']:.2f}
- 20-day Range: ${s1d['recent_low']:.2f} → ${s1d['recent_high']:.2f}

## 4H (إطار التأكيد):
- EMA9/21/50: ${s4h['ema9']:.2f} / ${s4h['ema21']:.2f} / ${s4h['ema50']:.2f}
- RSI: {s4h['rsi']:.1f} | Volume Ratio: {s4h['vol_ratio']:.2f}x
- VWAP: ${s4h['vwap']:.2f} (السعر {'فوق' if data['price'] > s4h['vwap'] else 'تحت'} VWAP)

## 1H (إطار الدخول):
- EMA9/21/50: ${s1h['ema9']:.2f} / ${s1h['ema21']:.2f} / ${s1h['ema50']:.2f}
- RSI: {s1h['rsi']:.1f} | Volume Ratio: {s1h['vol_ratio']:.2f}x
- VWAP: ${s1h['vwap']:.2f}

## 15M (إطار التوقيت):
- EMA9/21: ${s15['ema9']:.2f} / ${s15['ema21']:.2f}
- RSI: {s15['rsi']:.1f} | Volume Ratio: {s15['vol_ratio']:.2f}x

## آخر 10 شموع 1H:
{candles_1h_str}

## آخر 10 شموع 15M:
{candles_15m_str}

═══════════════════════════════════════
# 🔬 المنهجية الإلزامية (حلل بالترتيب):
═══════════════════════════════════════

## 1. Market Structure
- اتجاه Daily/4H/1H/15M (Higher Highs/Lows أم Lower Highs/Lows؟)
- BOS (Break of Structure) صاعد أم هابط؟
- CHoCH (Change of Character) حصل مؤخراً؟

## 2. Liquidity & Order Blocks
- أين آخر Order Block صاعد/هابط على 1H أو 4H؟
- Liquidity zones فوق Highs / تحت Lows (Stop Hunt zones)
- هل في Liquidity Sweep حدث؟

## 3. VWAP Relationship
- علاقة السعر بـ VWAP (rejection? bounce? cross?)
- VWAP يعمل كدعم أم مقاومة الآن؟

## 4. Confluence Check (الأهم!)
عد العوامل المتحققة من 6:
✓ Price Action واضح (rejection candle / engulfing / breakout)
✓ Volume يدعم (Volume Ratio ≥ 1.5x المتوسط)
✓ مستوى دعم/مقاومة قوي (من 4H أو Daily، ليس من 15M)
✓ Market Structure متوافقة (Daily و 1H نفس الاتجاه)
✓ EMAs aligned (9>21>50 للصاعد، العكس للهابط)
✓ خبر/كاتاليست داعم أو على الأقل لا يعارض

═══════════════════════════════════════
# 🚨 قواعد صارمة (لا استثناءات):
═══════════════════════════════════════

1. ❌ لا تعطي إشارة "ضعيفة" أبداً - SKIP أفضل ألف مرة من إشارة سيئة
2. ❌ لا تخرج عن الـ watchlist
3. ✅ R:R ≥ 1:2 إلزامي (حسبه من الأهداف ووقف الخسارة)
4. ✅ Strike قريب من السعر الحالي (ATM للـ 0DTE، OTM 1-3% للـ Swing)
5. ✅ SL منطقي على شمعة مرفوضة أو تحت Order Block
6. ✅ إذا السهم في range محايد: SKIP مع رسالة "لا توجد فرصة - يتداول في ${'{'+'low'+'}'} - ${'{'+'high'+'}'}"
7. ✅ إذا earnings خلال 48 ساعة: SKIP أو اقتراح Iron Condor/Straddle
8. ✅ Confluence < 4: SKIP فوراً

═══════════════════════════════════════
# 📋 الإخراج (JSON فقط، بدون أي نص خارجه):
═══════════════════════════════════════

{{
  "decision": "OPEN_SWING_LONG | OPEN_SWING_SHORT | OPEN_DTE_CALL | OPEN_DTE_PUT | SKIP",
  "confidence": 0-100,
  "confidenceLevel": "ضعيفة | جيدة | قوية | استثنائية",
  "entry": رقم,
  "stopLoss": رقم,
  "targets": [tp1, tp2, tp3],
  "entryType": "كسر (Breakout) | اختراق (Pullback) | انعكاس (Reversal)",
  "optionStrike": رقم (Strike المقترح),
  "optionExpiry": "YYYYMMDD (تاريخ انتهاء العقد)",
  "expiryType": "0DTE | 1DTE | 3DTE | Weekly | 2Weeks",
  "reasoning": "شرح بالعربي - structure، دعم/مقاومة، confluence، السيناريو المتوقع",
  "warnings": ["تحذير 1", "تحذير 2"],
  "confluenceFactors": ["BOS صاعد H1", "Volume 2.1x", "Order Block 4H", ...],
  "riskRewardRatio": رقم (مثل 2.5 لـ 1:2.5),
  "marketStructure": "Higher Highs & Lows (صاعد) | Lower Highs & Lows (هابط) | Range"
}}

تذكير أخير: إذا كانت Confluence < 4 أو الثقة < 75% → decision = "SKIP"
لا ترسل لي إشارات ضعيفة أبداً. أنا أثق بك."""

    try:
        resp = http_post(
            'https://api.anthropic.com/v1/messages',
            {
                'model': CLAUDE_MODEL,
                'max_tokens': 2500,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            headers={
                'x-api-key': CLAUDE_KEY,
                'anthropic-version': '2023-06-01'
            },
            timeout=90
        )
        text = resp.get('content', [{}])[0].get('text', '').strip()
        text = text.replace('```json', '').replace('```', '').strip()
        start = text.find('{')
        end = text.rfind('}')
        if start == -1: return {'decision': 'SKIP', 'reasoning': 'فشل تحليل الاستجابة'}
        return json.loads(text[start:end+1])
    except Exception as e:
        print(f'  ⚠️  Claude failed for {symbol}: {e}')
        return {'decision': 'SKIP', 'reasoning': f'خطأ: {e}'}

# ============ TELEGRAM ============
def tg_send(text, parse_mode='HTML'):
    if not TG_TOKEN or not TG_CHAT:
        print('Telegram not configured')
        return
    url = f'https://api.telegram.org/bot{TG_TOKEN}/sendMessage'
    try:
        http_post(url, {
            'chat_id': TG_CHAT,
            'text': text,
            'parse_mode': parse_mode,
            'disable_web_page_preview': True
        }, timeout=15)
    except Exception as e:
        print(f'Telegram error: {e}')

def format_new_signal(symbol, result, win_stats=None):
    """التنسيق الاحترافي الجديد"""
    decision = result.get('decision', 'SKIP')

    direction_map = {
        'OPEN_SWING_LONG': ('🟢', 'كول (CALL)', 'شراء Swing'),
        'OPEN_SWING_SHORT': ('🔴', 'بوت (PUT)', 'بيع Swing'),
        'OPEN_DTE_CALL': ('🟢', 'كول (CALL)', '0DTE Call'),
        'OPEN_DTE_PUT': ('🔴', 'بوت (PUT)', '0DTE Put'),
    }
    emoji, direction, trade_type = direction_map.get(decision, ('📊', '—', '—'))

    confidence = result.get('confidence', 0)
    conf_level = result.get('confidenceLevel', 'جيدة')
    expiry_type = result.get('expiryType', 'Weekly')

    targets = result.get('targets', [])
    rr = result.get('riskRewardRatio', 0)

    factors = result.get('confluenceFactors', [])
    factors_text = '\n'.join(f"   ✓ {f}" for f in factors[:5]) if factors else ''

    warnings = result.get('warnings', [])
    warnings_text = ''
    if warnings:
        warnings_text = '\n\n⚠️ <b>تحذيرات:</b>\n' + '\n'.join(f"   • {w}" for w in warnings)

    win_rate_line = ''
    if win_stats and win_stats['total'] >= 5:
        win_rate_line = f"\n✅ <b>نسبة النجاح السابقة:</b> {win_stats['rate']}% ({win_stats['wins']}/{win_stats['total']})"

    option_line = ''
    if result.get('optionStrike'):
        strike = result.get('optionStrike')
        expiry = result.get('optionExpiry', '')
        option_line = f"\n📋 <b>العقد المقترح:</b>\n   {emoji} {direction.split()[0]} | Expiry: <code>{expiry}</code> | Strike: <code>{strike}</code>"

    intraday_line = '\n⚡ <i>مناسبة لصفقة intraday</i>' if 'DTE' in decision else ''

    return f"""🤖 <b>رسالة من البوت الآلي</b>

📊 <b>إشارة تداول {symbol}</b>
{emoji} <b>الاتجاه:</b> {direction}
🟡 <b>درجة الثقة:</b> {conf_level} · {confidence}%{win_rate_line}
🕒 <b>صلاحية العقد:</b> {expiry_type}

⚙️ <b>خطة التنفيذ:</b>
   🔹 نوع الدخول: {result.get('entryType', '—')}
   🔹 منطقة الدخول: <code>{result.get('entry', 0):.2f}</code>
   🔹 مستوى الوقف: <code>{result.get('stopLoss', 0):.2f}</code>
{chr(10).join(f"   🔹 الهدف {i+1}: <code>{t:.2f}</code>" for i, t in enumerate(targets))}
   🔹 R:R: <code>1:{rr:.1f}</code>{intraday_line}

🧠 <b>Confluence ({len(factors)} عوامل):</b>
{factors_text}{option_line}

📝 <b>التحليل:</b>
{result.get('reasoning', '—')}{warnings_text}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}
<i>للمراقبة فقط — ليست توصية شراء</i>"""

# ============ POSITION MANAGEMENT ============
def create_position(symbol, result, current_price):
    """إنشاء صفقة جديدة من نتيجة Claude"""
    now = datetime.now(timezone.utc)
    decision = result.get('decision')

    type_map = {
        'OPEN_SWING_LONG': 'SWING_LONG',
        'OPEN_SWING_SHORT': 'SWING_SHORT',
        'OPEN_DTE_CALL': 'DTE_CALL',
        'OPEN_DTE_PUT': 'DTE_PUT',
    }

    # حساب expiresAt حسب نوع الصفقة
    if 'DTE' in decision:
        expires = now + timedelta(hours=8)  # 0DTE ينتهي اليوم
    else:
        expires = now + timedelta(days=10)  # Swing 10 أيام

    return {
        'id': f"{symbol}_{now.strftime('%Y%m%d_%H%M%S')}",
        'symbol': symbol,
        'type': type_map.get(decision, 'SWING_LONG'),
        'direction': 'LONG' if 'LONG' in decision or 'CALL' in decision else 'SHORT',
        'status': 'OPEN',
        'openedAt': now.isoformat(),
        'expiresAt': expires.isoformat(),

        # السعر والمستويات
        'openedPrice': current_price,
        'entry': result.get('entry', current_price),
        'originalStopLoss': result.get('stopLoss', 0),
        'currentStopLoss': result.get('stopLoss', 0),
        'targets': result.get('targets', []),
        'targetsHit': [],

        # Trailing Stop Tracking
        'trailingStage': 'INITIAL',  # INITIAL → BREAK_EVEN → AT_TP1

        # السعر الحالي
        'currentPrice': current_price,
        'currentPnL': 0,
        'lastChecked': now.isoformat(),

        # SL Confirmation
        'slTouchTime': None,  # متى لمس SL أول مرة
        'slConfirmed': False,

        # معلومات الفرصة
        'confidence': result.get('confidence', 0),
        'confidenceLevel': result.get('confidenceLevel', 'جيدة'),
        'entryType': result.get('entryType', ''),
        'reasoning': result.get('reasoning', ''),
        'warnings': result.get('warnings', []),
        'confluenceFactors': result.get('confluenceFactors', []),
        'riskRewardRatio': result.get('riskRewardRatio', 0),
        'marketStructure': result.get('marketStructure', ''),

        # Options Info
        'optionStrike': result.get('optionStrike'),
        'optionExpiry': result.get('optionExpiry'),
        'expiryType': result.get('expiryType'),
    }

def add_position(position):
    """إضافة صفقة جديدة لـ positions.json"""
    data = load_positions()
    data['positions'].append(position)
    save_positions(data)
    print(f"  💾 Saved position {position['id']}")

# ============ MAIN ============
def main():
    print(f'🚀 Ashum V2 Scanner — mode={SCAN_MODE}, min_conf={MIN_CONFIDENCE}, top={TOP_N}')
    print(f'📋 Symbols: {", ".join(SYMBOLS)}')

    if not CLAUDE_KEY:
        print('❌ CLAUDE_API_KEY missing')
        sys.exit(1)

    win_stats = calculate_win_rate()
    if win_stats:
        print(f"📊 Historical win rate: {win_stats['rate']}% ({win_stats['wins']}/{win_stats['total']})")

    # المرحلة 1: جمع كل الفرص المحتملة
    all_opportunities = []

    for symbol in SYMBOLS:
        print(f'\n🔍 {symbol}...')

        # Skip if already has open position
        if has_open_position(symbol):
            print(f'  ⏭️  لديه صفقة مفتوحة بالفعل')
            continue

        try:
            # جلب البيانات
            c15m, _ = fetch_candles(symbol, '5d', '15m')
            c1h, meta = fetch_candles(symbol, '1mo', '1h')
            c4h_raw, _ = fetch_candles(symbol, '3mo', '1h')
            c1d, _ = fetch_candles(symbol, '6mo', '1d')

            if not all([c15m, c1h, c4h_raw, c1d]):
                print(f'  ⏭️  بيانات غير كافية')
                continue

            c4h = aggregate_4h(c4h_raw)
            price = meta.get('regularMarketPrice') if meta else c15m[-1]['close']
            prev = meta.get('previousClose', c1d[-2]['close'] if len(c1d) > 1 else price)
            change_pct = ((price - prev) / prev * 100) if prev else 0

            # جلب الإضافات (الجديد!)
            print(f'  📰 جلب الأخبار...')
            news = fetch_news(symbol)
            earnings = fetch_earnings_date(symbol)
            w52 = fetch_52w_data(symbol)

            data = {
                'c15m': c15m, 'c1h': c1h, 'c4h': c4h, 'c1d': c1d,
                'price': price, 'change_pct': change_pct,
                'news': news, 'earnings_date': earnings, 'w52': w52
            }

            print(f'  💰 ${price:.2f} ({change_pct:+.2f}%) — يحلل بـ Claude...')
            result = claude_analyze(symbol, data)
            decision = result.get('decision', 'SKIP')
            confidence = result.get('confidence', 0)

            if decision == 'SKIP':
                print(f'  ⏭️  SKIP — {result.get("reasoning", "")[:80]}')
                continue

            if confidence < MIN_CONFIDENCE:
                print(f'  ⏭️  confidence {confidence}% < {MIN_CONFIDENCE}%')
                continue

            print(f'  ✅ {decision} @ {confidence}% — مرشّحة للـ Top {TOP_N}')
            all_opportunities.append({
                'symbol': symbol,
                'price': price,
                'result': result,
            })

            time.sleep(2)  # احترام API rate limit
        except Exception as e:
            print(f'  ❌ Error: {e}')

    # المرحلة 2: اختيار أعلى N فرص
    all_opportunities.sort(key=lambda x: x['result'].get('confidence', 0), reverse=True)
    top_opportunities = all_opportunities[:TOP_N]

    print(f'\n📊 إجمالي الفرص المكتشفة: {len(all_opportunities)}')
    print(f'🎯 أعلى {TOP_N} فرص:')
    for opp in top_opportunities:
        print(f"   - {opp['symbol']}: {opp['result'].get('confidence')}% — {opp['result'].get('decision')}")

    # المرحلة 3: حفظ + إرسال
    saved_count = 0
    for opp in top_opportunities:
        position = create_position(opp['symbol'], opp['result'], opp['price'])
        add_position(position)
        saved_count += 1

        # إرسال تيليقرام
        msg = format_new_signal(opp['symbol'], opp['result'], win_stats)
        tg_send(msg)
        time.sleep(1)

    # ملخص نهائي
    summary = f"""🎯 <b>ملخص الفحص</b>

🔍 أسهم تم فحصها: {len(SYMBOLS)}
💡 فرص مكتشفة: {len(all_opportunities)}
⭐ Top {TOP_N} (تنضم للمتابعة): {saved_count}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"""

    if not saved_count:
        summary += "\n\n<i>لا توجد فرص قوية الآن. الجودة قبل الكمية.</i>"

    print(f'\n{summary}')
    if saved_count or os.environ.get('SEND_SUMMARY', 'false').lower() == 'true':
        tg_send(summary)

    print(f'\n✅ Scan complete: {saved_count} positions opened')

if __name__ == '__main__':
    main()
