from __future__ import annotations

import re

from ..llm import complete
from ..persona import particle, pronoun
from .base import Context, Skill

MAX_REPLY_CHARS = 110
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=ครับ)\s+|(?<=ค่ะ)\s+|(?<=คะ)\s+")

CHAT_SYSTEM = (
    "คุณคือ JARVIS ผู้ช่วยเสียงพูดภาษาไทย {gender} แทนตัวเองว่า {pronoun} ลงท้ายด้วย {particle} ตอบสั้น กระชับ เป็นกันเอง "
    "ตอบสั้นมาก ไม่เกิน 20 คำ เพราะคำตอบจะถูกพูดออกลำโพงและทุกคำใช้เวลาสร้างเสียง "
    "ตัวอย่างความยาวที่ต้องการ: ถาม วันนี้กินอะไรดี ตอบ ลองสลัดผักกับข้าวกล้องดูไหม เบาท้องและอร่อย "
    "ข้อความที่ได้รับมาจากการแปลงเสียงพูดเป็นตัวอักษรด้วยโปรแกรมที่ไม่แม่นยำ "
    "อาจมีคำผิดหรือฟังไม่ครบ ถ้าข้อความดูไม่สมเหตุสมผลหรือไม่แน่ใจว่าหมายถึงอะไร "
    "ให้ถามกลับสั้นๆ เพื่อความชัดเจน อย่าเดาหรือแต่งเรื่องขึ้นมาตอบ"
)


def _system(ctx: Context) -> str:
    male = ctx.config.gender == "male"
    return CHAT_SYSTEM.format(gender="เพศชาย" if male else "เพศหญิง", pronoun=pronoun(ctx.config.gender),
                              particle=particle(ctx.config.gender))


def _profile_context(ctx: Context) -> str:
    profile = ctx.config.profile
    facts = []
    if profile.name:
        facts.append(f"ผู้ใช้ชื่อ {profile.name} ให้เรียกผู้ใช้ว่า {profile.name}")
    if profile.birth_date:
        facts.append(f"วันเกิดของผู้ใช้คือ {profile.birth_date}")
    if profile.birth_time:
        facts.append(f"เวลาเกิดของผู้ใช้คือ {profile.birth_time} น.")
    if profile.diet:
        facts.append(f"ผู้ใช้กิน{profile.diet} หากแนะนำอาหารให้สอดคล้องกับข้อนี้")
    if not facts:
        return ""
    return "\nข้อมูลผู้ใช้ที่บันทึกไว้: " + "; ".join(facts) + " ใช้ข้อมูลนี้เมื่อเกี่ยวข้องกับคำถาม และไม่เดาข้อมูลส่วนตัวอื่นเพิ่ม"


def shorten(text: str, gender: str, limit: int = MAX_REPLY_CHARS) -> str:
    """Qwen ignores length instructions (66–258 chars for the same prompt), and every spoken
    character costs synthesis time, so keep whole sentences up to `limit`."""
    parts = [p for p in _SENTENCE_END.split(text.strip()) if p]
    if not parts:
        return text
    reply = parts[0]
    for part in parts[1:]:
        if len(reply) + 1 + len(part) > limit:
            break
        reply = f"{reply} {part}"
    if len(reply) > limit * 1.4:
        # One run-on sentence: cut at the phrase break (Thai separates phrases with spaces)
        # nearest the limit; the last break before it often left a half-phrase like "AI คือ".
        breaks = [i for i, char in enumerate(reply) if char == " " and limit * 0.6 <= i <= limit * 1.4]
        if breaks:
            reply = reply[: min(breaks, key=lambda i: abs(i - limit))].rstrip(" ,")
    if not reply.endswith(("ครับ", "ค่ะ", "คะ", "?", "!")):
        reply = f"{reply}{particle(gender)}"
    return reply


def _answer(ctx: Context, text: str) -> str:
    reply = complete(
        [{"role": "system", "content": _system(ctx) + _profile_context(ctx)}, {"role": "user", "content": text}],
        ctx.config.llm_endpoint, temperature=0.7, max_tokens=160, timeout=60,
    ).strip()
    return shorten(reply, ctx.config.gender)


SKILLS = (Skill(intent="chat", description="คำถามหรือบทสนทนาทั่วไปที่ไม่ใช่การสั่งอุปกรณ์", handle=_answer),)
