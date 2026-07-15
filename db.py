"""Ijara hisobi — ma'lumotlar bazasi (SQLite).

Mijoz = alohida shaxs (id, ism, telefon). Bir xil ismli mijozlar telefon bilan farqlanadi.
Hisob qoidasi: chiqgan kun HAM, qaytgan kun HAM hisoblanmaydi.
"""
import os
import sqlite3
from datetime import datetime, date
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
    TASHKENT = ZoneInfo("Asia/Tashkent")
except Exception:
    TASHKENT = None

DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "arenda.db"

# Birinchi admin (ega) — bazada bo'lsa ham, bo'lmasa ham DOIM admin, o'chib ketmaydi.
OWNER_ID = int(os.getenv("OWNER_ID", "7589459697"))


def now_tk():
    if TASHKENT:
        return datetime.now(TASHKENT).replace(tzinfo=None)
    return datetime.utcnow()


def today_tk():
    return now_tk().date()


def _con():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    con = _con()
    con.execute("""CREATE TABLE IF NOT EXISTS mijozlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ism TEXT NOT NULL, telefon TEXT, adres TEXT, yaratilgan TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS partiyalar (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mijoz_id INTEGER NOT NULL,
        partiya_raqam INTEGER NOT NULL,
        mahsulot TEXT NOT NULL, miqdor REAL NOT NULL, kunlik_narx REAL NOT NULL,
        chiqgan_sana TEXT NOT NULL, yaratilgan TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS qaytarishlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, partiya_id INTEGER NOT NULL,
        miqdor REAL NOT NULL, qaytgan_sana TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS tolovlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
        summa REAL NOT NULL, sana TEXT NOT NULL, izoh TEXT, yaratilgan TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS qoshimcha (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
        tur TEXT NOT NULL, summa REAL NOT NULL, sana TEXT NOT NULL, izoh TEXT, yaratilgan TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS eslatmalar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
        vada_sana TEXT NOT NULL, izoh TEXT, yuborildi INTEGER DEFAULT 0, yaratilgan TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS api_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT, sana TEXT, model TEXT,
        in_tok INTEGER DEFAULT 0, out_tok INTEGER DEFAULT 0)""")

    # Eski versiyalardan ko'chirish: agar 'mijoz' (matn) ustuni bo'lsa —
    # mijoz_id ni to'ldirib, eski majburiy 'mijoz' ustunini butunlay olib tashlaymiz.
    cols = [r[1] for r in con.execute("PRAGMA table_info(partiyalar)").fetchall()]
    if "mijoz" in cols:
        if "mijoz_id" not in cols:
            con.execute("ALTER TABLE partiyalar ADD COLUMN mijoz_id INTEGER")
        # Bo'sh mijoz_id larni to'ldirish (ism bo'yicha mavjud mijozga bog'lash yoki yaratish)
        miss = con.execute("SELECT id, mijoz FROM partiyalar WHERE mijoz_id IS NULL AND mijoz IS NOT NULL").fetchall()
        for rid, nm in miss:
            m = con.execute("SELECT id FROM mijozlar WHERE LOWER(TRIM(ism))=LOWER(TRIM(?)) LIMIT 1", (nm,)).fetchone()
            cid = m[0] if m else con.execute(
                "INSERT INTO mijozlar (ism, telefon, yaratilgan) VALUES (?, NULL, ?)",
                (nm, now_tk().isoformat())).lastrowid
            con.execute("UPDATE partiyalar SET mijoz_id = ? WHERE id = ?", (cid, rid))
        # Jadvalni yangi sxema bilan qayta qurish (eski 'mijoz' ustunisiz)
        con.execute("DROP TABLE IF EXISTS partiyalar_new")
        con.execute("""CREATE TABLE partiyalar_new (
            id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
            partiya_raqam INTEGER NOT NULL, mahsulot TEXT NOT NULL, miqdor REAL NOT NULL,
            kunlik_narx REAL NOT NULL, chiqgan_sana TEXT NOT NULL, yaratilgan TEXT NOT NULL)""")
        con.execute("""INSERT INTO partiyalar_new
            (id, mijoz_id, partiya_raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, yaratilgan)
            SELECT id, mijoz_id, partiya_raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, yaratilgan
            FROM partiyalar WHERE mijoz_id IS NOT NULL""")
        con.execute("DROP TABLE partiyalar")
        con.execute("ALTER TABLE partiyalar_new RENAME TO partiyalar")

    # Manzil ustuni (eski bazalarga ham qo'shiladi)
    mcols = [r[1] for r in con.execute("PRAGMA table_info(mijozlar)").fetchall()]
    if "adres" not in mcols:
        con.execute("ALTER TABLE mijozlar ADD COLUMN adres TEXT")
    if "status" not in mcols:
        con.execute("ALTER TABLE mijozlar ADD COLUMN status TEXT")

    # Xodimlar (botga kirish huquqi). rol: 'admin' yoki 'xodim'.
    con.execute("""CREATE TABLE IF NOT EXISTS xodimlar (
        id INTEGER PRIMARY KEY,
        ism TEXT, rol TEXT NOT NULL DEFAULT 'xodim',
        qoshgan_id INTEGER, yaratilgan TEXT NOT NULL)""")
    # Ega doim admin
    con.execute("INSERT OR IGNORE INTO xodimlar (id, ism, rol, qoshgan_id, yaratilgan) VALUES (?, ?, 'admin', ?, ?)",
                (OWNER_ID, "Ega", OWNER_ID, now_tk().isoformat()))
    con.execute("UPDATE xodimlar SET rol='admin' WHERE id=?", (OWNER_ID,))
    # Eski ALLOWED_USER_IDS ni bir marta ko'chirish (xodim sifatida) — mavjud xodimlar kirishdan chiqib qolmasin
    for tok in os.getenv("ALLOWED_USER_IDS", "").replace(" ", "").split(","):
        if tok.isdigit() and int(tok) != OWNER_ID:
            con.execute("INSERT OR IGNORE INTO xodimlar (id, ism, rol, qoshgan_id, yaratilgan) VALUES (?, NULL, 'xodim', ?, ?)",
                        (int(tok), OWNER_ID, now_tk().isoformat()))

    # Sozlamalar (masalan pul yig'ish chegarasi — necha kunlik ijara)
    con.execute("CREATE TABLE IF NOT EXISTS sozlamalar (kalit TEXT PRIMARY KEY, qiymat TEXT)")
    # Pul yig'ish eslatmasi oxirgi yuborilgan sana (har mijozga)
    mcols2 = [r[1] for r in con.execute("PRAGMA table_info(mijozlar)").fetchall()]
    if "yig_sana" not in mcols2:
        con.execute("ALTER TABLE mijozlar ADD COLUMN yig_sana TEXT")
    con.commit()
    con.close()


