from __future__ import annotations

import re
import time

from collections.abc import Iterator

from ..llm import complete, stream
from ..persona import particle, pronoun
from .base import Context, Skill

MAX_REPLY_CHARS = 110
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=ครับ)\s+|(?<=ค่ะ)\s+|(?<=คะ)\s+")
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+|(?<=ครับ)\s+|(?<=ค่ะ)\s+|(?<=คะ)\s+|\n+")
STREAM_REPLY_CHARS = 140
EXPLAIN_REPLY_CHARS = 260
"""A "why" got one short sentence ("อ้างอิงจากแบบจำลองการพยากรณ์ครับ") under the 140 cap."""
_ASKS_WHY = re.compile(r"ทำไม|เพราะอะไร|ยังไง|อย่างไร|อธิบาย|เหตุผล|หมายความว่า")
# Without the last clause it explained a long rain with an invented "ระบบความกดอากาศต่ำ".
EXPLAIN_RULE = ("\nผู้ใช้ถามเหตุผลหรือวิธีการ ให้ตอบ 2-3 ประโยค ใช้เหตุผลจากข้อมูลจริงก่อน เช่น ฤดูกาล "
                "เสริมด้วยความรู้ทั่วไปได้โดยพูดเป็นความน่าจะเป็น ห้ามอ้างระบบอากาศหรือสาเหตุเฉพาะที่ไม่มีในข้อมูล")
FIRST_CHUNK_CHARS = 30
RUN_ON_CHARS = 60
HISTORY_TURNS = 6
FACTS_EXPIRE_SECONDS = 3600
"""Tool results older than this are dropped; younger ones are shown with their age."""

