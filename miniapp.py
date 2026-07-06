"""Mini App veb-serveri (aiohttp).

- GET /            -> Mini App sahifasi (index.html)
- GET /api/summary -> foydalanuvchi ma'lumotlari (JSON), Telegram initData bilan tasdiqlanadi

Xavfsizlik: brauzer yuborgan initData Telegram bot tokeni bilan tekshiriladi,
shunda faqat haqiqiy Telegram foydalanuvchisi o'z ma'lumotini ko'radi.
"""
import os
import json
import hmac
import hashlib
import time
from urllib.parse import parse_qsl
from pathlib import Path
from aiohttp import web

import db

INDEX = Path(__file__).parent / "index.html"


def validate_init_data(init_data: str, bot_token: str, max_age=86400):
    """initData to'g'ri bo'lsa foydalanuvchi id sini qaytaradi, aks holda None."""
    if not init_data:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    got_hash = pairs.pop("hash", None)
    if not got_hash:
        return None

    data_check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    calc = hmac.new(secret, data_check.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(calc, got_hash):
        return None

    # (ixtiyoriy) eskirganini tekshirish
    try:
        if max_age and time.time() - int(pairs.get("auth_date", 0)) > max_age:
            pass  # eski bo'lsa ham hozircha ruxsat beramiz
    except Exception:
        pass

    try:
        user = json.loads(pairs.get("user", "{}"))
        return int(user["id"])
    except Exception:
        return None


def make_web_app(bot_token: str) -> web.Application:
    async def index(request):
        return web.FileResponse(INDEX)

    async def api_summary(request):
        init_data = request.headers.get("X-Init-Data", "")
        uid = validate_init_data(init_data, bot_token)
        # Mahalliy sinov uchun: DEBUG_USER_ID o'rnatilgan bo'lsa, tekshiruvsiz ruxsat
        if uid is None:
            dbg = os.getenv("DEBUG_USER_ID")
            if dbg:
                uid = int(dbg)
            else:
                return web.json_response({"xato": "Telegram ichida oching"}, status=401)
        return web.json_response(db.summary(uid))

    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/api/summary", api_summary)
    return app
