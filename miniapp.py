"""Mini App veb-serveri (aiohttp)."""
import os
import json
import hmac
import time
import hashlib
from urllib.parse import parse_qsl
from pathlib import Path
from aiohttp import web

import db
import ai
import logic
import sms

INDEX = Path(__file__).parent / "index.html"


def _som(n):
    return f"{round(n):,}".replace(",", " ")


def _son(n):
    n = float(n)
    return str(int(n)) if n == int(n) else str(n)


def web_msg(res):
    if not res.get("ok"):
        return res.get("xato", "Xatolik")
    amal = res.get("amal")
    if amal == "chiqish":
        return (f"✅ {res['raqam']}-partiya ochildi: {_son(res['miqdor'])} ta {res['mahsulot']}, "
                f"kuniga {_som(res['kunlik_narx'])} so'm")
    if amal == "qaytarish":
        if res.get("aggregate"):
            return (f"✅ {_son(res['qty'])} ta {res['mahsulot']} qaytdi · "
                    f"Qolgan qarz: {_som(res.get('qolgan_qarz', 0))} so'm")
        return (f"✅ {res['partiya_raqam']}-partiya: {_son(res['qty'])} ta {res['mahsulot']} qaytdi. "
                f"Qolgan: {_son(res['qolgan'])} ta")
    if amal == "tolov":
        return f"✅ To'lov {_som(res.get('summa', 0))} so'm · Qolgan qarz: {_som(res.get('qolgan_qarz', 0))} so'm"
    if amal == "eslatma":
        return f"✅ Eslatma qo'shildi ({res.get('vada_sana', '')})"
    if amal == "malumot":
        return "✅ Ma'lumot tayyor"
    return "✅ Bajarildi"


def make_token(uid, bot_token, kun=30):
    """uid.expiry.signature — brauzer uchun oddiy imzolangan token."""
    exp = int(time.time()) + kun * 24 * 3600
    xom = f"{uid}.{exp}"
    imzo = hmac.new(bot_token.encode(), xom.encode(), hashlib.sha256).hexdigest()[:32]
    return f"{xom}.{imzo}"


def read_token(token, bot_token):
    try:
        uid_s, exp_s, imzo = (token or "").split(".")
        xom = f"{uid_s}.{exp_s}"
        kutilgan = hmac.new(bot_token.encode(), xom.encode(), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(kutilgan, imzo):
            return None
        if int(exp_s) < time.time():
            return None
        return int(uid_s)
    except Exception:
        return None


def validate_init_data(init_data, bot_token):
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    got = pairs.pop("hash", None)
    if not got:
        return None
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got):
        return None
    try:
        return int(json.loads(pairs.get("user", "{}"))["id"])
    except Exception:
        return None


class _BrovWrap:
    """AI amalini o'rab, brovdan ma'lumotini qo'shadi (AI obyekti qulflangan)."""

    def __init__(self, a, brov_kim=None, brov_miqdor=None):
        object.__setattr__(self, "_a", a)
        object.__setattr__(self, "_own", {"brov_kim": brov_kim, "brov_miqdor": brov_miqdor})

    def __getattr__(self, k):
        own = object.__getattribute__(self, "_own")
        if k in own:
            return own[k]
        return getattr(object.__getattribute__(self, "_a"), k)

    def __setattr__(self, k, v):
        object.__getattribute__(self, "_own")[k] = v


