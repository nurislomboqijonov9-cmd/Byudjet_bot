"""
Call Analyzer — lesa/kovka ijara kompaniyasi uchun qo'ng'iroq sifat nazorati.

Endpointlar:
  GET  /                  -> dashboard (oxirgi tahlillar + operator reytingi)
  GET  /call/{id}         -> bitta qo'ng'iroq batafsil (transkript bilan)
  POST /analyze           -> audio faylni QO'LDA yuklab tahlil qilish (pilot uchun)
  POST /webhook/telephony -> telefoniya (Bitrix va h.k.) chaqiradigan webhook
  GET  /health            -> tekshiruv
"""
import asyncio
import json
import traceback

import httpx
from fastapi import FastAPI, UploadFile, File, Form, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse

from config import settings
from analyzer import analyze_audio
from telegram import (format_message, format_short, send_message, reply_to,
                      send_audio_analysis, edit_caption, answer_callback,
                      format_dialog)
import json as _json_mod
import storage
import bitrix
import amo

app = FastAPI(title="Call Analyzer")


@app.on_event("startup")
def _startup():
    storage.init_db()
    if settings.AUTO_POLL and _crm_configured():
        asyncio.create_task(_poll_loop())


def _crm_configured() -> bool:
    if settings.CRM_SOURCE == "amocrm":
        return bool(settings.AMOCRM_BASE_URL and settings.AMOCRM_ACCESS_TOKEN)
    return bool(settings.BITRIX_WEBHOOK_URL)


@app.get("/health")
def health():
    return {
        "ok": True,
        "gemini_key": bool(settings.GEMINI_API_KEY),
        "telegram": bool(settings.TELEGRAM_BOT_TOKEN and settings.TELEGRAM_CHAT_ID),
        "model": settings.GEMINI_MODEL,
        "crm_source": settings.CRM_SOURCE,
        "crm_configured": _crm_configured(),
        "auto_poll": settings.AUTO_POLL,
        "last_marker": storage.kv_get(_marker_key(), "0"),
    }


def _marker_key() -> str:
    return "last_amo_ts" if settings.CRM_SOURCE == "amocrm" else "last_bitrix_id"


def _process(audio_bytes: bytes, mime_type: str, call: dict) -> dict:
    """Umumiy quvur: tahlil -> saqlash -> Telegram (audio + tugmali qisqa tahlil)."""
    result = analyze_audio(audio_bytes, mime_type=mime_type)
    row_id = storage.save_call(call, result)
    try:
        ext = "mp3"
        if "wav" in mime_type:
            ext = "wav"
        elif "ogg" in mime_type or "opus" in mime_type:
            ext = "ogg"
        elif "m4a" in mime_type or "mp4" in mime_type or "aac" in mime_type:
            ext = "m4a"
        op = (call.get("operator") or "call").replace(" ", "_")
        filename = f"{op}_{row_id}.{ext}"
        send_audio_analysis(None, audio_bytes, filename, call, result, row_id)
    except Exception:
        traceback.print_exc()  # Telegram yiqilsa ham tahlil saqlanadi
    result["_row_id"] = row_id
    return result


@app.post("/analyze")
async def analyze_upload(
    file: UploadFile = File(...),
    operator: str = Form("Qo'lda yuklangan"),
    phone: str = Form(""),
):
    """Pilot uchun: audio faylni to'g'ridan-to'g'ri yuklab sinab ko'rish."""
    audio = await file.read()
    mime = file.content_type or "audio/mp3"
    call = {"operator": operator, "phone": phone, "call_id": file.filename, "duration": 0}
    try:
        result = _process(audio, mime, call)
        return JSONResponse(result)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Tahlil xatosi: {e}")


