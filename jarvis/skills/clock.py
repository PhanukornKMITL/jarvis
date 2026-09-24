from __future__ import annotations

from datetime import datetime

from .base import Context, Skill

DAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
MONTHS = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
          "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม")


def _time(_ctx: Context, _text: str) -> str:
    now = datetime.now()
    minutes = f" {now.minute} นาที" if now.minute else " ตรง"
    return f"ตอนนี้ {now.hour} นาฬิกา{minutes}ค่ะ"


def _date(_ctx: Context, _text: str) -> str:
    now = datetime.now()
    return f"วันนี้วัน{DAYS[now.weekday()]}ที่ {now.day} {MONTHS[now.month - 1]} {now.year + 543} ค่ะ"


SKILLS = (
    Skill(intent="time", description="ถามเวลาตอนนี้ กี่โมงแล้ว", handle=_time),
    Skill(intent="date", description="ถามวันที่ วันนี้วันอะไร เดือนอะไร ปีอะไร", handle=_date),
)
