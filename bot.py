"""Ijara hisobi — Telegram bot."""
import os
import re
import asyncio
import logging
from io import BytesIO
from datetime import date, timedelta
from dotenv import load_dotenv
from aiohttp import web as aioweb
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup, MenuButtonWebApp, WebAppInfo, InputFile,
    BotCommand, BotCommandScopeChat,
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)

import db
import ai
import logic
import excel
import sms
from miniapp import make_web_app

load_dotenv()
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("arenda")

APP_VERSION = "104"

# Pul yig'ish tekshiruvi: har kuni shu soatdan keyin (Toshkent), qayta eslatma orasidagi kunlar
YIGISH_SOAT = int(os.getenv("YIGISH_SOAT", "9"))
QAYTA_ESLAT_KUN = int(os.getenv("QAYTA_ESLAT_KUN", "7"))


def som(n):
    return f"{round(n):,}".replace(",", " ")


def son(n):
    n = float(n)
    return str(int(n)) if n == int(n) else str(n)


def _malumot_text(d):
    lines = [f"👤 *{d['mijoz']}*"]
    if d.get("kesim_sana"):
        k = d["kesim_sana"].split("-")
        lines.append(f"📆 *Hisobot: boshidan {k[2]}.{k[1]}.{k[0]} gacha*")
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
    return db.is_allowed(uid)


def _haydovchi_matni(uid):
    vz = db.haydovchi_vazifalari(uid)
    url = webapp_url() or ""
    base = url.split("?")[0].rstrip("/")
    if not vz:
        return "🚚 Salom! Sizda hozircha yangi zakaz yo'q.\n\nYangi zakaz kelsa shu yerga xabar beraman."
    lines = [f"🚚 *Sizdagi zakazlar* ({len(vz)} ta)\n"]
    for v in vz:
        tur = "📥 Olib kelish" if v["tur"] == "olib_kelish" else "📤 Yetkazish"
        holat = "🟡 yangi" if v["holat"] == "yangi" else "🔵 yo'lda"
        lines.append(f"{tur} · {holat}\n👤 {v['mijoz']}"
                     + (f"\n📍 {v['manzil']}" if v.get("manzil") else "")
                     + f"\n👉 {base}/v/{v['token']}\n")
    return "\n".join(lines)


async def _ruxsat_sorovi(ctx, user):
    """Yangi (ruxsatsiz) odam kirsa — egaga tugmali xabar yuboradi (bir marta)."""
    yangi = db.ruxsat_sorov_qosh(user.id, user.username, user.full_name)
    if not yangi:
        return
    uname = f"@{user.username}" if user.username else "—"
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Xodim", callback_data=f"ruxx:{user.id}"),
         InlineKeyboardButton("👑 Admin", callback_data=f"ruxa:{user.id}")],
        [InlineKeyboardButton("🚚 Haydovchi", callback_data=f"ruxh:{user.id}"),
         InlineKeyboardButton("❌ Rad", callback_data=f"ruxn:{user.id}")],
    ])
    try:
        await ctx.bot.send_message(
            db.OWNER_ID,
            f"👤 *Yangi kirish so'rovi*\n\nIsm: {user.full_name}\nUsername: {uname}\n🆔 `{user.id}`\n\nRuxsat berilsinmi?",
            parse_mode="Markdown", reply_markup=kb)
    except Exception:
        log.exception("ruxsat so'rovi yuborilmadi")


async def guard(update: Update, ctx=None):
    uid = update.effective_user.id
    if not db.is_allowed(uid) and db.haydovchi_bormi(uid):
        await update.message.reply_text(_haydovchi_matni(uid), parse_mode="Markdown",
                                        disable_web_page_preview=True)
        return False
    if not db.is_allowed(uid):
        if ctx is not None:
            await _ruxsat_sorovi(ctx, update.effective_user)
            await update.message.reply_text(
                "🔒 Ruxsat hali yo'q.\n\nSo'rovingiz adminga yuborildi — "
                "u tasdiqlaganidan keyin ishlata olasiz.")
        else:
            await update.message.reply_text(
                "Kechirasiz, bu korxona boti. 🔒\n\n"
                f"Sizning ID: `{uid}`\n"
                "Bu raqamni adminga yuboring — u sizni qo'shadi.",
                parse_mode="Markdown",
            )
        return False
    return True


async def admin_guard(update: Update, ctx=None):
    if not await guard(update, ctx):
        return False
    if not db.is_admin(update.effective_user.id):
        await update.message.reply_text("Bu buyruq faqat adminlar uchun.")
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
        tuz = res.get("tuzatildi")
        nota = f"\n✏️ _«{tuz[0]}» → «{tuz[1]}» deb to'g'rilandi_" if tuz else ""
        text = (f"✅ *{res['mijoz']}* — {res['raqam']}-partiya ochildi\n\n"
                f"📦 {son(res['miqdor'])} ta {res['mahsulot']}\n"
                f"💵 kuniga {som(res['kunlik_narx'])} so'm\n📅 {res['sana']}{nota}")
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
    rows = []
    for m in matches:
        qtxt = ""
        try:
            q = (db.mijoz_detail(m["id"]) or {}).get("qolgan_qarz", 0)
            if q:
                qtxt = f" · {som(q)} qarz"
        except Exception:
            qtxt = ""
        rows.append([InlineKeyboardButton(
            f"{m['ism']} · {m['telefon'] or 'raqamsiz'}{qtxt}", callback_data=f"pick:{m['id']}")])
    if allow_new and ism:
        rows.append([InlineKeyboardButton(f"➕ Yangi mijoz: {ism}", callback_data="picknew")])
    return InlineKeyboardMarkup(rows)


async def _send_excel(message, detail):
    try:
        bio = excel.mijoz_excel(detail, gacha=detail.get("kesim_sana"))
        nom = "".join(c for c in detail["mijoz"] if c.isalnum() or c in " _-").strip() or "mijoz"
        if detail.get("kesim_sana"):
            nom += "_" + detail["kesim_sana"]
        await message.reply_document(document=InputFile(bio, filename=f"{nom}.xlsx"))
    except Exception:
        log.exception("excel yuborishda xatolik")


def _mijoz_qidir(query):
    """Ism yoki tel bo'yicha mijoz qidirish (AI'siz)."""
    q = (query or "").strip().lower()
    if not q:
        return []
    raqam = "".join(ch for ch in q if ch.isdigit())
    res = []
    for m in db.mijozlar():
        nom = (m.get("mijoz") or "").lower()
        tel = "".join(ch for ch in (m.get("telefon") or "") if ch.isdigit())
        if (q in nom) or (raqam and len(raqam) >= 4 and raqam in tel):
            res.append({"id": m["id"], "ism": m.get("mijoz"), "telefon": m.get("telefon")})
    return res


async def _mijoz_excel_yubor(message, mid):
    d = db.mijoz_detail(mid)
    if not d:
        await message.reply_text("Topilmadi.")
        return
    await _send_excel(message, d)


def _qidir_kb(matches):
    rows = [[InlineKeyboardButton(f"{m['ism']} \u00b7 {m['telefon'] or 'raqamsiz'}", callback_data=f"xls:{m['id']}")]
            for m in matches[:20]]
    return InlineKeyboardMarkup(rows)


async def mijoz_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    query = " ".join(ctx.args or []).strip()
    if not query:
        await update.message.reply_text("Ism yoki tel yozing:\n/mijoz Ixtiyor")
        return
    matches = _mijoz_qidir(query)
    if not matches:
        await update.message.reply_text(f"'{query}' \u2014 topilmadi.")
        return
    if len(matches) == 1:
        await _mijoz_excel_yubor(update.message, matches[0]["id"])
        return
    await update.message.reply_text("Kimning hisobotini chiqaray?", reply_markup=_qidir_kb(matches))


def _audit_bot(uid, mijoz_id, res, manba="bot"):
    try:
        if not res or not res.get("ok"):
            return
        amal = res.get("amal")
        if amal in (None, "malumot", "savol"):
            return
        db.audit_qosh(uid, db.audit_ism(uid), amal, (res.get("xabar") or "")[:200], mijoz_id, manba)
    except Exception:
        pass


async def _finish(update: Update, mijoz_id, t):
    res = logic.apply(mijoz_id, t)
    _audit_bot(update.effective_user.id, mijoz_id, res)
    text, kb = fmt(res)
    await update.effective_message.reply_text(text, parse_mode="Markdown", reply_markup=kb)
    if res.get("ok") and res.get("amal") == "malumot":
        await _send_excel(update.effective_message, res["detail"])


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
        _amal0 = getattr(t.amal, "value", t.amal) if t.amal else None
        if _amal0 == "savol" or (not t.mijoz and _amal0 == "savol"):
            savol = (getattr(t, "transkript", "") or "").strip()
            if savol and db.is_admin(update.effective_user.id):
                return await _agent_reply(update, ctx, savol)
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
    # Mijoz deb o'ylagan, lekin topilmadi — bu tahliliy savol bo'lishi mumkin: agentga yo'naltiramiz
    if amal == "malumot" and db.is_admin(update.effective_user.id):
        savol = (getattr(t, "transkript", "") or "").strip()
        if savol:
            return await _agent_reply(update, ctx, savol)
    await update.effective_message.reply_text(f"«{t.mijoz}» topilmadi.")


