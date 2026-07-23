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
    if "qayd" not in mcols2:
        con.execute("ALTER TABLE mijozlar ADD COLUMN qayd TEXT")

    # Zakazlar (2 qavatli model): mahsulot bo'yicha umumiy buyurtma. Chiqishlar (partiyalar) shunga bog'lanadi.
    con.execute("""CREATE TABLE IF NOT EXISTS zakazlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
        mahsulot TEXT NOT NULL, jami_miqdor REAL NOT NULL DEFAULT 0, yaratilgan TEXT NOT NULL)""")
    pcols = [r[1] for r in con.execute("PRAGMA table_info(partiyalar)").fetchall()]
    if "zakaz_id" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN zakaz_id INTEGER")
    if "manzil" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN manzil TEXT")
    if "brov_kim" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN brov_kim TEXT")
    if "brov_miqdor" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN brov_miqdor REAL")
    if "birlik" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN birlik TEXT")
    # Eski partiyalarni zakazga bog'lash: har (mijoz, mahsulot) uchun bitta zakaz, jami = chiqqanlar yig'indisi
    orphans = con.execute(
        "SELECT DISTINCT mijoz_id, mahsulot FROM partiyalar WHERE zakaz_id IS NULL").fetchall()
    for mid, mah in orphans:
        z = con.execute("SELECT id FROM zakazlar WHERE mijoz_id=? AND LOWER(TRIM(mahsulot))=LOWER(TRIM(?)) LIMIT 1",
                        (mid, mah)).fetchone()
        if z:
            zid = z[0]
        else:
            summ = con.execute("SELECT COALESCE(SUM(miqdor),0) FROM partiyalar WHERE mijoz_id=? AND mahsulot=?",
                               (mid, mah)).fetchone()[0]
            zid = con.execute("INSERT INTO zakazlar (mijoz_id, mahsulot, jami_miqdor, yaratilgan) VALUES (?,?,?,?)",
                              (mid, mah, summ, now_tk().isoformat())).lastrowid
        con.execute("UPDATE partiyalar SET zakaz_id=? WHERE mijoz_id=? AND mahsulot=? AND zakaz_id IS NULL",
                    (zid, mid, mah))

    # ---------- Brovdan (boshqadan olib turilgan) ----------
    con.execute("""CREATE TABLE IF NOT EXISTS brovlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kim TEXT NOT NULL, mahsulot TEXT NOT NULL,
        miqdor REAL NOT NULL, sana TEXT NOT NULL, izoh TEXT, yaratilgan TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS brov_qaytarish (
        id INTEGER PRIMARY KEY AUTOINCREMENT, brov_id INTEGER NOT NULL,
        miqdor REAL NOT NULL, sana TEXT NOT NULL)""")
    bcols = [r[1] for r in con.execute("PRAGMA table_info(brovlar)").fetchall()]
    if "mijoz_id" not in bcols:
        con.execute("ALTER TABLE brovlar ADD COLUMN mijoz_id INTEGER")
    if "kunlik_narx" not in bcols:
        con.execute("ALTER TABLE brovlar ADD COLUMN kunlik_narx REAL DEFAULT 0")
    # O'zimiz uchun qaydlar (eslatmasiz, shunchaki yozib qo'yish)
    con.execute("""CREATE TABLE IF NOT EXISTS qaydlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
        matn TEXT NOT NULL, sana TEXT NOT NULL, yaratilgan TEXT NOT NULL)""")

    # ---------- Ombor (ostatka) ----------
    con.execute("""CREATE TABLE IF NOT EXISTS ombor_mahsulot (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        total INTEGER NOT NULL DEFAULT 0, out_qty INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0)""")
    con.execute("""CREATE TABLE IF NOT EXISTS ombor_tarix (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mahsulot_id TEXT, mahsulot_nom TEXT,
        tur TEXT, miqdor INTEGER, ombor_after INTEGER, ts TEXT NOT NULL)""")
    c = con.execute("SELECT COUNT(*) FROM ombor_mahsulot").fetchone()[0]
    if c == 0:
        seed = [
            ("Lyulka", 38, 28), ("Fasadni lesa (Yashil lesa)", 2626, 2235),
            ("Fasad krest (yashil)", 48, 0), ("Stoyka 1.2 m", 202, 0),
            ("Stoyka 1 m", 294, 0), ("Stoyka 4 m", 4087, 3928),
            ("Stoyka 4.5 m", 818, 816), ("Stoyka 5 m", 4280, 3964),
            ("Stoyka 5.5 m", 351, 290), ("Lesa 80", 130, 126),
            ("Qizil havoza", 32, 0), ("Monolit lesa 1.5 m", 600, 425),
            ("Monolit lesa 2 m", 2398, 2178), ("Soyedinitel", 6662, 3502),
            ("Rezba", 9023, 5651), ("Univilka", 5773, 4399),
            ("Tayrot 1 m", 2789, 1674), ("Tayrot 1.2 m", 466, 466),
            ("Taxta", 2839, 2597), ("Balka 3 m", 2712, 1698),
            ("Balka 3.3 m", 10, 0),
        ]
        for i, (nom, total, out) in enumerate(seed):
            pid = _ombor_slug(nom) + f"-{i}"
            con.execute("INSERT INTO ombor_mahsulot (id, name, total, out_qty, sort_order) VALUES (?,?,?,?,?)",
                        (pid, nom, total, out, i))

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
def find_or_create_zakaz(mijoz_id, mahsulot, con=None):
    """(mijoz, mahsulot) uchun mavjud zakazni topadi yoki yangi ochadi. zakaz_id qaytaradi."""
    own = con is None
    if own:
        con = _con()
    z = con.execute("SELECT id FROM zakazlar WHERE mijoz_id=? AND LOWER(TRIM(mahsulot))=LOWER(TRIM(?)) LIMIT 1",
                    (mijoz_id, mahsulot)).fetchone()
    if z:
        zid = z[0]
    else:
        zid = con.execute("INSERT INTO zakazlar (mijoz_id, mahsulot, jami_miqdor, yaratilgan) VALUES (?,?,0,?)",
                          (mijoz_id, mahsulot, now_tk().isoformat())).lastrowid
    if own:
        con.commit()
        con.close()
    return zid


def set_zakaz_total(zakaz_id, jami_miqdor):
    con = _con()
    con.execute("UPDATE zakazlar SET jami_miqdor=? WHERE id=?", (float(jami_miqdor or 0), zakaz_id))
    con.commit()
    con.close()


