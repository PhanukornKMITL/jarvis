from __future__ import annotations

import asyncio

from .base import Context, Skill
from .garden import garden_detail


def summarize_status(devices: list[dict]) -> str:
    if not devices:
        return "ตอนนี้ยังไม่มีอุปกรณ์เชื่อมต่ออยู่"
    summaries: list[str] = []
    for device in devices:
        state = device.get("state", {})
        if device.get("device_type") == "garden":
            summaries.append(f"เซ็นเซอร์ต้นไม้ {garden_detail(state)}")
            continue
        device_name = "ไฟโต๊ะ" if device.get("device_id") == "desk_light" else device.get("name", "อุปกรณ์")
        online = "ออนไลน์" if device.get("online") else "ออฟไลน์"
        power = state.get("power")
        if power == "on":
            detail = "เปิดอยู่"
        elif power == "off":
            detail = "ปิดอยู่"
        else:
            detail = ", ".join(f"{key} {value}" for key, value in state.items()) or "ยังไม่มีข้อมูลสถานะ"
        summaries.append(f"{device_name} {online} {detail}")
    return "ค่ะ ".join(summaries)


def _answer(ctx: Context, _text: str) -> str:
    try:
        return summarize_status(ctx.devices())
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
        return "ติดต่อ JARVIS server ไม่ได้"


SKILLS = (Skill(intent="status", description="ถามสถานะไฟหรืออุปกรณ์", handle=_answer),)