# ---------- Komandalar ----------
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    uid = update.effective_user.id
    url = webapp_url()
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Hisobni ochish", web_app=WebAppInfo(url=url))]]) if url else None
    admin_qatri = "\n/xodimlar — xodimlar · /limit — yig'ish chegarasi" if db.is_admin(uid) else ""
    await update.message.reply_text(
        "Salom! Men ijara hisobi botiman. 🏗\n\n"
        "*Ovoz* yoki *matn* yuboring:\n\n"
        "📤 «Abbosga 100 ta lesa chiqdi, kuniga 2000 so'm»\n"
        "📥 «Abbos 1-partiyadan 30 ta qaytardi»\n\n"
        "Bir xil ismli mijoz bo'lsa — «qaysi biri?» deb so'rayman.\n\n"
        "/mijozlar — barcha qarzlar\n/qarzdorlar — pul yig'ish ro'yxati (Excel)\n"
        "/hisobot — umumiy Excel hisobot\n"
        "/kunlik — bugungi hisobot\n/xarajat — AI sarfi\n/app — hisobni ochish"
        f"{admin_qatri}\n\n"
        f"🆔 Sizning ID: `{uid}`",
        parse_mode="Markdown", reply_markup=kb,
    )


async def mijozlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
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
    if not await guard(update, ctx):
        return
    url = webapp_url()
    if not url:
        await update.message.reply_text("Mini App manzili yo'q. Railway'da domen (Networking → Generate Domain) qo'shing.")
        return
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("📊 Hisobni ochish", web_app=WebAppInfo(url=url))]])
    base = url.split("?")[0]
    await update.message.reply_text(
        "Ijara hisobini ochish 👇\n\n"
        f"🌐 Brauzer / ilova uchun havola:\n`{base}`\n\n_Batafsil:_ /ilova",
        parse_mode="Markdown", reply_markup=kb, disable_web_page_preview=True)


async def ilova_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    url = webapp_url()
    if not url:
        await update.message.reply_text("Domen yo'q. Railway'da Networking → Generate Domain qiling.")
        return
    base = url.split("?")[0]
    await update.message.reply_text(
        "🔨 *TEMIRCHI — ilova*\n\n"
        "Telegramsiz, alohida dastur bo'lib ishlaydi. Havola:\n\n"
        f"`{base}`\n\n"
        "👆 _bosib nusxa oling_\n\n"
        "📱 *Telefonda:*\n"
        "1. Havolani *Chrome*da oching (Telegram ichida emas)\n"
        "2. Login va parolni kiriting\n"
        "3. Menyu ⋮ → «Ilovani o'rnatish» / «Ekranga qo'shish»\n\n"
        "💻 *Kompyuterda:*\n"
        "1. Chrome yoki Edge'da havolani oching\n"
        "2. Login va parolni kiriting\n"
        "3. Manzil qatorining o'ng chetidagi ⊕ «O'rnatish»ni bosing\n\n"
        "🔑 Login/parol: adminda (`/parol` buyrug'i)",
        parse_mode="Markdown", disable_web_page_preview=True)


async def kunlik_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
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
    if not await guard(update, ctx):
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


# ---------- Qarzdorlar / chegara ----------
async def hisobot_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    ml = db.mijozlar()
    if not ml:
        await update.message.reply_text("Hozircha mijoz yo'q.")
        return
    jami_qarz = sum(m["qolgan_qarz"] for m in ml)
    try:
        cols = [x["nom"] for x in db.umumiy_ostatka()]   # ustunlar (mahsulot nomlari)
        for m in ml:
            m["ostatka_map"] = {it["nom"]: it["qolgan"] for it in db.mijoz_ostatka(m["id"])}
    except Exception:
        cols = []
    try:
        bio = excel.umumiy_excel(ml, sana=db.today_tk().isoformat(), mahsulotlar=cols)
        await update.message.reply_document(
            document=InputFile(bio, filename="umumiy_hisobot.xlsx"),
            caption=f"📊 Umumiy hisobot · {len(ml)} ta mijoz · umumiy qarz {som(jami_qarz)} so'm")
    except Exception:
        log.exception("umumiy excel xatolik")
        await update.message.reply_text("Excel yaratishda xatolik.")


async def qarzdorlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    lst = db.qarzdorlar()
    if not lst:
        await update.message.reply_text("Qarzdor yo'q. 👍")
        return
    limit_kun = db.get_limit_kun()
    over = [x for x in lst if x["over"]]
    lines = [f"📋 *Qarzdorlar* ({len(lst)} ta) · chegara {limit_kun} kunlik ijara\n"]
    for x in lst:
        bel = "🔴" if x["over"] else "🟡"
        kun = "rental yo'q" if x["kun"] is None else f"{round(x['kun'])} kunlik"
        tel = f" · {x['telefon']}" if x["telefon"] else ""
        lines.append(f"{bel} *{x['ism']}*{tel}\n   {som(x['qarz'])} so'm · {kun}")
    lines.append(f"\n🔴 Yig'ish kerak: {len(over)} ta · 🟡 Kuzatuvda: {len(lst) - len(over)} ta")
    lines.append(f"💰 Umumiy qarz: {som(sum(x['qarz'] for x in lst))} so'm")
    # Chegaradan oshganlarga SMS tugmasi
    kb = None
    if over:
        rows = [[InlineKeyboardButton(f"📩 SMS: {x['ism']}", callback_data=f"sms:{x['id']}")]
                for x in over[:20] if x["telefon"]]
        if rows:
            kb = InlineKeyboardMarkup(rows)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown", reply_markup=kb)
    try:
        bio = excel.qarzdorlar_excel(lst, limit_kun, sana=db.today_tk().isoformat())
        await update.message.reply_document(document=InputFile(bio, filename="qarzdorlar.xlsx"))
    except Exception:
        log.exception("qarzdorlar excel xatolik")


async def sms_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    if not ctx.args or not ctx.args[0].lstrip("-").isdigit():
        await update.message.reply_text("Format: `/sms <mijoz_id>`\n(mijoz ID sini /qarzdorlar dagi tugmadan olish osonroq)", parse_mode="Markdown")
        return
    await _sms_sorov(update.effective_message, int(ctx.args[0]))


async def _sms_sorov(message, mid):
    d = db.mijoz_detail(mid)
    if not d:
        await message.reply_text("Mijoz topilmadi.")
        return
    if not sms.is_configured():
        await message.reply_text("📵 SMS hali sozlanmagan. Railway'da ESKIZ_EMAIL, ESKIZ_PASSWORD, ESKIZ_FROM ni qo'shing.")
        return
    tel = sms.normalize_phone(d.get("telefon"))
    if not tel:
        await message.reply_text(f"«{d['mijoz']}» da to'g'ri telefon raqami yo'q.")
        return
    matn = sms.build_message(d)
    kb = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Ha, yubor", callback_data=f"smsok:{mid}"),
        InlineKeyboardButton("❌ Yo'q", callback_data="smsno"),
    ]])
    await message.reply_text(
        f"📩 *{d['mijoz']}* ({tel}) ga yuboriladi:\n\n«{matn}»\n\nYuborilsinmi?",
        parse_mode="Markdown", reply_markup=kb)


async def shablon_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    if not sms.is_configured():
        await update.message.reply_text("📵 SMS sozlanmagan (ESKIZ kalitlari yo'q).")
        return
    txt = sms.sample_template()
    await update.message.reply_text(f"📝 Shablon Eskiz moderatsiyasiga yuborilmoqda:\n\n«{txt}»")
    ok, info = await sms.submit_template(txt)
    if ok:
        await update.message.reply_text("✅ Shablon yuborildi. Endi Eskiz moderatsiyasini kuting (bir necha soat). Holatni /shablonlar bilan tekshiring.")
    else:
        await update.message.reply_text(
            f"❌ Yuborilmadi: {info}\n\n"
            "Agar shartnoma/kontrakt haqida bo'lsa — avval Eskizda shartnomani to'liq faollashtiring "
            "(korxona hisobidan 300 000 balans), keyin qayta yuboring.")


async def shablonlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    if not sms.is_configured():
        await update.message.reply_text("📵 SMS sozlanmagan.")
        return
    ok, data = await sms.list_templates()
    if not ok:
        await update.message.reply_text(f"❌ Olinmadi: {data}")
        return
    items = data if isinstance(data, list) else []
    if not items:
        await update.message.reply_text("Hozircha shablon yo'q. /shablon bilan yuboring.")
        return
    lines = ["📝 *Shablonlar holati:*\n"]
    for it in items:
        if not isinstance(it, dict):
            continue
        txt = str(it.get("template") or it.get("text") or "")[:55]
        st = str(it.get("status") or it.get("moderation") or it.get("original_status") or "—")
        lines.append(f"• «{txt}…»\n  holat: *{st}*")
    lines.append("\n_«active/tasdiqlangan» → tayyor · «moderation/на модерации» → kutilyapti_")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def yiguvchi_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    if ctx.args:
        a = ctx.args[0]
        if a.lower() in ("off", "yoq", "ochir", "o'chir", "0"):
            db.set_sozlama("yiguvchi_id", "")
            await update.message.reply_text("✅ Pul yig'uvchi o'chirildi. Kunlik hisobot barcha xodimlarga boradi.")
            return
        if a.lstrip("-").isdigit():
            db.set_sozlama("yiguvchi_id", a)
            await update.message.reply_text(
                f"✅ Pul yig'uvchi belgilandi: `{a}`\n\n"
                f"Har kuni {YIGISH_SOAT}:00 da shu odamga boradi:\n"
                "📋 Barcha qarzdorlar (Excel bilan)\n"
                "🔴 Chegaradan oshganlar — alohida (Excel bilan)\n\n"
                "⚠️ Bu odam botni bir marta ochsin (/start bossin) — shunda bot unga xabar yubora oladi.",
                parse_mode="Markdown")
            return
    cur = db.get_sozlama("yiguvchi_id")
    txt = f"🧾 Pul yig'uvchi: `{cur}`" if cur else "🧾 Pul yig'uvchi belgilanmagan (hisobot barcha xodimlarga boradi)."
    await update.message.reply_text(
        txt + "\n\nBelgilash: `/yiguvchi <id>` · O'chirish: `/yiguvchi off`", parse_mode="Markdown")


