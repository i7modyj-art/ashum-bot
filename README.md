# 🤖 Ashum Trading Bot V2

نظام تداول ذكي متقدم مع تتبع تلقائي وإدارة مخاطر احترافية.

## ✨ الميزات الجديدة في V2

- 🎯 **أعلى 3 فرص فقط** في كل فحص (جودة قبل كمية)
- 📊 **تتبع تلقائي للصفقات** مع TP/SL
- 🛡️ **Trailing Stop ذكي** (Entry → BE → TP1)
- ✅ **تأكيد SL** بإغلاق شمعة 15M (حماية من wick fakes)
- 📰 **Yahoo News integration** (آخر 48 ساعة)
- 📅 **Earnings Filter** (تجنب IV crush)
- 🎚️ **نسبة النجاح** في كل رسالة
- 💼 **منهجية Wall Street** (SMC + Order Blocks + Confluence)

## 📋 الأوامر

- `/help` - دليل الأوامر
- `/analyze AAPL` - تحليل عميق
- `/scan` - مسح فوري
- `/list` - قائمة الأسهم
- `/quote AAPL` - السعر الحالي
- `/open` - الصفقات المفتوحة
- `/history` - آخر 10 صفقات
- `/stats` - الإحصائيات

## 🏗️ الهيكل

```
ashum-bot/
├── .github/workflows/
│   ├── scan.yml          # فحص 8 مرات يومياً
│   ├── track.yml         # تتبع كل 15 دقيقة
│   └── listen.yml        # أوامر كل 5 دقايق
├── scan.py               # محرك الكشف
├── track.py              # محرك التتبع
├── listen.py             # معالج الأوامر
├── positions.json        # الصفقات المفتوحة
├── closed.json           # السجل التاريخي
└── last_offset.json      # offset تيليقرام
```

## 💰 التكلفة

- GitHub Actions: مجاني
- Yahoo Finance: مجاني
- Telegram: مجاني
- Claude API: ~$30-50/شهر

## 🛡️ Trailing Stop System

| المرحلة | السلوك |
|---|---|
| INITIAL | SL ثابت كما تم إعداده |
| BREAK_EVEN | بعد TP1 → SL ينتقل لـ Entry |
| AT_TP1 | بعد TP2 → SL ينتقل لـ TP1 |

## 🎯 SL Confirmation

عند لمس السعر للـ SL:
1. ⚠️ تنبيه فوري "السعر يلامس SL"
2. ⏳ انتظار إغلاق شمعة 15M
3. 🛑 إذا أُغلقت تحت SL → إغلاق مؤكّد
4. ✅ إذا ابتعد السعر → إلغاء التحذير
