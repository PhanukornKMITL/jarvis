"""Long-term memory: facts about the owner kept across days, like ChatGPT's memory.

Only two ways in, both chosen by the owner: saying "จำไว้ว่า..." or answering yes when
JARVIS offers "ให้ผมจำไว้ไหมครับว่า...". Everything lives in work/memories.json on this
machine (git-ignored) until "ลืม..." removes it; [memory] enabled = false turns it off.
"""

from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from datetime import datetime
from pathlib import Path

from .config import ROOT
from .llm import complete

PATH = ROOT / "work" / "memories.json"
MAX_IN_PROMPT = 30
THAI_MONTHS = ("ม.ค.", "ก.พ.", "มี.ค.", "เม.ย.", "พ.ค.", "มิ.ย.", "ก.ค.", "ส.ค.", "ก.ย.", "ต.ค.", "พ.ย.", "ธ.ค.")

# Gemma judged 14/15 real sentences right with this prompt (the miss, "ผมจะไปตลาดตอนเย็น",
# is only offered, and the owner can say no).
NOTICE_PROMPT = (
    "อ่านสิ่งที่ผู้ใช้พูดกับผู้ช่วยในบ้าน แล้วตัดสินว่ามีข้อมูลเกี่ยวกับตัวผู้ใช้ที่ควรจำไว้ใช้ในวันอื่นไหม "
    "เช่น ความชอบ สิ่งที่แพ้หรือไม่กิน สุขภาพ คนในครอบครัว สัตว์เลี้ยง งาน แผนหรือนัดในอนาคต "
    "ไม่ต้องจำคำถาม คำสั่ง อารมณ์ชั่วคราว หรือเรื่องทั่วไป "
    'ตอบ JSON บรรทัดเดียว {"remember": "ข้อความสั้นๆ ขึ้นต้นด้วย ผู้ใช้"} หรือ {"remember": ""}'
)


def _today() -> str:
    now = datetime.now()
    return f"{now.day} {THAI_MONTHS[now.month - 1]} {now.year + 543}"


def load(path: Path | None = None) -> list[dict]:
    try:
        return json.loads((path or PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def _save(items: list[dict], path: Path | None = None) -> None:
    path = path or PATH  # read at call time, so tests can point PATH elsewhere
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")


def as_user(text: str) -> str:
    """"ผมแพ้กุ้ง" → "ผู้ใช้แพ้กุ้ง", so the chat prompt can't mistake whose fact it is."""
    text = text.strip(" .")
    return text if text.startswith("ผู้ใช้") else "ผู้ใช้" + re.sub(r"^(?:ผม|ฉัน|เรา|หนู)\s*", "", text)


def add(text: str, path: Path | None = None) -> str:
    fact = as_user(text)
    items = load(path)
    if not any(item["text"] == fact for item in items):
        # The date turns "พรุ่งนี้" in a stored plan into a real day when it is used later.
        items.append({"text": fact, "added": _today()})
        _save(items, path)
    return fact


def forget(words: str, path: Path | None = None) -> list[str]:
    """Removes the memories sharing the most words with `words` ("ทั้งหมด" clears all)."""
    items = load(path)
    if re.search(r"ทั้งหมด|ทุกอย่าง|ทุกเรื่อง", words):
        _save([], path)
        return [item["text"] for item in items]
    wanted = [w for w in re.findall(r"[ก-๙a-zA-Z0-9]{2,}", words) if w not in ("เรื่อง", "ที่", "ว่า")]

    def overlap(item: dict) -> int:
        return sum(w in item["text"] for w in wanted)

    best = max((overlap(item) for item in items), default=0)
    if not best:
        return []
    gone = [item for item in items if overlap(item) == best]
    _save([item for item in items if item not in gone], path)
    return [item["text"] for item in gone]


def prompt_lines(path: Path | None = None) -> list[str]:
    return [f"{item['text']} (จำไว้เมื่อ {item['added']})" for item in load(path)[-MAX_IN_PROMPT:]]


# Questions carry no new fact: "แมวผมชื่ออะไร" got "ให้ผมจำไว้ไหมว่าคุณมีแมว".
_QUESTION = re.compile(r"(?:ไหม|มั้ย|อะไร|ยังไง|อย่างไร|เท่าไหร่|กี่|ที่ไหน|ไหน|เมื่อไหร่|หรือเปล่า|รึเปล่า|เหรอ|หรอ|\?)"
                       r"\s*(?:ดี|บ้าง|นะ)?\s*(?:ครับ|คะ|ค่ะ|นะ)?$")


def _known(fact: str) -> bool:
    return any(SequenceMatcher(None, fact, item["text"]).ratio() > 0.6 for item in load())


def notice(text: str, endpoint: str) -> str:
    """A fact worth offering to remember in `text`, or ""."""
    if len(text) < 6 or _QUESTION.search(text.strip()):
        return ""
    out = complete([{"role": "system", "content": NOTICE_PROMPT}, {"role": "user", "content": text}],
                   endpoint, temperature=0, max_tokens=60, timeout=20)
    try:
        fact = json.loads(out[out.find("{"):out.rfind("}") + 1]).get("remember", "")
    except ValueError:
        return ""
    return "" if not fact or _known(as_user(fact)) else as_user(fact)
