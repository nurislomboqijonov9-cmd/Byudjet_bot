"""Byudjet Telegram bot.

Ovozli xabar yuborasiz -> bot tushunadi -> avtomatik yozib qo'yadi.
Matn yozsangiz ham ishlaydi.
"""
import os
import logging
from dotenv import load_dotenv
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

import db
import ai

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
        "Menga *ovozli xabar* yuboring yoki yozing, men o'zim tushunib yozib qo'yaman.\n\n"
        "Masalan:\n"
        "• «Bugun bozorga 85 ming sarfladim»\n"
        "• «Umarga 90 ming qarz berdim»\n"
        "• «Maosh 4 million tushdi»\n\n"
        "Buyruqlar:\n"
        "/balans — hisobingiz\n"
        "/qarzlar — kim kimga qarzdor\n"
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


# ---------- Yozuvni saqlash va javob ----------
async def saqlash_va_javob(update: Update, t: ai.Tranzaksiya):
    uid = update.effective_user.id

    if not t.tushunildi or not t.tur or not t.summa:
        await update.message.reply_text(
            "Tushunolmadim 🤔 Iltimos qaytaring.\n"
            f"Eshitganim: «{t.transkript}»"
        )
        return

    entry_id = db.add_entry(
        uid, t.tur.value, t.summa,
        kim=t.kim, kategoriya=t.kategoriya, izoh=t.izoh, transkript=t.transkript,
    )

    belgi = TUR_BELGI.get(t.tur.value, t.tur.value)
    satr = [f"✅ Yozildi\n\n{belgi}: *{som(t.summa)} so'm*"]
    if t.kim:
        satr.append(f"👤 {t.kim}")
    if t.kategoriya:
        satr.append(f"🏷 {t.kategoriya}")
    if t.izoh:
        satr.append(f"📝 {t.izoh}")

    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"del:{entry_id}")
    ]])
    await update.message.reply_text("\n".join(satr), parse_mode="Markdown", reply_markup=kb)


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


async def on_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    entry_id = int(q.data.split(":")[1])
    if db.delete_entry(entry_id, q.from_user.id):
        await q.edit_message_text("🗑 Bekor qilindi.")
    else:
        await q.edit_message_text("Bu yozuv topilmadi.")


def main():
    token = os.environ["TELEGRAM_TOKEN"]
    db.init_db()
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("balans", balans_cmd))
    app.add_handler(CommandHandler("qarzlar", qarzlar_cmd))
    app.add_handler(CommandHandler("royxat", royxat_cmd))
    app.add_handler(CallbackQueryHandler(on_undo, pattern=r"^del:"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    log.info("Bot ishga tushdi.")
    app.run_polling()


if __name__ == "__main__":
    main()
