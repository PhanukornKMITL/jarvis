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

from concurrent.futures import ThreadPoolExecutor

from . import news, rainfall
from .config import Config
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
# Flood questions always get the combined report: "น้ำท่วมไหม" alone got no tool at all.
_FLOODING = re.compile(r"ท่วม|น้ำขัง|น้ำรอการระบาย")
_GOING_OUT = re.compile(r"ข้างนอก|นอกบ้าน|ออกไป|ออกจากบ้าน|ไปเที่ยว|เดินทาง|ตากผ้า|วิ่ง|ปั่นจักรยาน|เดินเล่น")

# A short prompt of its own: under the chat persona prompt ("ตอบเนื้อหาเลย") Gemma said
# "ผมขอเช็คก่อนนะครับ" and made the answer up instead of calling a tool (7/25 right, 25/25 here).
SELECT_PROMPT = ("คุณคือตัวเลือกเครื่องมือของ JARVIS ผู้ช่วยในบ้าน ถ้าคำถามต้องใช้ข้อมูลจริงเรื่องอากาศ ต้นไม้ "
                 "อุปกรณ์ น้ำท่วม ข่าว หรือวันเวลา ให้เรียกเครื่องมือที่เกี่ยวข้อง เรียกได้หลายตัว "
                 "ถ้าผู้ใช้กำลังจะออกไปข้างนอก เดินทาง ตากผ้า หรือทำกิจกรรมกลางแจ้ง ให้เรียก get_weather ด้วย "
                 "ถ้าไม่ต้องใช้ข้อมูลจริงให้ตอบคำเดียวว่า ไม่ต้องใช้")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    run: Callable[..., object]
    """Called as run(ctx, **arguments); returns JSON-serializable data."""
    parameters: dict = field(default_factory=dict)
    available: Callable[[Config], bool] = lambda _config: True
    """Offered to the LLM only when this is true (e.g. an API key is configured)."""

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
    """Rough, from rain alone, on the Thai Meteorological Department's daily scale (35-90 mm
    heavy, 90+ very heavy) plus a downpour check. There is no flood data; the words say so.
    63 mm of steady moderate rain was called "เสี่ยงสูง" with a Bangkok-street threshold."""
    if peak_mm >= 30 or total_mm >= 90:
        return "ฝนหนักมาก เสี่ยงน้ำท่วมขังสูง"
    if peak_mm >= 10 or total_mm >= 35:
        return "ฝนหนัก ที่ลุ่มและถนนบางจุดอาจมีน้ำขัง"
    if total_mm >= 10:
        return "ฝนไม่หนัก ไม่น่าจะท่วม อาจมีน้ำขังเล็กน้อย"
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


GISTDA_FLOOD = "https://api-gateway.gistda.or.th/api/2.0/resources/features/flood/7days"
FLOOD_FEATURES = 1000
"""Flooded cells fetched per question (~2.7 MB, under a second); the count covers them all."""
HOME_BOX_DEGREES = 0.15
"""Half-width of the "near home" box around [weather] lat/lon, about 15 km."""
RAI_PER_KM2 = 625
# Thai province codes (the API's pv_idn), with the short names people say.
PROVINCES = {
    "กรุงเทพ": 10, "สมุทรปราการ": 11, "นนทบุรี": 12, "ปทุมธานี": 13, "อยุธยา": 14, "อ่างทอง": 15,
    "ลพบุรี": 16, "สิงห์บุรี": 17, "ชัยนาท": 18, "สระบุรี": 19, "ชลบุรี": 20, "ระยอง": 21, "จันทบุรี": 22,
    "ตราด": 23, "ฉะเชิงเทรา": 24, "ปราจีนบุรี": 25, "นครนายก": 26, "สระแก้ว": 27, "โคราช": 30,
    "นครราชสีมา": 30, "บุรีรัมย์": 31, "สุรินทร์": 32, "ศรีสะเกษ": 33, "อุบล": 34, "ยโสธร": 35,
    "ชัยภูมิ": 36, "อำนาจเจริญ": 37, "บึงกาฬ": 38, "หนองบัวลำภู": 39, "ขอนแก่น": 40, "อุดร": 41,
    "เลย": 42, "หนองคาย": 43, "มหาสารคาม": 44, "ร้อยเอ็ด": 45, "กาฬสินธุ์": 46, "สกลนคร": 47,
    "นครพนม": 48, "มุกดาหาร": 49, "เชียงใหม่": 50, "ลำพูน": 51, "ลำปาง": 52, "อุตรดิตถ์": 53, "แพร่": 54,
    "น่าน": 55, "พะเยา": 56, "เชียงราย": 57, "แม่ฮ่องสอน": 58, "นครสวรรค์": 60, "อุทัยธานี": 61,
    "กำแพงเพชร": 62, "ตาก": 63, "สุโขทัย": 64, "พิษณุโลก": 65, "พิจิตร": 66, "เพชรบูรณ์": 67,
    "ราชบุรี": 70, "กาญจนบุรี": 71, "สุพรรณบุรี": 72, "นครปฐม": 73, "สมุทรสาคร": 74, "สมุทรสงคราม": 75,
    "เพชรบุรี": 76, "ประจวบ": 77, "นครศรีธรรมราช": 80, "กระบี่": 81, "พังงา": 82,
    "ภูเก็ต": 83, "สุราษฎร์": 84, "ระนอง": 85, "ชุมพร": 86, "สงขลา": 90, "หาดใหญ่": 90, "สตูล": 91,
    "ตรัง": 92, "พัทลุง": 93, "ปัตตานี": 94, "ยะลา": 95, "นราธิวาส": 96,
}


