from __future__ import annotations

import re
import time

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


def explain(result: dict) -> str:
    """Why a switch failed, in words: "สั่งไฟไม่สำเร็จ" alone left the owner guessing, and a
    follow-up "ทำไม" got "ผมควบคุมอุปกรณ์ไม่ได้"."""
    reason = result.get("reason")
    if reason == "disconnected":
        return (f"ไม่เห็นไฟโต๊ะเชื่อมต่ออยู่ค่ะ หลุดไปตั้งแต่ {result.get('lost_at')} "
                "ESP32 อาจไม่มีไฟเลี้ยง หลุด Wi-Fi หรือหาเครื่อง Mac ไม่เจอค่ะ")
    if reason == "never_connected":
        return "ไฟโต๊ะยังไม่ได้เชื่อมต่อกับผมเลยตั้งแต่เปิดระบบค่ะ ลองเช็คว่า ESP32 เสียบไฟและต่อ Wi-Fi อยู่ไหมคะ"
    if reason == "no_response":
        return "ไฟโต๊ะเชื่อมต่ออยู่แต่ไม่ตอบคำสั่งค่ะ ลองกดปุ่ม EN รีสตาร์ท ESP32 ดูนะคะ"
    return f"สั่งไฟไม่สำเร็จค่ะ ({result.get('error', 'ไม่ทราบสาเหตุ')})"


def _switch(ctx: Context, action: str, done: str) -> str:
    try:
        result = ctx.action(TARGET, action)
    except (OSError, ConnectionError):
        result = {"ok": False, "error": "ติดต่อเซิร์ฟเวอร์อุปกรณ์ไม่ได้ เซิร์ฟเวอร์อาจไม่ได้เปิดอยู่"}
    if result.get("ok"):
        return done
    why = explain(result)
    ctx.facts["device_problem"] = (time.time(), f"ปัญหาล่าสุดตอนสั่งไฟ: {why}")  # for a follow-up "ทำไม"
    return why


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
