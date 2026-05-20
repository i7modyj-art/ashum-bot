#!/usr/bin/env python3
"""
Ashum Trading Bot
Runs on GitHub Actions schedule.
Scans US stocks → Claude AI analysis → Telegram alerts.
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from urllib import request as urllib_request
from urllib.parse import quote
import urllib.error

# ============ CONFIG ============
CLAUDE_KEY = os.environ.get('CLAUDE_API_KEY', '').strip()
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '').strip()
SYMBOLS = os.environ.get('SYMBOLS', 'TSLA,NVDA,AAPL,META,GOOGL,MSFT,AMD').split(',')
MIN_CONFIDENCE = int(os.environ.get('MIN_CONFIDENCE', '75'))
SCAN_MODE = os.environ.get('SCAN_MODE', 'swing')  # swing or dte
CLAUDE_MODEL = 'claude-opus-4-5'

SYMBOLS = [s.strip().upper() for s in SYMBOLS if s.strip()]

# ============ HTTP HELPERS ============
def http_get(url, headers=None, timeout=20):
    req = urllib_request.Request(url, headers=headers or {})
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

def http_post(url, body, headers=None, timeout=30):
    data = json.dumps(body).encode('utf-8')
    h = {'Content-Type': 'application/json'}
    if headers: h.update(headers)
    req = urllib_request.Request(url, data=data, headers=h, method='POST')
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())

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

# ============ INDICATORS ============
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
    return {
        'price': closes[last],
        'ema9': e9, 'ema21': e21, 'ema50': e50,
        'rsi': rsi_v, 'atr': atr_v,
        'recent_high': max(c['high'] for c in recent),
        'recent_low': min(c['low'] for c in recent),
        'vol_ratio': last_vol / avg_vol if avg_vol > 0 else 1,
    }

# ============ CLAUDE AI ============
def claude_analyze(symbol, data):
    s15 = summarize(data['c15m'][-50:])
    s1h = summarize(data['c1h'][-50:])
    s4h = summarize(data['c4h'][-50:])
    s1d = summarize(data['c1d'][-50:])

    if not all([s15, s1h, s4h, s1d]):
        return {'decision': 'SKIP', 'reasoning': 'بيانات غير كافية'}

    def fmt_c(c):
        ts = datetime.fromtimestamp(c['time']/1000, tz=timezone.utc).strftime('%m-%d %H:%M')
        return f"{ts} O:{c['open']:.2f} H:{c['high']:.2f} L:{c['low']:.2f} C:{c['close']:.2f}"

    candles_1h_str = '\n'.join(fmt_c(c) for c in data['c1h'][-10:])
    candles_15m_str = '\n'.join(fmt_c(c) for c in data['c15m'][-10:])

    prompt = f"""أنت محلل تداول محترف بمنهجية ICT/SMC + Price Action + Volume.

السهم: {symbol}
السعر الحالي: ${data['price']:.2f}
تغير اليوم: {data['change_pct']:.2f}%

═══ تحليل 4 إطارات ═══

📊 يومي (Daily):
- EMA9/21/50: ${s1d['ema9']:.2f} / ${s1d['ema21']:.2f} / ${s1d['ema50']:.2f}
- RSI: {s1d['rsi']:.1f} | ATR: ${s1d['atr']:.2f}
- نطاق 20 يوم: ${s1d['recent_low']:.2f} → ${s1d['recent_high']:.2f}

📊 4 ساعات:
- EMA9/21/50: ${s4h['ema9']:.2f} / ${s4h['ema21']:.2f} / ${s4h['ema50']:.2f}
- RSI: {s4h['rsi']:.1f} | حجم: {s4h['vol_ratio']:.2f}x

📊 ساعة:
- EMA9/21/50: ${s1h['ema9']:.2f} / ${s1h['ema21']:.2f} / ${s1h['ema50']:.2f}
- RSI: {s1h['rsi']:.1f} | حجم: {s1h['vol_ratio']:.2f}x

📊 15 دقيقة:
- EMA9/21: ${s15['ema9']:.2f} / ${s15['ema21']:.2f}
- RSI: {s15['rsi']:.1f} | حجم: {s15['vol_ratio']:.2f}x

═══ آخر 10 شموع 1H ═══
{candles_1h_str}

═══ آخر 10 شموع 15M ═══
{candles_15m_str}

═══ المطلوب ═══

حلل بصرامة. التزم بشروط القرار:

**شروط Swing:** confluence ≥ 4 عوامل، اتجاه واضح على Daily/4H، entry على دعم/مقاومة، R:R ≥ 1:2، ثقة ≥ 75
**شروط 0DTE:** setup قوي 15m+1h، volume ≥ 1.5x، entry قريب (< 0.5% من السعر)، ثقة ≥ 85

أرجع JSON فقط:

{{
  "decision": "OPEN_SWING_LONG | OPEN_SWING_SHORT | OPEN_DTE_CALL | OPEN_DTE_PUT | SKIP",
  "confidence": 0-100,
  "entry": رقم,
  "stopLoss": رقم,
  "targets": [tp1, tp2, tp3],
  "reasoning": "شرح بالعربي مختصر",
  "warnings": [],
  "confluenceFactors": [],
  "riskRewardRatio": رقم
}}"""

    try:
        resp = http_post(
            'https://api.anthropic.com/v1/messages',
            {
                'model': CLAUDE_MODEL,
                'max_tokens': 2000,
                'messages': [{'role': 'user', 'content': prompt}]
            },
            headers={
                'x-api-key': CLAUDE_KEY,
                'anthropic-version': '2023-06-01'
            },
            timeout=60
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
        })
    except Exception as e:
        print(f'Telegram error: {e}')

def format_signal(symbol, result):
    type_map = {
        'OPEN_SWING_LONG': '🟢 شراء Swing',
        'OPEN_SWING_SHORT': '🔴 بيع Swing',
        'OPEN_DTE_CALL': '🟢⚡ Call 0DTE',
        'OPEN_DTE_PUT': '🔴⚡ Put 0DTE',
    }
    decision = result.get('decision', 'SKIP')
    type_text = type_map.get(decision, decision)

    targets = result.get('targets', [])
    targets_text = '\n'.join(f"   {i+1}️⃣ ${t:.2f}" for i, t in enumerate(targets))

    factors = result.get('confluenceFactors', [])
    factors_text = '\n'.join(f"   • {f}" for f in factors) if factors else ''

    warnings = result.get('warnings', [])
    warnings_text = ''
    if warnings:
        warnings_text = '\n\n⚠️ <b>تحذيرات:</b>\n' + '\n'.join(f"   • {w}" for w in warnings)

    return f"""🎯 <b>فرصة جديدة</b>

📊 <b>{symbol}</b> — {type_text}
🎚️ <b>الثقة:</b> {result.get('confidence', 0)}%
📐 <b>R:R:</b> 1:{result.get('riskRewardRatio', 0):.1f}

💰 <b>الدخول:</b> ${result.get('entry', 0):.2f}
🛑 <b>وقف الخسارة:</b> ${result.get('stopLoss', 0):.2f}
🎯 <b>الأهداف:</b>
{targets_text}

📝 <b>التحليل:</b>
{result.get('reasoning', '—')}

{('🔗 <b>Confluence:</b>' + chr(10) + factors_text) if factors_text else ''}{warnings_text}

⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"""

# ============ MAIN ============
def main():
    print(f'🚀 Ashum Bot starting — mode={SCAN_MODE}, min_conf={MIN_CONFIDENCE}')
    print(f'📋 Symbols: {", ".join(SYMBOLS)}')

    if not CLAUDE_KEY:
        print('❌ CLAUDE_API_KEY missing')
        sys.exit(1)
    if not TG_TOKEN or not TG_CHAT:
        print('⚠️  Telegram not configured — signals will print only')

    signals_found = []
    scan_label = '⚡ 0DTE' if SCAN_MODE == 'dte' else '📈 Swing'

    for symbol in SYMBOLS:
        print(f'\n🔍 {symbol} — fetching...')
        try:
            c15m, _ = fetch_candles(symbol, '5d', '15m')
            c1h, meta = fetch_candles(symbol, '1mo', '1h')
            c4h_raw, _ = fetch_candles(symbol, '3mo', '1h')
            c1d, _ = fetch_candles(symbol, '6mo', '1d')

            if not all([c15m, c1h, c4h_raw, c1d]):
                print(f'  ⏭️  insufficient data')
                continue

            c4h = aggregate_4h(c4h_raw)
            price = meta.get('regularMarketPrice') if meta else c15m[-1]['close']
            prev = meta.get('previousClose', c1d[-2]['close'] if len(c1d) > 1 else price)
            change_pct = ((price - prev) / prev * 100) if prev else 0

            data = {
                'c15m': c15m, 'c1h': c1h, 'c4h': c4h, 'c1d': c1d,
                'price': price, 'change_pct': change_pct
            }

            print(f'  💰 ${price:.2f} ({change_pct:+.2f}%) — analyzing...')
            result = claude_analyze(symbol, data)
            decision = result.get('decision', 'SKIP')
            confidence = result.get('confidence', 0)

            if decision == 'SKIP':
                print(f'  ⏭️  SKIP')
                continue

            if confidence < MIN_CONFIDENCE:
                print(f'  ⏭️  confidence {confidence}% < {MIN_CONFIDENCE}%')
                continue

            # Filter by mode
            is_dte = 'DTE' in decision
            if SCAN_MODE == 'dte' and not is_dte:
                print(f'  ⏭️  not 0DTE')
                continue
            if SCAN_MODE == 'swing' and is_dte:
                print(f'  ⏭️  not Swing')
                continue

            print(f'  ✅ {decision} @ {confidence}%')
            signals_found.append((symbol, result))

            # Send to Telegram
            tg_send(format_signal(symbol, result))

            # Rate limit
            time.sleep(2)
        except Exception as e:
            print(f'  ❌ Error: {e}')

    # Summary
    summary = f"📊 <b>ملخص المسح — {scan_label}</b>\n\n" \
              f"🔍 تم فحص: {len(SYMBOLS)} أسهم\n" \
              f"🎯 فرص مكتشفة: {len(signals_found)}\n" \
              f"⏰ {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"

    if signals_found:
        symbols_list = ', '.join(s[0] for s in signals_found)
        summary += f"\n\n<b>الفرص:</b> {symbols_list}"
    else:
        summary += "\n\n<i>لا توجد فرص بالشروط الحالية. الجودة قبل الكمية.</i>"

    print(f'\n{summary}')
    if signals_found or os.environ.get('SEND_SUMMARY', 'false').lower() == 'true':
        tg_send(summary)

if __name__ == '__main__':
    main()
