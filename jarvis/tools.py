"""Read-only tools the LLM picks by itself, then answers from their results.

Anything that changes the house (switching a light) stays a rule-based skill: a wrong
tool pick here only gives a wrong answer, never a wrong action. Results describe numbers
in words (ฝนปรอยๆ, ดินค่อนข้างแห้ง) because the owner wants what they mean, and computing
facts like "rain stops at 04:00" in code keeps a small model from misreading a table.
"""

from __future__ import annotations

import json
import re
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from .llm import tool_calls
from .skills.base import Context

DAYS = ("จันทร์", "อังคาร", "พุธ", "พฤหัสบดี", "ศุกร์", "เสาร์", "อาทิตย์")
MONTHS = ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน",
          "กรกฎาคม", "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม")
RAIN_MM = 0.5
"""Rain per hour below this counts as no rain."""
FORECAST_HOURS = 18
OUTING_HOURS = 3
"""How long a trip out is assumed to take: rain after leaving matters more than rain now."""
FORECAST_SPANS = 6
"""Rain spans passed on; each costs prompt tokens, and reading them was most of the wait."""
FORECAST_CACHE_SECONDS = 600
"""Open-Meteo updates hourly; refetching took ~0.8 s of every weather answer."""
DRY_SOIL_PERCENT = 30
# Going out needs the weather even when the question is about something else. Gemma missed
# it in "จะไปหาอะไรกินข้างนอก กินอะไรดี" 3/3 (topic: food), so code adds it.
_GOING_OUT = re.compile(r"ข้างนอก|นอกบ้าน|ออกไป|ออกจากบ้าน|ไปเที่ยว|เดินทาง|ตากผ้า|วิ่ง|ปั่นจักรยาน|เดินเล่น")

# A short prompt of its own: under the chat persona prompt ("ตอบเนื้อหาเลย") Gemma said
# "ผมขอเช็คก่อนนะครับ" and made the answer up instead of calling a tool (7/25 right, 25/25 here).
SELECT_PROMPT = ("คุณคือตัวเลือกเครื่องมือของ JARVIS ผู้ช่วยในบ้าน ถ้าคำถามต้องใช้ข้อมูลจริงเรื่องอากาศ ต้นไม้ "
                 "อุปกรณ์ หรือวันเวลา ให้เรียกเครื่องมือที่เกี่ยวข้อง เรียกได้หลายตัว "
                 "ถ้าผู้ใช้กำลังจะออกไปข้างนอก เดินทาง ตากผ้า หรือทำกิจกรรมกลางแจ้ง ให้เรียก get_weather ด้วย "
                 "ถ้าไม่ต้องใช้ข้อมูลจริงให้ตอบคำเดียวว่า ไม่ต้องใช้")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    run: Callable[..., object]
    """Called as run(ctx, **arguments); returns JSON-serializable data."""
    parameters: dict = field(default_factory=dict)

    def spec(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description,
            "parameters": {"type": "object", "properties": self.parameters}}}


def rain_words(mm: float) -> str:
    if mm < RAIN_MM:
        return "ไม่มีฝน"
    if mm < 2.5:
        return "ฝนปรอยๆ"
    if mm < 7.6:
        return "ฝนปานกลาง"
    return "ฝนหนัก"


def chance_words(percent: int) -> str:
    if percent < 30:
        return "ไม่น่าตก"
    if percent < 60:
        return "อาจจะตก"
    return "น่าจะตก"


def feel_words(temp: float, humidity: float) -> str:
    if temp >= 33:
        return "ร้อนมาก" + (" อบอ้าว" if humidity >= 60 else "")
    if temp >= 29:
        return "ร้อน อบอ้าว" if humidity >= 70 else "ค่อนข้างร้อน"
    if temp >= 24:
        return "กำลังสบาย ชื้นๆ" if humidity >= 85 else "กำลังสบาย"
    return "ค่อนข้างเย็น"


def soil_words(percent: float) -> str:
    if percent < DRY_SOIL_PERCENT * 0.66:
        return "ดินแห้งมาก ควรรดน้ำเลย"
    if percent < DRY_SOIL_PERCENT:
        return "ดินค่อนข้างแห้ง ควรรดน้ำ"
    return "ดินยังชื้นดี ยังไม่ต้องรดน้ำ"