async def brovdan_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    gr = db.brov_list()
    if not gr:
        await update.message.reply_text("Brovdan olingan narsa yo'q. 👍")
        return
    lines = ["🔁 *Brovdan olinganlar*\n"]
    jami = 0.0
    for g in gr:
        if g["qolgan"] <= 0:
            continue
        jami += g["qolgan"]
        lines.append(f"👤 *{g['kim']}* — {son(g['qolgan'])} ta qaytarilmagan")
        for b in g["items"]:
            if b["qolgan"] > 0:
                lines.append(f"   • {b['mahsulot']}: {son(b['qolgan'])}/{son(b['miqdor'])} · {str(b['sana'])[:10]}")
    if jami <= 0:
        await update.message.reply_text("Hammasi qaytarilgan. ✅")
        return
    lines.append(f"\n📦 Jami qaytarilmagan: *{son(jami)} ta*")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
    try:
        bio = excel.brov_excel(gr, sana=db.today_tk().isoformat())
        await update.message.reply_document(document=InputFile(bio, filename="brovdan.xlsx"))
    except Exception:
        log.exception("brov excel xatolik")


async def nomlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    lst = db.partiya_nomlari()
    if not lst:
        await update.message.reply_text("Partiya yo'q.")
        return
    yaxshi = [x for x in lst if x["omborda_bor"]]
    yomon = [x for x in lst if not x["omborda_bor"]]
    lines = ["🔤 *Partiyalardagi tovar nomlari*\n"]
    if yomon:
        lines.append("❌ *Omborda topilmadi* (to'g'rilash kerak):")
        for x in yomon[:25]:
            lines.append(f"   • {x['nom']} — {x['soni']} ta partiya")
        lines.append("")
    if yaxshi:
        lines.append("✅ *Ombor bilan mos:*")
        for x in yaxshi[:25]:
            lines.append(f"   • {x['nom']} — {x['soni']} ta")
    lines.append("\n*To'g'rilash:* `/nom Fasadni lesa = lesa`\n*Keyin:* `/ombor_hisobla`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def nom_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    txt = " ".join(ctx.args or "")
    if "=" not in txt:
        await update.message.reply_text(
            "Format: `/nom eski nom = yangi nom`\n"
            "Masalan: `/nom Fasadni lesa = lesa`\n\n"
            "Barcha partiyalarda o'sha nom almashadi. Ro'yxat: /nomlar", parse_mode="Markdown")
        return
    eski, yangi = txt.split("=", 1)
    res = db.rename_mahsulot(eski.strip(), yangi.strip())
    if not res.get("ok"):
        await update.message.reply_text(f"❌ {res.get('xato')}")
        return
    await update.message.reply_text(
        f"✅ «{eski.strip()}» → «{yangi.strip()}»\n"
        f"{res['partiya']} ta partiyada o'zgartirildi.\n\n"
        "Endi `/ombor_hisobla` bilan omborni qayta sanang.", parse_mode="Markdown")


async def ostatka_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    try:
        db.ombor_recalc()   # avval to'g'rilaymiz (o'chirilganlar omborga qaytadi)
    except Exception:
        pass
    lst = db.ombor_list()
    j_omb = j_ij = 0
    satlar = []
    for x in lst:
        if (x["total"] or 0) == 0 and (x["out"] or 0) == 0:
            continue
        satlar.append(f"• {x['name']}: omborda *{som(x['omborda'])}* · ijarada {som(x['out'])} · jami {som(x['total'])}")
        j_omb += x["omborda"]; j_ij += x["out"]
    if not satlar:
        await update.message.reply_text("📦 Ombor bo'sh.")
        return
    bosh = "📦 *OMBOR — ostatka*\n_(avtomat to'g'rilandi)_\n\n"
    yakun = f"\n━━━━━━━━\nJami omborda: *{som(j_omb)}* · ijarada: *{som(j_ij)}*\n\n_Tuzatish:_ `/ostatka_tuzat <nom> <jami>`"
    # uzun bo'lsa bo'lib yuboramiz (Telegram 4096 limit)
    matn = bosh; birinchi = True
    for s in satlar:
        if len(matn) + len(s) > 3500:
            await update.message.reply_text(matn, parse_mode="Markdown")
            matn = ""
        matn += s + "\n"
    await update.message.reply_text(matn + yakun, parse_mode="Markdown")


async def ostatka_tuzat_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    args = ctx.args or []
    if len(args) < 2 or not args[-1].lstrip("-").isdigit():
        await update.message.reply_text(
            "✏️ *Ombor sonini qo'lda tuzatish*\n\n"
            "`/ostatka_tuzat <nom> <jami>`\n\n"
            "Masalan: `/ostatka_tuzat Lyulka 120`\n"
            "_(jami = umumiy nechta bor. Ijarada soni avtomat hisoblanadi.)_",
            parse_mode="Markdown")
        return
    jami = int(args[-1])
    nom = " ".join(args[:-1]).strip()
    pid = db.ombor_by_name(nom)
    if not pid:
        await update.message.reply_text(f"❌ «{nom}» ombordan topilmadi. `/ostatka` bilan nomlarni ko'ring.", parse_mode="Markdown")
        return
    db.ombor_set_total(pid, jami)
    try:
        db.ombor_recalc()
    except Exception:
        pass
    for x in db.ombor_list():
        if x["id"] == pid:
            await update.message.reply_text(
                f"✅ *{x['name']}* yangilandi:\nJami: *{som(x['total'])}* · ijarada: {som(x['out'])} · omborda: *{som(x['omborda'])}*",
                parse_mode="Markdown")
            return
    await update.message.reply_text("✅ Yangilandi.")


async def ombor_hisobla_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    await update.message.reply_text("⏳ Hisoblanyapti…")
    res = db.ombor_recalc()
    oz = res["ozgargan"]
    nomos = res["nomos"]
    lines = ["🔄 *Ombor qayta hisoblandi*\n"]
    if oz:
        lines.append("*O'zgarganlar (arendada):*")
        for x in oz[:30]:
            lines.append(f"   • {x['name']}: {som(x['eski'])} → *{som(x['yangi'])}* (omborda {som(x['omborda'])})")
    else:
        lines.append("Hammasi joyida edi, o'zgarish yo'q. ✅")
    if nomos:
        lines.append("\n⚠️ *Ombordan topilmadi* (arendada turibdi, lekin bunday tovar yo'q):")
        for nom, q in nomos[:20]:
            lines.append(f"   • {nom} — {som(q)} dona")
        lines.append("_Ularni `/nom eski = yangi` bilan to'g'rilang yoki omborga qo'shing._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def parol_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    a = ctx.args or []
    # Umumiy (hamma uchun bitta) login
    if a and a[0].lower() in ("umumiy", "bitta", "hammaga_bitta"):
        if len(a) < 3:
            u = db.get_xodim(db.UMUMIY_ID)
            hozir = f"\n\nHozirgi: login `{u['login']}` · rol {u['rol']}" if (u and u.get("login")) else "\n\n_Hali qo'yilmagan._"
            await update.message.reply_text(
                "🔑 *Umumiy login (hamma uchun bitta)*\n\n"
                "Qo'yish: `/parol umumiy <login> <parol>`\n"
                "Masalan: `/parol umumiy temirchi 2026`\n\n"
                "Admin huquqi bilan: `/parol umumiy temirchi 2026 admin`\n"
                "O'chirish: `/parol umumiy ochir`" + hozir, parse_mode="Markdown")
            return
        if a[1].lower() == "ochir":
            db.umumiy_ochir()
            await update.message.reply_text("🗑 Umumiy login o'chirildi.")
            return
        login, parol = a[1], a[2]
        rol = a[3] if len(a) > 3 else "xodim"
        res = db.set_umumiy_parol(login, parol, rol)
        if not res.get("ok"):
            await update.message.reply_text(f"❌ {res.get('xato')}")
            return
        url = webapp_url() or ""
        base = url.split("?")[0]
        await update.message.reply_text(
            f"✅ *Umumiy login tayyor*\n\n"
            f"🔑 Login: `{res['login']}`\n🔒 Parol: `{parol}`\n👤 Huquq: {res['rol']}\n\n"
            f"🌐 Havola:\n`{base}`\n\n"
            "_Hamma xodim shu login/parol bilan kiradi._\n"
            "Alohida loginlarni o'chirish: `/parol ochir hamma`",
            parse_mode="Markdown", disable_web_page_preview=True)
        return
    if len(a) == 2 and a[0].lower() == "ochir" and a[1].lower() in ("hamma", "hammasi", "all"):
        db.barcha_parollarni_ochir()
        await update.message.reply_text("🗑 Alohida loginlar o'chirildi (umumiy login qoldi).")
        return
    if len(a) == 1 and a[0].lower() in ("hammaga", "hamma", "all"):
        import random
        yaratildi, bor = [], []
        for x in db.all_xodimlar():
            if x.get("login"):
                bor.append(x)
                continue
            asos = "".join(ch for ch in (x.get("ism") or "").lower()
                           if ch.isalnum()) or f"id{x['id']}"
            login, n = asos, 1
            while any((y.get("login") or "") == login for y in db.all_xodimlar()):
                n += 1
                login = f"{asos}{n}"
            parol = str(random.randint(1000, 9999))
            r = db.set_parol(x["id"], login, parol)
            if r.get("ok"):
                yaratildi.append((x, login, parol))
        if not yaratildi:
            await update.message.reply_text("Hammasida login bor. Ro'yxat: `/parol royxat`", parse_mode="Markdown")
            return
        lines = ["🔑 *Login va parollar yaratildi*\n"]
        for x, login, parol in yaratildi:
            rol = "👑" if x["rol"] == "admin" else "👷"
            lines.append(f"{rol} *{x.get('ism') or x['id']}*\n   login: `{login}` · parol: `{parol}`")
        if bor:
            lines.append(f"\n_({len(bor)} tasida allaqachon bor edi)_")
        lines.append("\n📋 Har biriga o'z login/parolini yuboring.")
        lines.append("🔗 Havola: /ilova")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    if len(a) == 1 and a[0].lower() in ("royxat", "list", "kim"):
        lst = db.loginli_xodimlar()
        if not lst:
            await update.message.reply_text("Hali hech kimga login berilmagan.")
            return
        lines = ["🔑 *Ilovaga kira oladiganlar:*\n"]
        for x in lst:
            rol = "👑" if x["rol"] == "admin" else "👷"
            lines.append(f"{rol} *{x.get('ism') or x['id']}* — login: `{x['login']}`")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
        return
    if len(a) == 2 and a[0].lower() == "ochir" and a[1].lstrip("-").isdigit():
        db.parolni_ochir(int(a[1]))
        await update.message.reply_text("🗑 Login/parol o'chirildi.")
        return
    if len(a) < 3 or not a[0].lstrip("-").isdigit():
        await update.message.reply_text(
            "🔑 *Ilova uchun login/parol*\n\n"
            "Berish: `/parol <id> <login> <parol>`\n"
            "Masalan: `/parol 12345678 akmal 4477`\n\n"
            "Umumiy (bitta) login: `/parol umumiy <login> <parol>`\n"
            "Hammaga alohida: `/parol hammaga`\n"
            "Ro'yxat: `/parol royxat`\n"
            "O'chirish: `/parol ochir <id>`\n\n"
            "_Xodim avval /xodim_qosh bilan qo'shilgan bo'lsin._", parse_mode="Markdown")
        return
    uid, login, parol = int(a[0]), a[1], " ".join(a[2:])
    res = db.set_parol(uid, login, parol)
    if not res.get("ok"):
        await update.message.reply_text(f"❌ {res.get('xato')}")
        return
    url = webapp_url() or "(domen yo'q)"
    base = url.split("?")[0]
    await update.message.reply_text(
        f"✅ Login berildi\n\n👤 ID: `{uid}`\n🔑 Login: `{login}`\n🔒 Parol: `{parol}`\n\n"
        f"🌐 Ilova manzili:\n{base}\n\n"
        "_Telefonda brauzerda oching → menyudan «Ekranga qo'shish». "
        "Kompyuterda Chrome → manzil yonidagi «O'rnatish» tugmasi._", parse_mode="Markdown")


async def haydovchilar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    lst = db.haydovchilar()
    lines = [f"🚚 *Haydovchilar* ({len(lst)} ta)\n"] if lst else ["🚚 Hali haydovchi qo'shilmagan.\n"]
    for h in lst:
        lines.append(f"👤 *{h.get('ism') or h['id']}*"
                     + (f" · 📞 {h['telefon']}" if h.get("telefon") else "")
                     + f"\n   🆔 `{h['id']}`")
    lines.append("\n➕ `/haydovchi_qosh <id> <ism>`\n🗑 `/haydovchi_ochir <id>`\n\n"
                 "_Haydovchi botga /start bossin — shunda zakaz xabari boradi._")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def haydovchi_qosh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    uid, ism = _parse_id_ism(ctx.args)
    if not uid:
        await update.message.reply_text(
            "Format: `/haydovchi_qosh <id> <ism>`\nMasalan: `/haydovchi_qosh 12345678 Aziz`",
            parse_mode="Markdown")
        return
    db.haydovchi_qosh(uid, ism)
    await update.message.reply_text(
        f"✅ 🚚 Haydovchi qo'shildi: *{ism or uid}*\n🆔 `{uid}`\n\n"
        "_Haydovchi botga /start bossin — shunda yangi zakaz xabari boradi._", parse_mode="Markdown")


async def haydovchi_ochir_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    uid, _ = _parse_id_ism(ctx.args)
    if not uid:
        await update.message.reply_text("Format: `/haydovchi_ochir <id>`", parse_mode="Markdown")
        return
    db.haydovchi_ochir(uid)
    await update.message.reply_text("🗑 Haydovchi o'chirildi.")


async def tovarlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    txt = " ".join(ctx.args or "").strip()
    if txt:
        res = db.set_tovar_royxat(txt)
        await update.message.reply_text(f"✅ Tovarlar ro'yxati yangilandi ({res['soni']} ta).")
        return
    juft = db.tovar_juftlar()
    on = db.get_sozlama("tovar_tekshir") == "1"
    lines = [f"📋 *Tovarlar ro'yxati* ({len(juft)} ta)", f"_tekshiruv: {'yoqilgan ✅' if on else 'oʻchiq ⏸'}_\n"]
    for nom, bir in juft:
        lines.append(f"   • {nom} — _{bir}_")
    lines.append(f"\n📦 1 komplekt = {int(db.KOM_TA)} ta.")
    lines.append("«kom» yozilsa ombordan 1, «ta» yozilsa 0.5 ayriladi (kom tovarlarda).")
    lines.append("\n*O'zgartirish:* `/tovarlar Oyoq 2m kom, Rezba 1m ta` (vergul bilan)")
    lines.append("*Yoqish:* `/tekshir on`")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def tekshir_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    if ctx.args and ctx.args[0].lower() in ("on", "off", "ha", "yoq", "1", "0"):
        on = ctx.args[0].lower() in ("on", "ha", "1")
        db.set_sozlama("tovar_tekshir", "1" if on else "0")
        await update.message.reply_text(
            "✅ Tovar tekshiruvi *YOQILDI* — endi ijarada faqat ombordagi tovar nomlarini qabul qiladi, boshqasiga «to'g'ri yozing» deydi." if on
            else "⏸ Tovar tekshiruvi *o'chirildi* — istalgan nom bilan chiqarish mumkin.", parse_mode="Markdown")
        return
    cur = db.get_sozlama("tovar_tekshir") == "1"
    await update.message.reply_text(
        f"🔎 Tovar tekshiruvi: *{'yoqilgan' if cur else 'oʻchiq'}*\n\n"
        "Yoqilganda ijarada faqat ombordagi tovar nomlari qabul qilinadi. "
        "Avval Mini App'da ombor ro'yxatini to'g'rilab oling, keyin yoqing.\n\n"
        "Yoqish: `/tekshir on` · O'chirish: `/tekshir off`", parse_mode="Markdown")


async def limit_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    if ctx.args and ctx.args[0].lstrip("-").isdigit():
        n = int(ctx.args[0])
        if n < 1:
            await update.message.reply_text("Chegara kamida 1 kun bo'lsin.")
            return
        db.set_limit_kun(n)
        await update.message.reply_text(
            f"✅ Chegara o'zgartirildi: *{n} kunlik ijara*.\n"
            "Qarzi shu chegaradan oshgan mijozlar uchun pul yig'ish eslatmasi keladi.",
            parse_mode="Markdown")
        return
    cur = db.get_limit_kun()
    await update.message.reply_text(
        f"📏 Hozirgi chegara: *{cur} kunlik ijara*.\n\n"
        "Mijozning qarzi shuncha kunlik ijarasiga tenglashsa — «pul yig'ish kerak» deb belgilanadi. "
        "Katta mijozga chegara balandroq, kichigiga past — o'zi miqyosga moslashadi.\n\n"
        "O'zgartirish: `/limit 30`", parse_mode="Markdown")


# ---------- Xodimlarni boshqarish (faqat admin) ----------
def _parse_id_ism(args):
    """'12345 Akmal Aka' -> (12345, 'Akmal Aka'). ID bo'lmasa (None, None)."""
    if not args or not args[0].lstrip("-").isdigit():
        return None, None
    return int(args[0]), (" ".join(args[1:]).strip() or None)


async def xodimlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    xs = db.all_xodimlar()
    lines = ["👥 *Xodimlar:*\n"]
    for x in xs:
        rol = "👑 Admin" if x["rol"] == "admin" else "👷 Xodim"
        ega = " · ega" if x["id"] == db.OWNER_ID else ""
        ism = x.get("ism") or "—"
        lines.append(f"{rol}{ega} · {ism}\n   🆔 `{x['id']}`")
    lines.append(
        "\n*Boshqarish:*\n"
        "➕ `/xodim_qosh <id> <ism>`\n"
        "👑 `/admin_qosh <id> <ism>`\n"
        "🗑 `/ochir <id>`"
    )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def xodim_qosh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    uid, ism = _parse_id_ism(ctx.args)
    if not uid:
        await update.message.reply_text("Format: `/xodim_qosh <id> <ism>`\nMasalan: `/xodim_qosh 12345678 Akmal`", parse_mode="Markdown")
        return
    db.add_xodim(uid, ism, "xodim", update.effective_user.id)
    await update.message.reply_text(f"✅ 👷 Xodim qo'shildi: *{ism or uid}*\n🆔 `{uid}`", parse_mode="Markdown")


async def admin_qosh_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    uid, ism = _parse_id_ism(ctx.args)
    if not uid:
        await update.message.reply_text("Format: `/admin_qosh <id> <ism>`", parse_mode="Markdown")
        return
    db.add_xodim(uid, ism, "admin", update.effective_user.id)
    await update.message.reply_text(f"✅ 👑 Admin qo'shildi: *{ism or uid}*\n🆔 `{uid}`", parse_mode="Markdown")


async def jurnal_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    rows = db.audit_royxat(30)
    if not rows:
        await update.message.reply_text("Jurnal hozircha bo'sh.")
        return
    lines = ["📋 *Oxirgi o'zgarishlar:*\n"]
    for r in rows:
        vaqt = (r.get("vaqt") or "")[:16].replace("T", " ")
        manba = "📱" if r.get("manba") == "ilova" else "💬"
        kim = r.get("ism") or str(r.get("uid"))
        tf = r.get("tafsilot") or ""
        lines.append(f"{manba} {vaqt} · *{kim}*\n   {r.get('amal')}: {tf}")
    await update.message.reply_text("\n".join(lines)[:4000], parse_mode="Markdown")


async def sorovlar_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    ss = db.ruxsat_sorovlar()
    if not ss:
        await update.message.reply_text("Kutayotgan kirish so'rovi yo'q.")
        return
    for s in ss[:20]:
        uname = f"@{s['username']}" if s.get("username") else "—"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Xodim", callback_data=f"ruxx:{s['uid']}"),
             InlineKeyboardButton("👑 Admin", callback_data=f"ruxa:{s['uid']}")],
            [InlineKeyboardButton("🚚 Haydovchi", callback_data=f"ruxh:{s['uid']}"),
             InlineKeyboardButton("❌ Rad", callback_data=f"ruxn:{s['uid']}")],
        ])
        await update.message.reply_text(
            f"👤 {s.get('ism') or '—'}\nUsername: {uname}\n🆔 `{s['uid']}`",
            parse_mode="Markdown", reply_markup=kb)


