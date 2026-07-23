"""Eskiz.uz orqali SMS yuborish (qo'shimcha kutubxonasiz, aiohttp bilan)."""
import os
import re
import time
import logging
import aiohttp

log = logging.getLogger("arenda.sms")

BASE = os.getenv("ESKIZ_BASE", "https://notify.eskiz.uz/api")

_token = None
_token_ts = 0.0
_TOKEN_TTL = 25 * 24 * 3600  # ~25 kun (Eskiz tokeni ~30 kun)


def is_configured():
    return bool(os.getenv("ESKIZ_EMAIL") and os.getenv("ESKIZ_PASSWORD"))


def normalize_phone(s):
    """Har xil formatdagi raqamni 998XXXXXXXXX ga keltiradi. Bo'lmasa None."""
    if not s:
        return None
    d = re.sub(r"\D", "", str(s))
    # bir nechta raqam yopishib qolgan bo'lsa — birinchi 12/9 xonasini olamiz
    if len(d) >= 12 and d.startswith("998"):
        d = d[:12]
    elif len(d) == 9:
        d = "998" + d
    elif len(d) == 12 and d.startswith("998"):
        pass
    else:
        # 9 xonalik qismini topishga urinish
        m = re.search(r"(?:998)?(\d{9})", d)
        if not m:
            return None
        d = "998" + m.group(1)
    return d if (len(d) == 12 and d.startswith("998")) else None


def build_message(detail):
    """Tasdiqlangan shablon asosida matn. Kalitlar env'da sozlanadi."""
    shablon = os.getenv(
        "SMS_MATN",
        "Hurmatli mijoz! {firma} dan ijara qarzingiz {summa} so'm. "
        "Iltimos, to'lovni amalga oshiring. Aloqa: {tel}",
    )
    firma = os.getenv("FIRMA_NOM", "Ustaxona")
    tel = os.getenv("FIRMA_TEL", "")
    summa = f"{round(detail.get('qolgan_qarz', 0)):,}".replace(",", " ")
    return shablon.format(firma=firma, summa=summa, tel=tel, ism=detail.get("mijoz", ""))


async def _login(session):
    global _token, _token_ts
    email = os.getenv("ESKIZ_EMAIL")
    pw = os.getenv("ESKIZ_PASSWORD")
    data = aiohttp.FormData()
    data.add_field("email", email)
    data.add_field("password", pw)
    async with session.post(f"{BASE}/auth/login", data=data, timeout=aiohttp.ClientTimeout(total=20)) as r:
        j = await r.json(content_type=None)
    tok = (j.get("data") or {}).get("token") if isinstance(j, dict) else None
    if not tok:
        raise RuntimeError(f"Eskiz login xato: {str(j)[:200]}")
    _token = tok
    _token_ts = time.time()
    return tok


async def _get_token(session, force=False):
    if force or not _token or (time.time() - _token_ts) > _TOKEN_TTL:
        return await _login(session)
    return _token


async def send_sms(phone, message):
    """(ok: bool, info: str) qaytaradi."""
    if not is_configured():
        return False, "SMS sozlanmagan (ESKIZ_EMAIL/PASSWORD yo'q)"
    mob = normalize_phone(phone)
    if not mob:
        return False, "Telefon raqami noto'g'ri"
    sender = os.getenv("ESKIZ_FROM", "4546")
    try:
        async with aiohttp.ClientSession() as session:
            token = await _get_token(session)

            async def _send(tok):
                data = aiohttp.FormData()
                data.add_field("mobile_phone", mob)
                data.add_field("message", message)
                data.add_field("from", sender)
                headers = {"Authorization": f"Bearer {tok}"}
                async with session.post(f"{BASE}/message/sms/send", data=data, headers=headers,
                                        timeout=aiohttp.ClientTimeout(total=20)) as r:
                    return r.status, await r.json(content_type=None)

            status, j = await _send(token)
            if status in (401, 403):  # token eskirgan bo'lsa — qayta login
                token = await _get_token(session, force=True)
                status, j = await _send(token)

        st = (j or {}).get("status") if isinstance(j, dict) else None
        if status == 200 and st in ("waiting", "success", "sent", "ok", None) and not (isinstance(j, dict) and j.get("error")):
            return True, "Yuborildi"
        return False, str(j)[:200]
    except Exception as e:
        log.exception("SMS yuborishda xatolik")
        return False, f"{type(e).__name__}: {str(e)[:150]}"


def sample_template():
    """Moderatsiyaga yuboriladigan shablon (namuna summa bilan)."""
    shablon = os.getenv(
        "SMS_MATN",
        "Hurmatli mijoz! {firma} dan ijara qarzingiz {summa} som. "
        "Iltimos tolovni amalga oshiring. Aloqa: {tel}",
    )
    return shablon.format(firma=os.getenv("FIRMA_NOM", "Ustaxona"),
                          summa="100000", tel=os.getenv("FIRMA_TEL", ""), ism="mijoz")


async def submit_template(text):
    """Shablonni Eskiz moderatsiyasiga yuboradi (POST /user/template)."""
    if not is_configured():
        return (False, "SMS sozlanmagan")
    try:
        async with aiohttp.ClientSession() as session:
            async def _do(tok):
                data = aiohttp.FormData()
                data.add_field("template", text)
                async with session.post(f"{BASE}/user/template", data=data,
                                        headers={"Authorization": f"Bearer {tok}"},
                                        timeout=aiohttp.ClientTimeout(total=20)) as r:
                    return r.status, await r.json(content_type=None)
            token = await _get_token(session)
            status, j = await _do(token)
            if status in (401, 403):
                token = await _get_token(session, force=True)
                status, j = await _do(token)
        ok = status in (200, 201) and not (isinstance(j, dict) and j.get("status") == "error")
        return (ok, str(j)[:250])
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:150]}")


async def list_templates():
    """Shablonlar va ularning moderatsiya holatini oladi (GET /user/templates)."""
    if not is_configured():
        return (False, "SMS sozlanmagan")
    try:
        async with aiohttp.ClientSession() as session:
            async def _do(tok):
                async with session.get(f"{BASE}/user/templates",
                                       headers={"Authorization": f"Bearer {tok}"},
                                       timeout=aiohttp.ClientTimeout(total=20)) as r:
                    return r.status, await r.json(content_type=None)
            token = await _get_token(session)
            status, j = await _do(token)
            if status in (401, 403):
                token = await _get_token(session, force=True)
                status, j = await _do(token)
        if status == 200:
            if isinstance(j, dict):
                return (True, j.get("result") or j.get("data") or j.get("templates") or [])
            if isinstance(j, list):
                return (True, j)
        return (False, str(j)[:250])
    except Exception as e:
        return (False, f"{type(e).__name__}: {str(e)[:150]}")
