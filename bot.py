"""Byudjet Telegram bot.

Ovozli xabar yuborasiz -> bot tushunadi -> avtomatik yozib qo'yadi.
Matn yozsangiz ham ishlaydi.
"""
import os
import asyncio
import logging
from dotenv import load_dotenv
from aiohttp import web as aioweb
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup,
    MenuButtonWebApp, WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

import db
import ai
import fx
from miniapp import make_web_app

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("byudjet")

# Faqat ruxsat etilgan foydalanuvchilar (bo'sh bo'lsa — hamma). Vergul bilan ID lar.
_allowed = os.getenv("ALLOWED_USER_IDS", "").replace(" ", "")
ALLOWED = {int(x) for x in _allowed.split(",") if x} if _allowed else None

TUR_BELGI = {
    "kirim": "🟢 Kirim",
    "chiqim": "🔴 Chiqim",
    "qarz_berdim": "📤 Qarz berdim",
    "qarz_oldim": "📥 Qarz oldim",
    "qarz_qaytardim": "↩️ Qarzimni qaytardim",
    "qarz_qaytarildi": "↪️ Qarzim qaytarildi",
}


def som(n):
    return f"{round(n):,}".replace(",", " ")


def _vaqt_matni(iso):
    """ISO vaqtni chiroyli ko'rsatish: '6-iyul 19:00'."""
    from datetime import datetime
    oylar = ["", "yanvar", "fevral", "mart", "aprel", "may", "iyun",
             "iyul", "avgust", "sentabr", "oktabr", "noyabr", "dekabr"]
    try:
        d = datetime.fromisoformat(iso)
        return f"{d.day}-{oylar[d.month]} {d.strftime('%H:%M')}"
    except Exception:
        return iso


def _eslatma_matni(row):
    """Vaqti kelgan eslatma uchun xabar matni."""
    s = som(row["summa"])
    kim = row.get("kim")
    tur = row["tur"]
    if tur == "qarz_berdim" and kim:
        return f"⏰ Eslatma: *{kim}* qarzini qaytarishi kerak edi — {s} so'm."
    if tur == "qarz_oldim" and kim:
        return f"⏰ Eslatma: *{kim}ga* qarzni qaytarishingiz kerak — {s} so'm."
    tavsif = kim or row.get("izoh") or row.get("kategoriya") or "yozuv"
    return f"⏰ Eslatma: {tavsif} — {s} so'm."


def ruxsat(user_id):
    return ALLOWED is None or user_id in ALLOWED


async def guard(update: Update):
    uid = update.effective_user.id
    if not ruxsat(uid):
        await update.message.reply_text("Kechirasiz, bu shaxsiy bot.")
        return False
    return True


# ---------- Komandalar ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(
        "Salom! Men sizning byudjet daftaringizman. 💰\n\n"
        "Menga *ovozli xabar*, *matn* yoki *chek rasmini* yuboring — o'zim tushunib yozib qo'yaman.\n\n"
        "Masalan:\n"
        "• «Bugun bozorga 85 ming sarfladim»\n"
        "• «Umarga 20 dollar qarz berdim» (o'zi so'mga o'giradi)\n"
        "• «Alidan 25 ming qarz, bugun kechga qaytaradi» ⏰\n"
        "• 🧾 Chek rasmini yuboring — summasini o'zi oladi\n\n"
        "Buyruqlar:\n"
        "/balans — hisobingiz\n"
        "/qarzlar — kim kimga qarzdor\n"
        "/limit — oylik norma belgilash\n"
        "/royxat — so'nggi yozuvlar",
        parse_mode="Markdown",
    )


async def balans_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    b = db.balance(update.effective_user.id)
    await update.message.reply_text(
        f"💰 *Umumiy balans:* {som(b['balans'])} so'm\n\n"
        f"Shu oy:\n🟢 Kirim: {som(b['oy_kirim'])}\n🔴 Chiqim: {som(b['oy_chiqim'])}",
        parse_mode="Markdown",
    )


async def qarzlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    d = db.debts(update.effective_user.id)
    if not d:
        await update.message.reply_text("Hozircha qarzlar yo'q. ✅")
        return
    menga, men = [], []
    for kim, v in sorted(d.items(), key=lambda x: -abs(x[1])):
        if v > 0:
            menga.append(f"• {kim}: {som(v)} so'm")
        else:
            men.append(f"• {kim}: {som(-v)} so'm")
    parts = []
    if menga:
        parts.append("📥 *Menga qarzdor:*\n" + "\n".join(menga))
    if men:
        parts.append("📤 *Men qarzdorman:*\n" + "\n".join(men))
    await update.message.reply_text("\n\n".join(parts), parse_mode="Markdown")


