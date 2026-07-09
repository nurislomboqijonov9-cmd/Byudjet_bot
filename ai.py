"""Gemini yordamida ijara amallarini ovoz/matndan ajratib olish."""
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


class Amal(str, Enum):
    chiqish = "chiqish"
    qaytarish = "qaytarish"
    tolov = "tolov"
    malumot = "malumot"


class IjaraAmal(BaseModel):
    tushunildi: bool = Field(description="Ijara amali aytildimi (True) yoki tushunarsiz (False)")
    amal: Amal | None = Field(default=None, description="'chiqish' (ijaraga berildi) yoki 'qaytarish'")
    mijoz: str | None = Field(default=None, description="Mijoz ismi (bosh harf bilan)")
    telefon: str | None = Field(default=None, description="Mijoz telefon raqami, aytilsa (masalan 'raqami 998901234567'). Aytilmasa null.")
    mahsulot: str | None = Field(default=None, description="Mahsulot nomi (masalan: lesa, temir ustun). Faqat chiqishda.")
    miqdor: float | None = Field(default=None, description="Dona soni. Qaytarishda 'hammasi' bo'lsa null.")
    hammasi: bool = Field(default=False, description="Qaytarishda hamma qolgan mahsulot qaytarilsa True")
    kunlik_narx: float | None = Field(default=None, description="Bitta dona uchun BIR KUNLIK ijara narxi (so'mda). Faqat chiqishda.")
    partiya: int | None = Field(default=None, description="Qaytarishda qaysi partiya raqami (masalan '1-partiya' -> 1). Aytilmasa null.")
    summa: float | None = Field(default=None, description="To'lov/predoplata puli so'mda (masalan '1 million' -> 1000000). Faqat to'lovda.")
    kun: int | None = Field(default=None, description="To'lov kun bilan aytilsa nechta kun (masalan '10 kunlik' -> 10). Faqat to'lovda.")
    sana: str | None = Field(default=None, description="Sana ISO (YYYY-MM-DD). Aytilmasa null (bugun bo'ladi).")
    transkript: str = Field(description="Aynan nima deyilgani")


PROMPT = """Sen ijara (arenda) hisobi yordamchisisan. Korxona lesa va temir mahsulotlarini
KUNLIK ijaraga beradi. Foydalanuvchi o'zbek tilida (ovoz yoki matn) gapiradi.

AMALLAR:
- "chiqish": mijozga mahsulot ijaraga chiqdi.
  Masalan: "Abbosga 100 ta lesa chiqdi kuniga 2000 so'm" ->
  amal=chiqish, mijoz=Abbos, mahsulot=lesa, miqdor=100, kunlik_narx=2000
- "qaytarish": mijoz mahsulotni qaytardi.
  Masalan: "Abbos 1-partiyadan 30 ta qaytardi" -> amal=qaytarish, mijoz=Abbos, partiya=1, miqdor=30
  "Karim 2-partiyadan hammasini qaytardi" -> amal=qaytarish, mijoz=Karim, partiya=2, hammasi=true
- "tolov": mijoz oldindan yoki keyin pul to'ladi (predoplata / to'lov / qarzini yopdi).
  Masalan: "Abbos 1 million predoplata berdi" -> amal=tolov, mijoz=Abbos, summa=1000000
  "Karim 10 kunlik berdi" -> amal=tolov, mijoz=Karim, kun=10
  "Abbos 500 ming to'ladi" -> amal=tolov, mijoz=Abbos, summa=500000
- "malumot": foydalanuvchi HECH QANDAY amal aytmasdan shunchaki mijoz ismini yozsa, yoki uning
  ma'lumoti/qarzini so'rasa. Masalan: "Do'smatov Davron", "Davron", "Karim qancha qarzi bor",
  "Abbos malumoti" -> amal=malumot, mijoz=ism. (Agar chiqish/qaytarish/tolov aniq aytilsa — malumot EMAS.)

QOIDALAR:
- Sonlarni raqam qil: "100 ta"=100, "2 ming"=2000, "yarim million"=500000.
- kunlik_narx — bitta dona uchun BIR KUNLIK narx (so'mda).
- "hammasini/hammasi/butunlay/to'liq qaytardi" bo'lsa hammasi=true, miqdor=null.
- partiya raqamini int qil ("1-partiya"/"birinchi partiya"=1). Aytilmasa null.
- sana aytilsa ISO (YYYY-MM-DD) qil (yuqoridagi hozirgi vaqtga tayan). Aytilmasa null.
- mijoz ismini bosh harf bilan yoz.
- Agar telefon raqam aytilsa (masalan "raqami 90 123 45 67", "telefoni ...") — telefon maydoniga yoz.
"""


def _extract(parts):
    resp = client().models.generate_content(
        model=MODEL,
        contents=parts,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=IjaraAmal,
            system_instruction=PROMPT,
        ),
    )
    if getattr(resp, "parsed", None) is not None:
        return resp.parsed
    import json
    return IjaraAmal(**json.loads(resp.text))


def _now_context():
    from datetime import datetime
    try:
        from zoneinfo import ZoneInfo
        now = datetime.now(ZoneInfo("Asia/Tashkent"))
    except Exception:
        now = datetime.now()
    kunlar = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]
    return f"Hozirgi sana (Toshkent): {now.strftime('%Y-%m-%d')}, hafta kuni: {kunlar[now.weekday()]}."


def from_audio(audio_bytes: bytes, mime_type: str = "audio/ogg") -> IjaraAmal:
    part = types.Part.from_bytes(data=audio_bytes, mime_type=mime_type)
    return _extract([_now_context(), part, "Yuqoridagi ovozli xabarni tahlil qil."])


def from_text(text: str) -> IjaraAmal:
    return _extract([_now_context(), text])
