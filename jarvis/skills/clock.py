from __future__ import annotations

import re
from datetime import datetime

from .base import Context, Skill

DAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
MONTHS = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
          "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม")


# "ขอข้อมูลวันที่และเวลา" was routed to date alone and the time was dropped.
_ASKS_TIME = re.compile(r"เวลา|โมง|นาฬิกา|นาที")
_ASKS_DATE = re.compile(r"วันที่|วันอะไร|วันนี้วัน|เดือน|ปี")


def _time_text(now: datetime) -> str:
    minutes = f" {now.minute} นาที" if now.minute else " ตรง"
    return f"ตอนนี้ {now.hour} นาฬิกา{minutes}"


def _date_text(now: datetime) -> str:
    return f"วันนี้วัน{DAYS[now.weekday()]}ที่ {now.day} {MONTHS[now.month - 1]} {now.year + 543}"


def _time(_ctx: Context, text: str) -> str:
    now = datetime.now()
    parts = [_date_text(now)] if _ASKS_DATE.search(text) else []
    return " ".join([*parts, _time_text(now)]) + "ค่ะ"


def _date(_ctx: Context, text: str) -> str:
    now = datetime.now()
    parts = [_time_text(now)] if _ASKS_TIME.search(text) else []
    return " ".join([_date_text(now), *parts]) + "ค่ะ"


SKILLS = (
    Skill(intent="time", description="ถามเวลาตอนนี้ กี่โมงแล้ว", handle=_time),
    Skill(intent="date", description="ถามวันที่ วันนี้วันอะไร เดือนอะไร ปีอะไร", handle=_date),
)