# ---------- Mijozlar ----------
def clean_phone(s):
    if not s:
        return None
    d = "".join(c for c in str(s) if c.isdigit())
    return d or None


def clean_phones(s):
    """Bir nechta raqamni ajratadi. Vergul, probel, tire — hammasi bo'ladi.
    Standart raqam uzunligi (9 yoki 12 xona) bo'yicha guruhlaydi, shunda
    '90 123 45 67' bitta raqam, '935053646 933979230' ikkita raqam bo'ladi."""
    if not s:
        return None
    import re
    tokens = [t for t in re.split(r"\D+", str(s)) if t]  # faqat raqam bo'laklari
    numbers = []
    cur = ""
    for tok in tokens:
        cur += tok
        if len(cur) == 9 or len(cur) >= 12:  # to'liq raqam yig'ildi
            numbers.append(cur)
            cur = ""
    if cur:
        numbers.append(cur)
    return ", ".join(numbers) or None


def phone_list(s):
    """Mijozning raqamlari ro'yxati (faqat raqamlar)."""
    if not s:
        return []
    import re
    return [d for p in re.split(r"[,;\n/]+", str(s)) if (d := "".join(c for c in p if c.isdigit()))]


def add_mijoz(ism, telefon=None):
    con = _con()
    cur = con.execute("INSERT INTO mijozlar (ism, telefon, yaratilgan) VALUES (?, ?, ?)",
                      (ism.strip(), clean_phones(telefon), now_tk().isoformat()))
    con.commit()
    mid = cur.lastrowid
    con.close()
    return mid


