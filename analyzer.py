"""
Qo'ng'iroq audiosini Gemini bilan tahlil qilish.
Gemini xom audioni to'g'ridan-to'g'ri tinglaydi — shuning uchun u
transkript bilan birga OHANG/EMOTSIYA ni ham baholaydi (faqat matn emas).
"""
import json
import tempfile
from typing import Optional

from google import genai
from google.genai import types

from config import settings
from protocol import protocol_as_text


SYSTEM_PROMPT = """Sen — lesa va kovka mahsulotlari IJARASI bilan shug'ullanadigan
kompaniyaning call-markazi sifat nazoratchisisan. Vazifang: operatorning mijoz bilan
qo'ng'irog'ini tinglab, kompaniya PROTOKOLIGA qanchalik rioya qilganini, muomala
madaniyatini va qo'ldan ketgan imkoniyatlarni baholash.

Audio asosan O'ZBEK tilida (ba'zan rus so'zlari aralashadi). Audioni diqqat bilan
tingla — nafaqat so'zlarni, balki OHANGNI ham (xotirjammi, keskin, quruq, iliqmi).

MUHIM QOIDALAR:
- Faqat audioda haqiqatan bo'lgan narsaga asoslan. O'zingdan to'qib chiqarma.
- Agar biror band SUHBAT KONTEKSTIGA umuman mos kelmasa (masalan mijoz butunlay
  boshqa masala uchun qo'ng'iroq qilgan), o'sha band uchun status = "not_applicable" qo'y.
- "Aytishi kerak edi, lekin aytmadi" qismida — protokolda yo'q bo'lsa ham, yaxshi
  operator ushlab qolgan bo'lardi degan imkoniyatlarni o'zing top (masalan mijoz
  ikkilanganda savol bermaslik, yopishni unutish, narxdan keyin sukut saqlash).
- Baholaring adolatli bo'lsin: quruq "ha/yo'q" javob bergan joyni imkoniyat sifatida belgila.

Javobni FAQAT o'zbek tilida yoz."""


# Gemini qaytaradigan JSON tuzilishi (promptda tushuntiriladi)
OUTPUT_SPEC = """Javobni FAQAT quyidagi JSON ko'rinishida qaytar (boshqa matn, izoh yoki ```
belgilari QO'SHMA):

{
  "transcript": "Qo'ng'iroqning to'liq transkripti. Har gapdan oldin 'Operator:' yoki 'Mijoz:' deb yoz.",
  "call_topic": "Qo'ng'iroq nima haqida edi — 1 jumla.",
  "call_type": "Qo'ng'iroq turi: 'sotuv_arenda' | 'mavjud_buyurtma' | 'muammo_shikoyat' | 'umumiy_savol' | 'ichki_xodim' | 'boshqa'. ('ichki_xodim' = xodimlar o'zaro, mijoz emas)",
  "overall_score": 0-100 oralig'ida butun son (protokol + muomala umumiy bahosi),
  "protocol": [
    {
      "id": band raqami (1-8),
      "name": "band nomi",
      "status": "done" | "partial" | "missed" | "not_applicable",
      "evidence": "audiodan qisqa dalil yoki nima uchun bajarilmagani",
      "comment": "qisqa izoh yoki maslahat"
    }
    // 8 ta band uchun ham
  ],
  "missed_opportunities": ["aytishi kerak edi, lekin aytmagan aniq nuqtalar ro'yxati"],
  "communication": {
    "greeting": "salomlashish qanday bo'ldi",
    "politeness": "xushmuomalalik darajasi haqida qisqa baho",
    "operator_tone": "operator ohangi (xotirjam / keskin / quruq / iliq va h.k.)",
    "client_mood": "mijozning kayfiyati/ohangi",
    "professionalism_score": 0-10 oralig'ida butun son
  },
  "red_flags": ["jiddiy muammolar bo'lsa (qo'polik, noto'g'ri ma'lumot, mijozni yo'qotish). yo'q bo'lsa bo'sh ro'yxat"],
  "recommendations": ["shu operatorga 2-4 ta aniq, amaliy maslahat"],
  "dispute": {
    "has_conflict": true yoki false (suhbatda tortishuv/kelishmovchilik/urishish bo'ldimi),
    "summary": "agar nizo bo'lsa — nima ustida bahslashishdi, qisqa. Bo'lmasa bo'sh matn.",
    "who_is_right": "kim haq: 'operator' | 'mijoz' | 'ikkalasi qisman' | 'aniq emas'. Nizo bo'lmasa bo'sh.",
    "reason": "nega shunday xulosaga kelding — audiodagi dalilga asoslanib qisqa izoh. Nizo bo'lmasa bo'sh."
  }
}"""