# Province names that are also everyday words ("ท่วมเลยไหม", "ตากผ้า"): only with จังหวัด/จ.
_EVERYDAY_WORDS = {"เลย", "ตาก"}


def province_code(name: str) -> int | None:
    """"จังหวัดพระนครศรีอยุธยา", "อยุธยา" → 14; longest match first ("นครนายก" before "นคร")."""
    for short in sorted(PROVINCES, key=len, reverse=True):
        if short in _EVERYDAY_WORDS:
            if re.search(rf"(?:จังหวัด|จ\.)\s*{short}", name) or name.strip() == short:
                return PROVINCES[short]
        elif short in name:
            return PROVINCES[short]
    return None


def flood_summary(features: list[dict], matched: int, where: str) -> dict:
    if not matched:
        # Pluak Daeng (Rayong) had flash floods on 24-25 Sep 2026 in the news while GISTDA had no
        # flooded cell in the province for 30 days; "ไม่พบ" made JARVIS say there was no flood.
        return {"flooding": f"ดาวเทียมยังไม่พบน้ำท่วมพื้นที่กว้าง{where}ในรอบ 7 วัน ซึ่งไม่ได้แปลว่าไม่มีน้ำท่วม "
                            "น้ำท่วมฉับพลัน น้ำป่า หรือน้ำท่วมถนนที่ลดเร็ว ดาวเทียมมักจับไม่ได้"}
    km2 = sum(f["properties"].get("f_area") or 0 for f in features) / 1e6
    districts: dict[str, float] = {}
    for f in features:
        p = f["properties"]
        name = f"{p.get('ap_tn', '')} {p.get('pv_tn', '')}".strip()
        districts[name] = districts.get(name, 0) + (p.get("f_area") or 0)
    top = sorted(districts, key=districts.get, reverse=True)[:3]
    people = sum(f["properties"].get("population") or 0 for f in features)
    partial = matched > len(features)
    return {"flooding": f"ดาวเทียมพบพื้นที่น้ำท่วม{where}ในรอบ 7 วัน",
            "area": f"{'อย่างน้อย' if partial else 'ราว'} {round(km2 * RAI_PER_KM2):,} ไร่",
            "most_in": top, "people_nearby": round(people),
            "updated": max(f["properties"].get("_createdAt", "") for f in features)[:10]}


def satellite_flood(ctx: Context, code: int | None, where: str) -> dict:
    """GISTDA flooded area in province `code`, or near home when None."""
    params = {"limit": FLOOD_FEATURES}
    if code:
        params["pv_idn"] = code
    else:
        lat, lon, d = ctx.config.weather_lat, ctx.config.weather_lon, HOME_BOX_DEGREES
        params["bbox"] = f"{lon - d},{lat - d},{lon + d},{lat + d}"
    url = GISTDA_FLOOD + "?" + "&".join(f"{k}={v}" for k, v in params.items())
    # The key goes in a header: the API echoes query strings back in its "links".
    request = urllib.request.Request(url, headers={"API-Key": ctx.config.gistda_api_key})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = json.loads(response.read())
    return flood_summary(data.get("features", []), data.get("numberMatched", 0), where)


def home_province(config: Config) -> str:
    """"ระยอง" from [home] place "... จังหวัดระยอง"; empty if unknown."""
    match = re.search(r"จังหวัด\s*(\S+)", config.profile.home_place)
    return match.group(1) if match else ""


