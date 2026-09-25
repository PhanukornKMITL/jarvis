from __future__ import annotations

import re

from collections.abc import Iterator

from ..llm import complete, stream
from ..persona import particle, pronoun
from .base import Context, Skill

MAX_REPLY_CHARS = 110
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=ครับ)\s+|(?<=ค่ะ)\s+|(?<=คะ)\s+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|(?<=ครับ)\s+|(?<=ค่ะ)\s+|(?<=คะ)\s+|\n+")
STREAM_REPLY_CHARS = 140
FIRST_CHUNK_CHARS = 30
RUN_ON_CHARS = 60
HISTORY_TURNS = 3

CHAT_SYSTEM = (
    "คุณคือ JARVIS ผู้ช่วยเสียงพูดภาษาไทย {gender} แทนตัวเองว่า {pronoun} ลงท้ายด้วย {particle} ตอบสั้น กระชับ เป็นกันเอง "
    "ตอบสั้นมาก 1-2 ประโยค ไม่เกิน 20 คำ เพราะคำตอบจะถูกพูดออกลำโพงและทุกคำใช้เวลาสร้างเสียง "
    "ไม่ต้องขอโทษหรือเกริ่นนำ ตอบเนื้อหาเลย "
    "ข้อความที่ได้รับมาจากการแปลงเสียงพูดเป็นตัวอักษรด้วยโปรแกรมที่ไม่แม่นยำ "
    "อาจมีคำผิดหรือฟังไม่ครบ ถ้าข้อความดูไม่สมเหตุสมผลหรือไม่แน่ใจว่าหมายถึงอะไร "
    "ให้ถามกลับสั้นๆ เพื่อความชัดเจน อย่าเดาหรือแต่งเรื่องขึ้นมาตอบ "
    "คุณเข้าอินเทอร์เน็ตทั่วไปไม่ได้ ใช้ได้แค่ข้อมูลในบ้านและพยากรณ์อากาศ"
)
# Always in the prompt, so llama-server reuses it from cache; only the data itself is new
# tokens (reading ~400 new tokens took ~1.3 s of every weather answer).
DATA_RULE = (
    "\nถ้ามีหัวข้อ ข้อมูลจริง ต่อท้าย ให้ตอบจากข้อมูลนั้นเท่านั้น ห้ามเดาตัวเลข ห้ามพูดถึงเวลาหรือความแรงของฝน"
    "ที่ไม่มีในข้อมูล พยากรณ์ให้พูดเป็นความน่าจะเป็น พูดเป็นภาษาคนทั่วไปที่เอาไปใช้ได้ เช่น ฝนหนัก พกร่ม อบอ้าว "
    "ดินแห้ง ไม่ต้องอ่านตัวเลขเปอร์เซ็นต์ มิลลิเมตร หรือความชื้น เว้นแต่ผู้ใช้ถามตัวเลขเอง "
    "ถ้าข้อมูลมี error ให้บอกตามจริง"
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
    if profile.birth_place:
        facts.append(f"เกิดที่ {profile.birth_place}")
    if profile.diet:
        # Qwen suggested fish and fish sauce to a vegetarian when this only said "มังสวิรัติ".
        facts.append(f"เรื่องอาหาร ผู้ใช้กิน{profile.diet} ถ้าแนะนำอาหารหรือสูตรอาหาร "
                     "ต้องไม่มีวัตถุดิบหรือเครื่องปรุงที่ขัดกับข้อนี้เด็ดขาด ใช้ของแทน เช่น ซีอิ๊วแทนน้ำปลา เต้าหู้แทนเนื้อสัตว์")
    if profile.about:
        facts.append(profile.about)
    if profile.interests:
        facts.append("สนใจ " + ", ".join(profile.interests))
    context = ""
    if facts:
        context += ("\nข้อมูลผู้ใช้ที่บันทึกไว้: " + "; ".join(facts)
                    + " ใช้ข้อมูลนี้เมื่อเกี่ยวข้องกับคำถาม และไม่เดาข้อมูลส่วนตัวอื่นเพิ่ม")
    if profile.personality:
        context += f"\nบุคลิกของคุณ: {profile.personality}"
    if profile.rules:
        context += "\nกติกา: " + " ".join(f"({index}) {rule}" for index, rule in enumerate(profile.rules, 1))
    return context


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
    reply = reply.rstrip(" .,")  # "…กินมังสวิรัติครับ." used to become "…ครับ.ครับ"
    if not reply.endswith(("ครับ", "ค่ะ", "คะ", "?", "!")):
        reply = f"{reply}{particle(gender)}"
    return reply


def speakable(text: str) -> str:
    """Qwen sometimes answers in markdown ("**สลัด**:", "1."), which TTS would read out."""
    text = re.sub(r"[*#`_>|]+", "", text)
    # Qwen2 slips Chinese into Thai: CJK characters and full-width punctuation ("，。").
    text = re.sub(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]+", " ", text)
    text = re.sub(r"(?<!\S)[เแโใไ](?!\S)", " ", text)  # a leading vowel left alone by the cut
    text = re.sub(r"(?m)^\s*(?:\d+[.)]|[-•])\s+", "", text)
    return " ".join(text.replace(":", " ").split())


def _messages(ctx: Context, text: str, data: tuple[str, ...] = ()) -> list[dict]:
    system = _system(ctx) + _profile_context(ctx) + DATA_RULE + ("\nข้อมูลจริง:\n" + "\n".join(data) if data else "")
    messages = [{"role": "system", "content": system}]
    for said, replied in ctx.history[-HISTORY_TURNS:]:
        messages += [{"role": "user", "content": said}, {"role": "assistant", "content": replied}]
    return messages + [{"role": "user", "content": text}]


def answer_from(ctx: Context, text: str, data: tuple[str, ...] = ()) -> str:
    """The whole reply at once; `data` are tool results to answer from."""
    reply = complete(_messages(ctx, text, data), ctx.config.llm_endpoint, temperature=0.7, max_tokens=160, timeout=60)
    return shorten(speakable(reply), ctx.config.gender)


def stream_from(ctx: Context, text: str, data: tuple[str, ...] = ()) -> Iterator[str]:
    """Yields whole sentences as the model writes them, up to STREAM_REPLY_CHARS in total."""
    pieces = stream(_messages(ctx, text, data), ctx.config.llm_endpoint, temperature=0.7, max_tokens=200, timeout=60)
    buffer, spoken = "", 0
    try:
        for piece in pieces:
            buffer += piece
            while True:
                match = _SENTENCE_BREAK.search(buffer)
                if match:
                    sentence, buffer = buffer[: match.start()], buffer[match.end():]
                elif len(buffer) > (FIRST_CHUNK_CHARS if spoken == 0 else RUN_ON_CHARS) and buffer.rfind(" ") >= 10:
                    # No sentence end yet: speak up to a phrase break so audio can start early.
                    cut = buffer.rindex(" ")
                    sentence, buffer = buffer[:cut], buffer[cut + 1:]
                else:
                    break
                sentence = speakable(sentence)
                if not sentence:
                    continue
                spoken += len(sentence)
                if spoken >= STREAM_REPLY_CHARS:
                    yield shorten(sentence, ctx.config.gender, limit=len(sentence))  # ensure it ends politely
                    return
                yield sentence
        tail = speakable(buffer)
        if tail:
            yield shorten(tail, ctx.config.gender, limit=len(tail))
    finally:
        pieces.close()  # stop generating once enough was said


SKILLS = (Skill(intent="chat", handle=answer_from, slow=True, stream=stream_from),)