async def royxat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    rows = db.last_entries(update.effective_user.id, 10)
    if not rows:
        await update.message.reply_text("Hali yozuv yo'q.")
        return
    lines = []
    for r in rows:
        belgi = TUR_BELGI.get(r["tur"], r["tur"])
        kim = f" ({r['kim']})" if r["kim"] else ""
        izoh = f" — {r['izoh']}" if r["izoh"] else ""
        lines.append(f"{belgi}{kim}: {som(r['summa'])}{izoh}")
    await update.message.reply_text("🧾 So'nggi yozuvlar:\n\n" + "\n".join(lines))


async def limit_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    uid = update.effective_user.id
    if ctx.args:
        raqam = "".join(c for c in "".join(ctx.args) if c.isdigit())
        if raqam:
            db.set_limit(uid, float(raqam))
            await update.message.reply_text(
                f"✅ Oylik norma: *{som(float(raqam))} so'm*", parse_mode="Markdown"
            )
        else:
            await update.message.reply_text("Summani raqamda yozing. Masalan: /limit 2000000")
        return
    limit = db.get_limit(uid)
    if not limit:
        await update.message.reply_text(
            "Oylik norma belgilanmagan.\n\nBelgilash: /limit 2000000\n"
            "Yoki ovozda: «oylik normani 2 million qil»"
        )
        return
    sarf = db.month_chiqim(uid)
    qoldi = limit - sarf
    holat = f"Qolgan: {som(qoldi)} so'm" if qoldi >= 0 else f"Oshgani: *{som(-qoldi)} so'm* ⚠️"
    await update.message.reply_text(
        f"📏 *Oylik norma:* {som(limit)} so'm\n"
        f"Sarflangan: {som(sarf)} so'm\n{holat}",
        parse_mode="Markdown",
    )


# ---------- Yozuvni saqlash va javob ----------
VALYUTA_BELGI = {"dollar": "$", "yevro": "€", "rubl": "₽"}


async def saqlash_va_javob(update: Update, t: ai.Tranzaksiya):
    uid = update.effective_user.id

    # 1) Oylik norma belgilash
    if getattr(t, "limit_belgilash", None):
        db.set_limit(uid, t.limit_belgilash)
        await update.message.reply_text(
            f"✅ Oylik xarajat normasi belgilandi: *{som(t.limit_belgilash)} so'm*",
            parse_mode="Markdown",
        )
        return

    if not t.tushunildi or not t.tur or not t.summa:
        await update.message.reply_text(
            "Tushunolmadim 🤔 Iltimos qaytaring.\n"
            f"Eshitganim: «{t.transkript}»"
        )
        return

    # 2) Valyutani so'mga o'girish
    valyuta = (getattr(t, "valyuta", None) or "som").lower()
    izoh = t.izoh
    if valyuta in VALYUTA_BELGI:
        kurs = fx.get_rate(valyuta)
        asl = t.summa
        t.summa = round(asl * kurs)
        belgi_v = VALYUTA_BELGI[valyuta]
        asl_matn = f"{som(asl)}{belgi_v}"
        izoh = f"{asl_matn} ({som(kurs)} kurs)" + (f" · {izoh}" if izoh else "")

    entry_id = db.add_entry(
        uid, t.tur.value, t.summa,
        kim=t.kim, kategoriya=t.kategoriya, izoh=izoh, transkript=t.transkript,
        eslatma_vaqti=t.eslatma_vaqti,
    )

    belgi = TUR_BELGI.get(t.tur.value, t.tur.value)
    satr = [f"✅ Yozildi\n\n{belgi}: *{som(t.summa)} so'm*"]
    if t.kim:
        satr.append(f"👤 {t.kim}")
    if t.kategoriya:
        satr.append(f"🏷 {t.kategoriya}")
    if izoh:
        satr.append(f"📝 {izoh}")
    if t.eslatma_vaqti:
        satr.append(f"⏰ Eslatma: {_vaqt_matni(t.eslatma_vaqti)}")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"del:{entry_id}")
    ]])
    await update.message.reply_text("\n".join(satr), parse_mode="Markdown", reply_markup=kb)

    # 3) Oylik norma tekshiruvi (faqat xarajat uchun)
    if t.tur.value == "chiqim":
        await _norma_tekshir(update, uid)