def flood_words(total_mm: float, peak_mm: float) -> str:
    """Rough, from rain alone: Bangkok streets tend to pond from about 30 mm in an hour or
    60 mm in a day. There is no flood data; the words say so."""
    if peak_mm >= 30 or total_mm >= 60:
        return "เสี่ยงน้ำท่วมขังสูง"
    if peak_mm >= 10 or total_mm >= 20:
        return "ถนนบางจุดอาจมีน้ำขัง"
    return "ฝนไม่มากพอจะทำให้น้ำท่วม"


def thai_season(month: int, day: int) -> str:
    """Thai Meteorological Department seasons; gives the model something true to explain with."""
    if (month == 5 and day >= 15) or 6 <= month <= 9 or (month == 10 and day < 15):
        return "ฤดูฝน" + (" ช่วงที่ฝนชุกที่สุดของปี ร่องมรสุมมักพาดผ่านไทย ฝนจึงตกบ่อยและตกนาน"
                         if month in (8, 9) else " มรสุมตะวันตกเฉียงใต้พาความชื้นมา")
    if (month == 10 and day >= 15) or month in (11, 12, 1, 2):
        return "ฤดูหนาว"
    return "ฤดูร้อน"


def weather_facts(data: dict, day: str = "today") -> dict:
    """`data` is an Open-Meteo forecast response. Timing facts always look FORECAST_HOURS
    ahead across midnight: with only today's hours, Gemma knew rain "returns at 06:00
    tomorrow" but not how hard, and invented "หนักขึ้น"."""
    current, hourly = data["current"], data["hourly"]
    now = current["time"][:13]
    hours = [{"time": t[11:16], "date": t[:10], "rain_mm": r, "rain_chance": p, "temp": round(c)}
             for t, r, p, c in zip(hourly["time"], hourly["precipitation"],
                                   hourly["precipitation_probability"], hourly["temperature_2m"])
             if t[:13] >= now]
    today = hours[0]["date"]
    tomorrow = next((h["date"] for h in hours if h["date"] != today), None)

    def when(h: dict) -> str:
        return ("พรุ่งนี้ " if h["date"] != today else "") + h["time"]

    def strongest(spell: list[dict]) -> str:
        return rain_words(max(h["rain_mm"] for h in spell))

    upcoming = hours[:FORECAST_HOURS]
    now_rain = max(current["precipitation"], upcoming[0]["rain_mm"])
    facts: dict = {"source": "พยากรณ์รายชั่วโมงจาก Open-Meteo (บริการพยากรณ์อากาศออนไลน์ อัปเดตทุกชั่วโมง)",
                   "now": rain_words(now_rain),
                   "feels": feel_words(current["temperature_2m"], current["relative_humidity_2m"]),
                   "temperature_now": round(current["temperature_2m"]),
                   "humidity_percent": current["relative_humidity_2m"],
                   "season": thai_season(int(today[5:7]), int(today[8:10]))}
    later = f"{FORECAST_HOURS} ชั่วโมงข้างหน้า"
    if now_rain >= RAIN_MM:
        stop = next((i for i, h in enumerate(upcoming) if h["rain_mm"] < RAIN_MM), None)
        if stop is None:
            facts["rain_stops"] = f"ยังไม่หยุดตลอด {later}"
        else:
            facts["rain_stops"] = f"ราว {when(upcoming[stop])} หลังตกต่อเนื่องอีกราว {stop} ชั่วโมง"
            # "ตกนานมากเลยเหรอ" was answered "ตกจนถึงหกโมงเช้า" from separate stop/return facts.
            back = next((i for i in range(stop, len(upcoming)) if upcoming[i]["rain_mm"] >= RAIN_MM), None)
            facts["rain_returns"] = (f"ไม่กลับมาตกใน {later}" if back is None else
                                     f"หยุดไปราว {back - stop} ชั่วโมง แล้วกลับมาตกราว {when(upcoming[back])} "
                                     f"เป็น{strongest(upcoming[back:back + 3])}")
    else:
        start = next((i for i, h in enumerate(upcoming) if h["rain_mm"] >= RAIN_MM), None)
        facts["rain_starts"] = (f"ไม่มีฝนตลอด {later}" if start is None else
                                f"ราว {when(upcoming[start])} เป็น{strongest(upcoming[start:start + 3])}")
    total = sum(h["rain_mm"] for h in upcoming)
    # "น้ำท่วมไหม" got an umbrella tip: there was nothing about flooding to answer from.
    facts["flooding"] = (f"{flood_words(total, max(h['rain_mm'] for h in upcoming))} "
                         f"(ประเมินจากปริมาณฝน {FORECAST_HOURS} ชั่วโมงข้างหน้าเท่านั้น ไม่มีข้อมูลน้ำท่วมจริง)")
    outing = upcoming[:OUTING_HOURS + 1]
    wet = [h for h in outing if h["rain_mm"] >= RAIN_MM]
    if not wet:
        facts["while_out"] = f"ถ้าออกไปตอนนี้ {OUTING_HOURS} ชั่วโมงข้างหน้าไม่น่ามีฝน"
    elif wet[0] is outing[0]:
        dry = next((h for h in outing if h["rain_mm"] < RAIN_MM), None)
        facts["while_out"] = (f"ถ้าออกไปตอนนี้ ตอนนี้{strongest(wet)}อยู่ และ" +
                              (f"น่าจะหยุดราว {when(dry)}" if dry else f"น่าจะตกต่ออีก {OUTING_HOURS} ชั่วโมง"))
    else:
        facts["while_out"] = f"ถ้าออกไปตอนนี้ ตอนนี้ยังไม่ตก แต่ราว {when(wet[0])} น่าจะมี{strongest(wet)}"
    spans: list[list] = []  # consecutive hours with the same rain merged, nothing to misread
    for h in upcoming:
        if spans and spans[-1][2] == rain_words(h["rain_mm"]):
            spans[-1][1] = h
        else:
            spans.append([h, h, rain_words(h["rain_mm"])])
    facts["next_hours"] = [
        f"{when(a)}-{b['time'] if b['date'] == a['date'] else when(b)} {label}" if a is not b else f"{when(a)} {label}"
        for a, b, label in spans[:FORECAST_SPANS]]
    pick = [h for h in hours if h["date"] == (tomorrow if day == "tomorrow" else today)]
    if pick:
        summary: dict = {"day": "พรุ่งนี้" if day == "tomorrow" else "ที่เหลือของวันนี้",
                         "temp_range": [min(h["temp"] for h in pick), max(h["temp"] for h in pick)]}
        for label, lo, hi in (("เช้า", "06", "12"), ("บ่าย", "12", "18"), ("เย็นถึงค่ำ", "18", "24")):
            part = [h for h in pick if lo <= h["time"][:2] < hi]
            if part:
                summary[label] = f"{chance_words(max(h['rain_chance'] for h in part))} แรงสุด{strongest(part)}"
        facts["day_summary"] = summary
    return facts


