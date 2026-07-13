"""Ijara hisobi — Telegram bot."""
import os
import re
import asyncio
import logging
from dotenv import load_dotenv
from aiohttp import web as aioweb
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo, InputFile,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

import db
import ai
import logic
import excel
from miniapp import make_web_app

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arenda")

_allowed = os.getenv("ALLOWED_USER_IDS", "").replace(" ", "")
ALLOWED = {int(x) for x in _allowed.split(",") if x} if _allowed else None
APP_VERSION = "22"


def som(n):
    return f"{round(n):,}".replace(",", " ")


def son(n):
    n = float(n)
    return str(int(n)) if n == int(n) else str(n)


def _malumot_text(d):
    lines = [f"👤 *{d['mijoz']}*"]
    if d.get("telefon"):
        lines.append(f"📞 {d['telefon']}")
    if d.get("adres"):
        lines.append(f"📍 {d['adres']}")
    ps = d.get("partiyalar") or []
    if ps:
        lines.append("\n📦 *Olgan mahsulotlar:*")
        for p in ps:
            holat = f"qolgan {son(p['qolgan'])}" if p["qolgan"] > 0 else "to'liq qaytgan ✓"
            lines.append(f"{p['partiya_raqam']}) {son(p['miqdor'])} ta {p['mahsulot']} · {holat} · kuniga {som(p['kunlik_narx'])}")
    lines.append(f"\n🧮 Hisoblangan: {som(d['hisoblangan'])} so'm")
    if d.get("yolkira"):
        lines.append(f"🚚 Yo'lkira: {som(d['yolkira'])} so'm")
    if d.get("remont"):
        lines.append(f"🔧 Remont: {som(d['remont'])} so'm")
    if d.get("tolangan"):
        lines.append(f"💵 To'langan: {som(d['tolangan'])} so'm")
    qq = d["qolgan_qarz"]
    if qq >= 0:
        lines.append(f"💰 *Qolgan qarz: {som(qq)} so'm*")
    else:
        lines.append(f"💰 *{som(-qq)} so'm — mijozning haqi bor*")
    return "\n".join(lines)


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
    if res["amal"] == "malumot":
        return _malumot_text(res["detail"]), None
    if res["amal"] == "eslatma":
        text = (f"✅ Eslatma qo'shildi\n\n👤 *{res['mijoz']}*\n📝 «{res['izoh']}»\n"
                f"📅 Va'da: {res['vada_sana']}\n⏰ O'sha kuni 11:00 da eslataman.")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"dele:{res['eslatma_id']}")]])
        return text, kb
    if res["amal"] == "chiqish":
        text = (f"✅ *{res['mijoz']}* — {res['raqam']}-partiya ochildi\n\n"
                f"📦 {son(res['miqdor'])} ta {res['mahsulot']}\n"
                f"💵 kuniga {som(res['kunlik_narx'])} so'm\n📅 {res['sana']}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delp:{res['partiya_id']}")]])
        return text, kb
    if res["amal"] == "tolov":
        izoh = f" ({res['izoh']})" if res.get("izoh") else ""
        qq = res["qolgan_qarz"]
        holat = f"💰 Qolgan qarz: {som(qq)} so'm" if qq >= 0 else f"💰 {som(-qq)} so'm — mijozning haqi bor (ortiqcha to'ladi)"
        text = (f"✅ *{res['mijoz']}* — to'lov qabul qilindi\n\n"
                f"💵 {som(res['summa'])} so'm{izoh}\n{holat}")
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delt:{res['tolov_id']}")]])
        return text, kb
    # qaytarish
    if res.get("aggregate"):
        qq = res["qolgan_qarz"]
        holat = f"💰 Qolgan qarz: {som(qq)} so'm" if qq >= 0 else f"💰 {som(-qq)} so'm — haqi bor"
        kam = "\n(shuncha ochiq edi, shuncha yozildi)" if res.get("kam") else ""
        satrlar = "\n".join(f"  {x['partiya_raqam']}-partiya: {son(x['qty'])} ta" for x in res["taqsim"])
        text = (f"✅ *{res['mijoz']}* — {son(res['qty'])} ta {res['mahsulot']} qaytdi{kam}\n"
                f"{satrlar}\n{holat}")
        ids = ",".join(str(i) for i in res["return_ids"])
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delr:{ids}")]]) if len(ids) <= 55 else None
        return text, kb
    ortdi = "\n(qolgani shuncha edi, shuncha yozildi)" if res.get("ortdi") else ""
    qq = res["qolgan_qarz"]
    holat = f"💰 Qolgan qarz: {som(qq)} so'm" if qq >= 0 else f"💰 {som(-qq)} so'm — haqi bor"
    text = (f"✅ *{res['mijoz']}* — {res['partiya_raqam']}-partiya\n\n"
            f"📥 {son(res['qty'])} ta {res['mahsulot']} qaytdi{ortdi}\n"
            f"📦 Qolgan: {son(res['qolgan'])} ta\n"
            f"🧮 Shu partiya: {som(res['partiya_narx'])} so'm\n{holat}")
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Bekor qilish", callback_data=f"delr:{res['return_id']}")]])
    return text, kb