async def _norma_tekshir(update: Update, uid: int):
    limit = db.get_limit(uid)
    if not limit:
        return
    sarf = db.month_chiqim(uid)
    if sarf > limit:
        oshgan = sarf - limit
        await update.message.reply_text(
            "⚠️ *Oylik normadan oshdingiz!*\n"
            f"Norma: {som(limit)} so'm\n"
            f"Sarfladingiz: {som(sarf)} so'm\n"
            f"Oshgani: *{som(oshgan)} so'm*",
            parse_mode="Markdown",
        )
    elif sarf >= 0.9 * limit:
        foiz = round(sarf / limit * 100)
        await update.message.reply_text(
            f"⚠️ Normaga yaqinlashdingiz: {som(sarf)} / {som(limit)} so'm ({foiz}%)"
        )


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    msg = await update.message.reply_text("🎧 Tinglayapman…")
    try:
        tg_file = await ctx.bot.get_file(update.message.voice.file_id)
        audio = bytes(await tg_file.download_as_bytearray())
        t = ai.from_audio(audio, mime_type="audio/ogg")
        await msg.delete()
        await saqlash_va_javob(update, t)
    except Exception as e:
        log.exception("voice xatolik")
        await msg.edit_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    try:
        t = ai.from_text(update.message.text)
        await saqlash_va_javob(update, t)
    except Exception as e:
        log.exception("text xatolik")
        await update.message.reply_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    msg = await update.message.reply_text("🧾 Chekni o'qiyapman…")
    try:
        photo = update.message.photo[-1]  # eng katta o'lcham
        tg_file = await ctx.bot.get_file(photo.file_id)
        img = bytes(await tg_file.download_as_bytearray())
        t = ai.from_image(img, mime_type="image/jpeg")
        await msg.delete()
        await saqlash_va_javob(update, t)
    except Exception as e:
        log.exception("photo xatolik")
        await msg.edit_text("Chekni o'qib bo'lmadi. Aniqroq, yorug' rasm yuboring.")


async def on_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    entry_id = int(q.data.split(":")[1])
    if db.delete_entry(entry_id, q.from_user.id):
        await q.edit_message_text("🗑 Bekor qilindi.")
    else:
        await q.edit_message_text("Bu yozuv topilmadi.")


def webapp_url():
    """Mini App manzili. Railway domen bergach avtomatik topiladi."""
    if os.getenv("WEBAPP_URL"):
        return os.environ["WEBAPP_URL"].rstrip("/")
    dom = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{dom}" if dom else None


async def reminder_loop(app):
    """Har daqiqada vaqti kelgan eslatmalarni tekshirib, xabar yuboradi."""
    while True:
        try:
            for r in db.due_reminders():
                try:
                    await app.bot.send_message(
                        chat_id=r["user_id"], text=_eslatma_matni(r), parse_mode="Markdown"
                    )
                    db.mark_reminder_sent(r["id"])
                except Exception:
                    log.exception("eslatma yuborishda xatolik")
        except Exception:
            log.exception("eslatma tekshiruvi xatolik")
        await asyncio.sleep(60)


async def run():
    token = os.environ["TELEGRAM_TOKEN"]
    db.init_db()
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balans", balans_cmd))
    app.add_handler(CommandHandler("qarzlar", qarzlar_cmd))
    app.add_handler(CommandHandler("royxat", royxat_cmd))
    app.add_handler(CommandHandler("limit", limit_cmd))
    app.add_handler(CallbackQueryHandler(on_undo, pattern=r"^del:"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Mini App veb-serveri (bot bilan bir jarayonda ishlaydi)
    port = int(os.getenv("PORT", "8080"))
    runner = aioweb.AppRunner(make_web_app(token))
    await runner.setup()
    site = aioweb.TCPSite(runner, "0.0.0.0", port)

    await app.initialize()
    await app.start()
    await site.start()

    # Menyu tugmasini Mini App'ga ulash
    url = webapp_url()
    if url:
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="📊 Byudjet", web_app=WebAppInfo(url=url))
            )
            log.info("Mini App tugmasi ulandi: %s", url)
        except Exception:
            log.exception("menyu tugmasini ulashda xatolik")
    else:
        log.warning("WEBAPP_URL / RAILWAY_PUBLIC_DOMAIN yo'q — Mini App tugmasi qo'yilmadi")

    await app.updater.start_polling()
    asyncio.create_task(reminder_loop(app))
    log.info("Bot + Mini App ishga tushdi (port %s).", port)
    await asyncio.Event().wait()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
