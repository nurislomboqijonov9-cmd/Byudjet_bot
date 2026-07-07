"""Ijara hisobi — Telegram bot."""
import os
import asyncio
import logging
from dotenv import load_dotenv
from aiohttp import web as aioweb
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

import db
import ai
import logic
from miniapp import make_web_app

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arenda")

_allowed = os.getenv("ALLOWED_USER_IDS", "").replace(" ", "")
ALLOWED = {int(x) for x in _allowed.split(",") if x} if _allowed else None
APP_VERSION = "6"


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


def webapp_url():
    if os.getenv("WEBAPP_URL"):
        base = os.environ["WEBAPP_URL"].rstrip("/")
    else:
        dom = os.getenv("RAILWAY_PUBLIC_DOMAIN")
        base = f"https://{dom}" if dom else None
    if not base:
        return None
    return f"{base}{'&' if '?' in base else '?'}v={APP_VERSION}"


# ---------- Javob formatlari ----------
def fmt(res):
    if not res.get("ok"):
        return res.get("xato", "Xatolik"), None
    if res["amal"] == "chiqish":
        text = (f"✅ *{res['mijoz']}* — {res['raqam']}-partiya ochildi\n\n"
                f"📦 {son(res['miqdor'])} ta {res['mahsulot']}\n"
                f"💵 kuniga {som(res['kunlik_narx'])} so'm\n📅 {res['sana']}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delp:{res['partiya_id']}")]])
        return text, kb
    else:
        ortdi = "\n(qolgani shuncha edi, shuncha yozildi)" if res.get("ortdi") else ""
        text = (f"✅ *{res['mijoz']}* — {res['partiya_raqam']}-partiya\n\n"
                f"📥 {son(res['qty'])} ta {res['mahsulot']} qaytdi{ortdi}\n"
                f"📦 Qolgan: {son(res['qolgan'])} ta\n"
                f"🧮 Shu partiya: {som(res['partiya_narx'])} so'm\n"
                f"💰 {res['mijoz']} jami: *{som(res['jami'])} so'm*")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delr:{res['return_id']}")]])
        return text, kb


def _pending_dict(t):
    return {"amal": t.amal.value if hasattr(t.amal, "value") else t.amal,
            "mahsulot": t.mahsulot, "miqdor": t.miqdor, "kunlik_narx": t.kunlik_narx,
            "partiya": t.partiya, "hammasi": t.hammasi, "sana": t.sana}


class _T:
    def __init__(self, d):
        self.__dict__.update(d)


def _disamb_kb(matches):
    rows = [[InlineKeyboardButton(f"{m['ism']} · {m['telefon'] or 'raqamsiz'}", callback_data=f"pick:{m['id']}")]
            for m in matches]
    return InlineKeyboardMarkup(rows)


# ---------- Amalni yo'naltirish ----------
async def bajar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, t):
    if not t.tushunildi or not t.amal or not t.mijoz:
        await update.message.reply_text(f"Tushunolmadim 🤔 Qaytaring.\nEshitganim: «{t.transkript}»")
        return

    amal = t.amal.value if hasattr(t.amal, "value") else t.amal
    matches = db.mijozlar_by_name(t.mijoz)
    tel = db.clean_phone(getattr(t, "telefon", None))

    # Telefon berilgan bo'lsa — shu bilan aniqlaymiz
    if tel:
        byphone = [m for m in matches if m["telefon"] == tel]
        if byphone:
            matches = byphone

    mijoz_id = None
    if amal == "chiqish":
        if tel and not any(m["telefon"] == tel for m in matches):
            mijoz_id = db.add_mijoz(t.mijoz, tel)
        elif len(matches) == 0:
            mijoz_id = db.add_mijoz(t.mijoz, tel)
        elif len(matches) == 1:
            mijoz_id = matches[0]["id"]
    else:  # qaytarish
        if len(matches) == 0:
            await update.message.reply_text(f"«{t.mijoz}» topilmadi.")
            return
        elif len(matches) == 1:
            mijoz_id = matches[0]["id"]

    if mijoz_id is None:
        # Bir nechta bir xil ismli mijoz — so'raymiz
        ctx.user_data["pending"] = _pending_dict(t)
        await update.message.reply_text(
            f"«{t.mijoz}» ismli bir nechta mijoz bor. Qaysi biri? 👇",
            reply_markup=_disamb_kb(matches),
        )
        return

    res = logic.apply(mijoz_id, t)
    text, kb = fmt(res)
    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=kb)


