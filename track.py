#!/usr/bin/env python3
"""
Ashum Trading Bot V2 - Position Tracking System
- Tracks open positions every 15 minutes
- Smart Trailing Stop (INITIAL → BREAK_EVEN → AT_TP1)
- SL confirmation via 15M candle close
- Real-time Telegram notifications
"""

import os
import sys
import json
import time
from datetime import datetime, timezone, timedelta
from urllib import request as urllib_request
import urllib.error

# ============ CONFIG ============
TG_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN', '').strip()
TG_CHAT = os.environ.get('TELEGRAM_CHAT_ID', '').strip()

POSITIONS_FILE = 'positions.json'
CLOSED_FILE = 'closed.json'

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

def save_closed(data):
    data['lastUpdated'] = datetime.now(timezone.utc).isoformat()
    with open(CLOSED_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def recalculate_stats():
    """تحديث الإحصائيات الشاملة"""
    data = load_closed()
    closed_list = data.get('closed', [])

    wins = [p for p in closed_list if p.get('result') == 'WIN']
    losses = [p for p in closed_list if p.get('result') == 'LOSS']
    expired = [p for p in closed_list if p.get('result') == 'EXPIRED']

    win_pnls = [p.get('finalPnL', 0) for p in wins]
    loss_pnls = [p.get('finalPnL', 0) for p in losses]
    all_pnls = [p.get('finalPnL', 0) for p in closed_list]

    data['stats'] = {
        'total': len(closed_list),
        'wins': len(wins),
        'losses': len(losses),
        'expired': len(expired),
        'winRate': round((len(wins) / len(closed_list)) * 100, 1) if closed_list else 0,
        'avgWin': round(sum(win_pnls) / len(win_pnls), 2) if win_pnls else 0,
        'avgLoss': round(sum(loss_pnls) / len(loss_pnls), 2) if loss_pnls else 0,
        'bestTrade': round(max(all_pnls), 2) if all_pnls else 0,
        'worstTrade': round(min(all_pnls), 2) if all_pnls else 0,
        'totalPnL': round(sum(all_pnls), 2) if all_pnls else 0,
    }
    save_closed(data)
    return data['stats']

# ============ YAHOO FINANCE ============
def get_current_price(symbol):
    """جلب السعر الحالي"""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=1m'
    try:
        data = http_get(url, headers={'User-Agent': 'Mozilla/5.0'})
        result = data.get('chart', {}).get('result', [None])[0]
        if not result: return None
        meta = result.get('meta', {})
        return meta.get('regularMarketPrice')
    except:
        return None

def get_15m_candles(symbol, count=4):
    """آخر شموع 15 دقيقة"""
    url = f'https://query1.finance.yahoo.com/v8/finance/chart/{symbol}?range=1d&interval=15m'
    try:
        data = http_get(url, headers={'User-Agent': 'Mozilla/5.0'})
        result = data.get('chart', {}).get('result', [None])[0]
        if not result: return []

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
        return candles[-count:]
    except:
        return []

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

# ============ TRACKING LOGIC ============
def is_long(position):
    return position['direction'] == 'LONG'

def calculate_pnl(position, current_price):
    """حساب الربح/الخسارة %"""
    entry = position['entry']
    if is_long(position):
        return ((current_price - entry) / entry) * 100
    else:
        return ((entry - current_price) / entry) * 100

def check_tp_hit(position, current_price):
    """فحص أي هدف تم ضربه (ولم يتم رصده سابقاً)"""
    targets_hit = position.get('targetsHit', [])
    targets = position.get('targets', [])
    newly_hit = []

    for i, target in enumerate(targets):
        if i in targets_hit: continue

        if is_long(position):
            if current_price >= target:
                newly_hit.append(i)
        else:
            if current_price <= target:
                newly_hit.append(i)

    return newly_hit

def check_sl_touch(position, current_price):
    """فحص هل السعر يلامس SL"""
    sl = position['currentStopLoss']
    if is_long(position):
        return current_price <= sl
    else:
        return current_price >= sl

def check_sl_confirmed(position):
    """تأكيد SL بإغلاق شمعة 15M
    
    Returns True if a completed 15M candle closed beyond SL
    """
    candles = get_15m_candles(position['symbol'], count=2)
    if len(candles) < 2:
        return False

    # نأخذ الشمعة المغلقة (قبل الأخيرة، لأن الأخيرة لسه تتشكل)
    last_closed = candles[-2]
    sl = position['currentStopLoss']

    if is_long(position):
        return last_closed['close'] < sl
    else:
        return last_closed['close'] > sl

def update_trailing_stop(position, newly_hit_targets):
    """تحديث Trailing Stop حسب الدرجات"""
    current_stage = position.get('trailingStage', 'INITIAL')
    new_sl = position['currentStopLoss']
    sl_changed = False

    # درجة 1: عند ضرب TP1 → SL ينتقل إلى Entry (Break Even)
    if 0 in newly_hit_targets and current_stage == 'INITIAL':
        new_sl = position['entry']
        position['trailingStage'] = 'BREAK_EVEN'
        sl_changed = True

    # درجة 2: عند ضرب TP2 → SL ينتقل إلى TP1
    if 1 in newly_hit_targets and current_stage in ['INITIAL', 'BREAK_EVEN']:
        new_sl = position['targets'][0]
        position['trailingStage'] = 'AT_TP1'
        sl_changed = True

    return new_sl, sl_changed

def close_position(position, current_price, result, close_reason):
    """إغلاق صفقة ونقلها للسجل"""
    now = datetime.now(timezone.utc)
    opened = datetime.fromisoformat(position['openedAt'].replace('Z', '+00:00'))
    duration_hours = round((now - opened).total_seconds() / 3600, 1)

    pnl = calculate_pnl(position, current_price)

    closed_record = {
        **position,
        'status': 'CLOSED',
        'closedAt': now.isoformat(),
        'exitPrice': current_price,
        'closeReason': close_reason,
        'result': result,  # WIN | LOSS | EXPIRED
        'finalPnL': round(pnl, 2),
        'durationHours': duration_hours,
    }

    # نقل إلى closed.json
    closed_data = load_closed()
    closed_data['closed'].append(closed_record)
    save_closed(closed_data)

    # حذف من positions.json
    positions_data = load_positions()
    positions_data['positions'] = [p for p in positions_data['positions'] if p['id'] != position['id']]
    save_positions(positions_data)

    return pnl, duration_hours

# ============ NOTIFICATIONS ============
def notify_tp_hit(position, target_index, current_price, sl_changed, new_sl):
    """تنبيه عند ضرب هدف"""
    target = position['targets'][target_index]
    pnl = calculate_pnl(position, current_price)
    direction = 'كول (CALL)' if is_long(position) else 'بوت (PUT)'

    msg = f"""✅ <b>تحقق هدف!</b>

📊 <b>{position['symbol']}</b> · {direction}
🎯 <b>الهدف {target_index + 1}:</b> <code>${target:.2f}</code> ✓
💰 <b>السعر الحالي:</b> <code>${current_price:.2f}</code>
📈 <b>الربح حتى الآن:</b> <code>+{pnl:.2f}%</code>"""

    if sl_changed:
        if position['trailingStage'] == 'BREAK_EVEN':
            msg += f"\n\n🛡️ <b>Trailing Stop تحرّك!</b>\n   SL ← Entry (<code>${new_sl:.2f}</code>)\n   <i>أصبحت آمنة من الخسارة</i>"
        elif position['trailingStage'] == 'AT_TP1':
            msg += f"\n\n🛡️ <b>Trailing Stop تحرّك!</b>\n   SL ← TP1 (<code>${new_sl:.2f}</code>)\n   <i>ربح مضمون</i>"

    remaining = len(position['targets']) - len(position['targetsHit'])
    if remaining > 0:
        next_target = position['targets'][target_index + 1] if target_index + 1 < len(position['targets']) else None
        if next_target:
            msg += f"\n\n<i>استمر للأهداف التالية · TP{target_index + 2} على ${next_target:.2f}</i>"

    tg_send(msg)

def notify_full_win(position, current_price):
    """تنبيه إغلاق كامل بربح"""
    pnl = calculate_pnl(position, current_price)
    opened = datetime.fromisoformat(position['openedAt'].replace('Z', '+00:00'))
    duration_hours = round((datetime.now(timezone.utc) - opened).total_seconds() / 3600, 1)

    stats = recalculate_stats()
    win_rate_line = ''
    if stats['total'] >= 3:
        win_rate_line = f"\n\n📊 نسبة النجاح الإجمالية: <b>{stats['winRate']}%</b> ({stats['wins']}/{stats['total']})"

    duration_str = f"{duration_hours} ساعة" if duration_hours < 24 else f"{round(duration_hours/24, 1)} يوم"

    msg = f"""🎉 <b>تحققت جميع الأهداف!</b>

📊 <b>{position['symbol']}</b> — صفقة ناجحة كاملة
💰 <b>الدخول:</b> <code>${position['entry']:.2f}</code>
💵 <b>الخروج:</b> <code>${current_price:.2f}</code>
📈 <b>الربح:</b> <b>+{pnl:.2f}%</b>
⏱️ <b>المدة:</b> {duration_str}{win_rate_line}"""

    tg_send(msg)

def notify_sl_warning(position, current_price):
    """تنبيه مبكر: السعر يلامس SL"""
    direction = 'كول (CALL)' if is_long(position) else 'بوت (PUT)'

    msg = f"""⚠️ <b>تحذير: السعر يلامس SL</b>

📊 <b>{position['symbol']}</b> · {direction}
🛑 <b>SL:</b> <code>${position['currentStopLoss']:.2f}</code>
💰 <b>السعر الحالي:</b> <code>${current_price:.2f}</code>
⏰ <b>انتظار تأكيد إغلاق شمعة 15M</b>

<i>إذا أُغلقت الشمعة تحت SL → سيتم الإغلاق</i>"""

    tg_send(msg)

def notify_sl_hit(position, current_price):
    """تنبيه إغلاق بـ SL (مؤكّد)"""
    pnl = calculate_pnl(position, current_price)
    direction = 'كول (CALL)' if is_long(position) else 'بوت (PUT)'

    msg = f"""🛑 <b>تم ضرب وقف الخسارة</b>

📊 <b>{position['symbol']}</b> · {direction}
💰 <b>سعر الدخول:</b> <code>${position['entry']:.2f}</code>
🛑 <b>السعر الحالي:</b> <code>${current_price:.2f}</code>
📉 <b>الخسارة:</b> <b>{pnl:.2f}%</b>

⚠️ <i>تأكيد: شمعة 15M أُغلقت تحت SL — ليس wick fake</i>
<i>الالتزام بـ SL هو السبيل للنجاح</i>"""

    tg_send(msg)

def notify_expired(position, current_price):
    """تنبيه انتهاء الصلاحية"""
    pnl = calculate_pnl(position, current_price)

    msg = f"""⏰ <b>انتهت صلاحية الفرصة</b>

📊 <b>{position['symbol']}</b>
💰 <b>سعر الدخول:</b> <code>${position['entry']:.2f}</code>
💵 <b>السعر الحالي:</b> <code>${current_price:.2f}</code>
{'📈' if pnl >= 0 else '📉'} <b>النتيجة:</b> {'+' if pnl >= 0 else ''}{pnl:.2f}%

<i>تم نقلها إلى السجل التاريخي</i>"""

    tg_send(msg)

# ============ MAIN TRACKING LOGIC ============
def track_position(position):
    """تتبع صفقة واحدة - يرجع True إذا تغيّر شي"""
    symbol = position['symbol']
    print(f'\n📊 {symbol} ({position["id"]}):')

    # 1. جلب السعر الحالي
    current_price = get_current_price(symbol)
    if current_price is None:
        print(f'  ⚠️  فشل جلب السعر')
        return False

    # تحديث السعر والـ PnL
    position['currentPrice'] = current_price
    position['currentPnL'] = round(calculate_pnl(position, current_price), 2)
    position['lastChecked'] = datetime.now(timezone.utc).isoformat()
    print(f'  💰 ${current_price:.2f} (PnL: {position["currentPnL"]:+.2f}%)')

    # 2. فحص انتهاء الصلاحية
    now = datetime.now(timezone.utc)
    expires = datetime.fromisoformat(position['expiresAt'].replace('Z', '+00:00'))
    if now > expires:
        print(f'  ⏰ انتهت الصلاحية')
        notify_expired(position, current_price)
        close_position(position, current_price, 'EXPIRED' if position['currentPnL'] < 0 else 'WIN', 'EXPIRED')
        return True

    # 3. فحص ضرب SL (لمس)
    if check_sl_touch(position, current_price):
        # هل أول مرة يلمس؟
        if not position.get('slTouchTime'):
            position['slTouchTime'] = now.isoformat()
            print(f'  ⚠️  السعر يلامس SL — انتظار تأكيد')
            notify_sl_warning(position, current_price)
            return True

        # فحص تأكيد إغلاق شمعة 15M
        if check_sl_confirmed(position):
            print(f'  🛑 SL مؤكّد بإغلاق شمعة 15M — إغلاق')
            notify_sl_hit(position, current_price)
            close_position(position, current_price, 'LOSS', 'SL_HIT_CONFIRMED')
            return True
        else:
            print(f'  ⏳ السعر تحت SL لكن لم تُغلق شمعة 15M بعد')
            return True
    else:
        # السعر ابتعد عن SL
        if position.get('slTouchTime'):
            position['slTouchTime'] = None
            print(f'  ✅ السعر ابتعد عن SL — إلغاء التحذير')

    # 4. فحص ضرب الأهداف
    newly_hit = check_tp_hit(position, current_price)
    if newly_hit:
        # تحديث Trailing Stop أولاً
        new_sl, sl_changed = update_trailing_stop(position, newly_hit)
        if sl_changed:
            position['currentStopLoss'] = new_sl

        # إضافة الأهداف المضروبة
        position['targetsHit'].extend(newly_hit)

        # تنبيه لكل هدف
        for target_idx in newly_hit:
            notify_tp_hit(position, target_idx, current_price, sl_changed and target_idx == newly_hit[0], new_sl)

        # هل كل الأهداف ضُربت؟
        if len(position['targetsHit']) == len(position['targets']):
            print(f'  🎉 جميع الأهداف ضُربت — إغلاق كامل')
            notify_full_win(position, current_price)
            close_position(position, current_price, 'WIN', 'ALL_TP_HIT')
        else:
            print(f'  ✅ ضُرب TP{newly_hit[0] + 1} — الصفقة مستمرة')

        return True

    print(f'  ⏳ مستمرة (TPs: {len(position["targetsHit"])}/{len(position["targets"])})')
    return False

def main():
    print(f'🤖 Ashum V2 Tracker — {datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")}')

    if not TG_TOKEN or not TG_CHAT:
        print('⚠️  Telegram not configured')

    data = load_positions()
    open_positions = [p for p in data['positions'] if p.get('status') != 'CLOSED']

    print(f'📋 صفقات مفتوحة: {len(open_positions)}')

    if not open_positions:
        print('✅ لا توجد صفقات للمتابعة')
        return

    changes = 0
    for position in open_positions:
        try:
            if track_position(position):
                changes += 1
            time.sleep(1)  # احترام rate limit
        except Exception as e:
            print(f'  ❌ خطأ في تتبع {position["symbol"]}: {e}')

    # حفظ التحديثات
    save_positions(data)

    print(f'\n✅ التتبع اكتمل: {changes} تغيير')

    # تحديث الإحصائيات
    stats = recalculate_stats()
    print(f'📊 الإحصائيات: {stats["wins"]}W / {stats["losses"]}L = {stats["winRate"]}%')

if __name__ == '__main__':
    main()
