"""Moments of the day: work mode (talk without the wake word, like Tony in his lab) and
good night. The good-morning greeting is added by the voice session on the first turn."""

from __future__ import annotations

import re
import time

from .base import Context, Skill

WORK_MODE_SECONDS = 600
"""Work mode ends after this long without a word from the owner."""
_WORK_ON = re.compile(r"เริ่มทำงาน|เข้าโหมดทำงาน|โหมดทำงาน")
_WORK_OFF = re.compile(r"เลิกทำงาน|ออกจากโหมดทำงาน|ปิดโหมดทำงาน|พอแค่นี้")
_NIGHT = re.compile(r"ฝันดี|ไปนอน(?:ก่อน|แล้ว|ละ)|จะนอนแล้ว|นอนแล้วนะ|ราตรีสวัสดิ์")


def _work_on(ctx: Context, _text: str) -> str:
    ctx.mode["work_until"] = time.monotonic() + WORK_MODE_SECONDS
    return "พร้อมค่ะ เข้าโหมดทำงาน พูดได้เลยไม่ต้องเรียกชื่อ บอกว่าเลิกทำงานเมื่อเสร็จนะคะ"


def _work_off(ctx: Context, _text: str) -> str:
    ctx.mode.pop("work_until", None)
    return "ออกจากโหมดทำงานแล้วค่ะ"


def _night(ctx: Context, _text: str) -> str:
    try:
        lights_on = any(d.get("device_type") == "light" and d.get("online") and d.get("state", {}).get("power") == "on"
                        for d in ctx.devices())
    except OSError:
        lights_on = False
    if lights_on:
        ctx.offers[:] = [("light_off", None)]
        return "ฝันดีค่ะ ไฟยังเปิดอยู่ ให้ผมปิดไฟไหมคะ"
    return "ฝันดีค่ะ พักผ่อนเยอะๆ นะคะ"


SKILLS = (
    Skill(intent="work_off", handle=_work_off, rule=lambda text: bool(_WORK_OFF.search(text))),
    Skill(intent="work_on", handle=_work_on, rule=lambda text: bool(_WORK_ON.search(text)) and not _WORK_OFF.search(text)),
    Skill(intent="good_night", handle=_night, rule=lambda text: bool(_NIGHT.search(text))),
)
