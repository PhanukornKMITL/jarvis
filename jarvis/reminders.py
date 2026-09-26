"""Reminders: "เตือนผมพรุ่งนี้บ่ายสองว่าไปหาหมอฟัน", spoken by JARVIS when the time comes.

Thai times and dates are read by code, not the LLM: a reminder at the wrong hour is worse
than none, and Thai clock words (บ่ายสอง, สามทุ่ม, ตีห้า) follow fixed rules. The LLM only
says what the reminder is about. Reminders live in work/reminders.json (git-ignored).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import ROOT
from .llm import complete

PATH = ROOT / "work" / "reminders.json"
DEFAULT_HOUR = 9
"""A date without a time ("วันที่ 5 ตุลา เตือนจ่ายค่าไฟ") is reminded at 9 in the morning."""
DAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัส", "ศุกร์", "เสาร์", "อาทิตย์")
MONTHS = (("มกรา", "ม.ค."), ("กุมภา", "ก.พ."), ("มีนา", "มี.ค."), ("เมษา", "เม.ย."), ("พฤษภา", "พ.ค."),
          ("มิถุนา", "มิ.ย."), ("กรกฎา", "ก.ค."), ("สิงหา", "ส.ค."), ("กันยา", "ก.ย."), ("ตุลา", "ต.ค."),
          ("พฤศจิกา", "พ.ย."), ("ธันวา", "ธ.ค."))
_WORDS = {"หนึ่ง": 1, "เอ็ด": 1, "สอง": 2, "สาม": 3, "สี่": 4, "ห้า": 5, "หก": 6, "เจ็ด": 7, "แปด": 8,
          "เก้า": 9, "สิบ": 10, "สิบเอ็ด": 11, "สิบสอง": 12, "ยี่สิบ": 20, "ครึ่ง": 30}
_NUM = r"(\d{1,2}|สิบเอ็ด|สิบสอง|หนึ่ง|สอง|สาม|สี่|ห้า|หก|เจ็ด|แปด|เก้า|สิบ)"


def _n(word: str) -> int:
    return int(word) if word.isdigit() else _WORDS[word]


def _clock(text: str) -> tuple[int, int] | None:
    """Hour and minute from Thai clock words or digits; None if no time is said."""
    minute = 0
    m = re.search(r"(\d{1,2})[:.](\d{2})", text)
    if m:
        return int(m.group(1)), int(m.group(2))
    half = re.search(r"ครึ่ง", text)
    mins = re.search(rf"{_NUM}\s*นาที", text)
    if half:
        minute = 30
    elif mins and not re.search(r"อีก\s*" + _NUM + r"\s*นาที", text):
        minute = _n(mins.group(1))
    if re.search(r"เที่ยงคืน|สองยาม", text):
        return 0, minute
    if re.search(r"เที่ยง", text):
        return 12, minute
    if m := re.search(rf"ตี\s*{_NUM}", text):
        return _n(m.group(1)), minute
    if m := re.search(rf"{_NUM}\s*ทุ่ม", text):
        return 18 + _n(m.group(1)), minute
    if m := re.search(rf"บ่าย\s*{_NUM}?\s*(?:โมง)?", text):
        return 12 + (_n(m.group(1)) if m.group(1) else 1), minute
    if m := re.search(rf"{_NUM}\s*โมง\s*(เช้า|เย็น|ค่ำ)?", text):
        hour, part = _n(m.group(1)), m.group(2)
        if part == "เช้า":
            return (hour + 6 if hour <= 5 else hour), minute  # สองโมงเช้า = 8
        if part in ("เย็น", "ค่ำ"):
            return (hour + 12 if hour < 12 else hour), minute
        return (hour + 12 if hour <= 6 else hour), minute  # สี่โมง = 16, สิบโมง = 10
    if m := re.search(rf"{_NUM}\s*นาฬิกา", text):
        return _n(m.group(1)), minute
    return None


def _day(text: str, today: date) -> date | None:
    if re.search(r"มะรืน", text):
        return today + timedelta(days=2)
    if re.search(r"พรุ่งนี้", text):
        return today + timedelta(days=1)
    if re.search(r"วันนี้|คืนนี้|เย็นนี้|บ่ายนี้|เช้านี้", text):
        return today
    if m := re.search(r"วันที่\s*(\d{1,2})(?:\s*(" + "|".join(a for pair in MONTHS for a in pair) + r"))?", text):
        day = int(m.group(1))
        month = next((i + 1 for i, pair in enumerate(MONTHS) if m.group(2) in pair), None) if m.group(2) else None
        month = month or (today.month if day >= today.day else today.month % 12 + 1)
        year = today.year if (month, day) >= (today.month, today.day) else today.year + 1
        try:
            return date(year, month, day)
        except ValueError:
            return None
    for index, name in enumerate(DAYS):
        if re.search(rf"(?:วัน)?{name}", text):
            ahead = (index - today.weekday()) % 7
            if re.search(rf"{name}\S*\s*หน้า", text):  # "จันทร์หน้า": in next week
                ahead = ahead + 7 if ahead and index >= today.weekday() else ahead or 7
            return today + timedelta(days=ahead)
    return None


def parse_when(text: str, now: datetime | None = None) -> datetime | None:
    """When a reminder in `text` should go off, or None if no time or day is said."""
    now = now or datetime.now()
    text = _LEAD.sub(" ", text)  # "เตือนก่อนครึ่งชั่วโมง" is not half past
    if m := re.search(rf"อีก\s*{_NUM}\s*(นาที|ชั่วโมง|ชม)", text):
        amount = _n(m.group(1))
        return now + (timedelta(minutes=amount) if m.group(2) == "นาที" else timedelta(hours=amount))
    clock, day = _clock(text), _day(text, now.date())
    if clock is None and day is None:
        return None
    hour, minute = clock if clock else (DEFAULT_HOUR, 0)
    at = datetime.combine(day or now.date(), datetime.min.time()).replace(hour=hour % 24, minute=minute)
    if day is None and at <= now:
        at += timedelta(days=1)  # "สามทุ่ม" said at 22:00 means tomorrow
    return at


_LEAD = re.compile(rf"(?:ก่อน|ล่วงหน้า)\s*{_NUM}?\s*(ครึ่ง)?\s*(นาที|ชั่วโมง|ชม)")


def lead_minutes(text: str) -> int:
    """"เตือนก่อน 30 นาที" / "ล่วงหน้าครึ่งชั่วโมง" → minutes before the time."""
    m = _LEAD.search(text)
    if not m:
        return 0
    amount = 0.5 if m.group(2) and not m.group(1) else _n(m.group(1)) if m.group(1) else 1
    return round(amount * (60 if m.group(3) != "นาที" else 1))


def repeat_of(text: str) -> str:
    if re.search(r"วันธรรมดา|จันทร์ถึงศุกร์|วันทำงาน", text):
        return "weekdays"
    if re.search(r"ทุกวัน(?!\S*(?:" + "|".join(DAYS) + "))", text):
        return "daily"
    if re.search(r"ทุกสัปดาห์|ทุกอาทิตย์|ทุกวัน(?:" + "|".join(DAYS) + ")", text):
        return "weekly"
    return ""


WHAT_PROMPT = ("ผู้ใช้ขอให้ตั้งเตือน สรุปว่าจะเตือนเรื่องอะไร เป็นวลีสั้นๆ ไม่ต้องใส่วันเวลา "
               'ตอบ JSON บรรทัดเดียว {"what": "..."} เช่น {"what": "ไปหาหมอฟัน"}')


def what_of(text: str, endpoint: str) -> str:
    out = complete([{"role": "system", "content": WHAT_PROMPT}, {"role": "user", "content": text}],
                   endpoint, temperature=0, max_tokens=40, timeout=20)
    try:
        return json.loads(out[out.find("{"):out.rfind("}") + 1]).get("what", "").strip()
    except ValueError:
        return ""


@dataclass
class Reminder:
    id: str
    what: str
    at: str
    """ISO time of the event."""
    lead: int = 0
    """Minutes before `at` to speak."""
    repeat: str = ""
    """"", "daily" or "weekly"."""
    notified: str = ""
    """ISO time it last went off, so a restart doesn't repeat it."""
    alarm: bool = False
    """Rings until answered ("ปลุก..."), instead of being said once."""

    @property
    def when(self) -> datetime:
        return datetime.fromisoformat(self.at)

    @property
    def due_at(self) -> datetime:
        return self.when - timedelta(minutes=self.lead)


