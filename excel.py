"""Mijoz hisobotini Excel (.xlsx) qilib beradi."""
from io import BytesIO
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side


BRAND = "1F4B45"
LIGHT = "EEF2F0"
_thin = Side(style="thin", color="D9DFDD")
BORDER = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)


def _dmy(s):
    s = str(s or "")[:10]
    p = s.split("-")
    return f"{p[2]}.{p[1]}.{p[0][2:]}" if len(p) == 3 else s


def _som(n):
    return f"{round(n or 0):,}".replace(",", " ")


def mijoz_excel(d):
    wb = Workbook()
    ws = wb.active
    ws.title = "Hisobot"
    ws.column_dimensions["A"].width = 6
    for c in "BCDEFGH":
        ws.column_dimensions[c].width = 15
    ws.column_dimensions["B"].width = 20

    def title(text, row, span=8, fill=BRAND, color="FFFFFF", size=13):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=span)
        c = ws.cell(row=row, column=1, value=text)
        c.font = Font(bold=True, color=color, size=size)
        c.fill = PatternFill("solid", fgColor=fill)
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[row].height = 22

    r = 1
    title(f"MIJOZ: {d['mijoz']}", r); r += 1
    for lbl, val in [("Telefon", d.get("telefon") or "-"), ("Manzil", d.get("adres") or "-"),
                     ("Status", d.get("status") or "-")]:
        ws.cell(row=r, column=1, value=lbl).font = Font(bold=True)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=8)
        ws.cell(row=r, column=2, value=val)
        r += 1
    r += 1

    # Partiyalar jadvali
    title("PARTIYALAR (chiqgan mollar)", r); r += 1
    heads = ["№", "Mahsulot", "Jami", "Qolgan", "Kunlik narx", "Chiqgan sana", "Kun", "Summa (so'm)"]
    for i, h in enumerate(heads, 1):
        c = ws.cell(row=r, column=i, value=h)
        c.font = Font(bold=True)
        c.fill = PatternFill("solid", fgColor=LIGHT)
        c.border = BORDER
        c.alignment = Alignment(horizontal="center")
    r += 1
    for p in d.get("partiyalar", []):
        row = [p["partiya_raqam"], p["mahsulot"], p["miqdor"], p["qolgan"],
               p["kunlik_narx"], _dmy(p["chiqgan_sana"]), p["kunlar"], round(p["narx"])]
        for i, v in enumerate(row, 1):
            c = ws.cell(row=r, column=i, value=v)
            c.border = BORDER
            if i in (3, 4, 5, 7, 8):
                c.alignment = Alignment(horizontal="right")
        r += 1
    r += 1

    # Qaytarishlar
    rets = [(p, rr) for p in d.get("partiyalar", []) for rr in p.get("qaytarishlar", [])]
    if rets:
        title("QAYTARISHLAR", r); r += 1
        for h, col in [("Partiya", 1), ("Mahsulot", 2), ("Soni", 3), ("Sana", 4)]:
            c = ws.cell(row=r, column=col, value=h)
            c.font = Font(bold=True); c.fill = PatternFill("solid", fgColor=LIGHT); c.border = BORDER
        r += 1
        for p, rr in rets:
            for i, v in enumerate([p["partiya_raqam"], p["mahsulot"], rr["miqdor"], _dmy(rr["qaytgan_sana"])], 1):
                ws.cell(row=r, column=i, value=v).border = BORDER
            r += 1
        r += 1

    # To'lovlar
    if d.get("tolovlar"):
        title("TO'LOVLAR (predoplata)", r); r += 1
        for p in d["tolovlar"]:
            ws.cell(row=r, column=1, value=_dmy(p["sana"]))
            ws.cell(row=r, column=2, value=(p.get("izoh") or "to'lov"))
            ws.cell(row=r, column=3, value=round(p["summa"])).alignment = Alignment(horizontal="right")
            r += 1
        r += 1

    # Qo'shimcha (yo'lkira / remont)
    if d.get("qoshimcha"):
        title("QO'SHIMCHA (yo'lkira / remont)", r); r += 1
        for q in d["qoshimcha"]:
            ws.cell(row=r, column=1, value=_dmy(q["sana"]))
            ws.cell(row=r, column=2, value=("Yo'lkira" if q["tur"] == "yolkira" else "Remont"))
            ws.cell(row=r, column=3, value=round(q["summa"])).alignment = Alignment(horizontal="right")
            r += 1
        r += 1

    # Yakuniy hisob
    title("YAKUNIY HISOB", r); r += 1
    rows = [("Hisoblangan (ijara)", d.get("hisoblangan", 0)),
            ("Yo'lkira", d.get("yolkira", 0)),
            ("Remont", d.get("remont", 0)),
            ("To'langan", d.get("tolangan", 0)),
            ("QOLGAN QARZ", d.get("qolgan_qarz", 0))]
    for lbl, val in rows:
        bold = lbl == "QOLGAN QARZ"
        c1 = ws.cell(row=r, column=1, value=lbl)
        ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=2)
        c1.font = Font(bold=bold)
        c3 = ws.cell(row=r, column=3, value=f"{_som(val)} so'm")
        c3.font = Font(bold=bold)
        c3.alignment = Alignment(horizontal="right")
        if bold:
            for col in (1, 2, 3):
                ws.cell(row=r, column=col).fill = PatternFill("solid", fgColor=LIGHT)
        r += 1

    bio = BytesIO()
    wb.save(bio)
    bio.seek(0)
    return bio
