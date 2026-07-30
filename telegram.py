"""Tahlil natijasini Telegram botga chiroyli xabar qilib yuborish."""
import html
import httpx

from config import settings

_STATUS_EMOJI = {
    "done": "✅",
    "partial": "🟡",
    "missed": "❌",
    "not_applicable": "➖",
}


def format_short(call: dict, result: dict) -> str:
    """Bir qarashda ko'rish uchun qisqa xulosa."""
    def esc(x):
        return html.escape(str(x)) if x is not None else ""

    operator = esc(call.get("operator") or "Noma'lum")
    phone = esc(call.get("phone") or "—")
    score = result.get("overall_score", "—")

    proto = result.get("protocol", [])
    done = sum(1 for p in proto if p.get("status") == "done")
    total = sum(1 for p in proto if p.get("status") != "not_applicable")
    missed = [p for p in proto if p.get("status") == "missed"]

    _TYPE_LABEL = {
        "sotuv_arenda": "🛒 Sotuv/arenda",
        "mavjud_buyurtma": "📦 Mavjud buyurtma",
        "muammo_shikoyat": "⚠️ Muammo/shikoyat",
        "umumiy_savol": "❓ Umumiy savol",
        "ichki_xodim": "👷 Ichki (xodim)",
        "boshqa": "• Boshqa",
    }
    ctype = result.get("call_type")

    lines = [
        f"⚡️ <b>Qisqa tahlil</b> — {operator} ({phone})",
    ]
    if ctype in _TYPE_LABEL:
        lines.append(f"{_TYPE_LABEL[ctype]}")
    if total > 0:
        lines.append(f"⭐️ Ball: <b>{esc(score)}/100</b>   ✅ Protokol: {done}/{total}")
    else:
        lines.append(f"⭐️ Ball: <b>{esc(score)}/100</b>   (protokol bu turga qo‘llanmaydi)")
    if missed:
        names = ", ".join(esc(p.get("name")) for p in missed[:4])
        lines.append(f"❌ Bajarilmadi: {names}")
    dispute = result.get("dispute") or {}
    if dispute.get("has_conflict"):
        lines.append(f"⚖️ Nizo bor — kim haq: <b>{esc(dispute.get('who_is_right') or 'aniq emas')}</b>")
    red = result.get("red_flags", [])
    if red:
        lines.append(f"⚠️ {esc(red[0])}")
    return "\n".join(lines)


def format_message(call: dict, result: dict) -> str:
    """result — analyze_audio qaytargan dict. call — meta ma'lumot (operator, phone...)."""
    def esc(x):
        return html.escape(str(x)) if x is not None else ""

    operator = esc(call.get("operator") or "Noma'lum")
    phone = esc(call.get("phone") or "—")
    score = result.get("overall_score", "—")

    lines = []
    lines.append(f"📞 <b>Qo'ng'iroq tahlili</b>")
    lines.append(f"👤 Operator: <b>{operator}</b>   ☎️ {phone}")
    topic = esc(result.get("call_topic", ""))
    if topic:
        lines.append(f"📝 {topic}")
    lines.append(f"⭐️ Umumiy ball: <b>{esc(score)}/100</b>")
    lines.append("")

    lines.append("<b>Protokol bandlari:</b>")
    for p in result.get("protocol", []):
        emoji = _STATUS_EMOJI.get(p.get("status"), "•")
        name = esc(p.get("name", f"Band {p.get('id')}"))
        lines.append(f"{emoji} {esc(p.get('id'))}) {name}")
        comment = esc(p.get("comment", ""))
        if comment and p.get("status") in ("missed", "partial"):
            lines.append(f"    ↳ {comment}")
    lines.append("")

    missed = result.get("missed_opportunities", [])
    if missed:
        lines.append("<b>Qo'ldan ketgan imkoniyatlar:</b>")
        for m in missed:
            lines.append(f"• {esc(m)}")
        lines.append("")

    comm = result.get("communication", {})
    if comm:
        lines.append("<b>Muomala:</b>")
        lines.append(f"• Ohang: {esc(comm.get('operator_tone', '—'))}")
        lines.append(f"• Xushmuomalalik: {esc(comm.get('politeness', '—'))}")
        lines.append(f"• Mijoz kayfiyati: {esc(comm.get('client_mood', '—'))}")
        lines.append(f"• Professionallik: {esc(comm.get('professionalism_score', '—'))}/10")
        lines.append("")

    red = result.get("red_flags", [])
    if red:
        lines.append("<b>⚠️ Jiddiy e'tibor:</b>")
        for r in red:
            lines.append(f"• {esc(r)}")
        lines.append("")

    recs = result.get("recommendations", [])
    if recs:
        lines.append("<b>Maslahatlar:</b>")
        for r in recs:
            lines.append(f"• {esc(r)}")

    dispute = result.get("dispute") or {}
    if dispute.get("has_conflict"):
        lines.append("")
        lines.append("<b>⚖️ Nizo tahlili:</b>")
        if dispute.get("summary"):
            lines.append(f"• {esc(dispute.get('summary'))}")
        lines.append(f"• Kim haq: <b>{esc(dispute.get('who_is_right') or 'aniq emas')}</b>")
        if dispute.get("reason"):
            lines.append(f"• Sabab: {esc(dispute.get('reason'))}")

    return "\n".join(lines)