def spoken(at: datetime, now: datetime | None = None) -> str:
    """"พรุ่งนี้ วันเสาร์ที่ 26 ก.ย. เวลา 14 นาฬิกา"."""
    now = now or datetime.now()
    days = (at.date() - now.date()).days
    near = {0: "วันนี้ ", 1: "พรุ่งนี้ ", 2: "มะรืนนี้ "}.get(days, "")
    clock = f"{at.hour} นาฬิกา" + (f" {at.minute} นาที" if at.minute else "")
    return f"{near}วัน{DAYS[at.weekday()]}ที่ {at.day} {MONTHS[at.month - 1][1]} เวลา {clock}"


def load(path: Path | None = None) -> list[Reminder]:
    try:
        return [Reminder(**item) for item in json.loads((path or PATH).read_text(encoding="utf-8"))]
    except (OSError, ValueError, TypeError):
        return []


def _save(items: list[Reminder], path: Path | None = None) -> None:
    path = path or PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps([vars(r) for r in items], ensure_ascii=False, indent=1), encoding="utf-8")


def add(reminder: Reminder) -> None:
    _save([*load(), reminder])


def new(what: str, at: datetime, lead: int = 0, repeat: str = "", alarm: bool = False) -> Reminder:
    return Reminder(uuid.uuid4().hex[:8], what, at.isoformat(timespec="minutes"), lead, repeat, alarm=alarm)