_forecast: dict = {}


def _weather(ctx: Context, day: str = "today") -> dict:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={ctx.config.weather_lat}&longitude={ctx.config.weather_lon}"
        "&current=temperature_2m,relative_humidity_2m,precipitation"
        "&hourly=temperature_2m,precipitation,precipitation_probability"
        "&timezone=auto&forecast_days=2"
    )
    try:
        cached = _forecast.get(url)
        if not cached or time.monotonic() - cached[0] > FORECAST_CACHE_SECONDS:
            with urllib.request.urlopen(url, timeout=8) as response:
                cached = _forecast[url] = (time.monotonic(), json.loads(response.read()))
        return weather_facts(cached[1], day)
    except (OSError, ValueError, KeyError, IndexError):
        return {"error": "ดึงข้อมูลอากาศไม่ได้ อาจเพราะอินเทอร์เน็ตมีปัญหา"}


_MENTIONS_RAIN = re.compile(r"ฝน|ร่ม|เปียก")


def rain_reminder(ctx: Context, text: str, reply: str) -> str | None:
    """A short umbrella reminder when the user is going out, rain is likely while they are
    out, and the reply forgot it: asked "จะไปหาอะไรกินข้างนอก กินอะไรดี" with the forecast
    in hand, Gemma answered only about food 6/6."""
    if not _GOING_OUT.search(text) or _MENTIONS_RAIN.search(reply):
        return None
    facts = _weather(ctx)
    outlook = facts.get("while_out", "")
    if "error" in facts or "ไม่น่ามีฝน" in outlook:
        return None
    return outlook.replace("ถ้าออกไปตอนนี้ ", "") + " อย่าลืมพกร่มนะคะ"


