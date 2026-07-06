"""Gemini yordamida audio yoki matndan tranzaksiyani ajratib olish.

Gemini audioni to'g'ridan-to'g'ri o'zbek tilida tushunadi va JSON qaytaradi —
alohida ovoz-matn (speech-to-text) tizimi kerak emas.
"""
import os
from enum import Enum
from google import genai
from google.genai import types
from pydantic import BaseModel, Field

MODEL = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

_client = None


def client():
    global _client
    if _client is None:
        _client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    return _client


class Tur(str, Enum):
    kirim = "kirim"
    chiqim = "chiqim"
    qarz_berdim = "qarz_berdim"
    qarz_oldim = "qarz_oldim"
    qarz_qaytardim = "qarz_qaytardim"
    qarz_qaytarildi = "qarz_qaytarildi"


class Tranzaksiya(BaseModel):
    tushunildi: bool = Field(description="Foydalanuvchi moliyaviy amal aytdimi (True) yoki tushunarsiz (False)")
    tur: Tur | None = Field(default=None, description="Amal turi")
    summa: float | None = Field(default=None, description="Summa so'mda, faqat raqam")
    kim: str | None = Field(default=None, description="Qarz bilan bog'liq odam ismi, bo'lmasa null")
    valyuta: str | None = Field(default=None, description="Valyuta: 'som' (odatiy), 'dollar', 'yevro' yoki 'rubl'. So'm bo'lsa 'som' yoki null.")
    kategoriya: str | None = Field(default=None, description="Kategoriya: Oziq-ovqat, Transport, Uy-joy, Kommunal, Salomatlik, Ish haqi, Qarz va h.k.")
    izoh: str | None = Field(default=None, description="Qisqa izoh")
    eslatma_vaqti: str | None = Field(default=None, description="Agar foydalanuvchi vaqt aytsa (masalan 'bugun kechga', 'ertaga', 'juma kuni') — eslatma vaqti ISO 8601 formatda (YYYY-MM-DDTHH:MM:SS). Vaqt aytilmasa null.")
    limit_belgilash: float | None = Field(default=None, description="Agar foydalanuvchi oylik xarajat normasi/limitini o'rnatmoqchi bo'lsa — summa so'mda. Aks holda null.")
    transkript: str = Field(description="Aynan nima deyilgani (audio matni yoki kiritilgan matn)")


PROMPT = """Sen shaxsiy byudjet yordamchisisan. Foydalanuvchi o'zbek tilida (ovozli yoki matnli)
pul harakatini aytadi. Uni tuzilgan ma'lumotga aylantir.

TURLAR:
- "kirim": pul kirdi (maosh, sotuvdan daromad, sovg'a puli)
- "chiqim": pul sarfladi (xarid, ovqat, transport, kommunal)
- "qarz_berdim": kimgadir qarz berdi (masalan: "Umarga 90 ming qarz berdim")
- "qarz_oldim": kimdandir qarz oldi ("Alidan 200 ming qarz oldim")
- "qarz_qaytardim": o'zi olgan qarzini qaytardi ("Aliga qarzimni qaytardim")
- "qarz_qaytarildi": unga qarzni qaytarishdi ("Umar qarzini qaytardi")

QOIDALAR:
- Summani RAQAM qilib ber. "90 ming" = 90000, "2 million" yoki "2 mln" = 2000000,
  "yarim million" = 500000, "bir yarim ming" = 1500.
- VALYUTA: agar boshqa valyutada aytilsa (dollar/$/dollor → 'dollar', yevro/euro → 'yevro', rubl/rubl → 'rubl'),
  summani O'SHA valyutadagi raqam qilib ber va valyuta maydonini to'ldir. So'mga O'ZING o'girma — bot o'giradi.
  So'm bo'lsa valyuta = 'som' yoki null.
- Qarz turlarida "kim" maydoniga odam ismini yoz (bosh harf bilan). Boshqa turlarda null.
- Kategoriyani mazmundan aniqla. Qarz bo'lsa kategoriya "Qarz".
- LIMIT BELGILASH: agar foydalanuvchi oylik xarajat normasini o'rnatmoqchi bo'lsa
  (masalan "oylik limitni 2 million qil", "normani 3 mln qilib qo'y") — limit_belgilash ga summani (so'mda) yoz,
  tushunildi=true, tur=null qil. Aks holda limit_belgilash = null.
- Agar bu moliyaviy amal bo'lmasa (salomlashish, savol va h.k.) — tushunildi=false qil.
- ESLATMA VAQTI: agar foydalanuvchi qachondir eslatishni istasa (masalan "bugun kechga berishi kerak",
  "ertaga qaytaradi", "juma kuni") — eslatma_vaqti ni ISO 8601 (YYYY-MM-DDTHH:MM:SS) qilib ber.
  Quyidagi taxminiy soatlardan foydalan: "ertalab"≈08:00, "tush/tushlik"≈13:00, "kech/kechqurun/kechga"≈19:00,
  "kechasi"≈21:00. Faqat kun aytilsa (masalan "ertaga")≈09:00. Vaqt umuman aytilmasa — null.
  Sanani hisoblashda YUQORIDA berilgan hozirgi vaqtga tayan.
- "transkript" ga aynan eshitilgan/kiritilgan matnni yoz.
"""


def _extract(parts):
    resp = client().models.generate_content(
        model=MODEL,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=Tranzaksiya,
            system_instruction=PROMPT,
        ),
    )
    if getattr(resp, "parsed", None) is not None:
        return resp.parsed
    import json
    return Tranzaksiya(**json.loads(resp.text))


def _now_context():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Tashkent"))
    except Exception:
        now = datetime.now()
    kunlar = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
    return (f"Hozirgi vaqt (Toshkent, UTC+5): {now.strftime('%Y-%m-%dT%H:%M:%S')}, "
            f"hafta kuni: {kunlar[now.weekday()]}.")


def from_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> Tranzaksiya:
    part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    return _extract([_now_context(), part, "Yuqoridagi ovozli xabarni tahlil qil."])


def from_text(text: str) -> Tranzaksiya:
    return _extract([_now_context(), text])


def from_image(image_bytes: bytes, mime_type: str = "image/jpeg") -> Tranzaksiya:
    part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)
    yol = ("Bu chek/kvitansiya rasmi. Undagi JAMI to'lov summasini (JAMI/ITOGO/TOTAL) 'chiqim' sifatida ol. "
           "Do'kon yoki joy nomini izohga yoz. Kategoriyani do'kon turiga qarab aniqla "
           "(masalan market/oziq-ovqat do'koni → Oziq-ovqat, dorixona → Salomatlik). "
           "Agar summa dollar/yevroda bo'lsa valyutani belgila. Chek tushunarsiz bo'lsa tushunildi=false.")
    return _extract([_now_context(), part, yol])
