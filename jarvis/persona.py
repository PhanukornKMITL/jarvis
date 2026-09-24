"""How JARVIS phrases what it says: polite particle for its gender, and the user's name.

Replies across the code are written with ค่ะ; this is the one place they are adapted.
"""

from __future__ import annotations

import re

# "คะ" only as a whole particle, so words like "คะแนน" are left alone; "ฉัน" but not "ฉันท์"/"ฉันทามติ".
_MALE = (
    (re.compile(r"นะคะ"), "นะครับ"), (re.compile(r"ค่ะ"), "ครับ"), (re.compile(r"คะ(?=[\s?!.,]|$)"), "ครับ"),
    (re.compile(r"ดิฉัน"), "ผม"), (re.compile(r"ฉัน(?!ท)"), "ผม"),
)


def particle(gender: str) -> str:
    return "ครับ" if gender == "male" else "ค่ะ"


def pronoun(gender: str) -> str:
    return "ผม" if gender == "male" else "ฉัน"


def apply_persona(text: str, gender: str, name: str = "") -> str:
    """"เปิดไฟให้แล้วค่ะ" → "เปิดไฟให้แล้วครับ คุณแบงค์" (male): the name follows the first particle."""
    if gender == "male":
        for pattern, replacement in _MALE:
            text = pattern.sub(replacement, text)
    name = name.strip()
    if not name or name in text:
        return text
    title = name if name.startswith("คุณ") else f"คุณ{name}"
    mark = particle(gender)
    index = text.find(mark)
    if index < 0:
        return f"{text} {title}"
    end = index + len(mark)
    return f"{text[:end]} {title}{text[end:]}"
