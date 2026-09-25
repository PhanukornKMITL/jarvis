"""Owner-controlled memory commands: "จำไว้ว่า...", "ลืมเรื่อง...", "คุณจำอะไรเกี่ยวกับผมบ้าง"."""

from __future__ import annotations

import re

from .. import memory
from .base import Context, Skill

_REMEMBER = re.compile(r"^(?:ช่วย)?จำไว้(?:ด้วย|หน่อย)?(?:นะ)?(?:ครับ|คะ|ค่ะ)?\s*(?:ว่า)\s*(.+)")
_FORGET = re.compile(r"^(?:ช่วย)?ลืม(?:เรื่อง|ที่ว่า)?\s*(.+?)\s*(?:ไป)?(?:ได้)?(?:เลย|แล้ว)?(?:นะ)?(?:ครับ|คะ|ค่ะ)?$")
_LIST = re.compile(r"(?:จำ|รู้)อะไรเกี่ยวกับ(?:ผม|ฉัน|ตัวผม)|คุณจำอะไรไว้บ้าง|ความจำของคุณมีอะไร")
OFF = "ระบบความจำปิดอยู่ค่ะ เปิดได้ที่ [memory] ใน config.toml"


def _spoken(fact: str) -> str:
    return fact.replace("ผู้ใช้", "คุณ", 1)


def _remember(ctx: Context, text: str) -> str:
    if not ctx.config.memory_enabled:
        return OFF
    return f"จำไว้แล้วค่ะว่า{_spoken(memory.add(_REMEMBER.search(text).group(1)))}"


def _forget(ctx: Context, text: str) -> str:
    if not ctx.config.memory_enabled:
        return OFF
    gone = memory.forget(_FORGET.search(text).group(1))
    if not gone:
        return "ผมไม่ได้จำเรื่องนั้นไว้ค่ะ"
    return "ลืมแล้วค่ะ " + " และ ".join(_spoken(fact) for fact in gone[:3])


def _list(ctx: Context, _text: str) -> str:
    if not ctx.config.memory_enabled:
        return OFF
    facts = [_spoken(item["text"]) for item in memory.load()]
    if not facts:
        return "ผมยังไม่ได้จำอะไรไว้ค่ะ บอกว่า จำไว้ว่า แล้วตามด้วยเรื่องที่อยากให้จำได้เลย"
    more = f" และอีก {len(facts) - 5} เรื่อง" if len(facts) > 5 else ""
    return "ผมจำไว้ว่า " + " ".join(facts[-5:]) + more + "ค่ะ"


SKILLS = (
    Skill(intent="remember", handle=_remember, rule=lambda text: bool(_REMEMBER.search(text))),
    Skill(intent="forget", handle=_forget, rule=lambda text: bool(_FORGET.search(text))),
    Skill(intent="memories", handle=_list, rule=lambda text: bool(_LIST.search(text))),
)