def upcoming(now: datetime | None = None) -> list[Reminder]:
    now = now or datetime.now()
    return sorted((r for r in load() if r.when >= now - timedelta(minutes=1) or r.repeat), key=lambda r: r.when)


def cancel(words: str) -> list[Reminder]:
    """Removes the reminders whose subject shares the most words with `words` ("ทั้งหมด": all)."""
    items = load()
    if re.search(r"ทั้งหมด|ทุกอัน|ทุกอย่าง", words):
        _save([])
        return items
    wanted = re.findall(r"[ก-๙a-zA-Z0-9]{2,}", words)
    score = {r.id: sum(w in r.what for w in wanted) for r in items}
    best = max(score.values(), default=0)
    gone = [r for r in items if best and score[r.id] == best]
    _save([r for r in items if r not in gone])
    return gone


def due(now: datetime | None = None) -> list[Reminder]:
    """Reminders to speak now; marks them, and moves repeating ones to their next time."""
    now = now or datetime.now()
    items, fired = load(), []
    for r in items:
        if r.due_at <= now and not r.notified:
            fired.append(Reminder(**vars(r)))
            if r.repeat:
                step = timedelta(days=7 if r.repeat == "weekly" else 1)
                at = r.when
                while at - timedelta(minutes=r.lead) <= now or (r.repeat == "weekdays" and at.weekday() >= 5):
                    at += step
                r.at = at.isoformat(timespec="minutes")
            else:
                r.notified = now.isoformat(timespec="minutes")
    if fired:
        # One-off reminders stay a day after going off, for "เลื่อนอีก 10 นาที".
        _save([r for r in items if not r.notified or now - datetime.fromisoformat(r.notified) < timedelta(days=1)])
    return fired


def snooze(reminder_id: str, minutes: int, now: datetime | None = None) -> Reminder | None:
    now = now or datetime.now()
    items = load()
    for r in items:
        if r.id == reminder_id:
            r.at, r.lead, r.notified = (now + timedelta(minutes=minutes)).isoformat(timespec="minutes"), 0, ""
            _save(items)
            return r
    return None