def make_web_app(bot_token):

    def check(request):
        uid = validate_init_data(request.headers.get("X-Init-Data", ""), bot_token)
        if uid is None:
            uid = read_token(request.headers.get("X-Token", ""), bot_token)
        if uid is None:
            dbg = os.getenv("DEBUG_USER_ID")
            uid = int(dbg) if dbg else None
        if uid is None:
            return None, web.json_response({"xato": "Telegram ichida oching"}, status=401)
        if not db.is_allowed(uid):
            return None, web.json_response({"xato": "Ruxsat yo'q"}, status=403)
        return uid, None

    async def index(request):
        # Ilova (PWA) eski faylni keshda ushlab qolmasin
        return web.FileResponse(INDEX, headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Pragma": "no-cache", "Expires": "0"})

    # ---- Mijoz uchun ochiq sahifa (login talab qilinmaydi) ----
    async def mijoz_sahifa(request):
        yol = Path(__file__).parent / "mijoz.html"
        if not yol.exists():
            return web.Response(status=404)
        return web.FileResponse(yol, headers={
            "Cache-Control": "no-cache, no-store, must-revalidate", "X-Robots-Tag": "noindex"})

    async def api_mijoz_ochiq(request):
        token = request.match_info.get("token", "")
        d = db.mijoz_ochiq(token)
        if not d:
            return web.json_response({"xato": "topilmadi"}, status=404)
        d["tel"] = os.getenv("FIRMA_TEL", "")
        return web.json_response(d, headers={"Cache-Control": "no-store", "X-Robots-Tag": "noindex"})

    async def tg_xabar(chat_id, matn):
        """Bot orqali xabar yuborish (Telegram HTTP API)."""
        try:
            import aiohttp as _ah
            async with _ah.ClientSession() as ss:
                await ss.post(f"https://api.telegram.org/bot{bot_token}/sendMessage",
                              json={"chat_id": chat_id, "text": matn,
                                    "parse_mode": "Markdown", "disable_web_page_preview": True},
                              timeout=_ah.ClientTimeout(total=12))
            return True
        except Exception:
            return False

    def _asos_url(request):
        base = os.getenv("WEBAPP_URL") or ""
        if not base:
            dom = os.getenv("RAILWAY_PUBLIC_DOMAIN")
            base = f"https://{dom}" if dom else str(request.url.origin())
        return base.split("?")[0].rstrip("/")

    # ---- Haydovchi sahifasi (login talab qilinmaydi, havola maxfiy) ----
    RASM_DIR = Path(os.getenv("DATA_DIR", "/data")) / "rasm"

    def _rasm_saqla(data_url, old="v"):
        """base64 data-url ni faylga yozadi, fayl nomini qaytaradi."""
        import base64, secrets, re as _re
        if not data_url or "," not in data_url:
            return None
        bosh, b64 = data_url.split(",", 1)
        kengaytma = "png" if "png" in bosh else "jpg"
        try:
            xom = base64.b64decode(b64)
        except Exception:
            return None
        if len(xom) > 6 * 1024 * 1024:
            return None
        RASM_DIR.mkdir(parents=True, exist_ok=True)
        nom = f"{old}-{secrets.token_urlsafe(8)}.{kengaytma}"
        (RASM_DIR / nom).write_bytes(xom)
        return nom

    async def haydovchi_sahifa(request):
        yol = Path(__file__).parent / "haydovchi.html"
        if not yol.exists():
            return web.Response(status=404)
        return web.FileResponse(yol, headers={
            "Cache-Control": "no-cache, no-store, must-revalidate", "X-Robots-Tag": "noindex"})

    async def api_vazifa_ochiq(request):
        d = db.vazifa_by_token(request.match_info.get("token", ""))
        if not d:
            return web.json_response({"xato": "topilmadi"}, status=404)
        return web.json_response(d, headers={"Cache-Control": "no-store"})

    async def api_v_holat(request):
        try:
            b = await request.json()
            v = db.vazifa_by_token(b.get("token"))
            if not v:
                return web.json_response({"ok": False, "xato": "topilmadi"}, status=404)
            return web.json_response(db.vazifa_holat(v["id"], b.get("holat")))
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_v_bajar(request):
        try:
            b = await request.json()
            v = db.vazifa_by_token(b.get("token"))
            if not v:
                return web.json_response({"ok": False, "xato": "topilmadi"}, status=404)
            if v["holat"] == "bajarildi":
                return web.json_response({"ok": True, "xabar": "Allaqachon tasdiqlangan"})
            rasm = _rasm_saqla(b.get("rasm"), "r")
            imzo = _rasm_saqla(b.get("imzo"), "i")
            db.vazifa_bajarildi(v["id"], rasm, imzo, b.get("qabul_qildi"))
            # Telefon bo'yicha mijozni aniqlaymiz: bor bo'lsa o'shanga, yo'q bo'lsa yangi yacheyka
            mid, yangi = db.vazifa_mijozini_aniqla(v, b.get("telefon"))
            qatorlar = [{"mahsulot": t["mahsulot"], "miqdor": t["miqdor"],
                         "birlik": t.get("birlik"), "kunlik_narx": t.get("kunlik_narx") or 0}
                        for t in (v.get("tovarlar") or [])]
            sana = db.today_tk().isoformat()
            if v["tur"] == "olib_kelish":
                res = logic.qator_qaytarish(mid, qatorlar, sana)
            else:
                res = logic.qator_chiqish(mid, qatorlar, sana, manzil=v.get("manzil"))
            xabar = res.get("xabar") or "Tasdiqlandi"
            if yangi:
                xabar += " · yangi mijoz ochildi"
            # Xodimlarga xabar
            try:
                m = db.get_mijoz(mid)
                bild = (f"✅ *YETKAZILDI*\n\n👤 {m['ism'] if m else '—'}\n"
                        + (f"📍 {v['manzil']}\n" if v.get("manzil") else "")
                        + f"🤝 Qabul qildi: {b.get('qabul_qildi') or '—'}\n"
                        + (f"📞 {b.get('telefon')}\n" if b.get("telefon") else "")
                        + ("🆕 Yangi mijoz ochildi\n" if yangi else "")
                        + f"\n{xabar}")
                for x in db.xodim_ids():
                    await tg_xabar(x, bild)
            except Exception:
                pass
            return web.json_response({"ok": True, "xabar": xabar})
        except Exception as e:
            import traceback; traceback.print_exc()
            return web.json_response({"ok": False, "xato": f"{type(e).__name__}"}, status=500)

    async def rasm_fayl(request):
        nom = request.match_info.get("nom", "")
        if not nom or "/" in nom or "\\" in nom or ".." in nom:
            return web.Response(status=404)
        yol = RASM_DIR / nom
        if not yol.exists():
            return web.Response(status=404)
        return web.FileResponse(yol, headers={"Cache-Control": "public, max-age=86400",
                                              "X-Robots-Tag": "noindex"})

    # ---- Vazifalar (menejer uchun, login bilan) ----
    async def api_vazifalar(request):
        uid, err = check(request)
        if err:
            return err
        return web.json_response({"vazifalar": db.vazifalar(request.query.get("holat") or None)})

    async def api_vazifa_qosh(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            lat = b.get("lat"); lon = b.get("lon")
            mid = b.get("mijoz_id")
            res = db.vazifa_qosh(int(mid) if mid not in (None, "", 0) else None, b.get("tovarlar") or [],
                                 tur=b.get("tur") or "yetkazish", manzil=b.get("manzil"),
                                 lat=float(lat) if lat not in (None, "") else None,
                                 lon=float(lon) if lon not in (None, "") else None,
                                 izoh=b.get("izoh"), haydovchi=b.get("haydovchi"), sana=b.get("sana"),
                                 mijoz_nom=b.get("mijoz_nom"), telefon=b.get("telefon"),
                                 haydovchi_id=b.get("haydovchi_id"))
            # Haydovchiga Telegramда xabar
            hid = b.get("haydovchi_id")
            if res.get("ok") and hid:
                try:
                    v = db.vazifa_by_token(res["token"])
                    link = f"{_asos_url(request)}/v/{res['token']}"
                    tovar = "\n".join(f"• {t['mahsulot']} — {int(t['miqdor']) if float(t['miqdor']).is_integer() else t['miqdor']} {t.get('birlik') or ''}"
                                      for t in (v.get("tovarlar") or []))
                    tur = "📥 OLIB KELISH" if v["tur"] == "olib_kelish" else "📤 YETKAZISH"
                    matn = (f"🚚 *YANGI ZAKAZ*\n{tur}\n\n"
                            f"👤 {v['mijoz']}\n"
                            + (f"📞 {v['telefon']}\n" if v.get("telefon") else "")
                            + (f"📍 {v['manzil']}\n" if v.get("manzil") else "")
                            + f"📅 {str(v.get('sana') or '')[:10]}\n\n"
                            + (tovar + "\n\n" if tovar else "")
                            + (f"📝 {v['izoh']}\n\n" if v.get("izoh") else "")
                            + f"👉 Ochish: {link}")
                    await tg_xabar(int(hid), matn)
                    res["xabar_yuborildi"] = True
                except Exception:
                    res["xabar_yuborildi"] = False
            return web.json_response(res)
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_haydovchilar(request):
        uid, err = check(request)
        if err:
            return err
        return web.json_response({"haydovchilar": db.haydovchilar()})

    async def api_vazifa_ochir(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.vazifa_ochir(int(b.get("id"))))
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_login(request):
        try:
            b = await request.json()
            uid = db.login_tekshir(b.get("login"), b.get("parol"))
            if not uid or not db.is_allowed(uid):
                return web.json_response({"ok": False, "xato": "Login yoki parol noto'g'ri"}, status=401)
            x = db.get_xodim(uid) or {}
            return web.json_response({"ok": True, "token": make_token(uid, bot_token),
                                      "ism": x.get("ism") or "", "rol": x.get("rol") or "xodim"})
        except Exception:
            return web.json_response({"ok": False, "xato": "Xato"}, status=400)

    async def manifest(request):
        return web.json_response({
            "name": "Temirchi — ijara hisobi", "short_name": "Temirchi",
            "description": "Temirchi — ijara va ombor hisobi",
            "start_url": "/", "scope": "/", "display": "standalone",
            "background_color": "#F3F5F4", "theme_color": "#1F4B45",
            "orientation": "portrait-primary",
            "icons": [
                {"src": "/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
                {"src": "/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
            ],
        }, headers={"Cache-Control": "no-cache"})

    async def sw_js(request):
        return web.Response(text=(
            "self.addEventListener('install',e=>self.skipWaiting());\n"
            "self.addEventListener('activate',e=>{e.waitUntil(caches.keys()"
            ".then(k=>Promise.all(k.map(x=>caches.delete(x)))).then(()=>self.clients.claim()));});\n"
            "self.addEventListener('fetch',function(e){});\n"),
                            content_type="application/javascript",
                            headers={"Cache-Control": "no-cache"})

    RUXSAT_RASM = {"icon-192.png", "icon-512.png", "logo.png"}

    async def icon(request):
        nom = request.match_info.get("nom") or request.path.lstrip("/")
        if nom not in RUXSAT_RASM:
            return web.Response(status=404)
        yol = Path(__file__).parent / nom
        if yol.exists():
            return web.FileResponse(yol, headers={"Cache-Control": "public, max-age=86400"})
        return web.Response(status=404)

    async def api_mijozlar(request):
        uid, err = check(request)
        if err:
            return err
        bolim = request.query.get("bolim") or None
        return web.json_response({"mijozlar": db.mijozlar(bolim=bolim)})

    async def api_mijoz(request):
        uid, err = check(request)
        if err:
            return err
        try:
            mid = int(request.query.get("id", ""))
        except Exception:
            return web.json_response({"xato": "id kerak"}, status=400)
        d = db.mijoz_detail(mid)
        if not d:
            return web.json_response({"xato": "topilmadi"}, status=404)
        return web.json_response(d)

    async def api_mijoz_qosh(request):
        uid, err = check(request)
        if err:
            return err
        body = await request.json()
        ism = (body.get("ism") or "").strip()
        if not ism:
            return web.json_response({"xato": "ism kerak"}, status=400)
        mid = db.add_mijoz(ism, body.get("telefon"), bolim=(body.get("bolim") or "ijara"))
        return web.json_response({"ok": True, "id": mid})

    async def api_mijoz_edit(request):
        uid, err = check(request)
        if err:
            return err
        try:
            body = await request.json()
            mid = int(body.get("mijoz_id"))
            ism = (body.get("ism") or "").strip()
            if not ism:
                return web.json_response({"ok": False, "xabar": "Ism kerak"})
            db.update_mijoz(mid, ism, body.get("telefon"))
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "xabar": f"Xato: {type(e).__name__}"})

    async def api_qoshish(request):
        uid, err = check(request)
        if err:
            return err
        try:
            body = await request.json()
            mid = int(body.get("mijoz_id"))
            matn = (body.get("matn") or "").strip()
            if not matn:
                return web.json_response({"ok": False, "xabar": "Matn bo'sh"})
            actions = ai.from_text(matn)
            actions = [a for a in actions if a.tushunildi and a.amal] or actions[:1]
            if not actions:
                return web.json_response({"ok": False, "xabar": "Tushunolmadim"})
            bkim = (body.get("brov_kim") or "").strip() or None
            bmiq = body.get("brov_miqdor")
            try:
                bmiq = float(bmiq) if bmiq not in (None, "") else None
            except Exception:
                bmiq = None
            if bkim:
                wrapped = []
                for a in actions:
                    am = a.amal.value if hasattr(a.amal, "value") else a.amal
                    wrapped.append(_BrovWrap(a, bkim, bmiq) if am == "chiqish" else a)
                actions = wrapped
            msgs, ok = [], False
            for a in actions:
                res = logic.apply(mid, a)
                if res.get("ok"):
                    ok = True
                msgs.append(web_msg(res))
            return web.json_response({"ok": ok, "xabar": " | ".join(msgs)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return web.json_response({"ok": False, "xabar": f"Server xato: {type(e).__name__}: {str(e)[:180]}"})

    async def api_qator(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            mid = int(b.get("mijoz_id"))
            qat = b.get("qatorlar") or []
            if (b.get("amal") or "chiqish") == "qaytarish":
                res = logic.qator_qaytarish(mid, qat, b.get("sana"), b.get("brov_kim"))
            else:
                res = logic.qator_chiqish(mid, qat, b.get("sana"), b.get("brov_kim"), b.get("manzil"))
            if res.get("ok"):
                return web.json_response({"ok": True, "xabar": res["xabar"]})
            return web.json_response({"ok": False, "xabar": res.get("xato", "Xato")})
        except Exception as e:
            import traceback; traceback.print_exc()
            return web.json_response({"ok": False, "xabar": f"Server xato: {type(e).__name__}: {str(e)[:150]}"})

    async def api_qoshish_audio(request):
        uid, err = check(request)
        if err:
            return err
        try:
            mid = int(request.headers.get("X-Mijoz-Id"))
            audio = await request.read()
            mime = request.headers.get("Content-Type", "audio/ogg").split(";")[0]
            actions = ai.from_audio(audio, mime_type=mime)
            actions = [a for a in actions if a.tushunildi and a.amal] or actions[:1]
            if not actions:
                return web.json_response({"ok": False, "xabar": "Tushunolmadim"})
            msgs, ok = [], False
            for a in actions:
                res = logic.apply(mid, a)
                if res.get("ok"):
                    ok = True
                msgs.append(web_msg(res))
            return web.json_response({"ok": ok, "xabar": " | ".join(msgs)})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return web.json_response({"ok": False, "xabar": f"Ovoz xato: {type(e).__name__}: {str(e)[:180]}"})

    async def api_ochirish(request):
        uid, err = check(request)
        if err:
            return err
        body = await request.json()
        try:
            mid = int(body.get("mijoz_id"))
        except Exception:
            return web.json_response({"xato": "mijoz_id kerak"}, status=400)
        db.delete_mijoz(mid)
        return web.json_response({"ok": True})

    async def api_mijoz_loc(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.set_mijoz_loc(int(b.get("mijoz_id")), b.get("lat"), b.get("lon")))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_adres(request):
        uid, err = check(request)
        if err:
            return err
        body = await request.json()
        try:
            mid = int(body.get("mijoz_id"))
        except Exception:
            return web.json_response({"xato": "mijoz_id kerak"}, status=400)
        db.set_adres(mid, body.get("adres"))
        return web.json_response({"ok": True})

    async def api_tolov(request):
        uid, err = check(request)
        if err:
            return err
        try:
            body = await request.json()
            mid = int(body.get("mijoz_id"))
            summa = float(body.get("summa"))
            if summa == 0:
                return web.json_response({"ok": False, "xabar": "Summa noto'g'ri"})
            izoh = "qarz qo'shildi" if summa < 0 else None
            sana = (body.get("sana") or db.today_tk().isoformat())[:10]
            db.add_tolov(mid, summa, sana, izoh)
            d = db.mijoz_detail(mid)
            return web.json_response({"ok": True, "qolgan_qarz": d["qolgan_qarz"]})
        except Exception as e:
            return web.json_response({"ok": False, "xabar": f"Xato: {type(e).__name__}"})

    async def api_tolov_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            body = await request.json()
            db.delete_tolov(int(body.get("tolov_id")))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_partiya_edit(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            pid = int(b.get("partiya_id"))
            p = db.get_partiya_by_id(pid)
            if not p:
                return web.json_response({"ok": False, "xabar": "Partiya topilmadi"})
            miqdor = float(b.get("miqdor"))
            qaytgan = sum(r["miqdor"] for r in db.returns_for(pid))
            if miqdor < qaytgan:
                return web.json_response({"ok": False, "xabar": f"Soni {int(qaytgan)} tadan kam bo'lmasin (shuncha qaytgan)"})
            mahsulot = (b.get("mahsulot") or p["mahsulot"]).strip()
            togri, _aniq, taklif = db.tovar_match(mahsulot)
            if togri:
                mahsulot = togri
            elif db.get_sozlama("tovar_tekshir") == "1" and not (b.get("brov_kim") or "").strip():
                qo = (" Shulardan qaysi biri? " + " / ".join(taklif)) if taklif else ""
                return web.json_response({"ok": False, "xabar": f"«{mahsulot}» — bunday tovar yo'q.{qo}"})
            _bm = b.get("brov_miqdor")
            try:
                _bm = float(_bm) if _bm not in (None, "") else None
            except Exception:
                _bm = None
            db.update_partiya(pid, mahsulot,
                              miqdor, float(b.get("kunlik_narx")), b.get("sana") or p["chiqgan_sana"],
                              manzil=(b.get("manzil") or None),
                              brov_kim=(b.get("brov_kim") or None), brov_miqdor=_bm)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "xabar": f"Xato: {type(e).__name__}"})

    async def api_yetkazma_sana(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.yetkazma_sana_ozgartir(
                int(b.get("mijoz_id")), b.get("eski_sana"), b.get("yangi_sana")))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_partiya_toplam(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.partiyalarni_toplam_yangila(b.get("ozgarishlar") or []))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_qaytarish(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            pid = int(b.get("partiya_id"))
            miqdor = float(b.get("miqdor"))
            sana = (b.get("sana") or db.today_tk().isoformat())[:10]
            p = db.get_partiya_by_id(pid)
            if not p:
                return web.json_response({"ok": False, "xabar": "Partiya topilmadi"})
            qolgan = db.partiya_hisob(p)["qolgan"]
            if miqdor <= 0:
                return web.json_response({"ok": False, "xabar": "Soni noto'g'ri"})
            if miqdor > qolgan:
                miqdor = qolgan
            db.add_return(pid, miqdor, sana)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "xabar": f"Xato: {type(e).__name__}"})

    async def api_qaytarish_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            db.delete_return(int(b.get("return_id")))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_partiya_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            db.delete_partiya(int(b.get("partiya_id")))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_qoshimcha(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            mid = int(b.get("mijoz_id"))
            tur = b.get("tur")
            summa = float(b.get("summa"))
            if tur not in ("yolkira", "remont") or summa <= 0:
                return web.json_response({"ok": False, "xabar": "Noto'g'ri"})
            sana = (b.get("sana") or db.today_tk().isoformat())[:10]
            db.add_qoshimcha(mid, tur, summa, sana, None)
            d = db.mijoz_detail(mid)
            return web.json_response({"ok": True, "qolgan_qarz": d["qolgan_qarz"]})
        except Exception as e:
            return web.json_response({"ok": False, "xabar": f"Xato: {type(e).__name__}"})

    async def api_qoshimcha_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            db.delete_qoshimcha(int(b.get("id")))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_status(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            mid = int(b.get("mijoz_id"))
            st = b.get("status")
            if st not in ("faol", "nofaol", "sotuv"):
                return web.json_response({"ok": False, "xabar": "Noto'g'ri status"})
            db.set_status(mid, st)
            return web.json_response({"ok": True})
        except Exception as e:
            import traceback
            traceback.print_exc()
            return web.json_response({"ok": False, "xabar": f"Server xato: {type(e).__name__}: {str(e)[:150]}"})

    async def api_tolov_turi(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.set_tolov_turi(int(b.get("mijoz_id")), b.get("turi")))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_eslatma(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            mid = int(b.get("mijoz_id"))
            vada = (b.get("vada_sana") or "")[:10]
            if not vada:
                return web.json_response({"ok": False, "xabar": "Sana kerak"})
            db.add_eslatma(mid, vada, (b.get("izoh") or "").strip() or None)
            return web.json_response({"ok": True})
        except Exception as e:
            return web.json_response({"ok": False, "xabar": f"Xato: {type(e).__name__}"})

    async def api_eslatma_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            db.delete_eslatma(int(b.get("id")))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_sms(request):
        uid, err = check(request)
        if err:
            return err
        if not sms.is_configured():
            return web.json_response({"ok": False, "xabar": "SMS sozlanmagan (ESKIZ kalitlari yo'q)"})
        try:
            b = await request.json()
            mid = int(b.get("mijoz_id"))
            d = db.mijoz_detail(mid)
            if not d:
                return web.json_response({"ok": False, "xabar": "Mijoz topilmadi"})
            tel = (b.get("telefon") or d.get("telefon"))
            ok, info = await sms.send_sms(tel, sms.build_message(d))
            return web.json_response({"ok": ok, "xabar": info})
        except Exception as e:
            return web.json_response({"ok": False, "xabar": f"Xato: {type(e).__name__}"})

    async def api_brov(request):
        uid, err = check(request)
        if err:
            return err
        return web.json_response({"brovlar": db.brov_list()})

    async def api_brov_add(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            mid = b.get("mijoz_id")
            mid = int(mid) if mid not in (None, "") else None
            try:
                narx = float(b.get("kunlik_narx") or 0)
            except Exception:
                narx = 0
            res = db.brov_add(b.get("kim"), b.get("mahsulot"), b.get("miqdor"),
                              b.get("sana"), b.get("izoh"), mijoz_id=mid, kunlik_narx=narx)
            # Narx yozilgan bo'lsa — mijozga ham hisoblanadi (ombor tegilmaydi)
            if res.get("ok") and mid and narx > 0:
                try:
                    kim = (b.get("kim") or "").strip()
                    miq = float(b.get("miqdor"))
                    sana = (b.get("sana") or db.today_tk().isoformat())[:10]
                    mah = (b.get("mahsulot") or "").strip()
                    # Brovdan: nom o'zgartirilmaydi — foydalanuvchi nima yozsa o'sha
                    db.add_partiya(mid, mah, miq, narx, sana,
                                   brov_kim=kim, brov_miqdor=miq)
                    res["hisoblandi"] = True
                except Exception:
                    import traceback; traceback.print_exc()
            return web.json_response(res)
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_brov_ret(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.brov_return(int(b.get("id")), b.get("miqdor"), b.get("sana")))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_brov_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            db.brov_delete(int(b.get("id")))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_qayd(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.set_qayd(int(b.get("mijoz_id")), b.get("matn") or ""))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_qayd_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            db.qayd_delete(int(b.get("id")))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_tovarlar(request):
        uid, err = check(request)
        if err:
            return err
        return web.json_response({"tovarlar": db.tovar_barcha(),
                                  "birliklar": {n: db.tovar_birlik(n) for n in db.tovar_barcha()},
                                  "brovchilar": db.brov_kimlar(),
                                  "tekshir": db.get_sozlama("tovar_tekshir") == "1"})

    async def api_ombor(request):
        uid, err = check(request)
        if err:
            return err
        return web.json_response({"mahsulotlar": db.ombor_list()})

    async def api_ombor_move(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            res = db.ombor_move(b.get("id"), b.get("tur"), b.get("miqdor"))
            return web.json_response(res)
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_ombor_total(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.ombor_set_total(b.get("id"), b.get("total")))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_ombor_add(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.ombor_add(b.get("name"), b.get("total") or 0))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_ombor_del(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            db.ombor_delete(b.get("id"))
            return web.json_response({"ok": True})
        except Exception:
            return web.json_response({"ok": False}, status=400)

    async def api_ombor_rename(request):
        uid, err = check(request)
        if err:
            return err
        try:
            b = await request.json()
            return web.json_response(db.ombor_rename(b.get("id"), b.get("name")))
        except Exception as e:
            return web.json_response({"ok": False, "xato": f"Xato: {type(e).__name__}"})

    async def api_ombor_tarix(request):
        uid, err = check(request)
        if err:
            return err
        pid = request.query.get("id") or None
        return web.json_response({"tarix": db.ombor_history(pid, 200)})

    app = web.Application(client_max_size=25 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_post("/api/login", api_login)
    app.router.add_get("/m/{token}", mijoz_sahifa)
    app.router.add_get("/v/{token}", haydovchi_sahifa)
    app.router.add_get("/api/v/{token}", api_vazifa_ochiq)
    app.router.add_post("/api/v_holat", api_v_holat)
    app.router.add_post("/api/v_bajar", api_v_bajar)
    app.router.add_get("/rasm/{nom}", rasm_fayl)
    app.router.add_get("/api/vazifalar", api_vazifalar)
    app.router.add_get("/api/haydovchilar", api_haydovchilar)
    app.router.add_post("/api/vazifa_qosh", api_vazifa_qosh)
    app.router.add_post("/api/vazifa_ochir", api_vazifa_ochir)
    app.router.add_get("/api/m/{token}", api_mijoz_ochiq)
    app.router.add_get("/manifest.json", manifest)
    app.router.add_get("/sw.js", sw_js)
    app.router.add_get("/{nom:icon-\\d+\\.png}", icon)
    app.router.add_get("/logo.png", icon)
    app.router.add_get("/api/mijozlar", api_mijozlar)
    app.router.add_get("/api/mijoz", api_mijoz)
    app.router.add_post("/api/mijoz_qosh", api_mijoz_qosh)
    app.router.add_post("/api/mijoz_edit", api_mijoz_edit)
    app.router.add_post("/api/qoshish", api_qoshish)
    app.router.add_post("/api/qator", api_qator)
    app.router.add_post("/api/qoshish_audio", api_qoshish_audio)
    app.router.add_post("/api/ochirish", api_ochirish)
    app.router.add_post("/api/adres", api_adres)
    app.router.add_post("/api/mijoz_loc", api_mijoz_loc)
    app.router.add_post("/api/tolov", api_tolov)
    app.router.add_post("/api/tolov_del", api_tolov_del)
    app.router.add_post("/api/partiya_edit", api_partiya_edit)
    app.router.add_post("/api/yetkazma_sana", api_yetkazma_sana)
    app.router.add_post("/api/partiya_toplam", api_partiya_toplam)
    app.router.add_post("/api/qaytarish", api_qaytarish)
    app.router.add_post("/api/qaytarish_del", api_qaytarish_del)
    app.router.add_post("/api/partiya_del", api_partiya_del)
    app.router.add_post("/api/qoshimcha", api_qoshimcha)
    app.router.add_post("/api/qoshimcha_del", api_qoshimcha_del)
    app.router.add_post("/api/status", api_status)
    app.router.add_post("/api/tolov_turi", api_tolov_turi)
    app.router.add_post("/api/eslatma", api_eslatma)
    app.router.add_post("/api/eslatma_del", api_eslatma_del)
    app.router.add_post("/api/sms", api_sms)
    app.router.add_post("/api/qayd", api_qayd)
    app.router.add_post("/api/qayd_del", api_qayd_del)
    app.router.add_get("/api/brov", api_brov)
    app.router.add_post("/api/brov_add", api_brov_add)
    app.router.add_post("/api/brov_ret", api_brov_ret)
    app.router.add_post("/api/brov_del", api_brov_del)
    app.router.add_get("/api/tovarlar", api_tovarlar)
    app.router.add_get("/api/ombor", api_ombor)
    app.router.add_post("/api/ombor_move", api_ombor_move)
    app.router.add_post("/api/ombor_total", api_ombor_total)
    app.router.add_post("/api/ombor_add", api_ombor_add)
    app.router.add_post("/api/ombor_del", api_ombor_del)
    app.router.add_post("/api/ombor_rename", api_ombor_rename)
    app.router.add_get("/api/ombor_tarix", api_ombor_tarix)
    return app