@app.post("/webhook/telephony")
async def webhook_telephony(request: Request):
    """
    Telefoniya qo'ng'iroq tugagach shu manzilni chaqiradi.
    Kutiladigan JSON (moslashuvchan):
      {
        "record_url": "https://.../record.mp3",   # majburiy
        "operator": "Oybek",                        # ixtiyoriy
        "phone": "+998...",                         # ixtiyoriy
        "call_id": "abc123",                        # ixtiyoriy
        "duration": 120                             # ixtiyoriy (sekund)
      }
    Xavfsizlik uchun URLga ?token=WEBHOOK_SECRET qo'shiladi.
    """
    if settings.WEBHOOK_SECRET:
        token = request.query_params.get("token", "")
        if token != settings.WEBHOOK_SECRET:
            raise HTTPException(401, "Noto'g'ri token")

    try:
        body = await request.json()
    except Exception:
        # ba'zi tizimlar form-data yuboradi
        form = await request.form()
        body = dict(form)

    record_url = body.get("record_url") or body.get("RECORD_URL")
    if not record_url:
        raise HTTPException(400, "record_url topilmadi")

    call = {
        "operator": body.get("operator") or body.get("USER_NAME") or "",
        "phone": body.get("phone") or body.get("PHONE_NUMBER") or "",
        "call_id": body.get("call_id") or body.get("CALL_ID") or "",
        "duration": body.get("duration") or body.get("DURATION") or 0,
    }

    headers = {}
    if settings.RECORD_AUTH_HEADER:
        # masalan "Authorization: Bearer xxx" -> ikki qismga bo'lamiz
        if ":" in settings.RECORD_AUTH_HEADER:
            k, v = settings.RECORD_AUTH_HEADER.split(":", 1)
            headers[k.strip()] = v.strip()

    async with httpx.AsyncClient(timeout=120, follow_redirects=True) as client:
        r = await client.get(record_url, headers=headers)
        r.raise_for_status()
        audio = r.content
        mime = r.headers.get("content-type", "audio/mp3").split(";")[0]
        if "audio" not in mime and "octet-stream" not in mime:
            mime = "audio/mp3"

    try:
        result = _process(audio, mime, call)
        return {"ok": True, "row_id": result["_row_id"],
                "overall_score": result.get("overall_score")}
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, f"Tahlil xatosi: {e}")


# ------------------------- TELEGRAM JAVOB (tiriklik testi) -------------------------

@app.post("/telegram/webhook")
async def telegram_webhook(request: Request):
    """
    Telegram botga yozilgan xabarga javob beradi (bot tirikligini bilish uchun).
    Buni bir marta ulash kerak (README ga qarang).
    """
    try:
        update = await request.json()
    except Exception:
        return {"ok": True}

    # --- Tugma bosilganda (Qisqa/To'liq almashtirish) ---
    cb = update.get("callback_query")
    if cb:
        try:
            data = cb.get("data") or ""
            msg = cb.get("message") or {}
            chat_id = (msg.get("chat") or {}).get("id")
            message_id = msg.get("message_id")
            row_id = int(data.split(":", 1)[1])
            row = storage.get_call(row_id)
            if row:
                result = _json_mod.loads(row["result_json"])
                call = {"operator": row["operator"], "phone": row["phone"],
                        "call_id": row["call_id"], "_row_id": row_id}
                if data.startswith("d:"):
                    # To'liq gaplashuvni alohida xabar qilib yuboramiz
                    send_message(format_dialog(call, result), chat_id)
                elif data.startswith("f:"):
                    # To'liq tahlil caption'ga sig'maydi — alohida to'liq xabar
                    send_message(format_message(call, result), chat_id)
                else:
                    edit_caption(chat_id, message_id, row_id, call, result, "short")
            answer_callback(cb.get("id"))
        except Exception:
            traceback.print_exc()
            try:
                answer_callback(cb.get("id"))
            except Exception:
                pass
        return {"ok": True}

    msg = update.get("message") or update.get("edited_message") or {}
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    text = (msg.get("text") or "").strip()
    if not chat_id:
        return {"ok": True}

    last = storage.kv_get(_marker_key(), "0")
    reply = (
        "🤖 <b>Call Analyzer ishlayapti!</b>\n\n"
        f"• CRM: {settings.CRM_SOURCE}\n"
        f"• Gemini: {'✅' if settings.GEMINI_API_KEY else '❌'}\n"
        f"• Avtomatik tekshiruv: {'yoqilgan' if settings.AUTO_POLL else 'o‘chirilgan'}\n"
        f"• Oxirgi belgi: {last}\n\n"
        "Yangi qo'ng'iroqlar tahlili shu yerga avtomatik tushadi. "
        "Sizning chat ID: <code>" + str(chat_id) + "</code>"
    )
    try:
        reply_to(chat_id, reply)
    except Exception:
        traceback.print_exc()
    return {"ok": True}


@app.get("/telegram/setup")
async def telegram_setup(request: Request):
    """
    Botni shu serverga ulaydi (webhook o'rnatadi). Bir marta ochilsa yetadi.
    Bundan keyin botga yozilgan har xabarga javob keladi.
    """
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN yo'q")
    base = str(request.base_url).rstrip("/")
    # Telegram faqat HTTPS qabul qiladi; Railway orqasida http ko'rinishi mumkin
    if base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    hook = f"{base}/telegram/webhook"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.get(
            f"https://api.telegram.org/bot{token}/setWebhook",
            params={"url": hook},
        )
    return {"webhook": hook, "telegram_response": r.json()}


