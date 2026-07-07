"""Ijara (arenda) hisobi — Telegram bot.

Ovoz yoki matn yuborasiz -> bot tushunib yozib qo'yadi.
  Chiqish:   "Abbosga 100 ta lesa chiqdi, kuniga 2000 so'm"
  Qaytarish: "Abbos 1-partiyadan 30 ta qaytardi"
"""
import os
import asyncio
import logging
from datetime import date
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
from miniapp import make_web_app

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arenda")

_allowed = os.getenv("ALLOWED_USER_IDS", "").replace(" ", "")
ALLOWED = {int(x) for x in _allowed.split(",") if x} if _allowed else None


def som(n):
    return f"{round(n):,}".replace(",", " ")


def son(n):
    n = float(n)
    return str(int(n)) if n == int(n) else str(n)


def ruxsat(uid):
    return ALLOWED is None or uid in ALLOWED


async def guard(update: Update):
    if not ruxsat(update.effective_user.id):
        await update.message.reply_text("Kechirasiz, bu korxona boti.")
        return False
    return True


# ---------- Komandalar ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    await update.message.reply_text(
        "Salom! Men ijara hisobi botiman. 🏗\n\n"
        "Menga *ovoz* yoki *matn* yuboring:\n\n"
        "📤 *Chiqish:*\n"
        "«Abbosga 100 ta lesa chiqdi, kuniga 2000 so'm»\n\n"
        "📥 *Qaytarish:*\n"
        "«Abbos 1-partiyadan 30 ta qaytardi»\n"
        "«Karim 2-partiyadan hammasini qaytardi»\n\n"
        "Hisob: chiqgan kun ham, qaytgan kun ham hisoblanmaydi.\n\n"
        "Buyruqlar:\n/mijozlar — barcha qarzlar\n"
        "📊 Batafsil ko'rish uchun pastdagi *Mini App* tugmasi.",
        parse_mode="Markdown",
    )


async def mijozlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    ml = db.mijozlar()
    if not ml:
        await update.message.reply_text("Hozircha mijoz yo'q.")
        return
    lines = ["🏗 *Mijozlar qarzi:*\n"]
    for m in ml:
        lines.append(f"👤 *{m['mijoz']}* — {som(m['jami'])} so'm  ({son(m['jami_qolgan'])} dona qolgan)")
    jami = sum(m["jami"] for m in ml)
    lines.append(f"\n💰 *Umumiy:* {som(jami)} so'm")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------- Amalni bajarish ----------
