from __future__ import annotations

import re

from .base import Context, Skill

TARGET = "desk_light"
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


def _mentions_light(text: str) -> bool:
    return bool(_LIGHT_WORD.search(text))


SKILLS = (
    Skill(
        intent="light_on",
        description="สั่งเปิดไฟ",
        handle=lambda ctx, _text: _switch(ctx, "turn_on", "เปิดไฟให้แล้วค่ะ"),
        rule=lambda text: bool(_ON.search(text)),
        rule_only=True,
        mentions=_mentions_light,
    ),
    Skill(
        intent="light_off",
        description="สั่งปิดไฟ",
        handle=lambda ctx, _text: _switch(ctx, "turn_off", "ปิดไฟให้แล้วค่ะ"),
        rule=lambda text: bool(_OFF.search(text)),
        rule_only=True,
        mentions=_mentions_light,
    ),
)
