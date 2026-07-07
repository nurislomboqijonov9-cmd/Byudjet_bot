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

    # Eski versiyadan (mijoz nomi bilan) ko'chirish
    cols = [r[1] for r in con.execute("PRAGMA table_info(partiyalar)").fetchall()]
    if "mijoz_id" not in cols and "mijoz" in cols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN mijoz_id INTEGER")
        names = [r[0] for r in con.execute("SELECT DISTINCT mijoz FROM partiyalar WHERE mijoz IS NOT NULL").fetchall()]
        for nm in names:
            cur = con.execute("INSERT INTO mijozlar (ism, telefon, yaratilgan) VALUES (?, NULL, ?)",
                              (nm, now_tk().isoformat()))
            con.execute("UPDATE partiyalar SET mijoz_id = ? WHERE mijoz = ?", (cur.lastrowid, nm))

    # Manzil ustuni (eski bazalarga ham qo'shiladi)
    mcols = [r[1] for r in con.execute("PRAGMA table_info(mijozlar)").fetchall()]
    if "adres" not in mcols:
        con.execute("ALTER TABLE mijozlar ADD COLUMN adres TEXT")
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


def mijozlar_by_name(ism):
    con = _con()
    rows = con.execute("SELECT * FROM mijozlar WHERE LOWER(TRIM(ism)) = LOWER(TRIM(?)) ORDER BY id",
                       (ism,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


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
    for r in returns_for(p["id"]):
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
    }


def mijoz_detail(mijoz_id, today=None):
    today = today or today_tk()
    m = get_mijoz(mijoz_id)
    if not m:
        return None
    ps = [partiya_hisob(p, today) for p in partiyalar_of(mijoz_id)]
    return {
        "id": mijoz_id, "mijoz": m["ism"], "telefon": m["telefon"], "adres": m.get("adres"),
        "partiyalar": ps,
        "jami": sum(x["narx"] for x in ps),
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
            "id": mid, "mijoz": d["mijoz"], "telefon": d["telefon"],
            "jami": d["jami"], "jami_qolgan": d["jami_qolgan"],
            "partiya_soni": len(d["partiyalar"]),
        })
    res.sort(key=lambda x: -x["jami"])
    return res
