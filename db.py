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
    con.commit()
    con.close()


# ---------- Mijozlar ----------
def clean_phone(s):
    if not s:
        return None
    d = "".join(c for c in str(s) if c.isdigit())
    return d or None


def add_mijoz(ism, telefon=None):
    con = _con()
    cur = con.execute("INSERT INTO mijozlar (ism, telefon, yaratilgan) VALUES (?, ?, ?)",
                      (ism.strip(), clean_phone(telefon), now_tk().isoformat()))
    con.commit()
    mid = cur.lastrowid
    con.close()
    return mid


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