def _pending_dict(t):
    return {"amal": t.amal.value if hasattr(t.amal, "value") else t.amal,
            "mijoz": t.mijoz, "telefon": getattr(t, "telefon", None),
            "mahsulot": t.mahsulot, "miqdor": t.miqdor, "kunlik_narx": t.kunlik_narx,
            "partiya": t.partiya, "hammasi": t.hammasi, "sana": t.sana,
            "summa": getattr(t, "summa", None), "kun": getattr(t, "kun", None),
            "izoh": getattr(t, "izoh", None),
            "tushunildi": True, "transkript": getattr(t, "transkript", "") or ""}


class _T:
    def __init__(self, d):
        self.__dict__.update(d)


def _disamb_kb(matches, allow_new=False, ism=None):
    rows = [[InlineKeyboardButton(f"{m['ism']} · {m['telefon'] or 'raqamsiz'}", callback_data=f"pick:{m['id']}")]
            for m in matches]
    if allow_new and ism:
        rows.append([InlineKeyboardButton(f"➕ Yangi mijoz: {ism}", callback_data="picknew")])
    return InlineKeyboardMarkup(rows)


async def _finish(update: Update, mijoz_id, t):
    res = logic.apply(mijoz_id, t)
    text, kb = fmt(res)
    await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    if res.get("ok") and res.get("amal") == "malumot":
        try:
            bio = excel.mijoz_excel(res["detail"])
            nom = "".join(c for c in res["detail"]["mijoz"] if c.isalnum() or c in " _-").strip() or "mijoz"
            await update.effective_message.reply_document(document=InputFile(bio, filename=f"{nom}.xlsx"))
        except Exception:
            log.exception("excel yuborishda xatolik")


def _arrow_dir(s):
    s = s or ""
    if "⬆" in s or "🔼" in s or "☝" in s:
        return "chiqish"
    if "⬇" in s or "🔽" in s or "👇" in s:
        return "qaytarish"
    return None


def _only_arrows(s):
    return _arrow_dir(s) is not None and re.sub(r"[⬆⬇🔼🔽☝👇\uFE0F\s]", "", s or "") == ""


