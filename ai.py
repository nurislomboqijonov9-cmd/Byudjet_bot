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
    kategoriya: str | None = Field(default=None, description="Kategoriya: Oziq-ovqat, Transport, Uy-joy, Kommunal, Salomatlik, Ish haqi, Qarz va h.k.")
    izoh: str | None = Field(default=None, description="Qisqa izoh")
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
- Summani so'mda raqam qilib ber. "90 ming" = 90000, "2 million" yoki "2 mln" = 2000000,
  "yarim million" = 500000, "bir yarim ming" = 1500, "20 dollar" bo'lsa summani 20 qoldirib izohga "dollar" yoz.
- Qarz turlarida "kim" maydoniga odam ismini yoz (bosh harf bilan). Boshqa turlarda null.
- Kategoriyani mazmundan aniqla. Qarz bo'lsa kategoriya "Qarz".
- Agar bu moliyaviy amal bo'lmasa (salomlashish, savol va h.k.) — tushunildi=false qil.
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


def from_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> Tranzaksiya:
    part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    return _extract([part, "Yuqoridagi ovozli xabarni tahlil qil."])


def from_text(text: str) -> Tranzaksiya:
    return _extract([text])
