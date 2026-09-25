"""Reminder commands: set (confirmed first), list, cancel, and snooze the one that just rang."""

from __future__ import annotations

import re
from datetime import datetime

from .. import reminders
from .base import Context, Skill

_ASKS = re.compile(r"เตือน|ปลุก|ตั้งเวลา")
_LIST = re.compile(r"(?:มี|ดู)(?:การ)?(?:เตือน|นัด)(?:อะไร)?(?:ไว้)?(?:บ้าง)|(?:ตั้ง)?เตือนอะไรไว้(?:บ้าง)?")
_CANCEL = re.compile(r"(?:ยกเลิก|ลบ)(?:การ)?(?:เตือน|นัด)(?:เรื่อง)?\s*(.*)")
_SNOOZE = re.compile(rf"เลื่อน(?:ไป)?(?:อีก)?\s*{reminders._NUM}?\s*(นาที|ชั่วโมง|ชม)")


def describe(r: reminders.Reminder) -> str:
    lead = f" เตือนก่อน {r.lead} นาที" if r.lead else ""
    repeat = {"daily": " ทุกวัน", "weekly": " ทุกสัปดาห์"}.get(r.repeat, "")
    return f"{r.what} {reminders.spoken(r.when)}{repeat}{lead}"


def _set(ctx: Context, text: str) -> str:
    at = reminders.parse_when(text)
    if at is None:
        return "ต้องการให้เตือนวันไหน กี่โมงคะ"
    if at < datetime.now():
        return f"{reminders.spoken(at)} ผ่านไปแล้วค่ะ ช่วยบอกเวลาใหม่อีกทีนะคะ"
    what = reminders.what_of(text, ctx.config.llm_endpoint) or "เรื่องที่ขอไว้"
    reminder = reminders.new(what, at, reminders.lead_minutes(text), reminders.repeat_of(text))
    ctx.offers[:] = [("reminder", reminder)]
    return f"ตั้งเตือน{describe(reminder)} ใช่ไหมคะ"


def _list(_ctx: Context, _text: str) -> str:
    items = reminders.upcoming()
    if not items:
        return "ยังไม่มีการเตือนที่ตั้งไว้ค่ะ"
    more = f" และอีก {len(items) - 5} รายการ" if len(items) > 5 else ""
    return "ที่ตั้งไว้มี " + " ".join(describe(r) for r in items[:5]) + more + "ค่ะ"


def _cancel(_ctx: Context, text: str) -> str:
    gone = reminders.cancel(_CANCEL.search(text).group(1))
    return "ยกเลิกเตือน" + " และ ".join(r.what for r in gone) + "แล้วค่ะ" if gone else "ไม่เจอการเตือนเรื่องนั้นค่ะ"


def _snooze(ctx: Context, text: str) -> str:
    if not ctx.fired:
        return "ตอนนี้ไม่มีเรื่องที่เพิ่งเตือนค่ะ"
    m = _SNOOZE.search(text)
    amount = reminders._n(m.group(1)) if m.group(1) else 10
    minutes = amount * (1 if m.group(2) == "นาที" else 60)
    r = reminders.snooze(ctx.fired[-1], minutes)
    return f"เลื่อนเตือน{r.what}ไปอีก {minutes} นาทีแล้วค่ะ" if r else "หาเรื่องที่เพิ่งเตือนไม่เจอค่ะ"


SKILLS = (
    Skill(intent="reminder_list", handle=_list, rule=lambda text: bool(_LIST.search(text))),
    Skill(intent="reminder_cancel", handle=_cancel, rule=lambda text: bool(_CANCEL.search(text))),
    Skill(intent="reminder_snooze", handle=_snooze, rule=lambda text: bool(_SNOOZE.search(text))),
    Skill(intent="reminder_set", handle=_set,
          rule=lambda text: bool(_ASKS.search(text)) and not _LIST.search(text) and not _CANCEL.search(text)
          and not _SNOOZE.search(text) and reminders.parse_when(text) is not None),
)
