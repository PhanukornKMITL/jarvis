from __future__ import annotations

from ..llm import complete
from .base import Context, Skill

CHAT_SYSTEM = (
    "คุณคือ JARVIS ผู้ช่วยเสียงพูดภาษาไทย เพศหญิง ลงท้ายด้วย ค่ะ ตอบสั้น กระชับ เป็นกันเอง "
    "ไม่เกิน 2-3 ประโยค เพราะคำตอบจะถูกพูดออกลำโพง "
    "ข้อความที่ได้รับมาจากการแปลงเสียงพูดเป็นตัวอักษรด้วยโปรแกรมที่ไม่แม่นยำ "
    "อาจมีคำผิดหรือฟังไม่ครบ ถ้าข้อความดูไม่สมเหตุสมผลหรือไม่แน่ใจว่าหมายถึงอะไร "
    "ให้ถามกลับสั้นๆ เพื่อความชัดเจน อย่าเดาหรือแต่งเรื่องขึ้นมาตอบ"
)


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


def _answer(ctx: Context, text: str) -> str:
    return complete(
        [{"role": "system", "content": CHAT_SYSTEM + _profile_context(ctx)}, {"role": "user", "content": text}],
        ctx.config.llm_endpoint, temperature=0.7, max_tokens=120, timeout=60,
    ).strip()


SKILLS = (Skill(intent="chat", description="คำถามหรือบทสนทนาทั่วไปที่ไม่ใช่การสั่งอุปกรณ์", handle=_answer),)
