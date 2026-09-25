"""'เมื่อกี้ผมพูดว่าอะไร' and 'พูดอีกทีได้ไหม', answered from the conversation itself.

With no history (right after a restart) Gemma answered 'คุณเพิ่งถามว่า "เมื่อกี้ผมพูดว่าอะไร"',
so this is a rule, not something the LLM has to get right."""

from __future__ import annotations

import re

from .base import Context, Skill

# Only "what did I say", not questions about what was said ("ตะกี้ผมบอกว่าจะไปไหน" is for chat).
_MINE = re.compile(r"(?:ผม|ฉัน|เรา).{0,4}(?:พูด|ถาม|บอก)(?:ไป)?(?:ว่า)?(?:อะไร|ไร)")
_YOURS = re.compile(r"พูดอีกที|พูดใหม่|ทวนอีกที|ทวนให้หน่อย|(?:เมื่อกี้|ตะกี้).{0,6}(?:คุณ|จาวิส|จาร์วิส)?.{0,4}พูดว่าอะไร")


def _recall(ctx: Context, text: str) -> str:
    if not ctx.history:
        return "ผมยังไม่มีบทสนทนาก่อนหน้านี้ค่ะ เราเพิ่งเริ่มคุยกัน"
    said, replied = ctx.history[-1]
    if _MINE.search(text):
        return f"เมื่อกี้คุณพูดว่า {said} ค่ะ"
    return f"ผมพูดว่า {replied}"


SKILLS = (Skill(intent="recall", handle=_recall, rule=lambda text: bool(_MINE.search(text) or _YOURS.search(text))),)