async def ochir_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await admin_guard(update, ctx):
        return
    uid, _ = _parse_id_ism(ctx.args)
    if not uid:
        await update.message.reply_text("Format: `/ochir <id>`", parse_mode="Markdown")
        return
    if uid == db.OWNER_ID:
        await update.message.reply_text("Egani o'chirib bo'lmaydi. 🔒")
        return
    target = db.get_xodim(uid)
    if not target:
        await update.message.reply_text("Bunday xodim ro'yxatda yo'q.")
        return
    # Adminni faqat ega o'chira oladi
    if target["rol"] == "admin" and not db.is_owner(update.effective_user.id):
        await update.message.reply_text("Adminni faqat ega (birinchi admin) o'chira oladi.")
        return
    db.remove_xodim(uid)
    holat = "👑 Admin" if target["rol"] == "admin" else "👷 Xodim"
    await update.message.reply_text(f"🗑 {holat} o'chirildi: *{target.get('ism') or uid}*", parse_mode="Markdown")


# ---------- Xabarlar ----------
def _amal_str(a):
    x = getattr(a, "amal", None)
    return x.value if hasattr(x, "value") else x


def _tasdiq_matni(actions):
    lines = ["🎙 *Tushundim — tasdiqlaysizmi?*\n"]
    for i, a in enumerate(actions, 1):
        am = _amal_str(a)
        mij = a.mijoz or "?"
        if am == "chiqish":
            lines.append(f"{i}) 📤 {mij}: {son(a.miqdor or 0)} ta {a.mahsulot or '?'} · kuniga {som(a.kunlik_narx or 0)} so'm")
        elif am == "qaytarish":
            lines.append(f"{i}) 📥 {mij}: {son(a.miqdor or 0)} ta {a.mahsulot or ''} qaytdi")
        elif am == "tolov":
            lines.append(f"{i}) 💵 {mij}: to'lov {som(a.summa or 0)} so'm")
        else:
            lines.append(f"{i}) • {mij}: {am}")
    lines.append(f"\n_Jami: {len(actions)} ta_")
    return "\n".join(lines)


