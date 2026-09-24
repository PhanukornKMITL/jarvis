"""Turn a transcript into a skill intent: skill rules first, then the local LLM."""

from __future__ import annotations

import json

from .llm import complete
from .skills import BY_INTENT, SKILLS

UNCLEAR = "unclear"
FALLBACK = "chat"

# How whisper actually misspells words over the Bluetooth headset mic.
STT_NOISE_HINTS = """\
- "ไฟ" อาจมาเป็น ฟาย ฟัย ภัย fy ไฟน์
- "ปิด" อาจมาเป็น บิด บิต bit พิ
- "เปิด" อาจมาเป็น เป็น เบิด เปิ้ด
- "ให้หน่อย" อาจมาเป็น ให้น้อย ห้าหน่อย หน้อย
- "ต้นไม้" อาจมาเป็น ตนไม้ ตุนไม้ ตนมา
- "ฝน" อาจมาเป็น ฟน"""


def _system_prompt() -> str:
    intents = "\n".join(f"- {skill.intent}: {skill.description}" for skill in SKILLS)
    names = ", ".join([*BY_INTENT, UNCLEAR])
    # Naming the question skills explicitly matters: "pick the nearest intent" alone sent
    # garbled "วันนี้ ฟนจะตกหมาย" to chat instead of weather.
    questions = " ".join(s.intent for s in SKILLS if not s.rule_only and s.intent != FALLBACK)
    return f"""คุณคือตัวแยกคำสั่งของ JARVIS ผู้ช่วยในบ้าน ตอบเป็น JSON บรรทัดเดียวเท่านั้น รูปแบบ {{"intent": "..."}}
ข้อความที่ได้มาจากการแปลงเสียงพูดภาษาไทยเป็นตัวอักษรผ่านไมค์บลูทูธคุณภาพต่ำ จึงมักสะกดเพี้ยนตามเสียง ให้เดาจากเสียงอ่าน:
{STT_NOISE_HINTS}
intent ที่ใช้ได้:
{intents}
- {UNCLEAR}: ใช้เฉพาะเมื่อฟังดูเหมือนสั่งเปิดหรือปิดอุปกรณ์ แต่แยกไม่ออกว่าเปิดหรือปิด
ถ้าเป็นคำถามที่เพี้ยน ให้เลือก {questions} ที่ใกล้เคียงที่สุด ถ้าไม่ใกล้อะไรเลยให้ตอบ {FALLBACK}
ต้องเป็นหนึ่งใน: {names}"""


def classify(transcript: str, endpoint: str, alternatives: tuple[str, ...] = ()) -> str:
    """`alternatives` are other transcripts of the same audio; their rules must agree."""
    texts = (transcript, *alternatives)
    fired = {skill.intent for skill in SKILLS if skill.rule and any(skill.rule(text) for text in texts)}
    if len(fired) > 1:
        return UNCLEAR
    if fired:
        return fired.pop()
    content = complete(
        [{"role": "system", "content": _system_prompt()}, {"role": "user", "content": transcript}],
        endpoint, temperature=0, max_tokens=24, timeout=30,
    )
    start, end = content.find("{"), content.rfind("}")
    try:
        intent = json.loads(content[start : end + 1]).get("intent") if 0 <= start < end else None
    except json.JSONDecodeError:
        intent = None
    if intent == UNCLEAR:
        # "unclear" is only for an on/off command that can't be told apart; Qwen also used it
        # for ordinary chat ("ขอคิดแบบนี้"), which then got "ฟังไม่ชัด" instead of an answer.
        return UNCLEAR if any(s.mentions and s.mentions(transcript) for s in SKILLS) else FALLBACK
    skill = BY_INTENT.get(intent or "")
    if skill is None:
        intent = FALLBACK
    elif skill.rule_only:
        # No rule matched, so the LLM choosing an action like switching a light is a guess:
        # ask again if the topic was mentioned, otherwise it was just chat ("ขึ้นมาใช่ไหม").
        return UNCLEAR if skill.mentions and skill.mentions(transcript) else FALLBACK
    if intent == FALLBACK and any(s.mentions and s.mentions(transcript) for s in SKILLS):
        # A garbled command ("บริฟัยให้น้อย") would otherwise get a rambling chat reply.
        return UNCLEAR
    return intent