# ------------------------- BITRIX AVTOMATIK POLLING -------------------------

def poll_once() -> dict:
    """
    Tanlangan CRM'dan (bitrix/amocrm) yangi qo'ng'iroqlarni bir marta oladi,
    har birini tahlil qiladi. Marker (qaysi joygacha ishlangani) bazada saqlanadi.
    """
    if not _crm_configured():
        return {"error": f"{settings.CRM_SOURCE} sozlanmagan"}

    marker_key = _marker_key()
    last_marker = storage.kv_get(marker_key, "0")

    if settings.CRM_SOURCE == "amocrm":
        new_calls, new_marker = amo.fetch_new_calls(
            last_marker, min_duration=settings.MIN_DURATION
        )
        downloader = amo.download_record
    else:
        # Bitrix: ID asosida marker
        raw = bitrix.fetch_new_calls(int(last_marker or 0),
                                     min_duration=settings.MIN_DURATION)
        new_calls = raw
        new_marker = str(max([int(last_marker or 0)] +
                             [c["bitrix_id"] for c in raw]))
        downloader = bitrix.download_record

    processed = 0
    for call in new_calls:
        if storage.call_exists(call["call_id"]):
            continue
        try:
            audio, mime = downloader(call["record_url"])
            _process(audio, mime, call)
            processed += 1
        except Exception:
            traceback.print_exc()  # bitta yiqilsa, qolganini davom ettiramiz

    storage.kv_set(marker_key, new_marker)
    return {"source": settings.CRM_SOURCE, "found": len(new_calls),
            "processed": processed, "marker": new_marker}


async def _poll_loop():
    """Fon rejimida doimiy ishlaydigan tsikl."""
    while True:
        try:
            # bloklovchi httpx chaqiruvlarini alohida oqimda bajaramiz
            res = await asyncio.to_thread(poll_once)
            if res.get("processed"):
                print(f"[poll] {res}")
        except Exception:
            traceback.print_exc()
        await asyncio.sleep(settings.POLL_INTERVAL)


@app.post("/cron/poll")
@app.get("/cron/poll")
async def cron_poll(request: Request):
    """Qo'lda yoki tashqi cron orqali bir marta tekshirish."""
    if settings.WEBHOOK_SECRET:
        if request.query_params.get("token", "") != settings.WEBHOOK_SECRET:
            raise HTTPException(401, "Noto'g'ri token")
    res = await asyncio.to_thread(poll_once)
    return res


# ------------------------- DASHBOARD -------------------------

def _page(body: str) -> str:
    return f"""<!doctype html><html lang="uz"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Call Analyzer</title>
<style>
:root {{ color-scheme: dark; }}
* {{ box-sizing: border-box; }}
body {{ font-family: -apple-system, Segoe UI, Roboto, sans-serif; margin:0;
  background:#0f1216; color:#e6e9ee; }}
.wrap {{ max-width: 900px; margin: 0 auto; padding: 20px; }}
h1 {{ font-size: 20px; }}
a {{ color:#6ea8fe; text-decoration:none; }}
.card {{ background:#171b21; border:1px solid #262c36; border-radius:12px;
  padding:14px 16px; margin-bottom:12px; }}
.row {{ display:flex; justify-content:space-between; align-items:center; gap:10px; }}
.badge {{ padding:3px 10px; border-radius:20px; font-weight:600; font-size:13px; }}
.muted {{ color:#8b94a3; font-size:13px; }}
table {{ width:100%; border-collapse:collapse; }}
td, th {{ padding:8px; text-align:left; border-bottom:1px solid #262c36; font-size:14px; }}
.pill {{ display:inline-block; padding:2px 8px; border-radius:6px; margin:2px 0; font-size:13px;}}
.done {{ background:#12351f; color:#6ee7a0; }}
.partial {{ background:#3a3410; color:#f2d27a; }}
.missed {{ background:#3a1616; color:#f2857a; }}
.na {{ background:#22262e; color:#8b94a3; }}
pre {{ white-space:pre-wrap; background:#0c0f13; padding:12px; border-radius:8px;
  font-size:13px; line-height:1.5; }}
</style></head><body><div class="wrap">{body}</div></body></html>"""


