"""Ijara (arenda) hisobi — ma'lumotlar bazasi (SQLite).

Korxona lesa va temir mahsulotlarini kunlik ijaraga beradi.
Ma'lumot hamma xodimlar uchun UMUMIY (bitta korxona).

Hisob qoidasi: chiqgan kun HAM, qaytgan kun HAM hisoblanmaydi —
faqat o'rtadagi to'liq kunlar sanaladi.
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
    con.execute("""
        CREATE TABLE IF NOT EXISTS partiyalar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            mijoz TEXT NOT NULL,
            partiya_raqam INTEGER NOT NULL,
            mahsulot TEXT NOT NULL,
            miqdor REAL NOT NULL,
            kunlik_narx REAL NOT NULL,
            chiqgan_sana TEXT NOT NULL,
            yaratilgan TEXT NOT NULL
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS qaytarishlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            partiya_id INTEGER NOT NULL,
            miqdor REAL NOT NULL,
            qaytgan_sana TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


# ---------- Yozish ----------
def next_raqam(mijoz):
    con = _con()
    r = con.execute("SELECT MAX(partiya_raqam) FROM partiyalar WHERE mijoz = ?", (mijoz,)).fetchone()
    con.close()
    return (r[0] or 0) + 1


def add_partiya(mijoz, mahsulot, miqdor, kunlik_narx, chiqgan_sana):
    raqam = next_raqam(mijoz)
    con = _con()
    cur = con.execute(
        """INSERT INTO partiyalar (mijoz, partiya_raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, yaratilgan)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (mijoz, raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, now_tk().isoformat()),
    )
    con.commit()
    pid = cur.lastrowid
    con.close()
    return pid, raqam


def add_return(partiya_id, miqdor, qaytgan_sana):
    con = _con()
    cur = con.execute(
        "INSERT INTO qaytarishlar (partiya_id, miqdor, qaytgan_sana) VALUES (?, ?, ?)",
        (partiya_id, miqdor, qaytgan_sana),
    )
    con.commit()
    rid = cur.lastrowid
    con.close()
    return rid


def delete_partiya(partiya_id):
    con = _con()
    con.execute("DELETE FROM qaytarishlar WHERE partiya_id = ?", (partiya_id,))
    con.execute("DELETE FROM partiyalar WHERE id = ?", (partiya_id,))
    con.commit()
    con.close()


def delete_return(return_id):
    con = _con()
    con.execute("DELETE FROM qaytarishlar WHERE id = ?", (return_id,))
    con.commit()
    con.close()


def delete_mijoz(mijoz):
    con = _con()
    ids = [r[0] for r in con.execute("SELECT id FROM partiyalar WHERE mijoz = ?", (mijoz,)).fetchall()]
    for pid in ids:
        con.execute("DELETE FROM qaytarishlar WHERE partiya_id = ?", (pid,))
    con.execute("DELETE FROM partiyalar WHERE mijoz = ?", (mijoz,))
    con.commit()
    con.close()


# ---------- O'qish ----------
def get_partiya(mijoz, raqam):
    con = _con()
    r = con.execute("SELECT * FROM partiyalar WHERE mijoz = ? AND partiya_raqam = ?", (mijoz, raqam)).fetchone()
    con.close()
    return dict(r) if r else None


def partiyalar_of(mijoz):
    con = _con()
    rows = con.execute("SELECT * FROM partiyalar WHERE mijoz = ? ORDER BY partiya_raqam", (mijoz,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def returns_for(partiya_id):
    con = _con()
    rows = con.execute("SELECT * FROM qaytarishlar WHERE partiya_id = ? ORDER BY id", (partiya_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def all_mijozlar():
    con = _con()
    rows = con.execute("SELECT DISTINCT mijoz FROM partiyalar ORDER BY mijoz").fetchall()
    con.close()
    return [r[0] for r in rows]


# ---------- Hisob-kitob (yurak) ----------
def _billable_days(d0, dend):
    """Chiqgan kun ham, tugash kun ham hisoblanmaydi."""
    return max(0, (dend - d0).days - 1)


def _pdate(s):
    return date.fromisoformat(str(s)[:10])


def partiya_hisob(p, today=None):
    """Bitta partiya bo'yicha: qolgan miqdor va jami narx (shu kungacha)."""
    today = today or today_tk()
    issue = _pdate(p["chiqgan_sana"])
    daily = p["kunlik_narx"]
    rets = returns_for(p["id"])
    narx = 0.0
    qaytgan = 0.0
    for r in rets:
        kun = _billable_days(issue, _pdate(r["qaytgan_sana"]))
        narx += r["miqdor"] * daily * kun
        qaytgan += r["miqdor"]
    qolgan = p["miqdor"] - qaytgan
    kunlar = 0
    if qolgan > 0:
        kunlar = _billable_days(issue, today)
        narx += qolgan * daily * kunlar
    return {
        "id": p["id"],
        "partiya_raqam": p["partiya_raqam"],
        "mahsulot": p["mahsulot"],
        "miqdor": p["miqdor"],
        "qolgan": qolgan,
        "kunlik_narx": daily,
        "chiqgan_sana": str(p["chiqgan_sana"])[:10],
        "kunlar": kunlar,
        "narx": narx,
    }


def mijoz_detail(mijoz, today=None):
    today = today or today_tk()
    ps = [partiya_hisob(p, today) for p in partiyalar_of(mijoz)]
    jami = sum(x["narx"] for x in ps)
    qolgan = sum(x["qolgan"] for x in ps)
    return {"mijoz": mijoz, "partiyalar": ps, "jami": jami, "jami_qolgan": qolgan}


def mijozlar(today=None):
    today = today or today_tk()
    res = []
    for m in all_mijozlar():
        d = mijoz_detail(m, today)
        res.append({
            "mijoz": m,
            "jami": d["jami"],
            "jami_qolgan": d["jami_qolgan"],
            "partiya_soni": len(d["partiyalar"]),
        })
    res.sort(key=lambda x: -x["jami"])
    return res