# ---------- Komandalar ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    url = webapp_url()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Hisobni ochish", web_app=WebAppInfo(url=url))]]) if url else None
    await update.message.reply_text(
        "Salom! Men ijara hisobi botiman. 🏗\n\n"
        "*Ovoz* yoki *matn* yuboring:\n\n"
        "📤 «Abbosga 100 ta lesa chiqdi, kuniga 2000 so'm»\n"
        "📥 «Abbos 1-partiyadan 30 ta qaytardi»\n\n"
        "Bir xil ismli mijoz bo'lsa — «qaysi biri?» deb so'rayman.\n\n"
        "/mijozlar — barcha qarzlar\n/app — hisobni ochish",
        parse_mode="Markdown", reply_markup=kb,
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
        tel = f" · {m['telefon']}" if m["telefon"] else ""
        lines.append(f"👤 *{m['mijoz']}*{tel}\n   {som(m['jami'])} so'm ({son(m['jami_qolgan'])} dona)")
    lines.append(f"\n💰 *Umumiy:* {som(sum(m['jami'] for m in ml))} so'm")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def app_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    url = webapp_url()
    if not url:
        await update.message.reply_text("Mini App manzili yo'q. Railway'da domen (Networking → Generate Domain) qo'shing.")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Hisobni ochish", web_app=WebAppInfo(url=url))]])
    await update.message.reply_text("Ijara hisobini ochish 👇", reply_markup=kb)


# ---------- Xabarlar ----------
async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    msg = await update.message.reply_text("🎧 Tinglayapman…")
    try:
        f = await ctx.bot.get_file(update.message.voice.file_id)
        audio = bytes(await f.download_as_bytearray())
        t = ai.from_audio(audio, mime_type="audio/ogg")
        await msg.delete()
        await bajar(update, ctx, t)
    except Exception:
        log.exception("voice xatolik")
        await msg.edit_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    try:
        t = ai.from_text(update.message.text)
        await bajar(update, ctx, t)
    except Exception:
        log.exception("text xatolik")
        await update.message.reply_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")


async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith("pick:"):
        mijoz_id = int(data.split(":")[1])
        pending = ctx.user_data.pop("pending", None)
        if not pending:
            await q.edit_message_text("Amal eskirdi. Qaytadan yuboring.")
            return
        res = logic.apply(mijoz_id, _T(pending))
        text, kb = fmt(res)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    elif data.startswith("delp:"):
        db.delete_partiya(int(data.split(":")[1]))
        await q.edit_message_text("🗑 Partiya bekor qilindi.")
    elif data.startswith("delr:"):
        db.delete_return(int(data.split(":")[1]))
        await q.edit_message_text("🗑 Qaytarish bekor qilindi.")


# ---------- Ishga tushirish ----------
async def run():
    token = os.environ["TELEGRAM_TOKEN"]
    db.init_db()
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mijozlar", mijozlar_cmd))
    app.add_handler(CommandHandler("app", app_cmd))
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^(pick|delp|delr):"))
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
            await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp(text="📊 Hisob", web_app=WebAppInfo(url=url)))
            log.info("Mini App ulandi: %s", url)
        except Exception:
            log.exception("menyu tugmasi xatolik")

    await app.updater.start_polling()
    log.info("Ijara boti + Mini App ishga tushdi (port %s).", port)
    await asyncio.Event().wait()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
