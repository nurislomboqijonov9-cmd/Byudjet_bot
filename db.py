"""Ma'lumotlar bazasi (SQLite). Har bir yozuv shu yerda saqlanadi."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

# DATA_DIR — ma'lumot saqlanadigan papka.
# Railway'da doimiy Volume /data ga ulanadi va DATA_DIR=/data qilib beriladi,
# shunda qayta ishga tushganda ma'lumot o'chib ketmaydi.
DATA_DIR = Path(os.getenv("DATA_DIR", Path(__file__).parent))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "byudjet.db"

# Har bir turning pul oqimiga ta'siri (+1 kirim, -1 chiqim)
CASHFLOW = {
    "kirim": 1,
    "chiqim": -1,
    "qarz_berdim": -1,      # pul chiqdi (kimdir menga qarzdor)
    "qarz_oldim": 1,        # pul kirdi (men qarzdorman)
    "qarz_qaytardim": -1,   # o'z qarzimni to'ladim
    "qarz_qaytarildi": 1,   # menga qarzni qaytarishdi
}


def init_db():
    con = sqlite3.connect(DB_PATH)
    con.execute("""
        CREATE TABLE IF NOT EXISTS yozuvlar (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            tur TEXT NOT NULL,
            summa REAL NOT NULL,
            kim TEXT,
            kategoriya TEXT,
            izoh TEXT,
            transkript TEXT,
            sana TEXT NOT NULL
        )
    """)
    con.commit()
    con.close()


def add_entry(user_id, tur, summa, kim=None, kategoriya=None, izoh=None, transkript=None):
    con = sqlite3.connect(DB_PATH)
    cur = con.execute(
        """INSERT INTO yozuvlar (user_id, tur, summa, kim, kategoriya, izoh, transkript, sana)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, tur, summa, kim, kategoriya, izoh, transkript, datetime.now().isoformat()),
    )
    con.commit()
    new_id = cur.lastrowid
    con.close()
    return new_id


def delete_entry(entry_id, user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.execute("DELETE FROM yozuvlar WHERE id = ? AND user_id = ?", (entry_id, user_id))
    con.commit()
    ok = cur.rowcount > 0
    con.close()
    return ok


def get_entry(entry_id, user_id):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    row = con.execute(
        "SELECT * FROM yozuvlar WHERE id = ? AND user_id = ?", (entry_id, user_id)
    ).fetchone()
    con.close()
    return dict(row) if row else None


def last_entries(user_id, limit=10):
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT * FROM yozuvlar WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()
    con.close()
    return [dict(r) for r in rows]


def last_entry_id(user_id):
    con = sqlite3.connect(DB_PATH)
    row = con.execute(
        "SELECT id FROM yozuvlar WHERE user_id = ? ORDER BY id DESC LIMIT 1", (user_id,)
    ).fetchone()
    con.close()
    return row[0] if row else None


def balance(user_id):
    """Umumiy balans + shu oy kirim/chiqimi."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute("SELECT tur, summa, sana FROM yozuvlar WHERE user_id = ?", (user_id,)).fetchall()
    con.close()

    now = datetime.now()
    total = oy_kirim = oy_chiqim = 0.0
    for r in rows:
        sign = CASHFLOW.get(r["tur"], 0)
        total += sign * r["summa"]
        d = datetime.fromisoformat(r["sana"])
        if d.year == now.year and d.month == now.month:
            if sign > 0:
                oy_kirim += r["summa"]
            elif sign < 0:
                oy_chiqim += r["summa"]
    return {"balans": total, "oy_kirim": oy_kirim, "oy_chiqim": oy_chiqim}


def debts(user_id):
    """Har bir odam bo'yicha qarz holati.
    Musbat = u menga qarzdor. Manfiy = men unga qarzdorman."""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT tur, summa, kim FROM yozuvlar WHERE user_id = ? AND kim IS NOT NULL AND kim != ''",
        (user_id,),
    ).fetchall()
    con.close()

    net = {}
    for r in rows:
        kim = r["kim"].strip()
        if not kim:
            continue
        s = r["summa"]
        t = r["tur"]
        if t == "qarz_berdim":
            net[kim] = net.get(kim, 0) + s      # u menga qarzdor bo'ldi
        elif t == "qarz_qaytarildi":
            net[kim] = net.get(kim, 0) - s      # qaytardi
        elif t == "qarz_oldim":
            net[kim] = net.get(kim, 0) - s      # men unga qarzdor
        elif t == "qarz_qaytardim":
            net[kim] = net.get(kim, 0) + s      # men to'ladim
    # 0 ga teng bo'lganlarni olib tashlaymiz
    return {k: v for k, v in net.items() if abs(v) > 0.001}