def update_mijoz(mijoz_id, ism, telefon):
    con = _con()
    con.execute("UPDATE mijozlar SET ism = ?, telefon = ? WHERE id = ?",
                (ism.strip(), clean_phones(telefon), mijoz_id))
    con.commit()
    con.close()


def get_mijoz(mijoz_id):
    con = _con()
    r = con.execute("SELECT * FROM mijozlar WHERE id = ?", (mijoz_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def set_adres(mijoz_id, adres):
    con = _con()
    con.execute("UPDATE mijozlar SET adres = ? WHERE id = ?", ((adres or "").strip() or None, mijoz_id))
    con.commit()
    con.close()


def set_status(mijoz_id, status):
    if status not in ("faol", "nofaol", "sotuv", None):
        return
    con = _con()
    con.execute("UPDATE mijozlar SET status = ? WHERE id = ?", (status, mijoz_id))
    con.commit()
    con.close()


def mijozlar_by_name(ism):
    con = _con()
    rows = con.execute("SELECT * FROM mijozlar WHERE LOWER(TRIM(ism)) = LOWER(TRIM(?)) ORDER BY id",
                       (ism,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def all_customers():
    con = _con()
    rows = con.execute("SELECT id, ism, telefon FROM mijozlar ORDER BY id").fetchall()
    con.close()
    return [dict(r) for r in rows]


def similar_mijozlar(ism, threshold=0.8):
    """Imloviy o'xshash (masalan Fathulla ~ Fatxulla) mijozlarni topadi."""
    from difflib import SequenceMatcher
    a = (ism or "").strip().lower()
    if not a:
        return []
    res = []
    for m in all_customers():
        b = (m["ism"] or "").strip().lower()
        if a == b:
            continue  # aniq mos kelganlar alohida ishlanadi
        r = SequenceMatcher(None, a, b).ratio()
        if r >= threshold:
            res.append((r, m))
    res.sort(key=lambda x: -x[0])
    return [m for _, m in res]


def delete_mijoz(mijoz_id):
    con = _con()
    ids = [r[0] for r in con.execute("SELECT id FROM partiyalar WHERE mijoz_id = ?", (mijoz_id,)).fetchall()]
    for pid in ids:
        con.execute("DELETE FROM qaytarishlar WHERE partiya_id = ?", (pid,))
    con.execute("DELETE FROM partiyalar WHERE mijoz_id = ?", (mijoz_id,))
    con.execute("DELETE FROM mijozlar WHERE id = ?", (mijoz_id,))
    con.commit()
    con.close()


# ---------- Xodimlar / ruxsat ----------
def is_allowed(uid):
    """Botga kirish huquqi bormi (ega, admin yoki xodim)."""
    if uid == OWNER_ID:
        return True
    con = _con()
    r = con.execute("SELECT 1 FROM xodimlar WHERE id = ?", (uid,)).fetchone()
    con.close()
    return r is not None


def is_admin(uid):
    if uid == OWNER_ID:
        return True
    con = _con()
    r = con.execute("SELECT rol FROM xodimlar WHERE id = ?", (uid,)).fetchone()
    con.close()
    return bool(r) and r[0] == "admin"


def is_owner(uid):
    return uid == OWNER_ID


def get_xodim(uid):
    con = _con()
    r = con.execute("SELECT * FROM xodimlar WHERE id = ?", (uid,)).fetchone()
    con.close()
    return dict(r) if r else None


def add_xodim(uid, ism=None, rol="xodim", qoshgan_id=None):
    """Yangi xodim/admin qo'shadi yoki mavjudini yangilaydi (rol/ism)."""
    if rol not in ("xodim", "admin"):
        rol = "xodim"
    con = _con()
    ex = con.execute("SELECT id FROM xodimlar WHERE id = ?", (uid,)).fetchone()
    if ex:
        con.execute("UPDATE xodimlar SET ism = COALESCE(?, ism), rol = ? WHERE id = ?", (ism or None, rol, uid))
    else:
        con.execute("INSERT INTO xodimlar (id, ism, rol, qoshgan_id, yaratilgan) VALUES (?, ?, ?, ?, ?)",
                    (uid, ism or None, rol, qoshgan_id, now_tk().isoformat()))
    con.commit()
    con.close()


def remove_xodim(uid):
    """Egani hech qachon o'chirmaydi."""
    if uid == OWNER_ID:
        return False
    con = _con()
    con.execute("DELETE FROM xodimlar WHERE id = ?", (uid,))
    con.commit()
    con.close()
    return True


def all_xodimlar():
    con = _con()
    rows = con.execute("SELECT * FROM xodimlar ORDER BY (rol='admin') DESC, id").fetchall()
    con.close()
    return [dict(r) for r in rows]


def xodim_ids():
    """Eslatma yuboriladigan hamma ID (ega ham albatta)."""
    con = _con()
    ids = [r[0] for r in con.execute("SELECT id FROM xodimlar").fetchall()]
    con.close()
    if OWNER_ID not in ids:
        ids.append(OWNER_ID)
    return ids


# ---------- Partiyalar ----------
def next_raqam(mijoz_id):
    con = _con()
    r = con.execute("SELECT MAX(partiya_raqam) FROM partiyalar WHERE mijoz_id = ?", (mijoz_id,)).fetchone()
    con.close()
    return (r[0] or 0) + 1


def add_partiya(mijoz_id, mahsulot, miqdor, kunlik_narx, chiqgan_sana):
    raqam = next_raqam(mijoz_id)
    con = _con()
    cur = con.execute(
        """INSERT INTO partiyalar (mijoz_id, partiya_raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, yaratilgan)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (mijoz_id, raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, now_tk().isoformat()),
    )
    con.commit()
    pid = cur.lastrowid
    con.close()
    return pid, raqam


def get_partiya(mijoz_id, raqam):
    con = _con()
    r = con.execute("SELECT * FROM partiyalar WHERE mijoz_id = ? AND partiya_raqam = ?", (mijoz_id, raqam)).fetchone()
    con.close()
    return dict(r) if r else None


def get_partiya_by_id(partiya_id):
    con = _con()
    r = con.execute("SELECT * FROM partiyalar WHERE id = ?", (partiya_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def update_partiya(partiya_id, mahsulot, miqdor, kunlik_narx, chiqgan_sana):
    con = _con()
    con.execute(
        "UPDATE partiyalar SET mahsulot=?, miqdor=?, kunlik_narx=?, chiqgan_sana=? WHERE id=?",
        (mahsulot, miqdor, kunlik_narx, str(chiqgan_sana)[:10], partiya_id),
    )
    con.commit()
    con.close()


def partiyalar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM partiyalar WHERE mijoz_id = ? ORDER BY partiya_raqam", (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def delete_partiya(partiya_id):
    con = _con()
    con.execute("DELETE FROM qaytarishlar WHERE partiya_id = ?", (partiya_id,))
    con.execute("DELETE FROM partiyalar WHERE id = ?", (partiya_id,))
    con.commit()
    con.close()


# ---------- Qaytarishlar ----------
def add_return(partiya_id, miqdor, qaytgan_sana):
    con = _con()
    cur = con.execute("INSERT INTO qaytarishlar (partiya_id, miqdor, qaytgan_sana) VALUES (?, ?, ?)",
                      (partiya_id, miqdor, qaytgan_sana))
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def delete_return(return_id):
    con = _con()
    con.execute("DELETE FROM qaytarishlar WHERE id = ?", (return_id,))
    con.commit()
    con.close()


def returns_for(partiya_id):
    con = _con()
    rows = con.execute("SELECT * FROM qaytarishlar WHERE partiya_id = ? ORDER BY id", (partiya_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------- To'lovlar (predoplata) ----------
def add_tolov(mijoz_id, summa, sana, izoh=None):
    con = _con()
    cur = con.execute("INSERT INTO tolovlar (mijoz_id, summa, sana, izoh, yaratilgan) VALUES (?, ?, ?, ?, ?)",
                      (mijoz_id, summa, sana, izoh, now_tk().isoformat()))
    con.commit()
    tid = cur.lastrowid
    con.close()
    return tid


def delete_tolov(tolov_id):
    con = _con()
    con.execute("DELETE FROM tolovlar WHERE id = ?", (tolov_id,))
    con.commit()
    con.close()


def tolovlar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM tolovlar WHERE mijoz_id = ? ORDER BY id DESC", (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def jami_tolov(mijoz_id):
    con = _con()
    r = con.execute("SELECT COALESCE(SUM(summa),0) FROM tolovlar WHERE mijoz_id = ?", (mijoz_id,)).fetchone()
    con.close()
    return r[0] or 0.0


def add_qoshimcha(mijoz_id, tur, summa, sana, izoh=None):
    con = _con()
    cur = con.execute("INSERT INTO qoshimcha (mijoz_id, tur, summa, sana, izoh, yaratilgan) VALUES (?, ?, ?, ?, ?, ?)",
                      (mijoz_id, tur, summa, sana, izoh, now_tk().isoformat()))
    con.commit()
    qid = cur.lastrowid
    con.close()
    return qid


def delete_qoshimcha(qid):
    con = _con()
    con.execute("DELETE FROM qoshimcha WHERE id = ?", (qid,))
    con.commit()
    con.close()


def qoshimcha_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM qoshimcha WHERE mijoz_id = ? ORDER BY id DESC", (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------- Eslatmalar (to'lov va'dasi) ----------
def add_eslatma(mijoz_id, vada_sana, izoh=None):
    con = _con()
    cur = con.execute("INSERT INTO eslatmalar (mijoz_id, vada_sana, izoh, yuborildi, yaratilgan) VALUES (?, ?, ?, 0, ?)",
                      (mijoz_id, str(vada_sana)[:10], izoh, now_tk().isoformat()))
    con.commit()
    eid = cur.lastrowid
    con.close()
    return eid


def delete_eslatma(eid):
    con = _con()
    con.execute("DELETE FROM eslatmalar WHERE id = ?", (eid,))
    con.commit()
    con.close()


def eslatmalar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM eslatmalar WHERE mijoz_id = ? ORDER BY vada_sana", (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def due_eslatmalar():
    """Va'da kuni kelgan (11:00 dan keyin) va hali yuborilmagan eslatmalar."""
    con = _con()
    rows = con.execute("SELECT * FROM eslatmalar WHERE COALESCE(yuborildi,0) = 0").fetchall()
    con.close()
    now = now_tk()
    today = now.date().isoformat()
    out = []
    for r in rows:
        vs = str(r["vada_sana"])[:10]
        if vs < today or (vs == today and now.hour >= 11):
            out.append(dict(r))
    return out


def mark_eslatma_sent(eid):
    con = _con()
    con.execute("UPDATE eslatmalar SET yuborildi = 1 WHERE id = ?", (eid,))
    con.commit()
    con.close()


def daily_rate(mijoz_id, today=None):
    """Mijozning hozirgi bir kunlik ijara narxi (qolgan × kunlik_narx yig'indisi)."""
    total = 0.0
    for p in partiyalar_of(mijoz_id):
        h = partiya_hisob(p, today)
        if h["qolgan"] > 0:
            total += h["qolgan"] * p["kunlik_narx"]
    return total


# ---------- Hisob-kitob ----------
def _billable_days(d0, dend):
    return max(0, (dend - d0).days - 1)


def _pdate(s):
    return date.fromisoformat(str(s)[:10])


def partiya_hisob(p, today=None):
    today = today or today_tk()
    issue = _pdate(p["chiqgan_sana"])
    daily = p["kunlik_narx"]
    narx = 0.0
    qaytgan = 0.0
    rets = returns_for(p["id"])
    for r in rets:
        narx += r["miqdor"] * daily * _billable_days(issue, _pdate(r["qaytgan_sana"]))
        qaytgan += r["miqdor"]
    qolgan = p["miqdor"] - qaytgan
    kunlar = 0
    if qolgan > 0:
        kunlar = _billable_days(issue, today)
        narx += qolgan * daily * kunlar
    return {
        "id": p["id"], "partiya_raqam": p["partiya_raqam"], "mahsulot": p["mahsulot"],
        "miqdor": p["miqdor"], "qolgan": qolgan, "kunlik_narx": daily,
        "chiqgan_sana": str(p["chiqgan_sana"])[:10], "kunlar": kunlar, "narx": narx,
        "qaytgan": qaytgan,
        "qaytarishlar": [{"id": r["id"], "miqdor": r["miqdor"], "qaytgan_sana": str(r["qaytgan_sana"])[:10]} for r in rets],
    }


def mijoz_detail(mijoz_id, today=None):
    today = today or today_tk()
    m = get_mijoz(mijoz_id)
    if not m:
        return None
    ps = [partiya_hisob(p, today) for p in partiyalar_of(mijoz_id)]
    hisoblangan = sum(x["narx"] for x in ps)
    tolangan = jami_tolov(mijoz_id)
    qo = qoshimcha_of(mijoz_id)
    yolkira = sum(x["summa"] for x in qo if x["tur"] == "yolkira")
    remont = sum(x["summa"] for x in qo if x["tur"] == "remont")
    return {
        "id": mijoz_id, "mijoz": m["ism"], "telefon": m["telefon"], "adres": m.get("adres"),
        "status": m.get("status"),
        "partiyalar": ps,
        "jami": hisoblangan,
        "hisoblangan": hisoblangan,
        "yolkira": yolkira,
        "remont": remont,
        "tolangan": tolangan,
        "qolgan_qarz": hisoblangan + yolkira + remont - tolangan,
        "tolovlar": tolovlar_of(mijoz_id),
        "qoshimcha": qo,
        "eslatmalar": eslatmalar_of(mijoz_id),
        "jami_qolgan": sum(x["qolgan"] for x in ps),
    }


def mijozlar(today=None):
    today = today or today_tk()
    con = _con()
    ids = [r[0] for r in con.execute("SELECT id FROM mijozlar ORDER BY id").fetchall()]
    con.close()
    res = []
    for mid in ids:
        d = mijoz_detail(mid, today)
        res.append({
            "id": mid, "mijoz": d["mijoz"], "telefon": d["telefon"], "status": d["status"],
            "jami": d["jami"], "tolangan": d["tolangan"], "qolgan_qarz": d["qolgan_qarz"],
            "jami_qolgan": d["jami_qolgan"],
            "partiya_soni": len(d["partiyalar"]),
        })
    res.sort(key=lambda x: -x["qolgan_qarz"])
    return res


def kunlik(sana=None):
    """Berilgan kun (default bugun) bo'yicha chiqqan va qaytgan mollar."""
    sana = str(sana or today_tk().isoformat())[:10]
    con = _con()
    prows = con.execute(
        """SELECT p.partiya_raqam, p.mahsulot, p.miqdor, p.kunlik_narx, m.ism AS mijoz
           FROM partiyalar p JOIN mijozlar m ON m.id = p.mijoz_id
           WHERE substr(p.chiqgan_sana,1,10) = ? ORDER BY m.ism""",
        (sana,)).fetchall()
    rrows = con.execute(
        """SELECT q.miqdor, p.mahsulot, m.ism AS mijoz
           FROM qaytarishlar q JOIN partiyalar p ON p.id = q.partiya_id
           JOIN mijozlar m ON m.id = p.mijoz_id
           WHERE substr(q.qaytgan_sana,1,10) = ? ORDER BY m.ism""",
        (sana,)).fetchall()
    con.close()
    return {
        "sana": sana,
        "chiqish": [dict(r) for r in prows],
        "qaytish": [dict(r) for r in rrows],
    }


# ---------- Gemini (AI) sarfini kuzatish ----------
def log_usage(model, in_tok, out_tok):
    try:
        con = _con()
        con.execute("INSERT INTO api_usage (sana, model, in_tok, out_tok) VALUES (?, ?, ?, ?)",
                    (today_tk().isoformat(), model or "", int(in_tok or 0), int(out_tok or 0)))
        con.commit()
        con.close()
    except Exception:
        pass


def oylik_sarf(oy=None):
    oy = oy or now_tk().strftime("%Y-%m")
    con = _con()
    r = con.execute(
        "SELECT COUNT(*), COALESCE(SUM(in_tok),0), COALESCE(SUM(out_tok),0) FROM api_usage WHERE substr(sana,1,7)=?",
        (oy,)).fetchone()
    con.close()
    return {"oy": oy, "req": r[0] or 0, "in_tok": r[1] or 0, "out_tok": r[2] or 0}


# ---------- Sozlamalar ----------
def get_sozlama(kalit, default=None):
    con = _con()
    r = con.execute("SELECT qiymat FROM sozlamalar WHERE kalit = ?", (kalit,)).fetchone()
    con.close()
    return r[0] if r else default


def set_sozlama(kalit, qiymat):
    con = _con()
    if con.execute("SELECT 1 FROM sozlamalar WHERE kalit = ?", (kalit,)).fetchone():
        con.execute("UPDATE sozlamalar SET qiymat = ? WHERE kalit = ?", (str(qiymat), kalit))
    else:
        con.execute("INSERT INTO sozlamalar (kalit, qiymat) VALUES (?, ?)", (kalit, str(qiymat)))
    con.commit()
    con.close()


def get_limit_kun():
    """Pul yig'ish chegarasi — qarz necha kunlik ijaraga tenglashsa eslatiladi."""
    v = get_sozlama("limit_kun")
    if v is None:
        try:
            return int(os.getenv("LIMIT_KUN", "15"))
        except Exception:
            return 15
    try:
        return int(v)
    except Exception:
        return 15


def set_limit_kun(n):
    set_sozlama("limit_kun", int(n))


# ---------- Qarzdorlar (pul yig'ish) ----------
def set_yig_sana(mijoz_id, sana):
    con = _con()
    con.execute("UPDATE mijozlar SET yig_sana = ? WHERE id = ?", (str(sana)[:10] if sana else None, mijoz_id))
    con.commit()
    con.close()


def qarzdorlar(limit_kun=None, today=None):
    """Qarzi bor mijozlar. Har biriga: qarz, kunlik ijara, qarz necha kunlik ijaraga teng,
    va chegaradan oshgani (over) belgisi. Chegaradan oshganlar birinchi, qarz kattaligi bo'yicha."""
    limit_kun = get_limit_kun() if limit_kun is None else limit_kun
    today = today or today_tk()
    con = _con()
    rows = con.execute("SELECT id, yig_sana FROM mijozlar ORDER BY id").fetchall()
    con.close()
    out = []
    for row in rows:
        mid = row["id"]
        d = mijoz_detail(mid, today)
        qarz = d["qolgan_qarz"]
        if qarz <= 0:
            continue
        rate = daily_rate(mid, today)          # hozirgi kunlik ijara
        kun = (qarz / rate) if rate > 0 else None   # None = rental yo'q, lekin qarz bor
        over = (kun is None) or (kun >= limit_kun)
        out.append({
            "id": mid, "ism": d["mijoz"], "telefon": d["telefon"], "status": d["status"],
            "qarz": qarz, "rate": rate, "kun": kun, "over": over,
            "yig_sana": row["yig_sana"], "jami_qolgan": d["jami_qolgan"],
        })
    out.sort(key=lambda x: (not x["over"], -(x["kun"] if x["kun"] is not None else 1e9), -x["qarz"]))
    return out
