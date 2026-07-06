"""Valyuta kursi (dollar/yevro/rubl -> so'm).

Jonli kursni internetdan oladi va bir necha soat eslab turadi.
Ololmasa — zaxira (env yoki ichki) kursdan foydalanadi.
"""
import os
import json
import time
import urllib.request

# Zaxira kurslar (env orqali o'zgartirса bo'ladi). 1 birlik = necha so'm.
DEFAULTS = {
    "dollar": float(os.getenv("USD_UZS", "12000")),
    "yevro": float(os.getenv("EUR_UZS", "13800")),
    "rubl": float(os.getenv("RUB_UZS", "150")),
}

_cache = {}
_cache_time = 0
_TTL = 6 * 3600  # 6 soat


def _fetch():
    """open.er-api.com dan USD asosidagi kurslarni oladi."""
    url = "https://open.er-api.com/v6/latest/USD"
    req = urllib.request.Request(url, headers={"User-Agent": "byudjet-bot"})
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read().decode())
    rates = data.get("rates", {})
    uzs = rates.get("UZS")
    if not uzs:
        raise ValueError("UZS kursi topilmadi")
    res = {"dollar": float(uzs)}
    if rates.get("EUR"):
        res["yevro"] = float(uzs) / float(rates["EUR"])
    if rates.get("RUB"):
        res["rubl"] = float(uzs) / float(rates["RUB"])
    return res


def get_rate(valyuta: str) -> float:
    """1 birlik valyuta necha so'm ekanini qaytaradi."""
    global _cache, _cache_time
    v = (valyuta or "").lower()
    if v in ("som", "", None):
        return 1.0
    now = time.time()
    if now - _cache_time > _TTL or not _cache:
        try:
            _cache = _fetch()
            _cache_time = now
        except Exception:
            _cache = {}  # zaxiraga tushamiz
    return _cache.get(v) or DEFAULTS.get(v, 1.0)
