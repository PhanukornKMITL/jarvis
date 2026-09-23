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


def _answer(ctx: Context, text: str) -> str:
    return complete(
        [{"role": "system", "content": CHAT_SYSTEM}, {"role": "user", "content": text}],
        ctx.config.llm_endpoint, temperature=0.7, max_tokens=120, timeout=60,
    ).strip()


SKILLS = (Skill(intent="chat", description="คำถามหรือบทสนทนาทั่วไปที่ไม่ใช่การสั่งอุปกรณ์", handle=_answer),)
