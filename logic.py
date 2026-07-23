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


_BIRLIK_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(kom(?:plekt)?|ta|dona)\b", re.I)


def _yozilgan_birlik(t, miqdor):
    """Matnda shu miqdor yonida 'kom' yoki 'ta' yozilganmi — o'shani qaytaradi."""
    matn = " ".join(str(getattr(t, k, "") or "") for k in ("transkript", "izoh"))
    if not matn or miqdor is None:
        return None
    topilgan = None
    for m in _BIRLIK_RE.finditer(matn):
        try:
            son = float(m.group(1).replace(",", "."))
        except Exception:
            continue
        b = m.group(2).lower()
        b = "kom" if b.startswith("kom") else "ta"
        if abs(son - float(miqdor)) < 1e-6:
            return b
        topilgan = topilgan or b
    return topilgan


def _oz_ulush(p, qty):
    """Partiyadan qaytgan qty ning nechtasi bizning omborimizniki (brovdan olingani hisobmas)."""
    try:
        m = float(p.get("miqdor") or 0)
        b = float(p.get("brov_miqdor") or 0)
    except Exception:
        return qty
    if m <= 0 or b <= 0:
        return qty
    b = min(b, m)
    return max(0.0, float(qty) * (m - b) / m)


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
        # Tovar nomi: ombordagi eng yaqin nomga avtomat to'g'rilanadi
        # (brovdan olingan tovarga tegilmaydi — u tashqi tovar)
        tuzatildi = None
        if db.get_sozlama("tovar_tekshir") == "1" and not (getattr(t, "brov_kim", None) or "").strip():
            togri, aniq, taklif = db.tovar_match(t.mahsulot)
            if togri is None:
                if taklif:
                    xat = (f"«{t.mahsulot}» — bunday tovar yo'q.\n"
                           f"Shulardan qaysi biri?  {' / '.join(taklif)}\n\n"
                           "To'g'ri nomini yozib qayta yuboring.")
                else:
                    xat = (f"«{t.mahsulot}» — bunday tovar yo'q. To'g'ri yozing.\n\n"
                           "Tovarlar: " + ", ".join(db.tovar_barcha()))
                return {"ok": False, "xato": xat}
            if not aniq:
                tuzatildi = (t.mahsulot, togri)
            t.mahsulot = togri
        brov_kim = getattr(t, "brov_kim", None)
        brov_miqdor = getattr(t, "brov_miqdor", None)
        # Birlik: yozilgan bo'lsa o'sha, yozilmasa — tovarning asosiy birligi (kom bo'lsa komplekt)
        _bir = _yozilgan_birlik(t, t.miqdor) or db.tovar_birlik(t.mahsulot)
        pid, raqam = db.add_partiya(mijoz_id, t.mahsulot, t.miqdor, t.kunlik_narx, _sana(t),
                                    manzil=getattr(t, "manzil", None),
                                    brov_kim=brov_kim, brov_miqdor=brov_miqdor, birlik=_bir)
        # Ombordan faqat o'zimizniki chiqadi (brovdan olingani o'z omborimizdan emas)
        _brov = float(brov_miqdor or (t.miqdor if (brov_kim or "").strip() else 0) or 0)
        _oz = max(0.0, float(t.miqdor) - _brov)
        if _oz > 0:
            _k = db.ombor_koeff(t.mahsulot, _bir)
            db.ombor_apply_by_name(t.mahsulot, "out", _oz * _k)
        return {
            "ok": True, "amal": "chiqish", "mijoz": m["ism"], "mijoz_id": mijoz_id,
            "partiya_id": pid, "raqam": raqam, "mahsulot": t.mahsulot,
            "miqdor": t.miqdor, "kunlik_narx": t.kunlik_narx, "sana": _sana(t),
            "tuzatildi": tuzatildi,
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
            qty = qolgan if getattr(t, "hammasi", False) else t.miqdor
            if not qty:
                return {"ok": False, "xato": "Nechta qaytarganini ayting"}
            ortdi = qty > qolgan   # ortiqcha bo'lsa minusga ketadi, cheklanmaydi
            rid = db.add_return(p["id"], qty, _sana(t))
            _oz_ret = _oz_ulush(p, qty)   # brovdan olingani omborga qo'shilmaydi
            if _oz_ret > 0:
                _k = db.ombor_koeff(p["mahsulot"], _yozilgan_birlik(t, qty))
                db.ombor_apply_by_name(p["mahsulot"], "ret", _oz_ret * _k)
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

        cap_max = sum(h["qolgan"] for p, h in order)
        qty = cap_all if getattr(t, "hammasi", False) else t.miqdor
        if not qty:
            return {"ok": False, "xato": "Nechta qaytarganini ayting"}
        kam = qty > cap_max   # ortiqcha bo'lsa minusga ketadi, cheklanmaydi

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
        # Ortib qolgani (chiqmagan tovar qaytdi) — eng eski partiyaga, minusga
        if remaining > 0 and order:
            p0 = order[0][0]
            rid = db.add_return(p0["id"], remaining, sana)
            return_ids.append(rid)
            taqsim.append({"partiya_raqam": p0["partiya_raqam"], "qty": remaining})
            remaining = 0

        d = db.mijoz_detail(mijoz_id)
        prodname = target[0][0]["mahsulot"]
        _oz_jami = sum(_oz_ulush(_p, _x["qty"]) for _p, _h in order
                       for _x in taqsim if _x["partiya_raqam"] == _p["partiya_raqam"])
        if _oz_jami > 0:
            _k = db.ombor_koeff(prodname, _yozilgan_birlik(t, qty))
            db.ombor_apply_by_name(prodname, "ret", _oz_jami * _k)
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
        # Sana aytilgan bo'lsa — o'sha kundagi holat (kesim)
        sana = getattr(t, "sana", None)
        sana = str(sana)[:10] if sana else None
        bugun = db.today_tk().isoformat()
        if sana and sana != bugun:
            try:
                d = db.mijoz_detail(mijoz_id, date.fromisoformat(sana), kesim=True)
            except Exception:
                d = db.mijoz_detail(mijoz_id)
        else:
            d = db.mijoz_detail(mijoz_id)
        return {"ok": True, "amal": "malumot", "mijoz_id": mijoz_id, "detail": d}

    return {"ok": False, "xato": "Tushunolmadim"}


def qator_chiqish(mijoz_id, qatorlar, sana=None, brov_kim=None, manzil=None):
    """Jadval orqali bir necha tovarni bitta yetkazma qilib qo'shadi."""
    m = db.get_mijoz(mijoz_id)
    if not m:
        return {"ok": False, "xato": "Mijoz topilmadi"}
    sana = str(sana or db.today_tk().isoformat())[:10]
    brov_kim = (brov_kim or "").strip() or None
    tekshir = db.get_sozlama("tovar_tekshir") == "1"
    natija, xatolar, tuzatilgan = [], [], []

    for q in (qatorlar or []):
        mah = (q.get("mahsulot") or "").strip()
        try:
            miq = float(str(q.get("miqdor") or "").replace(",", ".").replace(" ", ""))
        except Exception:
            miq = 0.0
        try:
            narx = float(str(q.get("kunlik_narx") or 0).replace(",", ".").replace(" ", ""))
        except Exception:
            narx = 0.0
        if not mah:
            continue
        if miq <= 0:
            xatolar.append(f"«{mah}» — sonini yozing")
            continue

        togri, aniq, taklif = db.tovar_match(mah)
        if togri:
            if not aniq:
                tuzatilgan.append((mah, togri))
            mah = togri
        elif tekshir and not brov_kim:
            qo = (" Shulardan: " + " / ".join(taklif)) if taklif else ""
            xatolar.append(f"«{mah}» — bunday tovar yo'q.{qo}")
            continue

        birlik = (q.get("birlik") or "").strip().lower() or db.tovar_birlik(mah)
        birlik = "kom" if birlik.startswith("kom") else "ta"
        pid, raqam = db.add_partiya(mijoz_id, mah, miq, narx, sana,
                                    manzil=(q.get("manzil") or manzil or None),
                                    brov_kim=brov_kim, brov_miqdor=(miq if brov_kim else None),
                                    birlik=birlik)
        if not brov_kim:
            db.ombor_apply_by_name(mah, "out", miq * db.ombor_koeff(mah, birlik))
        natija.append({"partiya_id": pid, "raqam": raqam, "mahsulot": mah,
                       "miqdor": miq, "birlik": birlik, "kunlik_narx": narx})

    if not natija:
        return {"ok": False, "xato": " · ".join(xatolar) or "Hech narsa qo'shilmadi"}
    xabar = f"{len(natija)} ta tovar qo'shildi"
    if tuzatilgan:
        xabar += " · to'g'rilandi: " + ", ".join(f"«{a}»→«{b}»" for a, b in tuzatilgan)
    if xatolar:
        xabar += " · ⚠️ " + " · ".join(xatolar)
    return {"ok": True, "amal": "qator", "qoshildi": natija, "xatolar": xatolar, "xabar": xabar}