def get_zakaz(zakaz_id):
    con = _con()
    r = con.execute("SELECT * FROM zakazlar WHERE id=?", (zakaz_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def delete_zakaz(zakaz_id):
    """Zakaz va uning barcha chiqishlari (partiyalari) hamda qaytarishlarini o'chiradi."""
    con = _con()
    pids = [r[0] for r in con.execute("SELECT id FROM partiyalar WHERE zakaz_id=?", (zakaz_id,)).fetchall()]
    for pid in pids:
        con.execute("DELETE FROM qaytarishlar WHERE partiya_id=?", (pid,))
    con.execute("DELETE FROM partiyalar WHERE zakaz_id=?", (zakaz_id,))
    con.execute("DELETE FROM zakazlar WHERE id=?", (zakaz_id,))
    con.commit()
    con.close()


def next_raqam(mijoz_id):
    con = _con()
    r = con.execute("SELECT MAX(partiya_raqam) FROM partiyalar WHERE mijoz_id = ?", (mijoz_id,)).fetchone()
    con.close()
    return (r[0] or 0) + 1


def add_partiya(mijoz_id, mahsulot, miqdor, kunlik_narx, chiqgan_sana, zakaz_id=None, manzil=None,
                brov_kim=None, brov_miqdor=None, birlik=None):
    raqam = next_raqam(mijoz_id)
    brov_kim = (brov_kim or "").strip() or None
    if brov_kim and not brov_miqdor:
        brov_miqdor = miqdor          # soni aytilmasa — hammasi o'shandan
    if not brov_kim:
        brov_miqdor = None
    con = _con()
    if zakaz_id is None:
        zakaz_id = find_or_create_zakaz(mijoz_id, mahsulot, con)
    cur = con.execute(
        """INSERT INTO partiyalar (mijoz_id, partiya_raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, yaratilgan, zakaz_id, manzil, brov_kim, brov_miqdor, birlik)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mijoz_id, raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, now_tk().isoformat(), zakaz_id,
         (manzil or None), brov_kim, brov_miqdor, (birlik or None)),
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


def update_partiya(partiya_id, mahsulot, miqdor, kunlik_narx, chiqgan_sana, manzil=None,
                   brov_kim=None, brov_miqdor=None):
    brov_kim = (brov_kim or "").strip() or None
    if brov_kim and not brov_miqdor:
        brov_miqdor = miqdor
    if not brov_kim:
        brov_miqdor = None
    con = _con()
    con.execute(
        "UPDATE partiyalar SET mahsulot=?, miqdor=?, kunlik_narx=?, chiqgan_sana=?, manzil=?, brov_kim=?, brov_miqdor=? WHERE id=?",
        (mahsulot, miqdor, kunlik_narx, str(chiqgan_sana)[:10], (manzil or None), brov_kim, brov_miqdor, partiya_id),
    )
    con.commit()
    con.close()


def partiyalar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM partiyalar WHERE mijoz_id = ? ORDER BY partiya_raqam", (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def zakazlar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM zakazlar WHERE mijoz_id = ? ORDER BY id", (mijoz_id,)).fetchall()
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


def partiya_hisob(p, today=None, kesim=False):
    today = today or today_tk()
    issue = _pdate(p["chiqgan_sana"])
    daily = p["kunlik_narx"]
    narx = 0.0
    qaytgan = 0.0
    rets = returns_for(p["id"])
    if kesim:
        kes = str(today)[:10]
        rets = [r for r in rets if str(r["qaytgan_sana"])[:10] <= kes]
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
        "birlik": (p.get("birlik") if isinstance(p, dict) else None) or tovar_birlik(p["mahsulot"]),
        "qaytgan": qaytgan,
        "qaytarishlar": [{"id": r["id"], "miqdor": r["miqdor"], "qaytgan_sana": str(r["qaytgan_sana"])[:10]} for r in rets],
    }


def mijoz_detail(mijoz_id, today=None, kesim=False):
    """kesim=True bo'lsa — o'sha sanadagi holat (keyingi harakatlar hisobga olinmaydi)."""
    today = today or today_tk()
    m = get_mijoz(mijoz_id)
    if not m:
        return None
    kes = str(today)[:10] if kesim else None
    raw = partiyalar_of(mijoz_id)
    if kes:
        raw = [p for p in raw if str(p["chiqgan_sana"])[:10] <= kes]
    ps = []
    for p in raw:
        h = partiya_hisob(p, today, kesim=kesim)
        h["zakaz_id"] = p.get("zakaz_id")
        h["manzil"] = p.get("manzil")
        h["brov_kim"] = p.get("brov_kim")
        h["brov_miqdor"] = p.get("brov_miqdor")
        ps.append(h)
    hisoblangan = sum(x["narx"] for x in ps)
    tolovlar_l = tolovlar_of(mijoz_id)
    qo = qoshimcha_of(mijoz_id)
    if kes:
        tolovlar_l = [t for t in tolovlar_l if str(t["sana"])[:10] <= kes]
        qo = [q for q in qo if str(q["sana"])[:10] <= kes]
    tolangan = sum(t["summa"] for t in tolovlar_l)
    yolkira = sum(x["summa"] for x in qo if x["tur"] == "yolkira")
    remont = sum(x["summa"] for x in qo if x["tur"] == "remont")

    # 2 qavatli guruh: zakaz (mahsulot bo'yicha jami) -> ichida chiqishlar
    zmap = {}
    for z in zakazlar_of(mijoz_id):
        zmap[z["id"]] = {
            "id": z["id"], "mahsulot": z["mahsulot"], "jami_miqdor": z["jami_miqdor"],
            "chiqishlar": [], "chiqdi": 0.0, "qolgan": 0.0, "narx": 0.0,
        }
    for h in ps:
        g = zmap.get(h.get("zakaz_id"))
        if not g:
            continue
        g["chiqishlar"].append(h)
        g["chiqdi"] += h["miqdor"]
        g["qolgan"] += h["qolgan"]
        g["narx"] += h["narx"]
    zakazlar_list = []
    for g in zmap.values():
        if not g["chiqishlar"] and (g["jami_miqdor"] or 0) <= 0:
            continue
        g["chiqishlar"].sort(key=lambda x: x["partiya_raqam"])
        g["qoldi"] = max(0.0, (g["jami_miqdor"] or 0) - g["chiqdi"])  # hali chiqarilmagan
        zakazlar_list.append(g)
    zakazlar_list.sort(key=lambda x: (x["mahsulot"] or "").lower())

    # Yetkazmalar: bir kunda chiqqan mahsulotlar = bitta karta (nakladnoy)
    deliv = {}
    for h in ps:
        key = h["chiqgan_sana"][:10]
        g = deliv.get(key)
        if not g:
            g = {"sana": key, "items": [], "jami_narx": 0.0, "jami_dona": 0.0, "qolgan_dona": 0.0}
            deliv[key] = g
        g["items"].append(h)
        g["jami_narx"] += h["narx"]
        g["jami_dona"] += h["miqdor"]
        g["qolgan_dona"] += h["qolgan"]
    for g in deliv.values():
        g["items"].sort(key=lambda x: x["partiya_raqam"])
    yetkazmalar = sorted(deliv.values(), key=lambda x: x["sana"], reverse=True)

    # Qolgan mahsulotlar (mahsulot bo'yicha jamlab)
    qmap = {}
    for h in ps:
        if h["qolgan"] <= 0:
            continue
        key = (h["mahsulot"] or "").strip().lower()
        g = qmap.setdefault(key, {"mahsulot": h["mahsulot"], "qolgan": 0.0, "narx": 0.0,
                                  "birlik": h.get("birlik") or "ta", "manzillar": set()})
        g["qolgan"] += h["qolgan"]
        g["narx"] += h["narx"]
        if h.get("manzil"):
            g["manzillar"].add(h["manzil"])
    qolganlar = []
    for g in qmap.values():
        qolganlar.append({"mahsulot": g["mahsulot"], "qolgan": g["qolgan"], "narx": g["narx"],
                          "birlik": g.get("birlik") or "ta", "manzillar": sorted(g["manzillar"])})
    qolganlar.sort(key=lambda x: -x["qolgan"])

    # Qaytarishlar: bir kunda qaytgan mahsulotlar = bitta yozuv
    rgr = {}
    for h in ps:
        for r in h.get("qaytarishlar", []):
            key = r["qaytgan_sana"][:10]
            g = rgr.get(key)
            if not g:
                g = {"sana": key, "items": []}
                rgr[key] = g
            g["items"].append({
                "mahsulot": h["mahsulot"], "miqdor": r["miqdor"],
                "partiya_raqam": h["partiya_raqam"], "return_id": r["id"],
            })
    qaytarishlar_guruh = sorted(rgr.values(), key=lambda x: x["sana"], reverse=True)

    # Manzillar (ob'ektlar): qaysi tovar qaysi manzilda — faqat qolgani bor bo'lganlar
    mgr = {}
    for h in ps:
        if h["qolgan"] <= 0:
            continue
        key = (h.get("manzil") or "").strip() or "Manzil belgilanmagan"
        g = mgr.get(key)
        if not g:
            g = {"manzil": key, "items": [], "jami_narx": 0.0, "qolgan_dona": 0.0}
            mgr[key] = g
        g["items"].append(h)
        g["jami_narx"] += h["narx"]
        g["qolgan_dona"] += h["qolgan"]
    for g in mgr.values():
        g["items"].sort(key=lambda x: (x["mahsulot"] or "").lower())
    manzillar = sorted(mgr.values(), key=lambda x: (x["manzil"] == "Manzil belgilanmagan", x["manzil"].lower()))

    # Brovdan olinganlar: kimdan qaysi tovardan qancha
    bgr = {}
    for h in ps:
        kim = (h.get("brov_kim") or "").strip()
        bm = float(h.get("brov_miqdor") or 0)
        if not kim or bm <= 0:
            continue
        g = bgr.setdefault(kim, {"kim": kim, "items": [], "jami": 0.0})
        g["items"].append({"mahsulot": h["mahsulot"], "miqdor": bm,
                           "partiya_raqam": h["partiya_raqam"], "qolgan": h["qolgan"]})
        g["jami"] += bm
    for g in bgr.values():
        g["items"].sort(key=lambda x: (x["mahsulot"] or "").lower())
    brovdan = sorted(bgr.values(), key=lambda x: x["kim"].lower())

    jami_qolgan_ = sum(x["qolgan"] for x in ps)
    _st = m.get("status")
    if _st != "sotuv":  # 'sotuv' qo'lda qo'yiladi, avtomat o'zgarmaydi
        _st = "faol" if jami_qolgan_ > 0 else ("nofaol" if ps else _st)

    return {
        "id": mijoz_id, "mijoz": m["ism"], "telefon": m["telefon"], "adres": m.get("adres"),
        "telefonlar": phone_list(m["telefon"]),
        "status": _st,
        "partiyalar": ps,
        "zakazlar": zakazlar_list,
        "yetkazmalar": yetkazmalar,
        "manzillar": manzillar,
        "qolganlar": qolganlar,
        "brovdan": brovdan,
        "brovlar": brov_list(mijoz_id),
        "qaydlar": qaydlar_of(mijoz_id),
        "qayd": get_qayd(mijoz_id),
        "qaytarishlar_guruh": qaytarishlar_guruh,
        "jami": hisoblangan,
        "hisoblangan": hisoblangan,
        "yolkira": yolkira,
        "remont": remont,
        "tolangan": tolangan,
        "qolgan_qarz": hisoblangan + yolkira + remont - tolangan,
        "tolovlar": tolovlar_l,
        "kesim_sana": kes,
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
            "adres": d.get("adres"),
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


# ================= OMBOR (ostatka) =================
def _ombor_norm(s):
    """Nom mosligini tekshirish uchun: qavs ichini olib, faqat harf/raqam/nuqta, kichik harf."""
    s = (s or "").lower()
    out, depth = [], 0
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(ch for ch in "".join(out) if ch.isalnum() or ch == '.')


def _ombor_slug(s):
    r = []
    for ch in (s or "").lower():
        r.append(ch if ch.isalnum() else '-')
    return "".join(r).strip('-') or "tovar"


def ombor_list():
    con = _con()
    rows = con.execute("SELECT id, name, total, out_qty FROM ombor_mahsulot ORDER BY sort_order, name").fetchall()
    con.close()
    return [{"id": r["id"], "name": r["name"], "total": r["total"], "out": r["out_qty"],
             "omborda": r["total"] - r["out_qty"]} for r in rows]


def ombor_by_name(nom):
    key = _ombor_norm(nom)
    if not key:
        return None
    con = _con()
    rows = con.execute("SELECT id, name FROM ombor_mahsulot").fetchall()
    con.close()
    for r in rows:
        if _ombor_norm(r["name"]) == key:
            return r["id"]
    return None


def ombor_move(pid, tur, miqdor):
    """Qo'lda harakat (guard bilan). tur: out/ret/add/writeoff."""
    miqdor = int(round(float(miqdor or 0)))
    if miqdor <= 0:
        return {"ok": False, "xato": "Miqdor noto'g'ri"}
    if tur not in ("out", "ret", "add", "writeoff"):
        return {"ok": False, "xato": "tur"}
    con = _con()
    r = con.execute("SELECT total, out_qty, name FROM ombor_mahsulot WHERE id=?", (pid,)).fetchone()
    if not r:
        con.close()
        return {"ok": False, "xato": "topilmadi"}
    total, out, name = r["total"], r["out_qty"], r["name"]
    if tur == "out":
        if miqdor > total - out:
            con.close(); return {"ok": False, "xato": "Omborda yetarli emas"}
        out += miqdor
    elif tur == "ret":
        if miqdor > out:
            con.close(); return {"ok": False, "xato": "Arendadagidan ko'p"}
        out -= miqdor
    elif tur == "add":
        total += miqdor
    elif tur == "writeoff":
        if miqdor > total - out:
            con.close(); return {"ok": False, "xato": "Omborda yetarli emas"}
        total -= miqdor
    con.execute("UPDATE ombor_mahsulot SET total=?, out_qty=? WHERE id=?", (total, out, pid))
    ombor = total - out
    con.execute("INSERT INTO ombor_tarix (mahsulot_id, mahsulot_nom, tur, miqdor, ombor_after, ts) VALUES (?,?,?,?,?,?)",
                (pid, name, tur, miqdor, ombor, now_tk().isoformat()))
    con.commit()
    con.close()
    return {"ok": True, "id": pid, "name": name, "total": total, "out": out, "omborda": ombor}


def ombor_apply_by_name(nom, tur, miqdor):
    """Ijaradan avtomat chaqiriladi: nom bo'yicha topib out +/- qiladi. Ijarani bloklamaydi."""
    pid = ombor_by_name(nom)
    if not pid:
        return (False, None)
    miqdor = int(round(float(miqdor or 0)))
    if miqdor <= 0:
        return (False, None)
    con = _con()
    r = con.execute("SELECT total, out_qty, name FROM ombor_mahsulot WHERE id=?", (pid,)).fetchone()
    total, out, name = r["total"], r["out_qty"], r["name"]
    if tur == "out":
        out += miqdor
    elif tur == "ret":
        out = max(0, out - miqdor)
    else:
        con.close()
        return (False, None)
    con.execute("UPDATE ombor_mahsulot SET out_qty=? WHERE id=?", (out, pid))
    ombor = total - out
    con.execute("INSERT INTO ombor_tarix (mahsulot_id, mahsulot_nom, tur, miqdor, ombor_after, ts) VALUES (?,?,?,?,?,?)",
                (pid, name, "ij_" + tur, miqdor, ombor, now_tk().isoformat()))
    con.commit()
    con.close()
    return (True, name)


def ombor_set_total(pid, total):
    total = max(0, int(round(float(total or 0))))
    con = _con()
    r = con.execute("SELECT out_qty FROM ombor_mahsulot WHERE id=?", (pid,)).fetchone()
    if not r:
        con.close(); return {"ok": False, "xato": "topilmadi"}
    out = min(r["out_qty"], total)
    con.execute("UPDATE ombor_mahsulot SET total=?, out_qty=? WHERE id=?", (total, out, pid))
    con.commit()
    con.close()
    return {"ok": True, "id": pid, "total": total, "out": out, "omborda": total - out}


def ombor_add(name, total=0):
    name = (name or "").strip()
    if not name:
        return {"ok": False, "xato": "nom"}
    total = max(0, int(round(float(total or 0))))
    con = _con()
    n = con.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM ombor_mahsulot").fetchone()[0]
    pid = _ombor_slug(name) + "-" + str(int(now_tk().timestamp()))
    con.execute("INSERT INTO ombor_mahsulot (id, name, total, out_qty, sort_order) VALUES (?,?,?,0,?)",
                (pid, name, total, n))
    con.commit()
    con.close()
    return {"ok": True, "id": pid, "name": name, "total": total, "out": 0, "omborda": total}


def ombor_delete(pid):
    con = _con()
    con.execute("DELETE FROM ombor_tarix WHERE mahsulot_id=?", (pid,))
    con.execute("DELETE FROM ombor_mahsulot WHERE id=?", (pid,))
    con.commit()
    con.close()


def ombor_rename(pid, name):
    name = (name or "").strip()
    if not name:
        return {"ok": False, "xato": "nom"}
    con = _con()
    con.execute("UPDATE ombor_mahsulot SET name=? WHERE id=?", (name, pid))
    con.execute("UPDATE ombor_tarix SET mahsulot_nom=? WHERE mahsulot_id=?", (name, pid))
    con.commit()
    con.close()
    return {"ok": True}


def ombor_history(pid=None, limit=200):
    con = _con()
    if pid:
        rows = con.execute("SELECT * FROM ombor_tarix WHERE mahsulot_id=? ORDER BY id DESC LIMIT ?", (pid, limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM ombor_tarix ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def ombor_names():
    con = _con()
    rows = con.execute("SELECT name FROM ombor_mahsulot ORDER BY sort_order, name").fetchall()
    con.close()
    return [r["name"] for r in rows]


def ombor_match_name(nom, cutoff=0.62):
    """Nomni ombordagi eng yaqin tovarga moslaydi.
    Qaytaradi: (to'g'ri_nom, aniqmi). Topilmasa (None, False)."""
    import difflib
    s = (nom or "").strip()
    if not s:
        return (None, False)
    pid = ombor_by_name(s)
    names = ombor_names()
    if pid:
        for n in names:
            if _ombor_norm(n) == _ombor_norm(s):
                return (n, True)
    if not names:
        return (None, False)
    key = _ombor_norm(s)
    norm_map = {_ombor_norm(n): n for n in names}
    # 1) ichma-ich (masalan "lesa 80" ~ "lesa")
    for k, n in norm_map.items():
        if k and (k in key or key in k) and abs(len(k) - len(key)) <= 4:
            return (n, False)
    # 2) imloviy yaqinlik
    best = difflib.get_close_matches(key, list(norm_map.keys()), n=1, cutoff=cutoff)
    if best:
        return (norm_map[best[0]], False)
    return (None, False)


# ================= BROVDAN (boshqadan olib turilgan) =================
def brov_add(kim, mahsulot, miqdor, sana=None, izoh=None, mijoz_id=None, kunlik_narx=0):
    kim = (kim or "").strip()
    mahsulot = (mahsulot or "").strip()
    try:
        miqdor = float(miqdor)
    except Exception:
        miqdor = 0
    if not kim or not mahsulot or miqdor <= 0:
        return {"ok": False, "xato": "Kim, mahsulot va soni kerak"}
    sana = str(sana or today_tk().isoformat())[:10]
    con = _con()
    try:
        kunlik_narx = float(kunlik_narx or 0)
    except Exception:
        kunlik_narx = 0
    cur = con.execute("INSERT INTO brovlar (kim, mahsulot, miqdor, sana, izoh, yaratilgan, mijoz_id, kunlik_narx) VALUES (?,?,?,?,?,?,?,?)",
                      (kim, mahsulot, miqdor, sana, (izoh or None), now_tk().isoformat(), mijoz_id, kunlik_narx))
    con.commit()
    bid = cur.lastrowid
    con.close()
    return {"ok": True, "id": bid}


def brov_return(brov_id, miqdor, sana=None):
    try:
        miqdor = float(miqdor)
    except Exception:
        miqdor = 0
    if miqdor <= 0:
        return {"ok": False, "xato": "Soni noto'g'ri"}
    con = _con()
    r = con.execute("SELECT miqdor FROM brovlar WHERE id=?", (brov_id,)).fetchone()
    if not r:
        con.close(); return {"ok": False, "xato": "Topilmadi"}
    q = con.execute("SELECT COALESCE(SUM(miqdor),0) FROM brov_qaytarish WHERE brov_id=?", (brov_id,)).fetchone()[0]
    qolgan = float(r[0]) - float(q or 0)
    if miqdor > qolgan:
        miqdor = qolgan
    if miqdor <= 0:
        con.close(); return {"ok": False, "xato": "Hammasi qaytarilgan"}
    con.execute("INSERT INTO brov_qaytarish (brov_id, miqdor, sana) VALUES (?,?,?)",
                (brov_id, miqdor, str(sana or today_tk().isoformat())[:10]))
    con.commit()
    con.close()
    return {"ok": True, "miqdor": miqdor}


def brov_delete(brov_id):
    con = _con()
    con.execute("DELETE FROM brov_qaytarish WHERE brov_id=?", (brov_id,))
    con.execute("DELETE FROM brovlar WHERE id=?", (brov_id,))
    con.commit()
    con.close()


def brov_ret_delete(ret_id):
    con = _con()
    con.execute("DELETE FROM brov_qaytarish WHERE id=?", (ret_id,))
    con.commit()
    con.close()


def brov_list(mijoz_id=None):
    """Kimdan qancha olingan / qaytarilgan / qolgan — odam bo'yicha guruh."""
    con = _con()
    if mijoz_id is None:
        rows = con.execute("SELECT * FROM brovlar ORDER BY sana DESC, id DESC").fetchall()
    else:
        rows = con.execute("SELECT * FROM brovlar WHERE mijoz_id=? ORDER BY sana DESC, id DESC",
                           (mijoz_id,)).fetchall()
    rets = con.execute("SELECT * FROM brov_qaytarish ORDER BY id").fetchall()
    con.close()
    rmap = {}
    for r in rets:
        rmap.setdefault(r["brov_id"], []).append(dict(r))
    bugun = today_tk()
    gr = {}
    for b in rows:
        b = dict(b)
        rets_b = rmap.get(b["id"], [])
        qaytgan = sum(x["miqdor"] for x in rets_b)
        b["qaytgan"] = qaytgan
        b["qolgan"] = max(0.0, float(b["miqdor"]) - qaytgan)
        b["qaytarishlar"] = rets_b
        # Pul: har qism o'z kuni bo'yicha (chiqgan va qaytgan kun hisobmas)
        narx = float(b.get("kunlik_narx") or 0)
        summa, kunlar = 0.0, 0
        if narx > 0:
            try:
                d0 = date.fromisoformat(str(b["sana"])[:10])
                for rr in rets_b:
                    dr = date.fromisoformat(str(rr["sana"])[:10])
                    k = max(0, (dr - d0).days - 1)
                    summa += narx * float(rr["miqdor"]) * k
                kq = max(0, (bugun - d0).days - 1)
                summa += narx * b["qolgan"] * kq
                kunlar = kq
            except Exception:
                pass
        b["kunlar"] = kunlar
        b["summa"] = summa
        g = gr.setdefault(b["kim"], {"kim": b["kim"], "items": [], "jami": 0.0, "qolgan": 0.0, "summa": 0.0})
        g["items"].append(b)
        g["jami"] += float(b["miqdor"])
        g["qolgan"] += b["qolgan"]
        g["summa"] += summa
    out = sorted(gr.values(), key=lambda x: (-x["qolgan"], x["kim"].lower()))
    return out


# ---------- Qaydlar (o'zimiz uchun izoh) ----------
def qayd_add(mijoz_id, matn, sana=None):
    matn = (matn or "").strip()
    if not matn:
        return {"ok": False, "xato": "Matn bo'sh"}
    con = _con()
    con.execute("INSERT INTO qaydlar (mijoz_id, matn, sana, yaratilgan) VALUES (?,?,?,?)",
                (mijoz_id, matn, str(sana or today_tk().isoformat())[:10], now_tk().isoformat()))
    con.commit()
    con.close()
    return {"ok": True}


def qayd_delete(qid):
    con = _con()
    con.execute("DELETE FROM qaydlar WHERE id=?", (qid,))
    con.commit()
    con.close()


def qaydlar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM qaydlar WHERE mijoz_id=? ORDER BY sana DESC, id DESC",
                       (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------- Nom to'g'rilash / ombor qayta hisoblash ----------
def rename_mahsulot(eski, yangi):
    """Barcha partiyalarda mahsulot nomini almashtiradi (nom normalizatsiyasi bo'yicha)."""
    eski_n = _ombor_norm(eski)
    yangi = (yangi or "").strip()
    if not eski_n or not yangi:
        return {"ok": False, "xato": "Eski va yangi nom kerak"}
    con = _con()
    rows = con.execute("SELECT id, mahsulot FROM partiyalar").fetchall()
    ids = [r["id"] for r in rows if _ombor_norm(r["mahsulot"]) == eski_n]
    for pid in ids:
        con.execute("UPDATE partiyalar SET mahsulot=? WHERE id=?", (yangi, pid))
    zrows = con.execute("SELECT id, mahsulot FROM zakazlar").fetchall()
    zids = [r["id"] for r in zrows if _ombor_norm(r["mahsulot"]) == eski_n]
    for zid in zids:
        con.execute("UPDATE zakazlar SET mahsulot=? WHERE id=?", (yangi, zid))
    con.commit()
    con.close()
    return {"ok": True, "partiya": len(ids), "zakaz": len(zids)}


def partiya_nomlari():
    """Partiyalarda uchraydigan mahsulot nomlari + nechta partiyada bor + ombordagi mosligi."""
    con = _con()
    rows = con.execute("SELECT mahsulot, COUNT(*) c FROM partiyalar GROUP BY mahsulot ORDER BY c DESC").fetchall()
    con.close()
    out = []
    for r in rows:
        nom = r["mahsulot"]
        pid = ombor_by_name(nom)
        out.append({"nom": nom, "soni": r["c"], "omborda_bor": pid is not None})
    return out


def ombor_recalc(today=None):
    """Ombordagi 'arendada' sonini partiyalardan qayta hisoblaydi.
    Brovdan olingan ulush hisobga olinmaydi. Nomi ombordagi bilan mos kelmaganlar alohida qaytariladi."""
    today = today or today_tk()
    con = _con()
    prows = con.execute("SELECT * FROM partiyalar").fetchall()
    con.close()
    hisob, nomos = {}, {}
    for p in prows:
        p = dict(p)
        h = partiya_hisob(p, today)
        qolgan = float(h["qolgan"])
        if qolgan <= 0:
            continue
        m = float(p.get("miqdor") or 0)
        b = min(float(p.get("brov_miqdor") or 0), m)
        oz = qolgan * ((m - b) / m) if m > 0 else qolgan   # brovdan ulushi chiqarib tashlanadi
        if oz <= 0:
            continue
        pid = ombor_by_name(p["mahsulot"])
        if pid:
            hisob[pid] = hisob.get(pid, 0.0) + oz
        else:
            nomos[p["mahsulot"]] = nomos.get(p["mahsulot"], 0.0) + oz

    con = _con()
    ozgargan = []
    for row in con.execute("SELECT id, name, total, out_qty FROM ombor_mahsulot").fetchall():
        yangi = int(round(hisob.get(row["id"], 0.0)))
        if yangi != int(row["out_qty"]):
            ozgargan.append({"name": row["name"], "eski": int(row["out_qty"]), "yangi": yangi,
                             "omborda": int(row["total"]) - yangi})
            con.execute("UPDATE ombor_mahsulot SET out_qty=? WHERE id=?", (yangi, row["id"]))
    con.commit()
    con.close()
    return {"ok": True, "ozgargan": ozgargan,
            "nomos": sorted(nomos.items(), key=lambda x: -x[1])}


def get_qayd(mijoz_id):
    """Mijozning yagona qayd matni. Eski alohida qaydlar bo'lsa — birlashtiriladi."""
    con = _con()
    r = con.execute("SELECT qayd FROM mijozlar WHERE id=?", (mijoz_id,)).fetchone()
    cur = (r[0] if r else None)
    con.close()
    if cur is not None:
        return cur
    eski = qaydlar_of(mijoz_id)
    if eski:
        return "\n".join(f"{str(q['sana'])[:10]} — {q['matn']}" for q in reversed(eski))
    return ""


def set_qayd(mijoz_id, matn):
    con = _con()
    con.execute("UPDATE mijozlar SET qayd=? WHERE id=?", (matn or "", mijoz_id))
    con.commit()
    con.close()
    return {"ok": True}


# ---------- Tovar lug'ati (nom tekshirish uchun) ----------
# (nom, birlik): "kom" — komplekt (1 kom = 2 ta), "ta" — dona
TOVAR_DEFAULT = [
    ("Oyoq 2m", "kom"), ("Qaychi 2m", "kom"), ("Oyoq 1.5m", "kom"), ("Qaychi 1.5m", "kom"),
    ("Rezba 1m", "ta"), ("Univilka", "ta"), ("Soedinitel", "ta"), ("Balka 3m", "ta"),
    ("Tayrot", "ta"), ("Gayka tayrot", "ta"),
    ("Lesa oyoq", "kom"), ("Lesa80 oyoq", "kom"), ("Lesa qaychi", "kom"),
    ("Taxta", "ta"), ("Balon", "ta"),
    ("Stoyka 4m", "ta"), ("Stoyka 4.5m", "ta"), ("Stoyka 5m", "ta"),
    ("Stoyka 5.5m", "ta"), ("Stoyka 1.2m", "ta"), ("Lyulka", "kom"),
]
KOM_TA = 2.0   # 1 komplekt = 2 ta


def tovar_juftlar():
    """[(nom, birlik), ...] — sozlamadan yoki default."""
    v = get_sozlama("tovar_royxat")
    if v:
        out = []
        for x in v.replace(",", "\n").split("\n"):
            x = x.strip()
            if not x:
                continue
            if "|" in x:
                nom, b = x.split("|", 1)
                out.append((nom.strip(), (b.strip().lower() or "ta")))
            else:
                p = x.rsplit(" ", 1)
                if len(p) == 2 and p[1].lower() in ("kom", "komplekt", "ta", "dona"):
                    out.append((p[0].strip(), "kom" if p[1].lower().startswith("kom") else "ta"))
                else:
                    out.append((x, "ta"))
        if out:
            return out
    return list(TOVAR_DEFAULT)


def tovar_royxat():
    """Faqat nomlar (tekshirish uchun)."""
    return [n for n, _b in tovar_juftlar()]


def tovar_barcha():
    """Tekshirish uchun to'liq ro'yxat: lug'at + ombordagi tovarlar."""
    out, korilgan = [], set()
    for n in tovar_royxat():
        k = _ombor_norm(n)
        if k and k not in korilgan:
            korilgan.add(k)
            out.append(n)
    try:
        for n in ombor_names():
            k = _ombor_norm(n)
            if k and k not in korilgan:
                korilgan.add(k)
                out.append(n)
    except Exception:
        pass
    return out


def tovar_birlik(nom):
    """Tovarning asosiy birligi: 'kom' yoki 'ta'."""
    key = _ombor_norm(nom)
    for n, b in tovar_juftlar():
        if _ombor_norm(n) == key:
            return b
    return "ta"


def set_tovar_royxat(lst):
    if isinstance(lst, str):
        lst = [x.strip() for x in lst.replace(",", "\n").split("\n") if x.strip()]
    satr = []
    for x in lst:
        if isinstance(x, (list, tuple)):
            satr.append(f"{x[0]}|{x[1]}")
        else:
            satr.append(str(x))
    set_sozlama("tovar_royxat", "\n".join(satr))
    return {"ok": True, "soni": len(satr)}


def ombor_koeff(nom, yozilgan_birlik):
    """Ombordan qancha ayirish kerakligi koeffitsienti.
    Tovar 'kom' da yuritilsa: 'kom' yozilsa 1, 'ta' yozilsa 0.5."""
    asos = tovar_birlik(nom)
    y = (yozilgan_birlik or "").lower()
    if asos == "kom":
        if y.startswith("ta") or y.startswith("don"):
            return 1.0 / KOM_TA
        return 1.0
    # asos 'ta' bo'lsa: 'kom' yozilsa 1 kom = 2 ta
    if y.startswith("kom"):
        return KOM_TA
    return 1.0


def tovar_match(nom, cutoff=0.58):
    """Ro'yxatdagi eng yaqin tovarni topadi.
    Qaytaradi: (to'g'ri_nom | None, aniqmi, taklif_ro'yxati)."""
    import difflib
    s = (nom or "").strip()
    key = _ombor_norm(s)
    names = tovar_barcha()
    if not key or not names:
        return (None, False, [])
    # aniq moslik
    for n in names:
        if _ombor_norm(n) == key:
            return (n, True, [])

    ball = []
    for idx, n in enumerate(names):
        k = _ombor_norm(n)
        r = difflib.SequenceMatcher(None, key, k).ratio()
        for w in n.lower().replace("*", " ").split():
            wn = _ombor_norm(w)
            if wn:
                r = max(r, difflib.SequenceMatcher(None, key, wn).ratio() * 0.97)
                if len(key) >= 3 and wn.startswith(key):
                    r = max(r, 0.85)
        if len(key) >= 3 and key in k:
            r = max(r, 0.9)
        if r >= 0.5:
            ball.append((-r, idx, n))     # idx — ro'yxat tartibi (lug'at oldinda)
    if not ball:
        return (None, False, [])
    ball.sort()
    top_r, _i, top_n = -ball[0][0], ball[0][1], ball[0][2]

    # Bir xil tovarning turli yozilishi (Soedinitel / Soyedinitel) — bitta deb qaraymiz
    def _bir_xil(a, b):
        return difflib.SequenceMatcher(None, _ombor_norm(a), _ombor_norm(b)).ratio() >= 0.80

    boshqa = []
    for negr, _ix, n in ball[1:]:
        if not _bir_xil(top_n, n):
            boshqa.append((-negr, n))

    yolgiz = (not boshqa) or ((top_r - boshqa[0][0]) >= 0.12)
    if yolgiz and top_r >= cutoff:
        return (top_n, False, [top_n])

    taklif = [top_n] + [n for _r, n in boshqa[:2]]
    return (None, False, taklif)

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
    if "qayd" not in mcols2:
        con.execute("ALTER TABLE mijozlar ADD COLUMN qayd TEXT")

    # Zakazlar (2 qavatli model): mahsulot bo'yicha umumiy buyurtma. Chiqishlar (partiyalar) shunga bog'lanadi.
    con.execute("""CREATE TABLE IF NOT EXISTS zakazlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
        mahsulot TEXT NOT NULL, jami_miqdor REAL NOT NULL DEFAULT 0, yaratilgan TEXT NOT NULL)""")
    pcols = [r[1] for r in con.execute("PRAGMA table_info(partiyalar)").fetchall()]
    if "zakaz_id" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN zakaz_id INTEGER")
    if "manzil" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN manzil TEXT")
    if "brov_kim" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN brov_kim TEXT")
    if "brov_miqdor" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN brov_miqdor REAL")
    if "birlik" not in pcols:
        con.execute("ALTER TABLE partiyalar ADD COLUMN birlik TEXT")
    # Eski partiyalarni zakazga bog'lash: har (mijoz, mahsulot) uchun bitta zakaz, jami = chiqqanlar yig'indisi
    orphans = con.execute(
        "SELECT DISTINCT mijoz_id, mahsulot FROM partiyalar WHERE zakaz_id IS NULL").fetchall()
    for mid, mah in orphans:
        z = con.execute("SELECT id FROM zakazlar WHERE mijoz_id=? AND LOWER(TRIM(mahsulot))=LOWER(TRIM(?)) LIMIT 1",
                        (mid, mah)).fetchone()
        if z:
            zid = z[0]
        else:
            summ = con.execute("SELECT COALESCE(SUM(miqdor),0) FROM partiyalar WHERE mijoz_id=? AND mahsulot=?",
                               (mid, mah)).fetchone()[0]
            zid = con.execute("INSERT INTO zakazlar (mijoz_id, mahsulot, jami_miqdor, yaratilgan) VALUES (?,?,?,?)",
                              (mid, mah, summ, now_tk().isoformat())).lastrowid
        con.execute("UPDATE partiyalar SET zakaz_id=? WHERE mijoz_id=? AND mahsulot=? AND zakaz_id IS NULL",
                    (zid, mid, mah))

    # ---------- Brovdan (boshqadan olib turilgan) ----------
    con.execute("""CREATE TABLE IF NOT EXISTS brovlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, kim TEXT NOT NULL, mahsulot TEXT NOT NULL,
        miqdor REAL NOT NULL, sana TEXT NOT NULL, izoh TEXT, yaratilgan TEXT NOT NULL)""")
    con.execute("""CREATE TABLE IF NOT EXISTS brov_qaytarish (
        id INTEGER PRIMARY KEY AUTOINCREMENT, brov_id INTEGER NOT NULL,
        miqdor REAL NOT NULL, sana TEXT NOT NULL)""")
    bcols = [r[1] for r in con.execute("PRAGMA table_info(brovlar)").fetchall()]
    if "mijoz_id" not in bcols:
        con.execute("ALTER TABLE brovlar ADD COLUMN mijoz_id INTEGER")
    if "kunlik_narx" not in bcols:
        con.execute("ALTER TABLE brovlar ADD COLUMN kunlik_narx REAL DEFAULT 0")
    # O'zimiz uchun qaydlar (eslatmasiz, shunchaki yozib qo'yish)
    con.execute("""CREATE TABLE IF NOT EXISTS qaydlar (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mijoz_id INTEGER NOT NULL,
        matn TEXT NOT NULL, sana TEXT NOT NULL, yaratilgan TEXT NOT NULL)""")

    # ---------- Ombor (ostatka) ----------
    con.execute("""CREATE TABLE IF NOT EXISTS ombor_mahsulot (
        id TEXT PRIMARY KEY, name TEXT NOT NULL,
        total INTEGER NOT NULL DEFAULT 0, out_qty INTEGER NOT NULL DEFAULT 0,
        sort_order INTEGER NOT NULL DEFAULT 0)""")
    con.execute("""CREATE TABLE IF NOT EXISTS ombor_tarix (
        id INTEGER PRIMARY KEY AUTOINCREMENT, mahsulot_id TEXT, mahsulot_nom TEXT,
        tur TEXT, miqdor INTEGER, ombor_after INTEGER, ts TEXT NOT NULL)""")
    c = con.execute("SELECT COUNT(*) FROM ombor_mahsulot").fetchone()[0]
    if c == 0:
        seed = [
            ("Lyulka", 38, 28), ("Fasadni lesa (Yashil lesa)", 2626, 2235),
            ("Fasad krest (yashil)", 48, 0), ("Stoyka 1.2 m", 202, 0),
            ("Stoyka 1 m", 294, 0), ("Stoyka 4 m", 4087, 3928),
            ("Stoyka 4.5 m", 818, 816), ("Stoyka 5 m", 4280, 3964),
            ("Stoyka 5.5 m", 351, 290), ("Lesa 80", 130, 126),
            ("Qizil havoza", 32, 0), ("Monolit lesa 1.5 m", 600, 425),
            ("Monolit lesa 2 m", 2398, 2178), ("Soyedinitel", 6662, 3502),
            ("Rezba", 9023, 5651), ("Univilka", 5773, 4399),
            ("Tayrot 1 m", 2789, 1674), ("Tayrot 1.2 m", 466, 466),
            ("Taxta", 2839, 2597), ("Balka 3 m", 2712, 1698),
            ("Balka 3.3 m", 10, 0),
        ]
        for i, (nom, total, out) in enumerate(seed):
            pid = _ombor_slug(nom) + f"-{i}"
            con.execute("INSERT INTO ombor_mahsulot (id, name, total, out_qty, sort_order) VALUES (?,?,?,?,?)",
                        (pid, nom, total, out, i))

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
def find_or_create_zakaz(mijoz_id, mahsulot, con=None):
    """(mijoz, mahsulot) uchun mavjud zakazni topadi yoki yangi ochadi. zakaz_id qaytaradi."""
    own = con is None
    if own:
        con = _con()
    z = con.execute("SELECT id FROM zakazlar WHERE mijoz_id=? AND LOWER(TRIM(mahsulot))=LOWER(TRIM(?)) LIMIT 1",
                    (mijoz_id, mahsulot)).fetchone()
    if z:
        zid = z[0]
    else:
        zid = con.execute("INSERT INTO zakazlar (mijoz_id, mahsulot, jami_miqdor, yaratilgan) VALUES (?,?,0,?)",
                          (mijoz_id, mahsulot, now_tk().isoformat())).lastrowid
    if own:
        con.commit()
        con.close()
    return zid


def set_zakaz_total(zakaz_id, jami_miqdor):
    con = _con()
    con.execute("UPDATE zakazlar SET jami_miqdor=? WHERE id=?", (float(jami_miqdor or 0), zakaz_id))
    con.commit()
    con.close()


def get_zakaz(zakaz_id):
    con = _con()
    r = con.execute("SELECT * FROM zakazlar WHERE id=?", (zakaz_id,)).fetchone()
    con.close()
    return dict(r) if r else None


def delete_zakaz(zakaz_id):
    """Zakaz va uning barcha chiqishlari (partiyalari) hamda qaytarishlarini o'chiradi."""
    con = _con()
    pids = [r[0] for r in con.execute("SELECT id FROM partiyalar WHERE zakaz_id=?", (zakaz_id,)).fetchall()]
    for pid in pids:
        con.execute("DELETE FROM qaytarishlar WHERE partiya_id=?", (pid,))
    con.execute("DELETE FROM partiyalar WHERE zakaz_id=?", (zakaz_id,))
    con.execute("DELETE FROM zakazlar WHERE id=?", (zakaz_id,))
    con.commit()
    con.close()


def next_raqam(mijoz_id):
    con = _con()
    r = con.execute("SELECT MAX(partiya_raqam) FROM partiyalar WHERE mijoz_id = ?", (mijoz_id,)).fetchone()
    con.close()
    return (r[0] or 0) + 1


def add_partiya(mijoz_id, mahsulot, miqdor, kunlik_narx, chiqgan_sana, zakaz_id=None, manzil=None,
                brov_kim=None, brov_miqdor=None, birlik=None):
    raqam = next_raqam(mijoz_id)
    brov_kim = (brov_kim or "").strip() or None
    if brov_kim and not brov_miqdor:
        brov_miqdor = miqdor          # soni aytilmasa — hammasi o'shandan
    if not brov_kim:
        brov_miqdor = None
    con = _con()
    if zakaz_id is None:
        zakaz_id = find_or_create_zakaz(mijoz_id, mahsulot, con)
    cur = con.execute(
        """INSERT INTO partiyalar (mijoz_id, partiya_raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, yaratilgan, zakaz_id, manzil, brov_kim, brov_miqdor, birlik)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (mijoz_id, raqam, mahsulot, miqdor, kunlik_narx, chiqgan_sana, now_tk().isoformat(), zakaz_id,
         (manzil or None), brov_kim, brov_miqdor, (birlik or None)),
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


def update_partiya(partiya_id, mahsulot, miqdor, kunlik_narx, chiqgan_sana, manzil=None,
                   brov_kim=None, brov_miqdor=None):
    brov_kim = (brov_kim or "").strip() or None
    if brov_kim and not brov_miqdor:
        brov_miqdor = miqdor
    if not brov_kim:
        brov_miqdor = None
    con = _con()
    con.execute(
        "UPDATE partiyalar SET mahsulot=?, miqdor=?, kunlik_narx=?, chiqgan_sana=?, manzil=?, brov_kim=?, brov_miqdor=? WHERE id=?",
        (mahsulot, miqdor, kunlik_narx, str(chiqgan_sana)[:10], (manzil or None), brov_kim, brov_miqdor, partiya_id),
    )
    con.commit()
    con.close()


def partiyalar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM partiyalar WHERE mijoz_id = ? ORDER BY partiya_raqam", (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def zakazlar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM zakazlar WHERE mijoz_id = ? ORDER BY id", (mijoz_id,)).fetchall()
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


def partiya_hisob(p, today=None, kesim=False):
    today = today or today_tk()
    issue = _pdate(p["chiqgan_sana"])
    daily = p["kunlik_narx"]
    narx = 0.0
    qaytgan = 0.0
    rets = returns_for(p["id"])
    if kesim:
        kes = str(today)[:10]
        rets = [r for r in rets if str(r["qaytgan_sana"])[:10] <= kes]
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
        "birlik": (p.get("birlik") if isinstance(p, dict) else None) or tovar_birlik(p["mahsulot"]),
        "qaytgan": qaytgan,
        "qaytarishlar": [{"id": r["id"], "miqdor": r["miqdor"], "qaytgan_sana": str(r["qaytgan_sana"])[:10]} for r in rets],
    }


def mijoz_detail(mijoz_id, today=None, kesim=False):
    """kesim=True bo'lsa — o'sha sanadagi holat (keyingi harakatlar hisobga olinmaydi)."""
    today = today or today_tk()
    m = get_mijoz(mijoz_id)
    if not m:
        return None
    kes = str(today)[:10] if kesim else None
    raw = partiyalar_of(mijoz_id)
    if kes:
        raw = [p for p in raw if str(p["chiqgan_sana"])[:10] <= kes]
    ps = []
    for p in raw:
        h = partiya_hisob(p, today, kesim=kesim)
        h["zakaz_id"] = p.get("zakaz_id")
        h["manzil"] = p.get("manzil")
        h["brov_kim"] = p.get("brov_kim")
        h["brov_miqdor"] = p.get("brov_miqdor")
        ps.append(h)
    hisoblangan = sum(x["narx"] for x in ps)
    tolovlar_l = tolovlar_of(mijoz_id)
    qo = qoshimcha_of(mijoz_id)
    if kes:
        tolovlar_l = [t for t in tolovlar_l if str(t["sana"])[:10] <= kes]
        qo = [q for q in qo if str(q["sana"])[:10] <= kes]
    tolangan = sum(t["summa"] for t in tolovlar_l)
    yolkira = sum(x["summa"] for x in qo if x["tur"] == "yolkira")
    remont = sum(x["summa"] for x in qo if x["tur"] == "remont")

    # 2 qavatli guruh: zakaz (mahsulot bo'yicha jami) -> ichida chiqishlar
    zmap = {}
    for z in zakazlar_of(mijoz_id):
        zmap[z["id"]] = {
            "id": z["id"], "mahsulot": z["mahsulot"], "jami_miqdor": z["jami_miqdor"],
            "chiqishlar": [], "chiqdi": 0.0, "qolgan": 0.0, "narx": 0.0,
        }
    for h in ps:
        g = zmap.get(h.get("zakaz_id"))
        if not g:
            continue
        g["chiqishlar"].append(h)
        g["chiqdi"] += h["miqdor"]
        g["qolgan"] += h["qolgan"]
        g["narx"] += h["narx"]
    zakazlar_list = []
    for g in zmap.values():
        if not g["chiqishlar"] and (g["jami_miqdor"] or 0) <= 0:
            continue
        g["chiqishlar"].sort(key=lambda x: x["partiya_raqam"])
        g["qoldi"] = max(0.0, (g["jami_miqdor"] or 0) - g["chiqdi"])  # hali chiqarilmagan
        zakazlar_list.append(g)
    zakazlar_list.sort(key=lambda x: (x["mahsulot"] or "").lower())

    # Yetkazmalar: bir kunda chiqqan mahsulotlar = bitta karta (nakladnoy)
    deliv = {}
    for h in ps:
        key = h["chiqgan_sana"][:10]
        g = deliv.get(key)
        if not g:
            g = {"sana": key, "items": [], "jami_narx": 0.0, "jami_dona": 0.0, "qolgan_dona": 0.0}
            deliv[key] = g
        g["items"].append(h)
        g["jami_narx"] += h["narx"]
        g["jami_dona"] += h["miqdor"]
        g["qolgan_dona"] += h["qolgan"]
    for g in deliv.values():
        g["items"].sort(key=lambda x: x["partiya_raqam"])
    yetkazmalar = sorted(deliv.values(), key=lambda x: x["sana"], reverse=True)

    # Qolgan mahsulotlar (mahsulot bo'yicha jamlab)
    qmap = {}
    for h in ps:
        if h["qolgan"] <= 0:
            continue
        key = (h["mahsulot"] or "").strip().lower()
        g = qmap.setdefault(key, {"mahsulot": h["mahsulot"], "qolgan": 0.0, "narx": 0.0,
                                  "birlik": h.get("birlik") or "ta", "manzillar": set()})
        g["qolgan"] += h["qolgan"]
        g["narx"] += h["narx"]
        if h.get("manzil"):
            g["manzillar"].add(h["manzil"])
    qolganlar = []
    for g in qmap.values():
        qolganlar.append({"mahsulot": g["mahsulot"], "qolgan": g["qolgan"], "narx": g["narx"],
                          "birlik": g.get("birlik") or "ta", "manzillar": sorted(g["manzillar"])})
    qolganlar.sort(key=lambda x: -x["qolgan"])

    # Qaytarishlar: bir kunda qaytgan mahsulotlar = bitta yozuv
    rgr = {}
    for h in ps:
        for r in h.get("qaytarishlar", []):
            key = r["qaytgan_sana"][:10]
            g = rgr.get(key)
            if not g:
                g = {"sana": key, "items": []}
                rgr[key] = g
            g["items"].append({
                "mahsulot": h["mahsulot"], "miqdor": r["miqdor"],
                "partiya_raqam": h["partiya_raqam"], "return_id": r["id"],
            })
    qaytarishlar_guruh = sorted(rgr.values(), key=lambda x: x["sana"], reverse=True)

    # Manzillar (ob'ektlar): qaysi tovar qaysi manzilda — faqat qolgani bor bo'lganlar
    mgr = {}
    for h in ps:
        if h["qolgan"] <= 0:
            continue
        key = (h.get("manzil") or "").strip() or "Manzil belgilanmagan"
        g = mgr.get(key)
        if not g:
            g = {"manzil": key, "items": [], "jami_narx": 0.0, "qolgan_dona": 0.0}
            mgr[key] = g
        g["items"].append(h)
        g["jami_narx"] += h["narx"]
        g["qolgan_dona"] += h["qolgan"]
    for g in mgr.values():
        g["items"].sort(key=lambda x: (x["mahsulot"] or "").lower())
    manzillar = sorted(mgr.values(), key=lambda x: (x["manzil"] == "Manzil belgilanmagan", x["manzil"].lower()))

    # Brovdan olinganlar: kimdan qaysi tovardan qancha
    bgr = {}
    for h in ps:
        kim = (h.get("brov_kim") or "").strip()
        bm = float(h.get("brov_miqdor") or 0)
        if not kim or bm <= 0:
            continue
        g = bgr.setdefault(kim, {"kim": kim, "items": [], "jami": 0.0})
        g["items"].append({"mahsulot": h["mahsulot"], "miqdor": bm,
                           "partiya_raqam": h["partiya_raqam"], "qolgan": h["qolgan"]})
        g["jami"] += bm
    for g in bgr.values():
        g["items"].sort(key=lambda x: (x["mahsulot"] or "").lower())
    brovdan = sorted(bgr.values(), key=lambda x: x["kim"].lower())

    jami_qolgan_ = sum(x["qolgan"] for x in ps)
    _st = m.get("status")
    if _st != "sotuv":  # 'sotuv' qo'lda qo'yiladi, avtomat o'zgarmaydi
        _st = "faol" if jami_qolgan_ > 0 else ("nofaol" if ps else _st)

    return {
        "id": mijoz_id, "mijoz": m["ism"], "telefon": m["telefon"], "adres": m.get("adres"),
        "telefonlar": phone_list(m["telefon"]),
        "status": _st,
        "partiyalar": ps,
        "zakazlar": zakazlar_list,
        "yetkazmalar": yetkazmalar,
        "manzillar": manzillar,
        "qolganlar": qolganlar,
        "brovdan": brovdan,
        "brovlar": brov_list(mijoz_id),
        "qaydlar": qaydlar_of(mijoz_id),
        "qayd": get_qayd(mijoz_id),
        "qaytarishlar_guruh": qaytarishlar_guruh,
        "jami": hisoblangan,
        "hisoblangan": hisoblangan,
        "yolkira": yolkira,
        "remont": remont,
        "tolangan": tolangan,
        "qolgan_qarz": hisoblangan + yolkira + remont - tolangan,
        "tolovlar": tolovlar_l,
        "kesim_sana": kes,
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
            "adres": d.get("adres"),
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


# ================= OMBOR (ostatka) =================
def _ombor_norm(s):
    """Nom mosligini tekshirish uchun: qavs ichini olib, faqat harf/raqam/nuqta, kichik harf."""
    s = (s or "").lower()
    out, depth = [], 0
    for ch in s:
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(ch)
    return "".join(ch for ch in "".join(out) if ch.isalnum() or ch == '.')


def _ombor_slug(s):
    r = []
    for ch in (s or "").lower():
        r.append(ch if ch.isalnum() else '-')
    return "".join(r).strip('-') or "tovar"


def ombor_list():
    con = _con()
    rows = con.execute("SELECT id, name, total, out_qty FROM ombor_mahsulot ORDER BY sort_order, name").fetchall()
    con.close()
    return [{"id": r["id"], "name": r["name"], "total": r["total"], "out": r["out_qty"],
             "omborda": r["total"] - r["out_qty"]} for r in rows]


def ombor_by_name(nom):
    key = _ombor_norm(nom)
    if not key:
        return None
    con = _con()
    rows = con.execute("SELECT id, name FROM ombor_mahsulot").fetchall()
    con.close()
    for r in rows:
        if _ombor_norm(r["name"]) == key:
            return r["id"]
    return None


def ombor_move(pid, tur, miqdor):
    """Qo'lda harakat (guard bilan). tur: out/ret/add/writeoff."""
    miqdor = int(round(float(miqdor or 0)))
    if miqdor <= 0:
        return {"ok": False, "xato": "Miqdor noto'g'ri"}
    if tur not in ("out", "ret", "add", "writeoff"):
        return {"ok": False, "xato": "tur"}
    con = _con()
    r = con.execute("SELECT total, out_qty, name FROM ombor_mahsulot WHERE id=?", (pid,)).fetchone()
    if not r:
        con.close()
        return {"ok": False, "xato": "topilmadi"}
    total, out, name = r["total"], r["out_qty"], r["name"]
    if tur == "out":
        if miqdor > total - out:
            con.close(); return {"ok": False, "xato": "Omborda yetarli emas"}
        out += miqdor
    elif tur == "ret":
        if miqdor > out:
            con.close(); return {"ok": False, "xato": "Arendadagidan ko'p"}
        out -= miqdor
    elif tur == "add":
        total += miqdor
    elif tur == "writeoff":
        if miqdor > total - out:
            con.close(); return {"ok": False, "xato": "Omborda yetarli emas"}
        total -= miqdor
    con.execute("UPDATE ombor_mahsulot SET total=?, out_qty=? WHERE id=?", (total, out, pid))
    ombor = total - out
    con.execute("INSERT INTO ombor_tarix (mahsulot_id, mahsulot_nom, tur, miqdor, ombor_after, ts) VALUES (?,?,?,?,?,?)",
                (pid, name, tur, miqdor, ombor, now_tk().isoformat()))
    con.commit()
    con.close()
    return {"ok": True, "id": pid, "name": name, "total": total, "out": out, "omborda": ombor}


def ombor_apply_by_name(nom, tur, miqdor):
    """Ijaradan avtomat chaqiriladi: nom bo'yicha topib out +/- qiladi. Ijarani bloklamaydi."""
    pid = ombor_by_name(nom)
    if not pid:
        return (False, None)
    miqdor = int(round(float(miqdor or 0)))
    if miqdor <= 0:
        return (False, None)
    con = _con()
    r = con.execute("SELECT total, out_qty, name FROM ombor_mahsulot WHERE id=?", (pid,)).fetchone()
    total, out, name = r["total"], r["out_qty"], r["name"]
    if tur == "out":
        out += miqdor
    elif tur == "ret":
        out = max(0, out - miqdor)
    else:
        con.close()
        return (False, None)
    con.execute("UPDATE ombor_mahsulot SET out_qty=? WHERE id=?", (out, pid))
    ombor = total - out
    con.execute("INSERT INTO ombor_tarix (mahsulot_id, mahsulot_nom, tur, miqdor, ombor_after, ts) VALUES (?,?,?,?,?,?)",
                (pid, name, "ij_" + tur, miqdor, ombor, now_tk().isoformat()))
    con.commit()
    con.close()
    return (True, name)


def ombor_set_total(pid, total):
    total = max(0, int(round(float(total or 0))))
    con = _con()
    r = con.execute("SELECT out_qty FROM ombor_mahsulot WHERE id=?", (pid,)).fetchone()
    if not r:
        con.close(); return {"ok": False, "xato": "topilmadi"}
    out = min(r["out_qty"], total)
    con.execute("UPDATE ombor_mahsulot SET total=?, out_qty=? WHERE id=?", (total, out, pid))
    con.commit()
    con.close()
    return {"ok": True, "id": pid, "total": total, "out": out, "omborda": total - out}


def ombor_add(name, total=0):
    name = (name or "").strip()
    if not name:
        return {"ok": False, "xato": "nom"}
    total = max(0, int(round(float(total or 0))))
    con = _con()
    n = con.execute("SELECT COALESCE(MAX(sort_order),0)+1 FROM ombor_mahsulot").fetchone()[0]
    pid = _ombor_slug(name) + "-" + str(int(now_tk().timestamp()))
    con.execute("INSERT INTO ombor_mahsulot (id, name, total, out_qty, sort_order) VALUES (?,?,?,0,?)",
                (pid, name, total, n))
    con.commit()
    con.close()
    return {"ok": True, "id": pid, "name": name, "total": total, "out": 0, "omborda": total}


def ombor_delete(pid):
    con = _con()
    con.execute("DELETE FROM ombor_tarix WHERE mahsulot_id=?", (pid,))
    con.execute("DELETE FROM ombor_mahsulot WHERE id=?", (pid,))
    con.commit()
    con.close()


def ombor_rename(pid, name):
    name = (name or "").strip()
    if not name:
        return {"ok": False, "xato": "nom"}
    con = _con()
    con.execute("UPDATE ombor_mahsulot SET name=? WHERE id=?", (name, pid))
    con.execute("UPDATE ombor_tarix SET mahsulot_nom=? WHERE mahsulot_id=?", (name, pid))
    con.commit()
    con.close()
    return {"ok": True}


def ombor_history(pid=None, limit=200):
    con = _con()
    if pid:
        rows = con.execute("SELECT * FROM ombor_tarix WHERE mahsulot_id=? ORDER BY id DESC LIMIT ?", (pid, limit)).fetchall()
    else:
        rows = con.execute("SELECT * FROM ombor_tarix ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


def ombor_names():
    con = _con()
    rows = con.execute("SELECT name FROM ombor_mahsulot ORDER BY sort_order, name").fetchall()
    con.close()
    return [r["name"] for r in rows]


def ombor_match_name(nom, cutoff=0.62):
    """Nomni ombordagi eng yaqin tovarga moslaydi.
    Qaytaradi: (to'g'ri_nom, aniqmi). Topilmasa (None, False)."""
    import difflib
    s = (nom or "").strip()
    if not s:
        return (None, False)
    pid = ombor_by_name(s)
    names = ombor_names()
    if pid:
        for n in names:
            if _ombor_norm(n) == _ombor_norm(s):
                return (n, True)
    if not names:
        return (None, False)
    key = _ombor_norm(s)
    norm_map = {_ombor_norm(n): n for n in names}
    # 1) ichma-ich (masalan "lesa 80" ~ "lesa")
    for k, n in norm_map.items():
        if k and (k in key or key in k) and abs(len(k) - len(key)) <= 4:
            return (n, False)
    # 2) imloviy yaqinlik
    best = difflib.get_close_matches(key, list(norm_map.keys()), n=1, cutoff=cutoff)
    if best:
        return (norm_map[best[0]], False)
    return (None, False)


# ================= BROVDAN (boshqadan olib turilgan) =================
def brov_add(kim, mahsulot, miqdor, sana=None, izoh=None, mijoz_id=None, kunlik_narx=0):
    kim = (kim or "").strip()
    mahsulot = (mahsulot or "").strip()
    try:
        miqdor = float(miqdor)
    except Exception:
        miqdor = 0
    if not kim or not mahsulot or miqdor <= 0:
        return {"ok": False, "xato": "Kim, mahsulot va soni kerak"}
    sana = str(sana or today_tk().isoformat())[:10]
    con = _con()
    try:
        kunlik_narx = float(kunlik_narx or 0)
    except Exception:
        kunlik_narx = 0
    cur = con.execute("INSERT INTO brovlar (kim, mahsulot, miqdor, sana, izoh, yaratilgan, mijoz_id, kunlik_narx) VALUES (?,?,?,?,?,?,?,?)",
                      (kim, mahsulot, miqdor, sana, (izoh or None), now_tk().isoformat(), mijoz_id, kunlik_narx))
    con.commit()
    bid = cur.lastrowid
    con.close()
    return {"ok": True, "id": bid}


def brov_return(brov_id, miqdor, sana=None):
    try:
        miqdor = float(miqdor)
    except Exception:
        miqdor = 0
    if miqdor <= 0:
        return {"ok": False, "xato": "Soni noto'g'ri"}
    con = _con()
    r = con.execute("SELECT miqdor FROM brovlar WHERE id=?", (brov_id,)).fetchone()
    if not r:
        con.close(); return {"ok": False, "xato": "Topilmadi"}
    q = con.execute("SELECT COALESCE(SUM(miqdor),0) FROM brov_qaytarish WHERE brov_id=?", (brov_id,)).fetchone()[0]
    qolgan = float(r[0]) - float(q or 0)
    if miqdor > qolgan:
        miqdor = qolgan
    if miqdor <= 0:
        con.close(); return {"ok": False, "xato": "Hammasi qaytarilgan"}
    con.execute("INSERT INTO brov_qaytarish (brov_id, miqdor, sana) VALUES (?,?,?)",
                (brov_id, miqdor, str(sana or today_tk().isoformat())[:10]))
    con.commit()
    con.close()
    return {"ok": True, "miqdor": miqdor}


def brov_delete(brov_id):
    con = _con()
    con.execute("DELETE FROM brov_qaytarish WHERE brov_id=?", (brov_id,))
    con.execute("DELETE FROM brovlar WHERE id=?", (brov_id,))
    con.commit()
    con.close()


def brov_ret_delete(ret_id):
    con = _con()
    con.execute("DELETE FROM brov_qaytarish WHERE id=?", (ret_id,))
    con.commit()
    con.close()


def brov_list(mijoz_id=None):
    """Kimdan qancha olingan / qaytarilgan / qolgan — odam bo'yicha guruh."""
    con = _con()
    if mijoz_id is None:
        rows = con.execute("SELECT * FROM brovlar ORDER BY sana DESC, id DESC").fetchall()
    else:
        rows = con.execute("SELECT * FROM brovlar WHERE mijoz_id=? ORDER BY sana DESC, id DESC",
                           (mijoz_id,)).fetchall()
    rets = con.execute("SELECT * FROM brov_qaytarish ORDER BY id").fetchall()
    con.close()
    rmap = {}
    for r in rets:
        rmap.setdefault(r["brov_id"], []).append(dict(r))
    bugun = today_tk()
    gr = {}
    for b in rows:
        b = dict(b)
        rets_b = rmap.get(b["id"], [])
        qaytgan = sum(x["miqdor"] for x in rets_b)
        b["qaytgan"] = qaytgan
        b["qolgan"] = max(0.0, float(b["miqdor"]) - qaytgan)
        b["qaytarishlar"] = rets_b
        # Pul: har qism o'z kuni bo'yicha (chiqgan va qaytgan kun hisobmas)
        narx = float(b.get("kunlik_narx") or 0)
        summa, kunlar = 0.0, 0
        if narx > 0:
            try:
                d0 = date.fromisoformat(str(b["sana"])[:10])
                for rr in rets_b:
                    dr = date.fromisoformat(str(rr["sana"])[:10])
                    k = max(0, (dr - d0).days - 1)
                    summa += narx * float(rr["miqdor"]) * k
                kq = max(0, (bugun - d0).days - 1)
                summa += narx * b["qolgan"] * kq
                kunlar = kq
            except Exception:
                pass
        b["kunlar"] = kunlar
        b["summa"] = summa
        g = gr.setdefault(b["kim"], {"kim": b["kim"], "items": [], "jami": 0.0, "qolgan": 0.0, "summa": 0.0})
        g["items"].append(b)
        g["jami"] += float(b["miqdor"])
        g["qolgan"] += b["qolgan"]
        g["summa"] += summa
    out = sorted(gr.values(), key=lambda x: (-x["qolgan"], x["kim"].lower()))
    return out


# ---------- Qaydlar (o'zimiz uchun izoh) ----------
def qayd_add(mijoz_id, matn, sana=None):
    matn = (matn or "").strip()
    if not matn:
        return {"ok": False, "xato": "Matn bo'sh"}
    con = _con()
    con.execute("INSERT INTO qaydlar (mijoz_id, matn, sana, yaratilgan) VALUES (?,?,?,?)",
                (mijoz_id, matn, str(sana or today_tk().isoformat())[:10], now_tk().isoformat()))
    con.commit()
    con.close()
    return {"ok": True}


def qayd_delete(qid):
    con = _con()
    con.execute("DELETE FROM qaydlar WHERE id=?", (qid,))
    con.commit()
    con.close()


def qaydlar_of(mijoz_id):
    con = _con()
    rows = con.execute("SELECT * FROM qaydlar WHERE mijoz_id=? ORDER BY sana DESC, id DESC",
                       (mijoz_id,)).fetchall()
    con.close()
    return [dict(r) for r in rows]


# ---------- Nom to'g'rilash / ombor qayta hisoblash ----------
def rename_mahsulot(eski, yangi):
    """Barcha partiyalarda mahsulot nomini almashtiradi (nom normalizatsiyasi bo'yicha)."""
    eski_n = _ombor_norm(eski)
    yangi = (yangi or "").strip()
    if not eski_n or not yangi:
        return {"ok": False, "xato": "Eski va yangi nom kerak"}
    con = _con()
    rows = con.execute("SELECT id, mahsulot FROM partiyalar").fetchall()
    ids = [r["id"] for r in rows if _ombor_norm(r["mahsulot"]) == eski_n]
    for pid in ids:
        con.execute("UPDATE partiyalar SET mahsulot=? WHERE id=?", (yangi, pid))
    zrows = con.execute("SELECT id, mahsulot FROM zakazlar").fetchall()
    zids = [r["id"] for r in zrows if _ombor_norm(r["mahsulot"]) == eski_n]
    for zid in zids:
        con.execute("UPDATE zakazlar SET mahsulot=? WHERE id=?", (yangi, zid))
    con.commit()
    con.close()
    return {"ok": True, "partiya": len(ids), "zakaz": len(zids)}


def partiya_nomlari():
    """Partiyalarda uchraydigan mahsulot nomlari + nechta partiyada bor + ombordagi mosligi."""
    con = _con()
    rows = con.execute("SELECT mahsulot, COUNT(*) c FROM partiyalar GROUP BY mahsulot ORDER BY c DESC").fetchall()
    con.close()
    out = []
    for r in rows:
        nom = r["mahsulot"]
        pid = ombor_by_name(nom)
        out.append({"nom": nom, "soni": r["c"], "omborda_bor": pid is not None})
    return out


def ombor_recalc(today=None):
    """Ombordagi 'arendada' sonini partiyalardan qayta hisoblaydi.
    Brovdan olingan ulush hisobga olinmaydi. Nomi ombordagi bilan mos kelmaganlar alohida qaytariladi."""
    today = today or today_tk()
    con = _con()
    prows = con.execute("SELECT * FROM partiyalar").fetchall()
    con.close()
    hisob, nomos = {}, {}
    for p in prows:
        p = dict(p)
        h = partiya_hisob(p, today)
        qolgan = float(h["qolgan"])
        if qolgan <= 0:
            continue
        m = float(p.get("miqdor") or 0)
        b = min(float(p.get("brov_miqdor") or 0), m)
        oz = qolgan * ((m - b) / m) if m > 0 else qolgan   # brovdan ulushi chiqarib tashlanadi
        if oz <= 0:
            continue
        pid = ombor_by_name(p["mahsulot"])
        if pid:
            hisob[pid] = hisob.get(pid, 0.0) + oz
        else:
            nomos[p["mahsulot"]] = nomos.get(p["mahsulot"], 0.0) + oz

    con = _con()
    ozgargan = []
    for row in con.execute("SELECT id, name, total, out_qty FROM ombor_mahsulot").fetchall():
        yangi = int(round(hisob.get(row["id"], 0.0)))
        if yangi != int(row["out_qty"]):
            ozgargan.append({"name": row["name"], "eski": int(row["out_qty"]), "yangi": yangi,
                             "omborda": int(row["total"]) - yangi})
            con.execute("UPDATE ombor_mahsulot SET out_qty=? WHERE id=?", (yangi, row["id"]))
    con.commit()
    con.close()
    return {"ok": True, "ozgargan": ozgargan,
            "nomos": sorted(nomos.items(), key=lambda x: -x[1])}


def get_qayd(mijoz_id):
    """Mijozning yagona qayd matni. Eski alohida qaydlar bo'lsa — birlashtiriladi."""
    con = _con()
    r = con.execute("SELECT qayd FROM mijozlar WHERE id=?", (mijoz_id,)).fetchone()
    cur = (r[0] if r else None)
    con.close()
    if cur is not None:
        return cur
    eski = qaydlar_of(mijoz_id)
    if eski:
        return "\n".join(f"{str(q['sana'])[:10]} — {q['matn']}" for q in reversed(eski))
    return ""


def set_qayd(mijoz_id, matn):
    con = _con()
    con.execute("UPDATE mijozlar SET qayd=? WHERE id=?", (matn or "", mijoz_id))
    con.commit()
    con.close()
    return {"ok": True}


# ---------- Tovar lug'ati (nom tekshirish uchun) ----------
# (nom, birlik): "kom" — komplekt (1 kom = 2 ta), "ta" — dona
TOVAR_DEFAULT = [
    ("Oyoq 2m", "kom"), ("Qaychi 2m", "kom"), ("Oyoq 1.5m", "kom"), ("Qaychi 1.5m", "kom"),
    ("Rezba 1m", "ta"), ("Univilka", "ta"), ("Soedinitel", "ta"), ("Balka 3m", "ta"),
    ("Tayrot", "ta"), ("Gayka tayrot", "ta"),
    ("Lesa oyoq", "kom"), ("Lesa80 oyoq", "kom"), ("Lesa qaychi", "kom"),
    ("Taxta", "ta"), ("Balon", "ta"),
    ("Stoyka 4m", "ta"), ("Stoyka 4.5m", "ta"), ("Stoyka 5m", "ta"),
    ("Stoyka 5.5m", "ta"), ("Stoyka 1.2m", "ta"), ("Lyulka", "kom"),
]
KOM_TA = 2.0   # 1 komplekt = 2 ta


def tovar_juftlar():
    """[(nom, birlik), ...] — sozlamadan yoki default."""
    v = get_sozlama("tovar_royxat")
    if v:
        out = []
        for x in v.replace(",", "\n").split("\n"):
            x = x.strip()
            if not x:
                continue
            if "|" in x:
                nom, b = x.split("|", 1)
                out.append((nom.strip(), (b.strip().lower() or "ta")))
            else:
                p = x.rsplit(" ", 1)
                if len(p) == 2 and p[1].lower() in ("kom", "komplekt", "ta", "dona"):
                    out.append((p[0].strip(), "kom" if p[1].lower().startswith("kom") else "ta"))
                else:
                    out.append((x, "ta"))
        if out:
            return out
    return list(TOVAR_DEFAULT)


def tovar_royxat():
    """Faqat nomlar (tekshirish uchun)."""
    return [n for n, _b in tovar_juftlar()]


def tovar_barcha():
    """Tekshirish uchun to'liq ro'yxat: lug'at + ombordagi tovarlar."""
    out, korilgan = [], set()
    for n in tovar_royxat():
        k = _ombor_norm(n)
        if k and k not in korilgan:
            korilgan.add(k)
            out.append(n)
    try:
        for n in ombor_names():
            k = _ombor_norm(n)
            if k and k not in korilgan:
                korilgan.add(k)
                out.append(n)
    except Exception:
        pass
    return out


def tovar_birlik(nom):
    """Tovarning asosiy birligi: 'kom' yoki 'ta'."""
    key = _ombor_norm(nom)
    for n, b in tovar_juftlar():
        if _ombor_norm(n) == key:
            return b
    return "ta"


def set_tovar_royxat(lst):
    if isinstance(lst, str):
        lst = [x.strip() for x in lst.replace(",", "\n").split("\n") if x.strip()]
    satr = []
    for x in lst:
        if isinstance(x, (list, tuple)):
            satr.append(f"{x[0]}|{x[1]}")
        else:
            satr.append(str(x))
    set_sozlama("tovar_royxat", "\n".join(satr))
    return {"ok": True, "soni": len(satr)}


def ombor_koeff(nom, yozilgan_birlik):
    """Ombordan qancha ayirish kerakligi koeffitsienti.
    Tovar 'kom' da yuritilsa: 'kom' yozilsa 1, 'ta' yozilsa 0.5."""
    asos = tovar_birlik(nom)
    y = (yozilgan_birlik or "").lower()
    if asos == "kom":
        if y.startswith("ta") or y.startswith("don"):
            return 1.0 / KOM_TA
        return 1.0
    # asos 'ta' bo'lsa: 'kom' yozilsa 1 kom = 2 ta
    if y.startswith("kom"):
        return KOM_TA
    return 1.0


