"""Mini App veb-serveri (aiohttp)."""
import os
import json
import hmac
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
            dbg = os.getenv("DEBUG_USER_ID")
            uid = int(dbg) if dbg else None
        if uid is None:
            return None, web.json_response({"xato": "Telegram ichida oching"}, status=401)
        if not db.is_allowed(uid):
            return None, web.json_response({"xato": "Ruxsat yo'q"}, status=403)
        return uid, None

    async def index(request):
        return web.FileResponse(INDEX)

    async def api_mijozlar(request):
        uid, err = check(request)
        if err:
            return err
        return web.json_response({"mijozlar": db.mijozlar()})

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
        mid = db.add_mijoz(ism, body.get("telefon"))
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
            togri, _aniq = db.ombor_match_name(mahsulot)
            if togri:
                mahsulot = togri
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
            db.add_qoshimcha(mid, tur, summa, db.today_tk().isoformat(), None)
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
            return web.json_response(db.qayd_add(int(b.get("mijoz_id")), b.get("matn"), b.get("sana")))
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
    app.router.add_get("/api/mijozlar", api_mijozlar)
    app.router.add_get("/api/mijoz", api_mijoz)
    app.router.add_post("/api/mijoz_qosh", api_mijoz_qosh)
    app.router.add_post("/api/mijoz_edit", api_mijoz_edit)
    app.router.add_post("/api/qoshish", api_qoshish)
    app.router.add_post("/api/qoshish_audio", api_qoshish_audio)
    app.router.add_post("/api/ochirish", api_ochirish)
    app.router.add_post("/api/adres", api_adres)
    app.router.add_post("/api/tolov", api_tolov)
    app.router.add_post("/api/tolov_del", api_tolov_del)
    app.router.add_post("/api/partiya_edit", api_partiya_edit)
    app.router.add_post("/api/qaytarish", api_qaytarish)
    app.router.add_post("/api/qaytarish_del", api_qaytarish_del)
    app.router.add_post("/api/partiya_del", api_partiya_del)
    app.router.add_post("/api/qoshimcha", api_qoshimcha)
    app.router.add_post("/api/qoshimcha_del", api_qoshimcha_del)
    app.router.add_post("/api/status", api_status)
    app.router.add_post("/api/eslatma", api_eslatma)
    app.router.add_post("/api/eslatma_del", api_eslatma_del)
    app.router.add_post("/api/sms", api_sms)
    app.router.add_post("/api/qayd", api_qayd)
    app.router.add_post("/api/qayd_del", api_qayd_del)
    app.router.add_get("/api/brov", api_brov)
    app.router.add_post("/api/brov_add", api_brov_add)
    app.router.add_post("/api/brov_ret", api_brov_ret)
    app.router.add_post("/api/brov_del", api_brov_del)
    app.router.add_get("/api/ombor", api_ombor)
    app.router.add_post("/api/ombor_move", api_ombor_move)
    app.router.add_post("/api/ombor_total", api_ombor_total)
    app.router.add_post("/api/ombor_add", api_ombor_add)
    app.router.add_post("/api/ombor_del", api_ombor_del)
    app.router.add_post("/api/ombor_rename", api_ombor_rename)
    app.router.add_get("/api/ombor_tarix", api_ombor_tarix)
    return app
