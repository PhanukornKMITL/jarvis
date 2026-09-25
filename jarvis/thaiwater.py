"""Measurements from ThaiWater (National Hydroinformatics Data Center): rain gauges, river
water levels, large dams and flash-flood warnings.

Satellites miss flash floods and flooded streets (Pluak Daeng, 24-25 Sep 2026: news and
130 mm at the local gauge, no flooded cell at GISTDA), and a forecast is only a forecast;
gauges measure what came down and how close rivers are to their banks, every hour or so.
The public API needs no key; its files are several MB, so they are cached.
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
from datetime import datetime

API = "https://api-v3.thaiwater.net/api/v1/thaiwater30/public/"
CACHE_SECONDS = 900
"""Gauges report hourly or more often; dams daily."""
NEAR_KM = 20
WATER_NEAR_KM = 25
"""River gauges are sparser than rain gauges."""
STALE_HOURS = 6
_cache: dict[str, tuple[float, object]] = {}
# Where each file keeps its station list.
_LISTS = {
    "rain_24h": lambda d: d["data"],
    "warning": lambda d: d["data"],
    "waterlevel_load": lambda d: d["waterlevel_data"]["data"],
    "thailand_main": lambda d: d["dam"]["data"]["data"],  # ~10 MB, also rain maps
}


def _get(path: str, seconds: float = CACHE_SECONDS) -> list[dict]:
    cached = _cache.get(path)
    if not cached or time.monotonic() - cached[0] > seconds:
        with urllib.request.urlopen(API + path, timeout=60) as response:
            cached = _cache[path] = (time.monotonic(), _LISTS[path](json.loads(response.read())))
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


def bank_words(percent: float, below_bank_m: float) -> str:
    """ThaiWater's own bands of water level as a share of bank-full capacity."""
    if percent > 100:
        return f"ล้นตลิ่ง {abs(below_bank_m):.2f} ม."
    if percent >= 90:
        return f"ใกล้ล้นตลิ่ง ต่ำกว่าตลิ่งอีก {below_bank_m:.2f} ม."
    if percent >= 70:
        return "น้ำมาก"
    if percent >= 30:
        return "น้ำปกติ"
    return "น้ำน้อย"


def _hours_old(stamp: str) -> float:
    try:
        return (datetime.now() - datetime.strptime(stamp, "%Y-%m-%d %H:%M")).total_seconds() / 3600
    except (TypeError, ValueError):
        return math.inf


def _level(station: dict) -> str | None:
    try:
        percent = float(station["storage_percent"])
        below = float(station["diff_wl_bank"])
        now, before = float(station["waterlevel_msl"]), float(station["waterlevel_msl_previous"])
    except (KeyError, TypeError, ValueError):
        return None
    trend = "กำลังขึ้น" if now - before > 0.01 else "กำลังลด" if before - now > 0.01 else "ทรงตัว"
    stamp = station.get("waterlevel_datetime", "")
    old = _hours_old(stamp)
    when = f"ข้อมูลเก่า {round(old)} ชม." if old > STALE_HOURS else f"เวลา {stamp[11:16]}"
    return (f"สถานี{station['station']['tele_station_name']['th']} {_place(station)}: {bank_words(percent, below)} "
            f"({percent:.0f}% ของความจุลำน้ำ) {trend} {when}")


def water_levels(province_code: int | None, lat: float, lon: float, district: str = "") -> dict:
    """River gauges in the area, most urgent first: over the bank, then fullest."""
    here = []
    for s in _get("waterlevel_load"):
        g = s.get("geocode") or {}
        if district:
            inside = g.get("amphoe_name", {}).get("th") == district
        elif province_code:
            inside = g.get("province_code") == str(province_code)
        else:
            inside = _km(s, lat, lon) <= WATER_NEAR_KM
        if inside and s.get("storage_percent") is not None:
            here.append(s)
    if not here:
        return {"water_level": "ไม่มีสถานีวัดระดับน้ำในพื้นที่นี้"}
    here.sort(key=lambda s: -float(s["storage_percent"]))
    lines = [line for line in map(_level, here[:5]) if line]
    result = {"source": "สถานีวัดระดับน้ำในแม่น้ำและคลองจาก ThaiWater (สสน.)", "water_level": lines}
    if not province_code and not district:
        nearest = min(here, key=lambda s: _km(s, lat, lon))
        result["nearest"] = f"{_level(nearest)} ห่างบ้านราว {round(_km(nearest, lat, lon))} กม."
    return result


def dams(province_code: int | None) -> list[str]:
    """Large dams in the province (Royal Irrigation Department, daily)."""
    lines = []
    for d in _get("thailand_main", seconds=3600):
        if province_code and (d.get("geocode") or {}).get("province_code") != str(province_code):
            continue
        try:
            name = d["dam"]["dam_name"]["th"]
            percent = float(d["dam_storage_percent"])
        except (KeyError, TypeError, ValueError):
            continue
        spill = f" ระบายน้ำล้น {d['dam_spilled']} ล้าน ลบ.ม." if d.get("dam_spilled") else ""
        lines.append(f"เขื่อน/อ่าง{name}: น้ำ {percent:.0f}% ของความจุ รับน้ำวันนี้ {d.get('dam_inflow')} ล้าน ลบ.ม. "
                     f"ปล่อย {d.get('dam_released')} ล้าน ลบ.ม.{spill} ({d.get('dam_date')})")
    return lines