# ---------- Amalni yo'naltirish ----------
async def bajar(update: Update, ctx: ContextTypes.DEFAULT_TYPE, t):
    # ⬆️/⬇️ stiker orqali yo'nalish tanlangan bo'lsa — o'shani qo'llaymiz
    yon = ctx.user_data.pop("yonalish", None)
    if yon:
        try:
            t.amal = ai.Amal(yon)
            t.tushunildi = True
        except Exception:
            pass

    if not t.tushunildi or not t.amal or not t.mijoz:
        await update.effective_message.reply_text(f"Tushunolmadim 🤔 Qaytaring.\nEshitganim: «{t.transkript}»")
        return

    amal = t.amal.value if hasattr(t.amal, "value") else t.amal
    tel = db.clean_phone(getattr(t, "telefon", None))
    matches = db.mijozlar_by_name(t.mijoz)
    if tel:
        byphone = [m for m in matches if tel in db.phone_list(m["telefon"])]
        if byphone:
            matches = byphone

    # 1) Aniq mos kelish
    if len(matches) == 1:
        await _finish(update, matches[0]["id"], t)
        return
    if len(matches) > 1:
        ctx.user_data["pending"] = _pending_dict(t)
        await update.effective_message.reply_text(
            f"«{t.mijoz}» ismli bir nechta mijoz bor. Qaysi biri? 👇",
            reply_markup=_disamb_kb(matches),
        )
        return

    # 2) Telefon bilan yangi mijoz (chiqish)
    if tel and amal == "chiqish":
        await _finish(update, db.add_mijoz(t.mijoz, tel), t)
        return

    # 3) Imloviy o'xshash mijoz bormi? (Fathulla ~ Fatxulla)
    fuzzy = db.similar_mijozlar(t.mijoz)
    if fuzzy:
        ctx.user_data["pending"] = _pending_dict(t)
        await update.effective_message.reply_text(
            f"«{t.mijoz}» topilmadi, lekin o'xshash mijoz bor. O'shami yoki yangimi? 👇",
            reply_markup=_disamb_kb(fuzzy, allow_new=(amal == "chiqish"), ism=t.mijoz),
        )
        return

    # 4) O'xshashi ham yo'q
    if amal == "chiqish":
        await _finish(update, db.add_mijoz(t.mijoz, tel), t)
        return
    await update.effective_message.reply_text(f"«{t.mijoz}» topilmadi.")


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
        "/mijozlar — barcha qarzlar\n/kunlik — bugungi hisobot\n/xarajat — AI sarfi\n/app — hisobni ochish",
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
        lines.append(f"👤 *{m['mijoz']}*{tel}\n   {som(m['qolgan_qarz'])} so'm ({son(m['jami_qolgan'])} dona)")
    lines.append(f"\n💰 *Umumiy qarz:* {som(sum(m['qolgan_qarz'] for m in ml))} so'm")
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