CHAT_SYSTEM = (
    "คุณคือ JARVIS ผู้ช่วยเสียงพูดภาษาไทย {gender} แทนตัวเองว่า {pronoun} ลงท้ายด้วย {particle} ตอบสั้น กระชับ เป็นกันเอง "
    "ตอบสั้นมาก 1-2 ประโยค ไม่เกิน 20 คำ เพราะคำตอบจะถูกพูดออกลำโพงและทุกคำใช้เวลาสร้างเสียง "
    "ไม่ต้องขอโทษหรือเกริ่นนำ ตอบเนื้อหาเลย "
    "ข้อความที่ได้รับมาจากการแปลงเสียงพูดเป็นตัวอักษรด้วยโปรแกรมที่ไม่แม่นยำ "
    "อาจมีคำผิดหรือฟังไม่ครบ ถ้าข้อความดูไม่สมเหตุสมผลหรือไม่แน่ใจว่าหมายถึงอะไร "
    "ให้ถามกลับสั้นๆ เพื่อความชัดเจน อย่าเดาหรือแต่งเรื่องขึ้นมาตอบ "
    # "ผ่านเครื่องมือของคุณ" came back as a vague "เข้าถึงข้อมูลจากเครื่องมือที่กำหนดได้".
    "คุณท่องเว็บหรือค้นเว็บทั่วไปไม่ได้ ใช้อินเทอร์เน็ตได้เฉพาะดึงพยากรณ์อากาศ ข้อมูลน้ำและฝน และหัวข่าว "
    # Asked where its data came from, it said "ชุดข้อมูลที่ถูกฝึกฝนมา" about a live forecast.
    "ที่มาของข้อมูลของคุณ: พยากรณ์อากาศจาก Open-Meteo, ฝนที่ตกจริง ระดับน้ำ เขื่อน และประกาศเตือนจาก ThaiWater, "
    "น้ำท่วมจากดาวเทียม GISTDA, หัวข่าวจาก Google News, อุปกรณ์และต้นไม้จากเซ็นเซอร์ในบ้าน, วันเวลาจากนาฬิกาเครื่อง "
    "ส่วนความรู้ทั่วไปมาจากสิ่งที่โมเดลเรียนมาซึ่งอาจไม่ใช่ข้อมูลล่าสุด"
)
# Always in the prompt, so llama-server reuses it from cache; only the data itself is new
# tokens (reading ~400 new tokens took ~1.3 s of every weather answer).
DATA_RULE = (
    "\nถ้ามีหัวข้อ ข้อมูลจริง ต่อท้าย และคำถามเกี่ยวกับข้อมูลนั้น ให้ตอบจากข้อมูลนั้น ห้ามเดาตัวเลข ห้ามพูดถึงเวลาหรือความแรงของฝน"
    "ที่ไม่มีในข้อมูล พยากรณ์ให้พูดเป็นความน่าจะเป็น พูดเป็นภาษาคนทั่วไปที่เอาไปใช้ได้ เช่น ฝนหนัก พกร่ม อบอ้าว "
    "ดินแห้ง ไม่ต้องอ่านตัวเลขเปอร์เซ็นต์ มิลลิเมตร หรือความชื้น เว้นแต่ผู้ใช้ถามตัวเลขเอง "
    "ถ้าข้อมูลมี error ให้บอกตามจริง ตอบสิ่งที่ผู้ใช้ถามตรงๆ ก่อน ถ้าข้อมูลไม่มีเรื่องนั้นโดยตรง ให้บอกว่าประเมินจาก"
    "ข้อมูลที่มี ถ้าผู้ใช้กำลังจะออกไปข้างนอกแล้วมีฝน ให้เตือนพกร่มสั้นๆ ท้ายคำตอบ ถ้าไม่ได้จะออกไปไหนไม่ต้องแนะนำร่ม "
    "ถ้าไม่เกี่ยวกับคำถามห้ามพูดถึงข้อมูลนั้น ถ้าข้อมูลมีชื่อสถานที่หรือตัวเลขที่ตอบคำถามได้ให้บอกตรงๆ "
    "ห้ามตอบเลี่ยงว่าให้ไปตรวจสอบข้อมูลเอง"
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
    if profile.home_place:
        # "ผมอยู่ที่ไหน" got "ผมไม่ทราบ": it didn't know it lives in the user's home.
        # Phrased as a fact: as an instruction it was also said after an unrelated flood answer.
        facts.append(f"บ้านของผู้ใช้อยู่{profile.home_place} คุณติดตั้งอยู่ที่บ้านนี้ "
                     "ผู้ใช้คุยกับคุณผ่านไมค์ที่บ้าน จึงน่าจะอยู่บ้านด้วย")
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


_APOLOGY = re.compile(r"^(?:ผม|ดิฉัน|ฉัน)?(?:ต้อง)?(?:ขออภัย|ขอโทษ)[^ ]*(?:ครับ|ค่ะ|คะ)?[ ,]*")


def speakable(text: str) -> str:
    """Qwen sometimes answers in markdown ("**สลัด**:", "1."), which TTS would read out.
    A leading apology goes too: "ผมขออภัยครับ" kept coming back despite the prompt."""
    text = _APOLOGY.sub("", text.strip())
    text = re.sub(r"[*#`_>|]+", "", text)
    # Qwen2 slips Chinese into Thai: CJK characters and full-width punctuation ("，。").
    text = re.sub(r"[\u3000-\u303f\u3040-\u30ff\u3400-\u9fff\uff00-\uffef]+", " ", text)
    text = re.sub(r"(?<!\S)[เแโใไ](?!\S)", " ", text)  # a leading vowel left alone by the cut
    text = re.sub(r"(?m)^\s*(?:\d+[.)]|[-•])\s+", "", text)
    return " ".join(text.replace(":", " ").split())


def _messages(ctx: Context, text: str) -> list[dict]:
    system = _system(ctx) + _profile_context(ctx) + DATA_RULE
    now = time.time()
    for name, (at, _) in list(ctx.facts.items()):
        if now - at > FACTS_EXPIRE_SECONDS:
            del ctx.facts[name]
    if ctx.facts:
        system += "\nข้อมูลจริง:\n" + "\n".join(
            f"(ดึงมาเมื่อ {round((now - at) / 60)} นาทีก่อน) {line}" if now - at >= 60 else line
            for at, line in ctx.facts.values())
    if _ASKS_WHY.search(text):
        system += EXPLAIN_RULE
    messages = [{"role": "system", "content": system}]
    for said, replied in ctx.history[-HISTORY_TURNS:]:
        messages += [{"role": "user", "content": said}, {"role": "assistant", "content": replied}]
    return messages + [{"role": "user", "content": text}]


def answer_from(ctx: Context, text: str) -> str:
    """The whole reply at once, from ctx.facts when tools were used."""
    reply = complete(_messages(ctx, text), ctx.config.llm_endpoint, temperature=0.7, max_tokens=160, timeout=60)
    limit = EXPLAIN_REPLY_CHARS if _ASKS_WHY.search(text) else MAX_REPLY_CHARS
    return shorten(speakable(reply), ctx.config.gender, limit)


def _phrase_break(buffer: str) -> int:
    """The last space not next to a number: "ในรอบ 7 วัน" was spoken as "ในรอบ" then "เจ็ด วัน",
    and "23 นาฬิกา" as "23" then "นาฬิกา"."""
    for index in range(len(buffer) - 2, 0, -1):  # a trailing space: the next word is unknown yet
        if buffer[index] == " " and not buffer[index + 1].isdigit() and not buffer[index - 1].isdigit():
            return index
    return -1


def stream_from(ctx: Context, text: str) -> Iterator[str]:
    """Yields whole sentences as the model writes them, up to STREAM_REPLY_CHARS in total
    (EXPLAIN_REPLY_CHARS for a "why"), answering from ctx.facts when tools were used."""
    limit = EXPLAIN_REPLY_CHARS if _ASKS_WHY.search(text) else STREAM_REPLY_CHARS
    pieces = stream(_messages(ctx, text), ctx.config.llm_endpoint, temperature=0.7, max_tokens=320, timeout=60)
    buffer, spoken = "", 0
    try:
        for piece in pieces:
            buffer += piece
            while True:
                match = _SENTENCE_BREAK.search(buffer)
                if match:
                    sentence, buffer = buffer[: match.start()], buffer[match.end():]
                elif len(buffer) > (FIRST_CHUNK_CHARS if spoken == 0 else RUN_ON_CHARS) and _phrase_break(buffer) >= 10:
                    # No sentence end yet: speak up to a phrase break so audio can start early.
                    cut = _phrase_break(buffer)
                    sentence, buffer = buffer[:cut], buffer[cut + 1:]
                else:
                    break
                sentence = speakable(sentence)
                if not sentence:
                    continue
                spoken += len(sentence)
                if spoken >= limit:
                    yield shorten(sentence, ctx.config.gender, limit=len(sentence))  # ensure it ends politely
                    return
                yield sentence
        tail = speakable(buffer)
        if tail:
            yield shorten(tail, ctx.config.gender, limit=len(tail))
    finally:
        pieces.close()  # stop generating once enough was said


SKILLS = (Skill(intent="chat", handle=answer_from, slow=True, stream=stream_from),)