def province_name(code: int) -> str:
    return next(name for name, number in PROVINCES.items() if number == code)


def _rain_measured(ctx: Context, province: str = "") -> dict:
    where = place_in(province, ctx.config) if province else Place("")
    if province and not where.name:
        return {"error": f"ไม่รู้จักพื้นที่ {province}"}
    name = province_name(where.province) if where.province else home_province(ctx.config)
    try:
        return rainfall.measured(where.province, name, ctx.config.weather_lat, ctx.config.weather_lon, where.district)
    except (OSError, ValueError, KeyError):
        return {"error": "ดึงข้อมูลฝนจากสถานีวัดของ ThaiWater ไม่ได้"}


def _news(_ctx: Context, query: str = "") -> dict:
    try:
        found = news.headlines(query)
    except (OSError, ValueError):
        return {"error": "ดึงข่าวไม่ได้"}
    return {"source": "หัวข่าวจาก Google News ภายใน 2 วัน ให้บอกชื่อสำนักข่าวและเวลาเมื่อพูดถึง",
            "news": found or [f"ไม่พบข่าวเรื่อง {query} ใน 2 วันที่ผ่านมา"]}


@dataclass(frozen=True)
class Place:
    name: str
    """What to call it and search news for; "" = home."""
    province: int | None = None
    district: str = ""


def place_in(text: str, config: Config) -> Place:
    """The place a question is about, found in the words themselves: a small model passed
    "ปลวกแดง" (a district) as a province and got "ไม่รู้จักจังหวัด"."""
    code = province_code(text)
    if code:
        return Place(province_name(code), code)
    try:
        found = rainfall.district_in(text)
    except (OSError, ValueError, KeyError):
        found = None
    if found:
        return Place(found[0], found[1], found[0])
    return Place("")


def _flood_report(ctx: Context, place: str = "") -> dict:
    """All flood evidence for one place in one result, strongest first. Given the sources as
    separate results, Gemma repeated the first (satellite: nothing) and dropped news of the
    Pluak Daeng flash flood and 163 mm at a Rayong gauge."""
    where = place_in(place, ctx.config) if place else Place("")
    area = where.name or home_province(ctx.config)
    label = f"อ.{where.district}" if where.district else (f"จ.{where.name}" if where.name else "แถวบ้าน")
    jobs = {
        "news": lambda: [line for line in news.headlines(f"{area} น้ำท่วม") if "ท่วม" in line or "น้ำ" in line],
        "gauges": lambda: rainfall.measured(where.province, province_name(where.province) if where.province else area,
                                            ctx.config.weather_lat, ctx.config.weather_lon, where.district),
    }
    if ctx.config.gistda_api_key:
        jobs["satellite"] = lambda: satellite_flood(ctx, where.province, f"ใน{label}")
    if not where.name:
        jobs["forecast"] = lambda: _weather(ctx).get("flooding")
    with ThreadPoolExecutor(len(jobs)) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
    found = {}
    for name, future in futures.items():
        try:
            found[name] = future.result()
        except (OSError, ValueError, KeyError, TypeError):
            found[name] = None
    findings = []
    if found.get("news"):
        findings.append("ข่าว: " + " / ".join(found["news"][:2]))
    gauges = found.get("gauges") or {}
    warnings = [w for w in gauges.get("official_warnings", []) if not w.startswith("ไม่มี")]
    if warnings:
        findings.append("ประกาศเตือนจาก ThaiWater: " + " / ".join(warnings[:2]))
    if gauges.get("rain_measured"):
        findings.append("สถานีวัดฝน: " + gauges["rain_measured"])
    if found.get("satellite"):
        findings.append("ดาวเทียม GISTDA: " + " ".join(str(v) for v in found["satellite"].values()))
    if found.get("forecast"):
        findings.append("พยากรณ์ฝนข้างหน้า: " + found["forecast"])
    if not findings:
        return {"error": "ดึงข้อมูลน้ำท่วมไม่ได้เลยสักแหล่ง"}
    return {"place": label, "findings": findings,
            "how_to_answer": "สรุปจากข้อแรกๆ ก่อน ถ้ามีข่าวให้บอกชื่อสำนักข่าวและเวลา ข่าวและสถานีวัดเห็นน้ำท่วมฉับพลัน"
                             "ที่ดาวเทียมมองไม่เห็น อย่าบอกว่าไม่ท่วมเพราะดาวเทียมไม่พบ"}


