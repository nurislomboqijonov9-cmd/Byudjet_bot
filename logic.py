"""Amalni bajarish mantig'i (bot ham, Mini App ham ishlatadi).

apply() — MA'LUM mijoz_id uchun chiqish/qaytarishni bajaradi.
Mijozni topish (ism -> id) chaqiruvchi tomonda hal qilinadi.
"""
import re
import db
from datetime import date


def _sana(t):
    return (getattr(t, "sana", None) or date.today().isoformat())[:10]


def _norm(s):
    """Mahsulot nomini solishtirish uchun bir xillaydi.
    'stoyka 4 m', 'stoyka 4m', 'stoyka 4m lik', 'stoyka 4 metrlik' -> 'stoyka 4m'.
    Lekin 'lesa' != 'lesa 5m', '4 m' != '4.5 m' (raqam saqlanadi)."""
    s = (s or "").strip().lower()
    s = re.sub(r"(\d[\d.,]*)\s*m(?:\s*lik|etrlik|etrli|etr|eter|lik)?\b", r"\1m", s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def apply(mijoz_id, t):
    """t — ai.IjaraAmal. Natija: dict (ok, ...)."""
    m = db.get_mijoz(mijoz_id)
    if not m:
        return {"ok": False, "xato": "Mijoz topilmadi"}

    amal = getattr(t, "amal", None)
    amal = amal.value if hasattr(amal, "value") else amal

    # ----- CHIQISH -----
    if amal == "chiqish":
        if not t.mahsulot or not t.miqdor or t.miqdor <= 0 or t.kunlik_narx is None or t.kunlik_narx < 0:
            return {"ok": False, "xato": "Chiqish uchun mahsulot, soni va kunlik narx kerak (tekin bo'lsa 0)"}
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

        # 1) Aniq partiya raqami aytilgan bo'lsa — o'sha partiyadan
        if getattr(t, "partiya", None):
            p = db.get_partiya(mijoz_id, t.partiya)
            if not p:
                return {"ok": False, "xato": f"{t.partiya}-partiya topilmadi"}
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
                "jami": d["jami"], "qolgan_qarz": d["qolgan_qarz"], "ortdi": ortdi,
            }

        # 2) Mahsulot bo'yicha umumiy qaytarish (eng eski partiyadan boshlab)
        aktiv = [(p, db.partiya_hisob(p)) for p in partiyalar]
        aktiv = [(p, h) for p, h in aktiv if h["qolgan"] > 0]
        if not aktiv:
            return {"ok": False, "xato": f"{m['ism']}da qaytariladigan mahsulot yo'q"}

        mahsulot = _norm(getattr(t, "mahsulot", None))
        prods = {}
        for p, h in aktiv:
            prods.setdefault(_norm(p["mahsulot"]), []).append((p, h))

        if mahsulot:
            target = prods.get(mahsulot)
            if not target:
                bor = ", ".join(sorted(set(p["mahsulot"] for p, h in aktiv)))
                return {"ok": False, "xato": f"«{getattr(t,'mahsulot','')}» aniq topilmadi. Aniq nomini yozing. Mavjud: {bor}"}
        elif len(prods) == 1:
            target = list(prods.values())[0]
        else:
            bor = ", ".join(sorted(set(p["mahsulot"] for p, h in aktiv)))
            return {"ok": False, "xato": f"Qaysi mahsulot qaytdi? ({bor})"}

        target = list(target)
        target.sort(key=lambda x: x[0]["partiya_raqam"])  # eng eski chiqishdan

        # Narx aytilgan bo'lsa: avval aynan shu narxdagilardan (eng eskisidan),
        # yetmasa qolganini eng eski chiqishlardan (boshqa narxdagilardan) ayiramiz.
        narx = getattr(t, "kunlik_narx", None)
        if narx:
            priced = [(p, h) for p, h in target if abs((p["kunlik_narx"] or 0) - narx) < 0.5]
            boshqa = [(p, h) for p, h in target if abs((p["kunlik_narx"] or 0) - narx) >= 0.5]
            if not priced:
                nx = ", ".join(sorted(set(str(int(p["kunlik_narx"])) for p, h in target)))
                return {"ok": False, "xato": f"«{target[0][0]['mahsulot']}» {int(narx)} so'mdan topilmadi. Narxlar: {nx}"}
            order = priced + boshqa               # narxdagilar oldin, keyin eng eskilar
            cap_all = sum(h["qolgan"] for p, h in priced)   # «hammasi» narx bilan = faqat shu narx
        else:
            order = target
            cap_all = sum(h["qolgan"] for p, h in order)

        cap_max = sum(h["qolgan"] for p, h in order)  # raqam berilsa — fallback bilan yetadigan maksimum
        qty = cap_all if getattr(t, "hammasi", False) else t.miqdor
        if not qty:
            return {"ok": False, "xato": "Nechta qaytarganini ayting"}
        kam = False
        if qty > cap_max:
            qty = cap_max
            kam = True

        remaining = qty
        sana = _sana(t)
        return_ids, taqsim = [], []
        for p, h in order:
            if remaining <= 0:
                break
            take = min(remaining, h["qolgan"])
            if take <= 0:
                continue
            rid = db.add_return(p["id"], take, sana)
            return_ids.append(rid)
            taqsim.append({"partiya_raqam": p["partiya_raqam"], "qty": take})
            remaining -= take

        d = db.mijoz_detail(mijoz_id)
        prodname = target[0][0]["mahsulot"]
        return {
            "ok": True, "amal": "qaytarish", "aggregate": True, "mijoz": m["ism"], "mijoz_id": mijoz_id,
            "mahsulot": prodname, "qty": qty, "return_ids": return_ids, "taqsim": taqsim,
            "jami": d["jami"], "qolgan_qarz": d["qolgan_qarz"], "kam": kam,
        }

    # ----- TO'LOV / PREDOPLATA -----
    if amal == "tolov":
        summa = getattr(t, "summa", None)
        kun = getattr(t, "kun", None)
        izoh = None
        if not summa and kun:
            rate = db.daily_rate(mijoz_id)
            if rate <= 0:
                return {"ok": False, "xato": "Hozir chiqgan mahsulot yo'q — kunni pulga aylantirib bo'lmaydi. Pul summasini ayting."}
            summa = rate * kun
            izoh = f"{kun} kunlik"
        if not summa or summa <= 0:
            return {"ok": False, "xato": "To'lov summasini ayting"}
        tid = db.add_tolov(mijoz_id, summa, _sana(t), izoh)
        d = db.mijoz_detail(mijoz_id)
        return {
            "ok": True, "amal": "tolov", "mijoz": m["ism"], "mijoz_id": mijoz_id,
            "tolov_id": tid, "summa": summa, "izoh": izoh,
            "tolangan": d["tolangan"], "qolgan_qarz": d["qolgan_qarz"],
        }

    # ----- ESLATMA (to'lov va'dasi) -----
    if amal == "eslatma":
        vada = getattr(t, "sana", None)
        if not vada:
            return {"ok": False, "xato": "Qaysi kunga va'da qildi? Sanani ayting."}
        izoh = getattr(t, "izoh", None) or "to'lov va'da qildi"
        eid = db.add_eslatma(mijoz_id, vada, izoh)
        return {"ok": True, "amal": "eslatma", "mijoz": m["ism"], "mijoz_id": mijoz_id,
                "eslatma_id": eid, "vada_sana": str(vada)[:10], "izoh": izoh}

    # ----- MA'LUMOT (mijoz haqida) -----
    if amal == "malumot":
        return {"ok": True, "amal": "malumot", "mijoz_id": mijoz_id, "detail": db.mijoz_detail(mijoz_id)}

    return {"ok": False, "xato": "Tushunolmadim"}
