from __future__ import annotations

import re

from .base import Context, Skill

TARGET = "desk_light"
import random

# Varied, as a butler would; some address the owner as บอส/เจ้านาย (VISION.md).
ON_REPLIES = ("เปิดไฟให้แล้วค่ะ", "เปิดแล้วค่ะ บอส", "เรียบร้อยค่ะ ไฟเปิดแล้ว", "ได้เลยค่ะ เจ้านาย")
OFF_REPLIES = ("ปิดไฟให้แล้วค่ะ", "ปิดแล้วค่ะ บอส", "เรียบร้อยค่ะ ไฟปิดแล้ว", "ได้เลยค่ะ เจ้านาย")
ON_REPLY, OFF_REPLY = ON_REPLIES[0], OFF_REPLIES[0]
# Spellings whisper actually produced for these words over a Bluetooth headset mic.
_LIGHT = r"(?:ไฟ|ฟาย|ฟัย|ภัย|fy|fai)"
_ON = re.compile(rf"(?:เปิด|เปิ้ด|เบิด)\s*{_LIGHT}|open\s*{_LIGHT}", re.IGNORECASE)
# "ปิด" is a substring of "เปิด", so the lookbehind keeps "เปิดไฟ" out. Real "ปิดไฟ" came
# out as พิฟัย / ปีฟาย / ภิกฟาย; "เปิด" never lost its เ- vowel that way.
_OFF = re.compile(rf"(?<!เ)(?:ปิ|ปี|บิ|พิ|ภิ)[ดตทก]?\s*{_LIGHT}|bit\s*{_LIGHT}|bitfy", re.IGNORECASE)
# "ภัย" is left out here: it is a common real word, unlike the misspellings ฟาย/ฟัย.
_LIGHT_WORD = re.compile(r"ไฟ|ฟาย|ฟัย")


def _switch(ctx: Context, action: str, done: str) -> str:
    return done if ctx.action(TARGET, action).get("ok") else "สั่งไฟไม่สำเร็จค่ะ"


def mentions_light(text: str) -> bool:
    return bool(_LIGHT_WORD.search(text))


SKILLS = (
    Skill(
        intent="light_on",
        handle=lambda ctx, _text: _switch(ctx, "turn_on", random.choice(ON_REPLIES)),
        rule=lambda text: bool(_ON.search(text)),
        mentions=mentions_light,
    ),
    Skill(
        intent="light_off",
        handle=lambda ctx, _text: _switch(ctx, "turn_off", random.choice(OFF_REPLIES)),
        rule=lambda text: bool(_OFF.search(text)),
        mentions=mentions_light,
    ),
)
