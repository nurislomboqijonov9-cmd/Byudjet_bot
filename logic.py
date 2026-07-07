"""Amalni bajarish mantig'i (bot ham, Mini App ham ishlatadi).

apply() — MA'LUM mijoz_id uchun chiqish/qaytarishni bajaradi.
Mijozni topish (ism -> id) chaqiruvchi tomonda hal qilinadi.
"""
import db
from datetime import date


def _sana(t):
    return (getattr(t, "sana", None) or date.today().isoformat())[:10]


def apply(mijoz_id, t):
    """t — ai.IjaraAmal. Natija: dict (ok, ...)."""
    m = db.get_mijoz(mijoz_id)
    if not m:
        return {"ok": False, "xato": "Mijoz topilmadi"}

    amal = getattr(t, "amal", None)
    amal = amal.value if hasattr(amal, "value") else amal

    # ----- CHIQISH -----
    if amal == "chiqish":
        if not (t.mahsulot and t.miqdor and t.kunlik_narx):
            return {"ok": False, "xato": "Chiqish uchun mahsulot, soni va kunlik narx kerak"}
        pid, raqam = db.add_partiya(mijoz_id, t.mahsulot, t.miqdor, t.kunlik_narx, _sana(t))
        return {
            "ok": True, "amal": "chiqish", "mijoz": m["ism"], "mijoz_id": mijoz_id,
            "partiya_id": pid, "raqam": raqam, "mahsulot": t.mahsulot,
            "miqdor": t.miqdor, "kunlik_narx": t.kunlik_narx, "sana": _sana(t),
        }

    # ----- QAYTARISH -----
    if amal == "qaytarish":
        partiyalar = db.partiyalar_of(mijoz_id)
        if not partiyalar:
            return {"ok": False, "xato": f"{m['ism']}da partiya yo'q"}
        p = None
        if getattr(t, "partiya", None):
            p = db.get_partiya(mijoz_id, t.partiya)
            if not p:
                return {"ok": False, "xato": f"{t.partiya}-partiya topilmadi"}
        else:
            aktiv = [x for x in partiyalar if db.partiya_hisob(x)["qolgan"] > 0]
            if len(aktiv) == 1:
                p = aktiv[0]
            elif not aktiv:
                return {"ok": False, "xato": f"{m['ism']}da qaytariladigan mahsulot yo'q"}
            else:
                ro = ", ".join(f"{x['partiya_raqam']}-{x['mahsulot']}" for x in aktiv)
                return {"ok": False, "xato": f"Qaysi partiya? ({ro}) — raqamini ayting"}

        h = db.partiya_hisob(p)
        qolgan = h["qolgan"]
        if qolgan <= 0:
            return {"ok": False, "xato": f"{p['partiya_raqam']}-partiya allaqachon to'liq qaytarilgan"}
        qty = qolgan if getattr(t, "hammasi", False) else t.miqdor
        if not qty:
            return {"ok": False, "xato": "Nechta qaytarganini ayting"}
        ortdi = False
        if qty > qolgan:
            qty = qolgan
            ortdi = True
        rid = db.add_return(p["id"], qty, _sana(t))
        h2 = db.partiya_hisob(p)
        d = db.mijoz_detail(mijoz_id)
        return {
            "ok": True, "amal": "qaytarish", "mijoz": m["ism"], "mijoz_id": mijoz_id,
            "return_id": rid, "partiya_raqam": p["partiya_raqam"], "mahsulot": p["mahsulot"],
            "qty": qty, "qolgan": h2["qolgan"], "partiya_narx": h2["narx"],
            "jami": d["jami"], "ortdi": ortdi,
        }

    return {"ok": False, "xato": "Tushunolmadim"}