def _garden(ctx: Context) -> object:
    gardens = [d for d in ctx.devices() if d.get("device_type") == "garden"]
    if not gardens:
        return {"error": "ไม่มีเซ็นเซอร์ต้นไม้เชื่อมต่ออยู่"}
    result = []
    for garden in gardens:
        state = dict(garden.get("state", {}))
        if "soil_moisture" in state:
            state["soil"] = soil_words(state["soil_moisture"])
        result.append({"source": "เซ็นเซอร์ในสวนที่บ้าน", "name": garden.get("name"),
                       "online": garden.get("online"), **state})
    return result


def _devices(ctx: Context) -> object:
    return [{"source": "อุปกรณ์ในบ้านที่เชื่อมกับ JARVIS",
             "name": "ไฟโต๊ะ" if d.get("device_id") == "desk_light" else d.get("name"),
             "type": d.get("device_type"), "online": d.get("online"), "state": d.get("state")}
            for d in ctx.devices()]


def _datetime(_ctx: Context) -> dict:
    now = datetime.now()
    return {"source": "นาฬิกาของเครื่อง", "date": f"วัน{DAYS[now.weekday()]}ที่ {now.day} {MONTHS[now.month - 1]} {now.year + 543}",
            "time": f"{now.hour} นาฬิกา {now.minute} นาที"}


TOOLS = (
    Tool("get_weather", "พยากรณ์อากาศที่บ้าน: ฝนตกตอนนี้ไหม ฝนจะหยุดหรือเริ่มกี่โมง ร้อนไหม ความชื้น "
         "ภาพรวมเช้า บ่าย เย็น", _weather,
         {"day": {"type": "string", "enum": ["today", "tomorrow"], "description": "ถามเรื่องวันนี้หรือพรุ่งนี้"}}),
    Tool("get_garden", "เซ็นเซอร์ต้นไม้: ความชื้นดิน อุณหภูมิ ต้องรดน้ำไหม", _garden),
    Tool("get_devices", "สถานะอุปกรณ์ในบ้าน เช่น ไฟเปิดหรือปิดอยู่ ออนไลน์ครบไหม", _devices),
    Tool("get_datetime", "วันที่และเวลาตอนนี้", _datetime),
)
BY_NAME = {tool.name: tool for tool in TOOLS}


@dataclass(frozen=True)
class Call:
    name: str
    arguments: dict

    def label(self) -> str:
        return self.name + "(" + ",".join(f"{k}={v}" for k, v in self.arguments.items()) + ")"


def pick(text: str, endpoint: str) -> list[Call]:
    """Which tools the LLM wants for `text`; empty when it can answer without data."""
    calls = tool_calls([{"role": "system", "content": SELECT_PROMPT}, {"role": "user", "content": text}],
                       endpoint, [tool.spec() for tool in TOOLS], max_tokens=48, timeout=30)
    picked = []
    for call in calls:
        tool = BY_NAME.get(call.get("name", ""))
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if tool:
            known = tool.parameters.keys()
            picked.append(Call(tool.name, {k: v for k, v in arguments.items() if k in known}))
    if _GOING_OUT.search(text) and not any(call.name == "get_weather" for call in picked):
        picked.append(Call("get_weather", {"day": "tomorrow"} if "พรุ่งนี้" in text else {}))
    return picked


def run(ctx: Context, calls: list[Call]) -> list[str]:
    """One line per tool for the answer prompt: "get_weather: {...}"."""
    lines = []
    for call in calls:
        try:
            result = BY_NAME[call.name].run(ctx, **call.arguments)
        except (OSError, ValueError, KeyError, TypeError) as error:  # e.g. device server down
            result = {"error": f"อ่านข้อมูลไม่ได้ ({type(error).__name__})"}
        lines.append(f"{call.name}: {json.dumps(result, ensure_ascii=False)}")
    return lines
