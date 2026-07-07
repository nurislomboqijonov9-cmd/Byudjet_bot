"""Mini App veb-serveri (aiohttp).

- GET  /                 -> Mini App sahifasi
- GET  /api/mijozlar     -> barcha mijozlar va qarzlari
- GET  /api/mijoz?ism=.. -> bitta mijoz tafsiloti
- POST /api/ochirish     -> mijoz yacheykasini o'chirish

Xavfsizlik: Telegram initData tekshiriladi + (agar berilgan bo'lsa) ruxsat ro'yxati.
"""
import os
import json
import hmac
import hashlib
from urllib.parse import parse_qsl
from pathlib import Path
from aiohttp import web

import db

INDEX = Path(__file__).parent / "index.html"


def validate_init_data(init_data: str, bot_token: str):
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


def make_web_app(bot_token: str, allowed=None) -> web.Application:

    def check(request):
        init = request.headers.get("X-Init-Data", "")
        uid = validate_init_data(init, bot_token)
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
        ism = request.query.get("ism", "")
        if not ism:
            return web.json_response({"xato": "ism kerak"}, status=400)
        return web.json_response(db.mijoz_detail(ism))

    async def api_ochirish(request):
        uid, err = check(request)
        if err:
            return err
        try:
            body = await request.json()
        except Exception:
            body = {}
        ism = (body or {}).get("mijoz")
        if not ism:
            return web.json_response({"xato": "mijoz kerak"}, status=400)
        db.delete_mijoz(ism)
        return web.json_response({"ok": True})

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/mijozlar", api_mijozlar)
    app.router.add_get("/api/mijoz", api_mijoz)
    app.router.add_post("/api/ochirish", api_ochirish)
    return app