async def kunlik_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    k = db.kunlik()
    sana = k["sana"].split("-")
    sana = f"{sana[2]}.{sana[1]}.{sana[0][2:]}" if len(sana) == 3 else k["sana"]
    lines = [f"📅 *Bugungi hisobot* ({sana})\n"]
    if k["chiqish"]:
        lines.append("📤 *Chiqqan:*")
        for c in k["chiqish"]:
            lines.append(f"• {c['mijoz']}: {son(c['miqdor'])} ta {c['mahsulot']} · kuniga {som(c['kunlik_narx'])} so'm")
    if k["qaytish"]:
        lines.append("\n📥 *Qaytgan:*")
        for c in k["qaytish"]:
            lines.append(f"• {c['mijoz']}: {son(c['miqdor'])} ta {c['mahsulot']}")
    if not k["chiqish"] and not k["qaytish"]:
        lines.append("Bugun harakat bo'lmadi.")
    else:
        tc = sum(c["miqdor"] for c in k["chiqish"])
        tq = sum(c["miqdor"] for c in k["qaytish"])
        lines.append(f"\n📊 Jami: chiqqan {son(tc)} dona · qaytgan {son(tq)} dona")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def xarajat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    s = db.oylik_sarf()
    in_price = float(os.getenv("GEMINI_IN_USD", "0.30"))
    out_price = float(os.getenv("GEMINI_OUT_USD", "2.50"))
    kurs = float(os.getenv("USD_UZS", "12650"))
    usd = s["in_tok"] / 1_000_000 * in_price + s["out_tok"] / 1_000_000 * out_price
    somm = usd * kurs
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
    lines = [
        f"📊 *Gemini (AI) sarfi* — {s['oy']}\n",
        f"So'rovlar: {s['req']} ta",
        f"Kirish tokenlar: {som(s['in_tok'])}",
        f"Chiqish tokenlar: {som(s['out_tok'])}",
        f"\n💵 Taxminiy narx: *${usd:.2f}* (~{som(somm)} so'm)",
        f"Model: {model}",
        "\nℹ️ Google AI Studio *bepul* rejimida bo'lsangiz — narx 0 (limit bor). "
        "Aniq to'lov: AI Studio / Cloud Billing dashboardida.\n"
        "🖥 Server (Railway) haqi alohida — Railway dashboardida.",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


# ---------- Xabarlar ----------
def _amal_str(a):
    x = getattr(a, "amal", None)
    return x.value if hasattr(x, "value") else x


def _tasdiq_matni(actions):
    lines = ["🎙 *Tushundim — tasdiqlaysizmi?*\n"]
    for a in actions:
        am = _amal_str(a)
        mij = a.mijoz or "?"
        if am == "chiqish":
            lines.append(f"📤 {mij}: {son(a.miqdor or 0)} ta {a.mahsulot or '?'} · kuniga {som(a.kunlik_narx or 0)} so'm")
        elif am == "qaytarish":
            lines.append(f"📥 {mij}: {son(a.miqdor or 0)} ta {a.mahsulot or ''} qaytdi")
        elif am == "tolov":
            lines.append(f"💵 {mij}: to'lov {som(a.summa or 0)} so'm")
        else:
            lines.append(f"• {mij}: {am}")
    return "\n".join(lines)


_TASDIQ_KB = InlineKeyboardMarkup([[
    InlineKeyboardButton("✅ Tasdiqlash", callback_data="tasdiq:ok"),
    InlineKeyboardButton("❌ Bekor", callback_data="tasdiq:no"),
]])


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    msg = await update.message.reply_text("🎧 Tinglayapman…")
    try:
        f = await ctx.bot.get_file(update.message.voice.file_id)
        audio = bytes(await f.download_as_bytearray())
        actions = ai.from_audio(audio, mime_type="audio/ogg")
        await msg.delete()
    except Exception:
        log.exception("voice xatolik")
        await msg.edit_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return
    actions = [a for a in actions if a.tushunildi and a.amal]
    if not actions:
        await update.message.reply_text("Tushunolmadim 🤔 Qaytaring.")
        return
    # Mol chiqishi bo'lsa — avval tasdiqlatamiz
    if any(_amal_str(a) == "chiqish" for a in actions):
        ctx.user_data["tasdiq"] = [_pending_dict(a) for a in actions]
        await update.message.reply_text(_tasdiq_matni(actions), parse_mode="Markdown", reply_markup=_TASDIQ_KB)
    else:
        for a in actions:
            await bajar(update, ctx, a)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    txt = update.message.text or ""
    if _only_arrows(txt):
        return await _set_yonalish(update, ctx, _arrow_dir(txt))
    try:
        actions = ai.from_text(txt)
    except Exception:
        log.exception("text xatolik")
        await update.message.reply_text("Xatolik yuz berdi. Qaytadan urinib ko'ring.")
        return
    actions = [a for a in actions if a.tushunildi and a.amal] or actions[:1]
    if not actions:
        await update.message.reply_text("Tushunolmadim 🤔 Qaytaring.")
        return
    for a in actions:
        await bajar(update, ctx, a)


async def _set_yonalish(update: Update, ctx: ContextTypes.DEFAULT_TYPE, yon):
    if yon == "chiqish":
        ctx.user_data["yonalish"] = "chiqish"
        await update.message.reply_text(
            "📤 *Chiqish* rejimi. Endi mijoz va tafsilotlarni yozing/ayting.\n"
            "Masalan: «Abbos 50 ta lesa, kuniga 2000»", parse_mode="Markdown")
    elif yon == "qaytarish":
        ctx.user_data["yonalish"] = "qaytarish"
        await update.message.reply_text(
            "📥 *Qaytarish* rejimi. Endi mijoz va sonini yozing/ayting.\n"
            "Masalan: «Abbos 1-partiyadan 30 ta»", parse_mode="Markdown")
    else:
        await update.message.reply_text("⬆️ (chiqish) yoki ⬇️ (qaytarish) stikerini yuboring.")


async def handle_sticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update):
        return
    emoji = update.message.sticker.emoji if update.message.sticker else ""
    await _set_yonalish(update, ctx, _arrow_dir(emoji))


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
    elif data == "picknew":
        pending = ctx.user_data.pop("pending", None)
        if not pending:
            await q.edit_message_text("Amal eskirdi. Qaytadan yuboring.")
            return
        mijoz_id = db.add_mijoz(pending["mijoz"], pending.get("telefon"))
        res = logic.apply(mijoz_id, _T(pending))
        text, kb = fmt(res)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    elif data.startswith("delp:"):
        db.delete_partiya(int(data.split(":")[1]))
        await q.edit_message_text("🗑 Partiya bekor qilindi.")
    elif data.startswith("delr:"):
        for x in data.split(":", 1)[1].split(","):
            if x.strip().isdigit():
                db.delete_return(int(x))
        await q.edit_message_text("🗑 Qaytarish bekor qilindi.")
    elif data.startswith("delt:"):
        db.delete_tolov(int(data.split(":")[1]))
        await q.edit_message_text("🗑 To'lov bekor qilindi.")
    elif data.startswith("dele:"):
        db.delete_eslatma(int(data.split(":")[1]))
        await q.edit_message_text("🗑 Eslatma bekor qilindi.")
    elif data == "tasdiq:ok":
        pending = ctx.user_data.pop("tasdiq", None)
        if not pending:
            await q.edit_message_text("Amal eskirdi. Qaytadan yuboring.")
            return
        await q.edit_message_text("✅ Tasdiqlandi.")
        for pd in pending:
            await bajar(update, ctx, _T(pd))
    elif data == "tasdiq:no":
        ctx.user_data.pop("tasdiq", None)
        await q.edit_message_text("❌ Bekor qilindi.")