def _build_prompt() -> str:
    return f"""{SYSTEM_PROMPT}

===== KOMPANIYA PROTOKOLI (operator SHU bandlarni bajarishi kerak) =====
{protocol_as_text()}

===== BAHOLASH TARTIBI =====
1. Avval audioni to'liq transkript qil (operator/mijoz ajratib).
2. QO'NG'IROQ TURINI aniqla (call_type). Bu juda muhim, chunki baholash
   shunga bog'liq:
   - "sotuv_arenda" (mijoz mahsulot/arenda so'rayapti) → 8 band TO'LIQ baholanadi.
   - "mavjud_buyurtma" / "muammo_shikoyat" / "umumiy_savol" → faqat MANTIQAN
     mos keladigan bandlarni bahola. Mavzuga aloqasi yo'q bandlarni
     "not_applicable" qo'y (masalan mijoz "buyurtmam qayerda?" desa, unga
     kovka yoki katalog taklif qilishni talab qilma).
   - "ichki_xodim" (xodimlar o'zaro, mijoz emas — masalan haydovchiga
     "200 ta lesa opketdingmi?") → PROTOKOL UMUMAN QO'LLANILMAYDI. Barcha
     bandlarni "not_applicable" qo'y, overall_score ni faqat aniqlik va
     muomilaga qarab ber. Sotuv talab qilma.
3. MUHIM ADOLAT QOIDASI: "not_applicable" bandlar umumiy ballni
   PASAYTIRMASIN. Ball faqat shu qo'ng'iroqqa HAQIQATAN tegishli bandlar
   bo'yicha hisoblanadi. Mavzuga aloqasi yo'q narsa uchun jazolanmasin.
4. "Aytishi kerak edi, lekin aytmadi" — faqat shu qo'ng'iroq turiga MOS
   kelsagina yoz. Ichki yoki xizmat qo'ng'irog'iga sotuv imkoniyati yozma.
5. Muomala madaniyati va ohangni bahola.
6. Agar tortishuv bo'lsa — xolisona kim haq ekanini aniqla (dispute).
7. Umumiy ball va aniq maslahatlar ber.

{OUTPUT_SPEC}"""


def _extract_json(text: str) -> dict:
    """Modeldan kelgan matndan JSON ni ishonchli ajratib olish."""
    text = text.strip()
    if text.startswith("```"):
        # ```json ... ``` bo'lsa tozalash
        text = text.split("```", 2)[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip("` \n")
    # birinchi { dan oxirgi } gacha
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1:
        text = text[start : end + 1]
    return json.loads(text)


def analyze_audio(audio_bytes: bytes, mime_type: str = "audio/mp3") -> dict:
    """
    Xom audio baytlarni Gemini ga berib, tuzilgan tahlil (dict) qaytaradi.
    20 MB dan katta fayllar uchun Files API ishlatiladi.
    """
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY o'rnatilmagan. .env ga qo'shing.")

    client = genai.Client(api_key=settings.GEMINI_API_KEY)
    prompt = _build_prompt()
    config = types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2,
    )

    if len(audio_bytes) <= 19 * 1024 * 1024:
        # inline — tez yo'l
        contents = [
            prompt,
            types.Part.from_bytes(data=audio_bytes, mime_type=mime_type),
        ]
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL, contents=contents, config=config
        )
    else:
        # katta fayl — Files API orqali yuklaymiz
        suffix = ".mp3" if "mp3" in mime_type else ".wav" if "wav" in mime_type else ".ogg"
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            uploaded = client.files.upload(file=tmp.name)
        resp = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=[prompt, uploaded],
            config=config,
        )

    return _extract_json(resp.text)
