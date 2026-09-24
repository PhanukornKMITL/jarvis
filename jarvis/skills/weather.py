from __future__ import annotations

import json
import urllib.request

from .base import Context, Skill

WEATHER_CODES = (
    ((0,), "ท้องฟ้าแจ่มใส"),
    ((1, 2, 3), "มีเมฆบางส่วน"),
    ((45, 48), "มีหมอก"),
    (range(51, 68), "มีฝนตก"),
    (range(80, 83), "มีฝนตกเป็นช่วงๆ"),
    (range(95, 100), "มีพายุฝนฟ้าคะนอง"),
)


def _answer(ctx: Context, _text: str) -> str:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={ctx.config.weather_lat}&longitude={ctx.config.weather_lon}"
        "&current=temperature_2m,relative_humidity_2m,weather_code"
        "&daily=precipitation_probability_max&timezone=auto&forecast_days=1"
    )
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            data = json.loads(response.read())
        current = data["current"]
        rain = data["daily"]["precipitation_probability_max"][0]
    except (OSError, ValueError, KeyError, IndexError):
        return "ดึงข้อมูลสภาพอากาศไม่ได้ค่ะ ตรวจอินเทอร์เน็ตด้วยนะคะ"
    sky = next((label for codes, label in WEATHER_CODES if current["weather_code"] in codes), "")
    text = f"ตอนนี้{sky} อุณหภูมิ {round(current['temperature_2m'])} องศา ความชื้น {current['relative_humidity_2m']} เปอร์เซ็นต์"
    if rain is not None:
        text += f" โอกาสฝนตกวันนี้ {rain} เปอร์เซ็นต์"
    return text + " ค่ะ"


SKILLS = (Skill(intent="weather", description="ถามสภาพอากาศ ฝน อุณหภูมิข้างนอก", handle=_answer, slow=True),)