async def bajar(update: Update, t: ai.IjaraAmal):
    if not t.tushunildi or not t.amal or not t.mijoz:
        await update.message.reply_text(
            "Tushunolmadim 🤔 Qaytaring.\n"
            f"Eshitganim: «{t.transkript}»"
        )
        return

    sana = (t.sana or date.today().isoformat())[:10]

    # ----- CHIQISH -----
    if t.amal == ai.Amal.chiqish:
        if not (t.mahsulot and t.miqdor and t.kunlik_narx):
            await update.message.reply_text(
                "Chiqish uchun kerak: mijoz, mahsulot, soni va kunlik narx.\n"
                "Masalan: «Abbosga 100 ta lesa chiqdi, kuniga 2000 so'm»"
            )
            return
        pid, raqam = db.add_partiya(t.mijoz, t.mahsulot, t.miqdor, t.kunlik_narx, sana)
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delp:{pid}")]])
        await update.message.reply_text(
            f"✅ *{t.mijoz}* — {raqam}-partiya ochildi\n\n"
            f"📦 {son(t.miqdor)} ta {t.mahsulot}\n"
            f"💵 kuniga {som(t.kunlik_narx)} so'm (dona)\n"
            f"📅 {sana}",
            parse_mode="Markdown", reply_markup=kb,
        )
        return

    # ----- QAYTARISH -----
    partiyalar = db.partiyalar_of(t.mijoz)
    if not partiyalar:
        await update.message.reply_text(f"«{t.mijoz}» degan mijoz topilmadi.")
        return

    # Qaysi partiya?
    p = None
    if t.partiya:
        p = db.get_partiya(t.mijoz, t.partiya)
        if not p:
            await update.message.reply_text(f"{t.mijoz}da {t.partiya}-partiya yo'q.")
            return
    else:
        aktiv = [x for x in partiyalar if db.partiya_hisob(x)["qolgan"] > 0]
        if len(aktiv) == 1:
            p = aktiv[0]
        elif len(aktiv) == 0:
            await update.message.reply_text(f"{t.mijoz}da qaytariladigan mahsulot yo'q.")
            return
        else:
            ro = ", ".join(f"{x['partiya_raqam']}-{x['mahsulot']}" for x in aktiv)
            await update.message.reply_text(
                f"{t.mijoz}da bir nechta partiya bor: {ro}\nQaysi partiya? Raqamini ayting."
            )
            return

    h = db.partiya_hisob(p)
    qolgan = h["qolgan"]
    if qolgan <= 0:
        await update.message.reply_text(f"{t.mijoz} {p['partiya_raqam']}-partiya allaqachon to'liq qaytarilgan.")
        return

    qty = qolgan if t.hammasi else t.miqdor
    if not qty:
        await update.message.reply_text("Nechta qaytarganini ayting.")
        return
    ortdi = ""
    if qty > qolgan:
        qty = qolgan
        ortdi = f"\n(qolgani {son(qolgan)} ta edi, shuncha yozildi)"

    rid = db.add_return(p["id"], qty, sana)
    h2 = db.partiya_hisob(p)
    d = db.mijoz_detail(t.mijoz)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delr:{rid}")]])
    await update.message.reply_text(
        f"✅ *{t.mijoz}* — {p['partiya_raqam']}-partiya\n\n"
        f"📥 {son(qty)} ta {p['mahsulot']} qaytdi{ortdi}\n"
        f"📦 Qolgan: {son(h2['qolgan'])} ta\n"
        f"🧮 Shu partiya hisobi: {som(h2['narx'])} so'm\n"
        f"💰 {t.mijoz} jami qarzi: *{som(d['jami'])} so'm*",
        parse_mode="Markdown", reply_markup=kb,
    )


# ---------- Xabar turlari ----------
async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    msg = await update.message.reply_text("🎧 Tinglayapman…")
    try:
        f = await ctx.bot.get_file(update.message.voice.file_id)
        audio = bytes(await f.download_as_bytearray())
        t = ai.from_audio(audio, mime_type="audio/ogg")
        await msg.delete()
        await bajar(update, t)
    except Exception:
        log.exception("voice xatolik")
        await msg.edit_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    try:
        t = ai.from_text(update.message.text)
        await bajar(update, t)
    except Exception:
        log.exception("text xatolik")
        await update.message.reply_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")


async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith("delp:"):
        db.delete_partiya(int(data.split(":")[1]))
        await q.edit_message_text("🗑 Partiya bekor qilindi.")
    elif data.startswith("delr:"):
        db.delete_return(int(data.split(":")[1]))
        await q.edit_message_text("🗑 Qaytarish bekor qilindi.")


# ---------- Ishga tushirish ----------
def webapp_url():
    if os.getenv("WEBAPP_URL"):
        return os.environ["WEBAPP_URL"].rstrip("/")
    dom = os.getenv("RAILWAY_PUBLIC_DOMAIN")
    return f"https://{dom}" if dom else None


async def run():
    token = os.environ["TELEGRAM_TOKEN"]
    db.init_db()
    app = ApplicationBuilder().token(token).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mijozlar", mijozlar_cmd))
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^del[pr]:"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    port = int(os.getenv("PORT", "8080"))
    runner = aioweb.AppRunner(make_web_app(token, ALLOWED))
    await runner.setup()
    site = aioweb.TCPSite(runner, "0.0.0.0", port)

    await app.initialize()
    await app.start()
    await site.start()

    url = webapp_url()
    if url:
        try:
            await app.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text="📊 Hisob", web_app=WebAppInfo(url=url))
            )
            log.info("Mini App ulandi: %s", url)
        except Exception:
            log.exception("menyu tugmasi xatolik")
    else:
        log.warning("WEBAPP_URL yo'q — Mini App tugmasi qo'yilmadi")

    await app.updater.start_polling()
    log.info("Ijara boti + Mini App ishga tushdi (port %s).", port)
    await asyncio.Event().wait()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