def _score_color(s):
    try:
        s = int(s)
    except Exception:
        return "#8b94a3"
    if s >= 80:
        return "#2c7a4b"
    if s >= 50:
        return "#8a7320"
    return "#8a3030"


@app.get("/", response_class=HTMLResponse)
def dashboard():
    calls = storage.recent_calls(50)
    stats = storage.operator_stats()

    body = "<h1>📞 Call Analyzer — qo'ng'iroq sifati</h1>"

    if stats:
        body += "<div class='card'><b>Operator reytingi</b><table>"
        body += "<tr><th>Operator</th><th>Qo'ng'iroq</th><th>O'rtacha ball</th></tr>"
        for s in stats:
            body += (f"<tr><td>{s['operator']}</td><td>{s['calls']}</td>"
                     f"<td><b>{s['avg_score']}</b></td></tr>")
        body += "</table></div>"

    if not calls:
        body += ("<div class='card muted'>Hali tahlil yo'q. Pilot uchun audio yuklang:"
                 " <code>POST /analyze</code> (file, operator, phone).</div>")
    for c in calls:
        color = _score_color(c["overall_score"])
        op = c["operator"] or "Noma'lum"
        body += (
            f"<a href='/call/{c['id']}'><div class='card'><div class='row'>"
            f"<div><b>{op}</b> "
            f"<span class='muted'>{c['phone'] or ''}</span></div>"
            f"<span class='badge' style='background:{color}'>"
            f"{c['overall_score']}/100</span></div>"
            f"<div class='muted'>{c['call_id'] or ''}</div></div></a>"
        )
    return _page(body)


@app.get("/call/{row_id}", response_class=HTMLResponse)
def call_detail(row_id: int):
    row = storage.get_call(row_id)
    if not row:
        raise HTTPException(404, "Topilmadi")
    r = json.loads(row["result_json"])

    body = "<a href='/'>&larr; Orqaga</a>"
    op = row["operator"] or "Noma'lum"
    body += f"<h1>{op} — {r.get('overall_score')}/100</h1>"
    body += f"<div class='muted'>{r.get('call_topic','')}</div>"

    body += "<div class='card'><b>Protokol bandlari</b><br>"
    css = {"done": "done", "partial": "partial", "missed": "missed",
           "not_applicable": "na"}
    label = {"done": "✅ bajarildi", "partial": "🟡 qisman",
             "missed": "❌ bajarilmadi", "not_applicable": "➖ mos emas"}
    for p in r.get("protocol", []):
        cls = css.get(p.get("status"), "na")
        body += (f"<div><span class='pill {cls}'>{label.get(p.get('status'),'')}</span> "
                 f"<b>{p.get('id')}) {p.get('name','')}</b></div>")
        if p.get("comment"):
            body += f"<div class='muted' style='margin:2px 0 8px 4px'>{p.get('comment')}</div>"
    body += "</div>"

    if r.get("missed_opportunities"):
        body += "<div class='card'><b>Qo'ldan ketgan imkoniyatlar</b><ul>"
        for m in r["missed_opportunities"]:
            body += f"<li>{m}</li>"
        body += "</ul></div>"

    comm = r.get("communication", {})
    if comm:
        body += ("<div class='card'><b>Muomala</b>"
                 f"<div class='muted'>Ohang: {comm.get('operator_tone','—')} • "
                 f"Xushmuomalalik: {comm.get('politeness','—')} • "
                 f"Mijoz: {comm.get('client_mood','—')} • "
                 f"Professionallik: {comm.get('professionalism_score','—')}/10</div></div>")

    if r.get("recommendations"):
        body += "<div class='card'><b>Maslahatlar</b><ul>"
        for rec in r["recommendations"]:
            body += f"<li>{rec}</li>"
        body += "</ul></div>"

    disp = r.get("dispute") or {}
    if disp.get("has_conflict"):
        body += ("<div class='card'><b>⚖️ Nizo tahlili</b>"
                 f"<div class='muted' style='margin-top:6px'>{disp.get('summary','')}</div>"
                 f"<div style='margin-top:6px'>Kim haq: <b>{disp.get('who_is_right','aniq emas')}</b></div>"
                 f"<div class='muted' style='margin-top:4px'>{disp.get('reason','')}</div></div>")

    if r.get("transcript"):
        body += f"<div class='card'><b>Transkript</b><pre>{r['transcript']}</pre></div>"

    return _page(body)