def format_dialog(call: dict, result: dict) -> str:
    """To'liq gaplashuv (transkript) — alohida xabar sifatida."""
    operator = html.escape(str(call.get("operator") or "Operator"))
    phone = html.escape(str(call.get("phone") or "—"))
    tr = result.get("transcript") or "Transkript topilmadi."
    tr = html.escape(str(tr))
    return f"💬 <b>To‘liq gaplashuv</b> — {operator} ({phone})\n\n{tr}"


def send_message(text: str, chat_id: str = None) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        # Telegram sozlanmagan bo'lsa jimgina o'tkazamiz (dashboard baribir ishlaydi)
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    # Telegram xabar uzunligi 4096 belgidan oshmasin
    for chunk in _split(text, 4000):
        httpx.post(
            url,
            json={"chat_id": chat_id, "text": chunk, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=30,
        )


def _split(text: str, size: int):
    lines = text.split("\n")
    buf = ""
    for ln in lines:
        if len(buf) + len(ln) + 1 > size:
            yield buf
            buf = ""
        buf += ln + "\n"
    if buf:
        yield buf


def reply_to(chat_id, text: str) -> None:
    """Muayyan chatga javob yuborish (bot tirikligini tekshirish uchun)."""
    token = settings.TELEGRAM_BOT_TOKEN
    if not token or not chat_id:
        return
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    httpx.post(
        url,
        json={"chat_id": chat_id, "text": text, "parse_mode": "HTML",
              "disable_web_page_preview": True},
        timeout=30,
    )


# ============ AUDIO + TUGMALI XABAR ============
import json as _json

_CAPTION_LIMIT = 1024


def _trim(text: str, limit: int = _CAPTION_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n… (to‘liq: dashboard)"


def build_caption(call: dict, result: dict, view: str) -> str:
    """view = 'short' yoki 'full'."""
    if view == "full":
        return _trim(format_message(call, result))
    return _trim(format_short(call, result))


def build_keyboard(row_id, view: str) -> dict:
    """Qisqa/To'liq almashtirish tugmalari (+ ixtiyoriy dashboard havolasi)."""
    if view == "full":
        toggle = {"text": "⚡️ Qisqa tahlil", "callback_data": f"s:{row_id}"}
    else:
        toggle = {"text": "📄 To‘liq tahlil", "callback_data": f"f:{row_id}"}
    rows = [[toggle]]
    rows.append([{"text": "💬 Dialog (to‘liq gaplashuv)", "callback_data": f"d:{row_id}"}])
    if settings.PUBLIC_BASE_URL:
        url = settings.PUBLIC_BASE_URL.rstrip("/") + f"/call/{row_id}"
        rows.append([{"text": "🔗 Batafsil (dashboard)", "url": url}])
    return {"inline_keyboard": rows}


def send_audio_analysis(chat_id, audio_bytes: bytes, filename: str,
                        call: dict, result: dict, row_id) -> None:
    """Audio + qisqa tahlil (caption) + [To'liq tahlil] tugmasi — bitta xabar."""
    token = settings.TELEGRAM_BOT_TOKEN
    chat_id = chat_id or settings.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return
    caption = build_caption(call, result, "short")
    kb = build_keyboard(row_id, "short")
    data = {
        "chat_id": str(chat_id),
        "caption": caption,
        "parse_mode": "HTML",
        "reply_markup": _json.dumps(kb),
    }
    files = {"audio": (filename, audio_bytes, "audio/mpeg")}
    url = f"https://api.telegram.org/bot{token}/sendAudio"
    r = httpx.post(url, data=data, files=files, timeout=180)
    # audio formatini qabul qilmasa — hujjat sifatida yuboramiz
    if r.status_code != 200 or not r.json().get("ok"):
        files = {"document": (filename, audio_bytes, "application/octet-stream")}
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        httpx.post(url, data=data, files=files, timeout=180)


def edit_caption(chat_id, message_id, row_id, call: dict, result: dict, view: str) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return
    url = f"https://api.telegram.org/bot{token}/editMessageCaption"
    httpx.post(
        url,
        json={
            "chat_id": str(chat_id),
            "message_id": message_id,
            "caption": build_caption(call, result, view),
            "parse_mode": "HTML",
            "reply_markup": build_keyboard(row_id, view),
        },
        timeout=30,
    )


def answer_callback(callback_id) -> None:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return
    httpx.post(
        f"https://api.telegram.org/bot{token}/answerCallbackQuery",
        json={"callback_query_id": callback_id},
        timeout=15,
    )