TOOLS = (
    Tool("get_weather", "พยากรณ์อากาศที่บ้าน: ฝนตกตอนนี้ไหม ฝนจะหยุดหรือเริ่มกี่โมง ร้อนไหม ความชื้น "
         "ภาพรวมเช้า บ่าย เย็น", _weather,
         {"day": {"type": "string", "enum": ["today", "tomorrow"], "description": "ถามเรื่องวันนี้หรือพรุ่งนี้"}}),
    Tool("get_garden", "เซ็นเซอร์ต้นไม้: ความชื้นดิน อุณหภูมิ ต้องรดน้ำไหม", _garden),
    Tool("get_devices", "สถานะอุปกรณ์ในบ้าน เช่น ไฟเปิดหรือปิดอยู่ ออนไลน์ครบไหม", _devices),
    Tool("get_datetime", "วันที่และเวลาตอนนี้", _datetime),
    Tool("get_flood_report", "สถานการณ์น้ำท่วม: รวมข่าว ประกาศเตือน สถานีวัดฝน ดาวเทียม และพยากรณ์ "
         "แถวบ้าน หรือจังหวัด/อำเภอที่ถาม", _flood_report,
         {"place": {"type": "string", "description": "จังหวัดหรืออำเภอที่ถาม เว้นว่างถ้าถามแถวบ้าน"}}),
    Tool("get_rain_measured", "ฝนที่ตกจริงจากสถานีวัดฝนใน 24 ชั่วโมงที่ผ่านมา เมื่อวานฝนหนักแค่ไหน "
         "แถวบ้านหรือในจังหวัด/อำเภอที่ถาม", _rain_measured,
         {"province": {"type": "string", "description": "จังหวัดหรืออำเภอที่ถาม เว้นว่างถ้าถามแถวบ้าน"}}),
    Tool("get_news", "หัวข่าวล่าสุดของไทยใน 2 วัน ค้นตามคำสำคัญ หรือข่าวเด่นถ้าไม่ระบุ", _news,
         {"query": {"type": "string", "description": "คำค้น เช่น ระยอง น้ำท่วม เว้นว่างถ้าขอข่าวเด่น"}}),
)
BY_NAME = {tool.name: tool for tool in TOOLS}


@dataclass(frozen=True)
class Call:
    name: str
    arguments: dict

    def label(self) -> str:
        return self.name + "(" + ",".join(f"{k}={v}" for k, v in self.arguments.items()) + ")"


def pick(text: str, config: Config) -> list[Call]:
    """Which tools the LLM wants for `text`; empty when it can answer without data."""
    offered = [tool.spec() for tool in TOOLS if tool.available(config)]
    calls = tool_calls([{"role": "system", "content": SELECT_PROMPT}, {"role": "user", "content": text}],
                       config.llm_endpoint, offered, max_tokens=48, timeout=30)
    picked = []
    for call in calls:
        tool = BY_NAME.get(call.get("name", ""))
        tool = tool if tool and tool.available(config) else None
        try:
            arguments = json.loads(call.get("arguments") or "{}")
        except json.JSONDecodeError:
            arguments = {}
        if tool:
            known = tool.parameters.keys()
            picked.append(Call(tool.name, {k: v for k, v in arguments.items() if k in known}))
    if _FLOODING.search(text):
        # One report instead of the LLM's mix of weather, news and satellite calls.
        where = place_in(text, config)
        picked = [c for c in picked if c.name in ("get_devices", "get_garden", "get_datetime")]
        picked.append(Call("get_flood_report", {"place": where.name} if where.name else {}))
    elif _GOING_OUT.search(text) and "get_weather" not in {call.name for call in picked}:
        picked.append(Call("get_weather", {"day": "tomorrow"} if "พรุ่งนี้" in text else {}))
    return picked


def _run_one(ctx: Context, call: Call) -> str:
    try:
        result = BY_NAME[call.name].run(ctx, **call.arguments)
    except (OSError, ValueError, KeyError, TypeError) as error:  # e.g. device server down
        result = {"error": f"อ่านข้อมูลไม่ได้ ({type(error).__name__})"}
    return f"{call.name}: {json.dumps(result, ensure_ascii=False)}"


def run(ctx: Context, calls: list[Call]) -> list[str]:
    """One line per tool for the answer prompt: "get_weather: {...}". Tools run in parallel:
    a flood question asks four services."""
    with ThreadPoolExecutor(max(1, len(calls))) as pool:
        return list(pool.map(lambda call: _run_one(ctx, call), calls))
