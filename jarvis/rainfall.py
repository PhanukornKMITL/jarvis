"""Rain that actually fell, from ThaiWater (National Hydroinformatics Data Center) gauges.

Satellites miss flash floods and flooded streets (Pluak Daeng, 24-25 Sep 2026: news and
130 mm at the local gauge, no flooded cell at GISTDA), and a forecast is only a forecast;
gauges measure what came down, hourly, with official flash-flood warnings alongside.
The public API needs no key; its 24 h rainfall file is ~4.5 MB, so it is cached.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request

API = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/"
CACHE_SECONDS = 900
"""Gauges report hourly."""
NEAR_KM = 20
_cache: dict[str, tuple[float, object]] = {}


def _get(path: str) -> list[dict]:
    cached = _cache.get(path)
    if not cached or time.monotonic() - cached[0] > CACHE_SECONDS:
        with urllib.request.urlopen(API + path, timeout=30) as response:
            cached = _cache[path] = (time.monotonic(), json.loads(response.read())["data"])
    return cached[1]


def rain_words(mm_24h: float) -> str:
    """Thai Meteorological Department scale for 24 hours."""
    if mm_24h < 0.1:
        return "ไม่มีฝน"
    if mm_24h <= 10:
        return "ฝนเล็กน้อย"
    if mm_24h <= 35:
        return "ฝนปานกลาง"
    if mm_24h <= 90:
        return "ฝนหนัก"
    return "ฝนหนักมาก"


def _km(station: dict, lat: float, lon: float) -> float:
    s = station["station"]
    try:
        return math.hypot((float(s["tele_station_lat"]) - lat) * 111, (float(s["tele_station_long"]) - lon) * 109)
    except (KeyError, TypeError, ValueError):
        return math.inf


def _place(station: dict) -> str:
    g = station["geocode"]
    return f"ต.{g['tumbon_name']['th']} อ.{g['amphoe_name']['th']} จ.{g['province_name']['th']}"


def _reading(station: dict) -> str:
    return (f"{_place(station)} {rain_words(station['rain_24h'])} ({station['rain_24h']} มม. ใน 24 ชม. "
            f"ถึง {station['rainfall_datetime'][11:16]})")


def districts(province_code: int | None = None) -> dict[str, int]:
    """District name → province code, from the gauges' own place names."""
    return {s["geocode"]["amphoe_name"]["th"]: int(s["geocode"]["province_code"]) for s in _get("rain_24h")
            if s["geocode"].get("amphoe_name", {}).get("th") and s["geocode"].get("province_code")
            and (province_code is None or s["geocode"]["province_code"] == str(province_code))}


def _inside(station: dict, province_code: int | None, lat: float, lon: float, district: str) -> bool:
    g = station["geocode"]
    if district:
        return g.get("amphoe_name", {}).get("th") == district
    if province_code:
        return g.get("province_code") == str(province_code)
    return _km(station, lat, lon) <= NEAR_KM


def heavy_spots(province_code: int | None, lat: float, lon: float, district: str = "") -> list[tuple[str, str, float]]:
    """(tambon, district, mm) of gauges over 35 mm in 24 h, wettest first: "<อำเภอ>ท่วมบริเวณไหน"
    got "ไม่ได้ระบุบริเวณ" while three gauges in that district had 54-90 mm."""
    spots = [(s["geocode"]["tumbon_name"]["th"], s["geocode"]["amphoe_name"]["th"], s["rain_24h"])
             for s in _get("rain_24h") if isinstance(s.get("rain_24h"), (int, float)) and s["rain_24h"] > 35
             and _inside(s, province_code, lat, lon, district)]
    return sorted(spots, key=lambda spot: -spot[2])


def district_in(text: str) -> tuple[str, int] | None:
    """(district, province code) of a district named in `text`, from the gauges' own place
    names ("ปลวกแดงน้ำท่วมไหม" → ("ปลวกแดง", 21)); longest name first."""
    names = districts()
    for name in sorted(names, key=len, reverse=True):
        if len(name) >= 3 and name in text:
            return name, names[name]
    return None


def measured(province_code: int | None, province_name: str, lat: float, lon: float, district: str = "") -> dict:
    stations = [s for s in _get("rain_24h") if isinstance(s.get("rain_24h"), (int, float))]
    here = [s for s in stations if _inside(s, province_code, lat, lon, district)]
    where = f"อ.{district}" if district else f"จ.{province_name}" if province_code else f"รอบบ้าน {NEAR_KM} กม."
    if not here:
        return {"rain_measured": f"ไม่มีสถานีวัดฝนใน{where}"}
    wettest = sorted(here, key=lambda s: -s["rain_24h"])[:3]
    result = {"source": "สถานีวัดฝนจริงจาก ThaiWater (สสน.) อัปเดตทุกชั่วโมง",
              "rain_measured": f"ฝนที่ตกจริงใน{where} 24 ชม. ที่ผ่านมา หนักสุด: " + "; ".join(map(_reading, wettest))}
    if not province_code and not district:
        nearest = min(here, key=lambda s: _km(s, lat, lon))
        result["nearest_gauge"] = f"{_reading(nearest)} ห่างบ้านราว {round(_km(nearest, lat, lon))} กม."
    area = province_name or ""
    # One message can hold several warnings separated by blank lines.
    warnings = [part.strip() for w in _get("warning") for part in w["message"].split("\n\n")
                if area and f"จ.{area}" in part]
    result["official_warnings"] = warnings[:3] or [f"ไม่มีประกาศเตือนจาก ThaiWater ที่ระบุ จ.{area}" if area
                                                   else "ไม่มีประกาศเตือน"]
    return result