_TASDIQ_KB = InlineKeyboardMarkup([
    [InlineKeyboardButton("✅ Tasdiqlash", callback_data="tasdiq:ok")],
    [InlineKeyboardButton("✏️ Tahrirlash", callback_data="tasdiq:edit"),
     InlineKeyboardButton("❌ Bekor", callback_data="tasdiq:no")],
])


async def _show_tasdiq(update: Update, ctx: ContextTypes.DEFAULT_TYPE, actions):
    ctx.user_data["tasdiq"] = [_pending_dict(a) for a in actions]
    tr = ""
    for a in actions:
        if getattr(a, "transkript", None):
            tr = a.transkript
            break
    ctx.user_data["tasdiq_transkript"] = tr
    await update.effective_message.reply_text(_tasdiq_matni(actions), parse_mode="Markdown", reply_markup=_TASDIQ_KB)


async def handle_voice(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
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
    # Mol chiqishi yoki bir nechta amal bo'lsa — avval tasdiqlatamiz
    if any(_amal_str(a) == "chiqish" for a in actions) or len(actions) > 1:
        await _show_tasdiq(update, ctx, actions)
    else:
        for a in actions:
            await bajar(update, ctx, a)


async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    txt = update.message.text or ""
    if ctx.user_data.get("loc"):
        t = txt.strip()
        # Faqat qisqa, raqamsiz matn = mijoz ismi. Aks holda oddiy buyruq deb ishlaymiz.
        if t and len(t) <= 40 and not any(c.isdigit() for c in t) and len(t.split()) <= 4:
            if await _loc_biriktir(update, ctx, t):
                return
        else:
            ctx.user_data.pop("loc", None)
    if ctx.user_data.pop("await_edit", False):
        try:
            actions = await asyncio.to_thread(ai.from_text, txt)
        except Exception:
            log.exception("tahrir xatolik")
            await update.message.reply_text("Xatolik. Qaytadan urinib ko'ring.")
            return
        actions = [a for a in actions if a.tushunildi and a.amal]
        if not actions:
            await update.message.reply_text("Tushunolmadim 🤔 Qaytaring.")
            return
        return await _show_tasdiq(update, ctx, actions)
    if _only_arrows(txt):
        return await _set_yonalish(update, ctx, _arrow_dir(txt))
    # --- AI'dan OLDIN: oddiy ism/tel bo'lsa -> to'g'ridan hisobot (Gemini'siz, tez) ---
    _t = txt.strip()
    _raqam = "".join(c for c in _t if c.isdigit())
    _lookup = bool(_t) and (
        (not any(c.isdigit() for c in _t) and len(_t.split()) <= 4 and len(_t) <= 40)
        or (len(_raqam) >= 7 and len(_t) <= 20)
    )
    if _lookup:
        _mm = _mijoz_qidir(_t)
        if len(_mm) == 1:
            await _mijoz_excel_yubor(update.message, _mm[0]["id"])
            return
        if len(_mm) > 1:
            await update.message.reply_text("Kimning hisobotini chiqaray?", reply_markup=_qidir_kb(_mm))
            return
        # topilmasa -> AI'ga o'tadi
    try:
        actions = await asyncio.to_thread(ai.from_text, txt)
    except Exception:
        log.exception("text xatolik")
        matches = _mijoz_qidir(txt)
        if len(matches) == 1:
            await _mijoz_excel_yubor(update.message, matches[0]["id"])
            return
        if len(matches) > 1:
            await update.message.reply_text("Kimning hisobotini chiqaray?", reply_markup=_qidir_kb(matches))
            return
        await update.message.reply_text("Ism yoki tel bo'yicha topilmadi. Buyruq bo'lsa — qaytadan yozing.")
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


async def handle_location(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    loc = update.message.location or (update.message.venue.location if update.message.venue else None)
    if not loc:
        return
    ctx.user_data["loc"] = (loc.latitude, loc.longitude)
    nom = f"\n🏢 {update.message.venue.title}" if update.message.venue else ""
    await update.message.reply_text(
        f"📍 *Lokatsiya qabul qilindi*{nom}\n\n"
        f"`{loc.latitude:.6f}, {loc.longitude:.6f}`\n"
        "👆 _bosib nusxa oling_\n\n"
        "*1-yo'l:* nusxalab, ilovada «🔗 Lokatsiya» maydoniga qo'ying.\n"
        "*2-yo'l:* shu yerga *mijoz ismini* yozing — lokatsiya o'shanga biriktiriladi "
        "va vazifa yaratganda o'zi qo'yiladi.",
        parse_mode="Markdown")


async def _loc_biriktir(update: Update, ctx: ContextTypes.DEFAULT_TYPE, ism):
    """Lokatsiya kutilayotgan bo'lsa — ismga qarab mijozga biriktiradi."""
    loc = ctx.user_data.get("loc")
    if not loc:
        return False
    ms = db.mijozlar_by_name(ism)
    if not ms:
        ms = db.similar_mijozlar(ism)
    if not ms:
        await update.message.reply_text(
            f"«{ism}» topilmadi. Mijoz ismini aniq yozing yoki /start bosing.")
        return True
    if len(ms) > 1:
        ctx.user_data["loc_pending"] = True
        kb = InlineKeyboardMarkup([[InlineKeyboardButton(f"{m['ism']} · {m['telefon'] or 'raqamsiz'}",
                                                        callback_data=f"loc:{m['id']}")] for m in ms[:8]])
        await update.message.reply_text("Qaysi mijozga biriktiray? 👇", reply_markup=kb)
        return True
    db.set_mijoz_loc(ms[0]["id"], loc[0], loc[1])
    ctx.user_data.pop("loc", None)
    await update.message.reply_text(
        f"✅ Lokatsiya *{ms[0]['ism']}* ga biriktirildi.\n\n"
        "Endi vazifa yaratganingizda haydovchiga avtomat yuboriladi.", parse_mode="Markdown")
    return True


async def handle_sticker(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    emoji = update.message.sticker.emoji if update.message.sticker else ""
    await _set_yonalish(update, ctx, _arrow_dir(emoji))


async def on_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    data = q.data
    if data.startswith(("ruxx:", "ruxa:", "ruxh:", "ruxn:")):
        if not db.is_admin(q.from_user.id):
            return
        tuid = int(data.split(":")[1])
        srov = db.ruxsat_sorov_get(tuid)
        ism = (srov or {}).get("ism") or ""
        if data.startswith("ruxn:"):
            db.ruxsat_sorov_ochir(tuid)
            await q.edit_message_text(f"❌ Rad etildi: {ism} (`{tuid}`)", parse_mode="Markdown")
        elif data.startswith("ruxh:"):
            db.haydovchi_qosh(tuid, ism)
            db.ruxsat_sorov_ochir(tuid)
            await q.edit_message_text(f"✅ Ruxsat berildi (🚚 Haydovchi): {ism} (`{tuid}`)", parse_mode="Markdown")
            try:
                await ctx.bot.send_message(tuid, "✅ Sizga haydovchi sifatida ruxsat berildi! /start bosing.")
            except Exception:
                pass
        else:
            rol = "admin" if data.startswith("ruxa:") else "xodim"
            db.add_xodim(tuid, ism, rol, q.from_user.id)
            db.ruxsat_sorov_ochir(tuid)
            belgi = "👑 Admin" if rol == "admin" else "👷 Xodim"
            await q.edit_message_text(f"✅ Ruxsat berildi ({belgi}): {ism} (`{tuid}`)", parse_mode="Markdown")
            try:
                await ctx.bot.send_message(tuid, "✅ Sizga ruxsat berildi! Endi botdan foydalanishingiz mumkin — /start bosing.")
            except Exception:
                pass
        return
    if data.startswith("xls:"):
        try:
            await q.answer()
        except Exception:
            pass
        mid = int(data.split(":")[1])
        await _mijoz_excel_yubor(q.message, mid)
        return
    if data.startswith("pick:"):
        mijoz_id = int(data.split(":")[1])
        pending = ctx.user_data.pop("pending", None)
        if not pending:
            await q.edit_message_text("Amal eskirdi. Qaytadan yuboring.")
            return
        res = logic.apply(mijoz_id, _T(pending))
        _audit_bot(q.from_user.id, mijoz_id, res)
        text, kb = fmt(res)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
        if res.get("ok") and res.get("amal") == "malumot":
            await _send_excel(q.message, res["detail"])
    elif data == "picknew":
        pending = ctx.user_data.pop("pending", None)
        if not pending:
            await q.edit_message_text("Amal eskirdi. Qaytadan yuboring.")
            return
        mijoz_id = db.add_mijoz(pending["mijoz"], pending.get("telefon"))
        res = logic.apply(mijoz_id, _T(pending))
        _audit_bot(q.from_user.id, mijoz_id, res)
        text, kb = fmt(res)
        await q.edit_message_text(text, parse_mode="Markdown", reply_markup=kb)
    elif data.startswith("delp:"):
        db.delete_partiya(int(data.split(":")[1]))
        db.audit_qosh(q.from_user.id, db.audit_ism(q.from_user.id), "o'chirish", "partiya bekor qilindi", None, "bot")
        await q.edit_message_text("🗑 Partiya bekor qilindi.")
    elif data.startswith("delr:"):
        for x in data.split(":", 1)[1].split(","):
            if x.strip().isdigit():
                db.delete_return(int(x))
        db.audit_qosh(q.from_user.id, db.audit_ism(q.from_user.id), "o'chirish", "qaytarish bekor qilindi", None, "bot")
        await q.edit_message_text("🗑 Qaytarish bekor qilindi.")
    elif data.startswith("delt:"):
        db.delete_tolov(int(data.split(":")[1]))
        db.audit_qosh(q.from_user.id, db.audit_ism(q.from_user.id), "o'chirish", "to'lov bekor qilindi", None, "bot")
        await q.edit_message_text("🗑 To'lov bekor qilindi.")
    elif data.startswith("dele:"):
        db.delete_eslatma(int(data.split(":")[1]))
        db.audit_qosh(q.from_user.id, db.audit_ism(q.from_user.id), "o'chirish", "eslatma bekor qilindi", None, "bot")
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
    elif data.startswith("loc:"):
        mid = int(data.split(":")[1])
        loc = ctx.user_data.pop("loc", None)
        if not loc:
            await q.edit_message_text("Lokatsiya eskirdi. Qaytadan yuboring.")
            return
        db.set_mijoz_loc(mid, loc[0], loc[1])
        m = db.get_mijoz(mid)
        await q.edit_message_text(f"✅ Lokatsiya *{m['ism'] if m else mid}* ga biriktirildi.",
                                  parse_mode="Markdown")
    elif data.startswith("sms:"):
        await q.edit_message_reply_markup(reply_markup=None)
        await _sms_sorov(q.message, int(data.split(":")[1]))
    elif data == "smsno":
        await q.edit_message_text("❌ SMS bekor qilindi.")
    elif data.startswith("smsok:"):
        mid = int(data.split(":")[1])
        d = db.mijoz_detail(mid)
        if not d:
            await q.edit_message_text("Mijoz topilmadi.")
            return
        await q.edit_message_text("📤 Yuborilyapti…")
        ok, info = await sms.send_sms(d.get("telefon"), sms.build_message(d))
        if ok:
            await q.edit_message_text(f"✅ SMS yuborildi: *{d['mijoz']}*", parse_mode="Markdown")
        else:
            await q.edit_message_text(f"❌ Yuborilmadi: {info}")
    elif data == "tasdiq:edit":
        tr = ctx.user_data.get("tasdiq_transkript", "")
        ctx.user_data.pop("tasdiq", None)
        ctx.user_data["await_edit"] = True
        msg = "✏️ To'g'rilab, *matn* ko'rinishida qayta yuboring."
        if tr:
            msg += f"\n\nEshitganim: «{tr}»"
        await q.edit_message_text(msg, parse_mode="Markdown")


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
    for uid in db.xodim_ids():
        try:
            await app.bot.send_message(chat_id=uid, text=text, parse_mode="Markdown")
        except Exception:
            log.exception("eslatma yuborishda xatolik")


def _qarzdor_qatri(x):
    tel = f" · 📞 {x['telefon']}" if x["telefon"] else ""
    kun = "rental yo'q" if x["kun"] is None else f"~{round(x['kun'])} kunlik"
    bel = "🔴" if x["over"] else "🟡"
    return f"{bel} *{x['ism']}*{tel}\n   💰 {som(x['qarz'])} so'm · {kun}"


def _qarzdor_matni(lst, limit_kun, sarlavha):
    lines = [f"{sarlavha} ({len(lst)} ta)", f"_chegara: {limit_kun} kunlik ijara_\n"]
    for x in lst[:30]:
        lines.append(_qarzdor_qatri(x))
    if len(lst) > 30:
        lines.append(f"\n… va yana {len(lst) - 30} ta (to'liq ro'yxat Excelda)")
    lines.append(f"\n💰 Jami: *{som(sum(x['qarz'] for x in lst))} so'm*")
    return "\n".join(lines)


def _yig_recipients():
    """Pul yig'uvchi belgilangan bo'lsa — o'shanga, aks holda barcha xodimlarga."""
    yid = db.get_sozlama("yiguvchi_id")
    if yid and yid.lstrip("-").isdigit():
        return [int(yid)]
    return db.xodim_ids()


async def _daily_report(app):
    """Kuniga bir marta (YIGISH_SOAT dan keyin): pul yig'uvchiga qarzdorlar + chegaradan oshganlar (Excel bilan)."""
    now = db.now_tk()
    if now.hour < YIGISH_SOAT:
        return
    if db.get_sozlama("yig_oxirgi_kun") == now.date().isoformat():
        return  # bugun allaqachon yuborildi
    db.set_sozlama("yig_oxirgi_kun", now.date().isoformat())
    limit_kun = db.get_limit_kun()
    lst = db.qarzdorlar(limit_kun)
    if not lst:
        return
    over = [x for x in lst if x["over"]]
    sana = db.today_tk().isoformat()

    text1 = _qarzdor_matni(lst, limit_kun, "📋 *BUGUNGI QARZDORLAR*")
    try:
        data1 = excel.qarzdorlar_excel(lst, limit_kun, sana=sana).getvalue()
    except Exception:
        data1 = None
    text2 = data2 = None
    if over:
        text2 = _qarzdor_matni(over, limit_kun, f"🔴 *CHEGARADAN OSHGANLAR — PUL YIG'ISH*")
        try:
            data2 = excel.qarzdorlar_excel(over, limit_kun, sana=sana).getvalue()
        except Exception:
            data2 = None

    for uid in _yig_recipients():
        try:
            await app.bot.send_message(chat_id=uid, text=text1, parse_mode="Markdown")
            if data1:
                await app.bot.send_document(chat_id=uid, document=InputFile(BytesIO(data1), filename="qarzdorlar.xlsx"))
            if text2:
                await app.bot.send_message(chat_id=uid, text=text2, parse_mode="Markdown")
                if data2:
                    await app.bot.send_document(chat_id=uid, document=InputFile(BytesIO(data2), filename="chegaradan_oshgan.xlsx"))
        except Exception:
            log.exception("kunlik hisobot yuborishda xatolik")


async def _set_commands(app):
    # Hamma ko'radigan asosiy menyu (Telegram "/" tugmasida chiqadi)
    umumiy = [
        BotCommand("qarzdorlar", "🔴 Pul yig'ish ro'yxati"),
        BotCommand("hisobot", "📊 Umumiy Excel hisobot"),
        BotCommand("mijozlar", "👥 Barcha qarzlar"),
        BotCommand("sms", "📩 Qarzdorga SMS"),
        BotCommand("kunlik", "📅 Bugungi harakatlar"),
        BotCommand("brovdan", "🔁 Brovdan olinganlar"),
        BotCommand("app", "📱 Hisobni ochish"),
        BotCommand("ilova", "🔨 Temirchi ilovasi (havola)"),
        BotCommand("xarajat", "💵 AI sarfi"),
        BotCommand("start", "ℹ️ Yordam"),
    ]
    # Admin/ega qo'shimcha ko'radigan buyruqlar
    admin_extra = umumiy + [
        BotCommand("limit", "📏 Yig'ish chegarasi"),
        BotCommand("xodimlar", "🧑‍🔧 Xodimlar"),
        BotCommand("haydovchilar", "🚚 Haydovchilar"),
        BotCommand("parol", "🔑 Ilova uchun login/parol"),
        BotCommand("tovarlar", "📋 Tovarlar ro'yxati"),
        BotCommand("nomlar", "🔤 Tovar nomlarini tekshirish"),
        BotCommand("ombor_hisobla", "🔄 Omborni qayta sanash"),
        BotCommand("xodim_qosh", "➕ Xodim qo'shish"),
        BotCommand("admin_qosh", "👑 Admin qo'shish"),
        BotCommand("ochir", "🗑 Xodim o'chirish"),
    ]
    try:
        await app.bot.set_my_commands(umumiy)
        # Adminlarga (jadvaldagilar + ega) to'liq ro'yxat
        for x in db.all_xodimlar():
            if x["rol"] == "admin":
                try:
                    await app.bot.set_my_commands(admin_extra, scope=BotCommandScopeChat(chat_id=x["id"]))
                except Exception:
                    pass
    except Exception:
        log.exception("buyruq menyusini o'rnatishda xatolik")


# ==========================================================================
# SAVOL-JAVOB AGENTI (faqat admin) — savol -> SQL SELECT -> javob
# Xavfsizlik: faqat SELECT + so'rov bir martalik XOTIRA-NUSXAda bajariladi
# (real bazaga umuman tegilmaydi).
# ==========================================================================
import sqlite3 as _sqlite3

_YOZISH_RE = re.compile(
    r"\b(insert|update|delete|drop|alter|create|replace|attach|detach|pragma|vacuum|reindex|truncate|grant|revoke)\b",
    re.IGNORECASE)


def _sql_tozala(sql):
    s = (sql or "").strip()
    if s.startswith("```"):
        s = re.sub(r"^```[a-zA-Z]*", "", s).strip()
        if s.endswith("```"):
            s = s[:-3].strip()
    return s.strip().rstrip(";").strip()


def _sql_xavfsizmi(sql):
    s = _sql_tozala(sql)
    if not s:
        return False
    low = s.lower()
    if not (low.startswith("select") or low.startswith("with")):
        return False
    if ";" in s:                       # bitta so'rovgina
        return False
    if _YOZISH_RE.search(s):           # yozuv so'zlari yo'q
        return False
    return True


def _db_sxema():
    con = _sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    parts = []
    for name, sql in con.execute(
            "SELECT name, sql FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' AND name NOT IN ('api_usage','sozlamalar') ORDER BY name"):
        if sql:
            parts.append(sql.strip())
    con.close()
    return "\n\n".join(parts)


def _agent_baza():
    """Bir martalik xotira-nusxa + hisoblangan v_mijoz_qarz jadvali."""
    src = _sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    mem = _sqlite3.connect(":memory:")
    src.backup(mem)
    src.close()
    mem.row_factory = _sqlite3.Row
    try:
        mem.execute("CREATE TABLE v_mijoz_qarz (mijoz_id INTEGER, ism TEXT, telefon TEXT, "
                    "bolim TEXT, status TEXT, qolgan_qarz REAL, jami_qolgan REAL)")
        for b in ("ijara", "sotuv"):
            try:
                for m in db.mijozlar(bolim=b):
                    mem.execute("INSERT INTO v_mijoz_qarz VALUES (?,?,?,?,?,?,?)",
                                (m.get("id"), m.get("mijoz") or m.get("ism"), m.get("telefon"),
                                 b, m.get("status"), m.get("qolgan_qarz") or 0, m.get("jami_qolgan") or 0))
            except Exception:
                pass
        mem.commit()
    except Exception:
        log.exception("v_mijoz_qarz yaratish")
    return mem


def _jadval_matn(cols, rows):
    if not rows:
        return "(bo'sh)"
    satrlar = [" | ".join(cols)]
    for r in rows:
        satrlar.append(" | ".join("" if v is None else str(v) for v in r))
    return "\n".join(satrlar)


def _sql_bajar(sql, limit=50):
    mem = _agent_baza()
    try:
        cur = mem.execute(sql)
        cols = [d[0] for d in cur.description] if cur.description else []
        rows = [list(r) for r in cur.fetchmany(limit)]
        return cols, rows
    finally:
        mem.close()


def agent_javob(savol, sql_korsat=False):
    """Savol -> SQL -> bajarish -> o'zbekcha javob. 1 marta retry."""
    try:
        sxema = _db_sxema()
    except Exception:
        log.exception("sxema"); return "Baza sxemasini o'qishda xatolik."
    xato = None
    for _ in range(2):
        try:
            sql = ai.savol_sql(savol, sxema, xato)
        except Exception:
            log.exception("savol_sql"); return "AI bilan bog'lanishda xatolik."
        if not _sql_xavfsizmi(sql):
            xato = "Faqat SELECT ruxsat etiladi (yozuv so'rovlari mumkin emas)."
            continue
        sql = _sql_tozala(sql)
        try:
            cols, rows = _sql_bajar(sql)
        except Exception as e:
            xato = str(e); continue
        try:
            javob = ai.natija_javob(savol, _jadval_matn(cols, rows))
        except Exception:
            log.exception("natija_javob"); javob = _jadval_matn(cols, rows)
        javob = javob or "Ma'lumot topilmadi."
        if sql_korsat:
            javob += f"\n\n<code>{sql}</code>"
        return javob
    return "Savolga javob topolmadim 🤔 Boshqacharoq so'rab ko'ring."


async def _agent_reply(update, ctx, savol, sql_korsat=False):
    kutish = await update.effective_message.reply_text("⏳ O'ylayapman…")
    try:
        loop = asyncio.get_event_loop()
        javob = await loop.run_in_executor(None, agent_javob, savol, sql_korsat)
    except Exception:
        log.exception("agent xatolik"); javob = "Xatolik yuz berdi."
    try:
        await ctx.bot.delete_message(update.effective_chat.id, kutish.message_id)
    except Exception:
        pass
    await update.effective_message.reply_text(javob, parse_mode="HTML", disable_web_page_preview=True)


async def savol_cmd(update, ctx):
    if not await admin_guard(update, ctx):
        return
    savol = " ".join(ctx.args).strip() if ctx.args else ""
    if not savol:
        await update.message.reply_text("Savol yozing. Masalan:\n/savol bu oy qancha to'lov kirdi")
        return
    await _agent_reply(update, ctx, savol, sql_korsat=False)


async def sql_cmd(update, ctx):
    if not await admin_guard(update, ctx):
        return
    savol = " ".join(ctx.args).strip() if ctx.args else ""
    if not savol:
        await update.message.reply_text("/sql <savol> — javob + ishlatilgan SQL")
        return
    await _agent_reply(update, ctx, savol, sql_korsat=True)


BACKUP_HOUR = int(os.getenv("BACKUP_HOUR", "3"))


def _backup_bytes():
    """Bazaning izchil (consistent) nusxasini bytes ko'rinishida qaytaradi."""
    import sqlite3
    import tempfile
    src = sqlite3.connect(f"file:{db.DB_PATH}?mode=ro", uri=True)
    tf = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tf.close()
    try:
        dst = sqlite3.connect(tf.name)
        with dst:
            src.backup(dst)
        dst.close()
        with open(tf.name, "rb") as f:
            return f.read()
    finally:
        src.close()
        try:
            os.unlink(tf.name)
        except Exception:
            pass


def _backup_fayl_nomi():
    return f"temirchi_backup_{db.now_tk().strftime('%Y-%m-%d')}.db"


async def _backup_yubor(app, chat_ids=None):
    if chat_ids is None:
        chat_ids = [x["id"] for x in db.all_xodimlar() if x.get("rol") == "admin"]
        extra = os.getenv("BACKUP_CHAT_ID")
        if extra:
            try:
                if int(extra) not in chat_ids:
                    chat_ids.append(int(extra))
            except Exception:
                pass
    if not chat_ids:
        log.warning("backup: admin topilmadi")
        return
    data = _backup_bytes()
    nomi = _backup_fayl_nomi()
    cap = f"🗄 Avtomat backup · {db.now_tk().strftime('%Y-%m-%d %H:%M')}"
    for cid in chat_ids:
        try:
            await app.bot.send_document(chat_id=cid, document=InputFile(BytesIO(data), filename=nomi), caption=cap)
        except Exception:
            log.exception("backup yuborish xatolik (%s)", cid)


async def backup_loop(app):
    while True:
        try:
            now = db.now_tk()
            target = now.replace(hour=BACKUP_HOUR, minute=0, second=0, microsecond=0)
            if target <= now:
                target += timedelta(days=1)
            await asyncio.sleep(max(30, (target - now).total_seconds()))
            await _backup_yubor(app)
        except Exception:
            log.exception("backup_loop xatolik")
            await asyncio.sleep(300)


async def backup_cmd(update, ctx):
    if not await admin_guard(update, ctx):
        return
    kutish = await update.message.reply_text("⏳ Backup tayyorlanmoqda…")
    try:
        data = _backup_bytes()
        await update.message.reply_document(
            document=InputFile(BytesIO(data), filename=_backup_fayl_nomi()),
            caption=f"🗄 Baza zaxira nusxasi · {db.now_tk().strftime('%Y-%m-%d %H:%M')}")
        try:
            await ctx.bot.delete_message(update.effective_chat.id, kutish.message_id)
        except Exception:
            pass
    except Exception:
        log.exception("backup_cmd xatolik")
        await update.message.reply_text("Backup yaratishda xatolik.")


async def _pp_sms(app, x, bosqich):
    """Mijozga predoplata SMS (1=1 kun qoldi, 0=tugadi)."""
    try:
        if not sms.is_configured():
            return
        tel = sms.normalize_phone(x.get("telefon"))
        if not tel:
            return
        if bosqich == 1:
            matn = ("Hurmatli mijoz! TEMIRCHI ijara xizmatidan foydalanganingiz uchun rahmat. "
                    "Oldindan to'lovingiz muddati tugashiga 1 kun qoldi. "
                    "Iltimos, to'lovni o'z vaqtida yangilang.")
        else:
            matn = ("TEMIRCHI: Hurmatli mijoz, oldindan to'lov muddatingiz tugadi. "
                    "Iltimos, bugun to'lovni amalga oshiring, aks holda "
                    "mahsulotlarni qaytarib olishga majbur bo'lamiz.")
        await sms.send_sms(x.get("telefon"), matn)
    except Exception:
        log.exception("predoplata sms")


async def _predoplata_check(app):
    """Kuniga bir marta (YIGISH_SOAT dan keyin): predoplata tugash nazorati."""
    now = db.now_tk()
    if now.hour < YIGISH_SOAT:
        return
    kun = now.date().isoformat()
    if db.get_sozlama("pp_oxirgi_kun") == kun:
        return
    db.set_sozlama("pp_oxirgi_kun", kun)

    lst = db.predoplata_royxati()
    ikki, tugadi = [], []
    for x in lst:
        k = x["qolgan_kun"]
        mid = x["id"]
        if k <= 0:
            b = "0"
        elif k <= 1:
            b = "1"
        elif k <= 2:
            b = "2"
        else:
            b = ""
        oldingi = db.get_sozlama(f"pp_bosqich_{mid}") or ""
        if b == "":
            if oldingi:
                db.set_sozlama(f"pp_bosqich_{mid}", "")   # to'ldirildi -> reset
            continue
        if b == oldingi:
            continue
        db.set_sozlama(f"pp_bosqich_{mid}", b)
        if b == "2":
            ikki.append(x)
        elif b == "1":
            await _pp_sms(app, x, 1)
        elif b == "0":
            tugadi.append(x)
            await _pp_sms(app, x, 0)

    if ikki or tugadi:
        satlar = ["💳 *PREDOPLATA — nazorat*\n"]
        if tugadi:
            satlar.append("🔴 *Tugadi — pul olish kerak:*")
            for x in tugadi:
                tel = f" · {x['telefon']}" if x.get("telefon") else ""
                satlar.append(f"• *{x['mijoz']}*{tel} — kuniga {som(x['kunlik'])} so'm")
        if ikki:
            satlar.append("\n🟡 *2 kun ichida tugaydi:*")
            for x in ikki:
                tel = f" · {x['telefon']}" if x.get("telefon") else ""
                satlar.append(f"• *{x['mijoz']}*{tel} — {x['tugash']} gacha")
        matn = "\n".join(satlar)
        for uid in _yig_recipients():
            try:
                await app.bot.send_message(uid, matn, parse_mode="Markdown")
            except Exception:
                pass


async def predoplata_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not await guard(update, ctx):
        return
    lst = db.predoplata_royxati()
    if not lst:
        await update.message.reply_text(
            "💳 Oldindan to'lov (predoplata) mijozlari yo'q.\n\n"
            "_Mijoz ijara summasidan ko'proq to'lasa — 'krediti' hisoblanadi va shu yerda kuzatiladi._",
            parse_mode="Markdown")
        return
    satlar = ["💳 *Oldindan to'lov — mijozlar*\n"]
    for x in lst:
        k = x["qolgan_kun"]
        bel = "🔴" if k <= 0 else ("🟡" if k <= 2 else "🟢")
        kunmatn = "bugun tugadi" if k <= 0 else f"~{int(round(k))} kun qoldi"
        tel = f" · {x['telefon']}" if x.get("telefon") else ""
        satlar.append(f"{bel} *{x['mijoz']}*{tel}\n    {kunmatn} · kredit {som(x['qoldiq'])} so'm · {x['tugash']}")
    matn = "\n".join(satlar)
    for i in range(0, len(matn), 3500):
        await update.message.reply_text(matn[i:i+3500], parse_mode="Markdown")


async def reminder_loop(app):
    while True:
        try:
            for r in db.due_eslatmalar():
                await _send_eslatma(app, r)
                db.mark_eslatma_sent(r["id"])
            await _daily_report(app)
            await _predoplata_check(app)
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
    app.add_handler(CommandHandler("ilova", ilova_cmd))
    app.add_handler(CommandHandler("kunlik", kunlik_cmd))
    app.add_handler(CommandHandler("xarajat", xarajat_cmd))
    app.add_handler(CommandHandler("qarzdorlar", qarzdorlar_cmd))
    app.add_handler(CommandHandler("predoplata", predoplata_cmd))
    app.add_handler(CommandHandler("hisobot", hisobot_cmd))
    app.add_handler(CommandHandler("mijoz", mijoz_cmd))
    app.add_handler(CommandHandler("limit", limit_cmd))
    app.add_handler(CommandHandler("yiguvchi", yiguvchi_cmd))
    app.add_handler(CommandHandler("brovdan", brovdan_cmd))
    app.add_handler(CommandHandler("nomlar", nomlar_cmd))
    app.add_handler(CommandHandler("nom", nom_cmd))
    app.add_handler(CommandHandler("ombor_hisobla", ombor_hisobla_cmd))
    app.add_handler(CommandHandler("ostatka", ostatka_cmd))
    app.add_handler(CommandHandler("ostatka_tuzat", ostatka_tuzat_cmd))
    app.add_handler(CommandHandler("parol", parol_cmd))
    app.add_handler(CommandHandler("haydovchilar", haydovchilar_cmd))
    app.add_handler(CommandHandler("haydovchi_qosh", haydovchi_qosh_cmd))
    app.add_handler(CommandHandler("haydovchi_ochir", haydovchi_ochir_cmd))
    app.add_handler(CommandHandler("tovarlar", tovarlar_cmd))
    app.add_handler(CommandHandler("tekshir", tekshir_cmd))
    app.add_handler(CommandHandler("shablon", shablon_cmd))
    app.add_handler(CommandHandler("shablonlar", shablonlar_cmd))
    app.add_handler(CommandHandler("sms", sms_cmd))
    app.add_handler(CommandHandler("xodimlar", xodimlar_cmd))
    app.add_handler(CommandHandler("xodim_qosh", xodim_qosh_cmd))
    app.add_handler(CommandHandler("admin_qosh", admin_qosh_cmd))
    app.add_handler(CommandHandler("ochir", ochir_cmd))
    app.add_handler(CommandHandler("backup", backup_cmd))
    app.add_handler(CommandHandler("sorovlar", sorovlar_cmd))
    app.add_handler(CommandHandler("jurnal", jurnal_cmd))
    app.add_handler(CommandHandler("savol", savol_cmd))
    app.add_handler(CommandHandler("sql", sql_cmd))
    app.add_handler(CallbackQueryHandler(on_cb, pattern=r"^(xls:|pick:|picknew|delp:|delr:|delt:|dele:|tasdiq:|sms:|smsok:|smsno|loc:)"))
    app.add_handler(MessageHandler(filters.VOICE, handle_voice))
    app.add_handler(MessageHandler(filters.LOCATION | filters.VENUE, handle_location))
    app.add_handler(MessageHandler(filters.Sticker.ALL, handle_sticker))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    port = int(os.getenv("PORT", "8080"))
    runner = aioweb.AppRunner(make_web_app(token))
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
    asyncio.create_task(backup_loop(app))
    await _set_commands(app)
    log.info("Ijara boti + Mini App ishga tushdi (port %s).", port)
    await asyncio.Event().wait()


def main():
    asyncio.run(run())


if __name__ == "__main__":
    main()
