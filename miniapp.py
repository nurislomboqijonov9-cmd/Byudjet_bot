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

INDEX = Path(__file__).parent / "index.html"


def _som(n):
    return f"{round(n):,}".replace(",", " ")


def _son(n):
    n = float(n)
    return str(int(n)) if n == int(n) else str(n)


def web_msg(res):
    if not res.get("ok"):
        return res.get("xato", "Xatolik")
    if res["amal"] == "chiqish":
        return (f"✅ {res['raqam']}-partiya ochildi: {_son(res['miqdor'])} ta {res['mahsulot']}, "
                f"kuniga {_som(res['kunlik_narx'])} so'm")
    return (f"✅ {res['partiya_raqam']}-partiya: {_son(res['qty'])} ta {res['mahsulot']} qaytdi. "
            f"Qolgan: {_son(res['qolgan'])} ta · Jami: {_som(res['jami'])} so'm")


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


def make_web_app(bot_token, allowed=None):

    def check(request):
        uid = validate_init_data(request.headers.get("X-Init-Data", ""), bot_token)
        if uid is None:
            dbg = os.getenv("DEBUG_USER_ID")
            uid = int(dbg) if dbg else None
        if uid is None:
            return None, web.json_response({"xato": "Telegram ichida oching"}, status=401)
        if allowed and uid not in allowed:
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
            t = ai.from_text(matn)
            res = logic.apply(mid, t)
            return web.json_response({"ok": res.get("ok", False), "xabar": web_msg(res)})
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
            t = ai.from_audio(audio, mime_type=mime)
            res = logic.apply(mid, t)
            return web.json_response({"ok": res.get("ok", False), "xabar": web_msg(res)})
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
            if summa <= 0:
                return web.json_response({"ok": False, "xabar": "Summa noto'g'ri"})
            db.add_tolov(mid, summa, db.today_tk().isoformat(), None)
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
            db.update_partiya(pid, (b.get("mahsulot") or p["mahsulot"]).strip(),
                              miqdor, float(b.get("kunlik_narx")), b.get("sana") or p["chiqgan_sana"])
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

    app = web.Application(client_max_size=25 * 1024 * 1024)
    app.router.add_get("/", index)
    app.router.add_get("/api/mijozlar", api_mijozlar)
    app.router.add_get("/api/mijoz", api_mijoz)
    app.router.add_post("/api/mijoz_qosh", api_mijoz_qosh)
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
    return app
