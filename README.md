# 💰 Byudjet Telegram Bot

Ovozli xabar yuborasiz — bot o'zi tushunib, daromad/xarajat/qarzni avtomatik yozib qo'yadi.

**Masalan aytasiz:**
- «Bugun bozorga 85 ming sarfladim» → 🔴 Chiqim, 85 000
- «Umarga 90 ming qarz berdim» → 📤 Qarz, Umar menga qarzdor
- «Maosh 4 million tushdi» → 🟢 Kirim, 4 000 000

Matn yozsangiz ham ishlaydi. Gemini audioni to'g'ridan-to'g'ri o'zbek tilida tushunadi — alohida ovoz-matn dasturi kerak emas.

---

## Nima kerak

- **Python 3.10+** kompyuteringizda o'rnatilgan bo'lishi kerak
- **Telegram bot tokeni** (bepul)
- **Gemini API kaliti** (bepul limit bor)

---

## 1-qadam: Telegram bot yaratish

1. Telegramda [@BotFather](https://t.me/BotFather) ga yozing
2. `/newbot` buyrug'ini yuboring, botga nom bering
3. U sizga **token** beradi (masalan `123456:ABC-DEF...`) — saqlab qo'ying

## 2-qadam: Gemini kaliti olish

1. [aistudio.google.com](https://aistudio.google.com) ga kiring (Google akkaunt)
2. "Get API key" → "Create API key" bosing
3. Berilgan kalitni saqlab qo'ying

## 3-qadam: (ixtiyoriy) O'z Telegram ID ngizni bilish

Botni faqat o'zingiz ishlatishingiz uchun. [@userinfobot](https://t.me/userinfobot) ga yozing — u sizning ID raqamingizni beradi.

---

## O'rnatish va ishga tushirish

Terminal (buyruqlar oynasi) da papka ichida:

```bash
# 1) Kutubxonalarni o'rnatish
pip install -r requirements.txt

# 2) Sozlamalar faylini tayyorlash
cp .env.example .env
```

Endi `.env` faylini ochib, o'z ma'lumotlaringizni yozing:

```
TELEGRAM_TOKEN=BotFather bergan token
GEMINI_API_KEY=aistudio bergan kalit
ALLOWED_USER_IDS=sizning_id_ngiz     # ixtiyoriy
```

Ishga tushirish:

```bash
python bot.py
```

"Bot ishga tushdi" chiqsa, Telegramda botingizga `/start` yozing. Tayyor! 🎉

---

## Buyruqlar

| Buyruq | Vazifasi |
|--------|----------|
| `/start` | Boshlash va yordam |
| `/balans` | Umumiy hisob + shu oy kirim/chiqimi |
| `/qarzlar` | Kim kimga qancha qarzdor |
| `/royxat` | So'nggi 10 ta yozuv |

Har bir yozuvdan keyin **↩️ Bekor qilish** tugmasi chiqadi — xato bo'lsa bosib o'chirasiz.

---

## Doim yoqilgan turishi uchun

Bot faqat kompyuter yoqiq va `python bot.py` ishlab turganda javob beradi. Doimiy ishlashi uchun:

- **Eng oson:** arzon VPS ijaraga oling (oyiga ~$4–5), botni o'sha yerda `python bot.py` bilan ishga tushiring
- Yoki kompyuteringizda kerak bo'lganda qo'lda yoqib qo'ying

Xohlasangiz, keyingi bosqichda buni bepul/arzon serverga joylashda ham yordam beraman.

---

## Ma'lumotlar qayerda

Barcha yozuvlar shu papkadagi `byudjet.db` faylida (SQLite) saqlanadi. Boshqa hech kimga yuborilmaydi.