# ---------- Ishga tushirish ----------
async def _send_eslatma(app, r):
    d = db.mijoz_detail(r["mijoz_id"])
    if not d:
        return
    vada = str(r["vada_sana"])[:10]
    header = (f"⏰ *BUGUN TO'LOV VA'DASI!*\n\n"
              f"📝 «{r.get('izoh') or ''}»\n"
              f"📅 Va'da sanasi: {vada}\n\n")
    text = header + _malumot_text(d)
    for uid in (ALLOWED or []):
        try:
            await app.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
        except Exception:
            log.exception("eslatma yuborishda xatolik")


async def reminder_loop(app):
    while True:
        try:
            for r in db.due_eslatmalar():
                await _send_eslatma(app, r)
                db.mark_eslatma_sent(r["id"])
        except Exception:
            log.exception("eslatma tekshiruvi xatolik")
        await asyncio.sleep(60)


async def run():
    token = os.environ["TELEGRAM_TOKEN"]
    db.init_db()
    app = ApplicationBuilder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("mijozlar", mijozlar_cmd))
    app.add_handler(CommandHandler("app", app_cmd))
    app.add_handler(CommandHandler("kunlik", kunlik_cmd))
    app.add_handler(CommandHandler("xarajat", xarajat_cmd))
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^(pick:|picknew|delp:|delr:|delt:|dele:|tasdiq:)"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
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
    asyncio.create_task(reminder_loop(app))
    log.info("Ijara boti + Mini App ishga tushdi (port %s).", port)
    await asyncio.Event().wait()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
