from __future__ import annotations

from .base import Context, Skill

DRY_SOIL_PERCENT = 30


def garden_detail(state: dict) -> str:
    parts: list[str] = []
    moisture = state.get("soil_moisture")
    if moisture is not None:
        advice = "ดินค่อนข้างแห้ง ควรรดน้ำนะคะ" if moisture < DRY_SOIL_PERCENT else "ดินยังชื้นดีอยู่ค่ะ"
        parts.append(f"ความชื้นในดิน {moisture} เปอร์เซ็นต์ {advice}")
    if state.get("temperature") is not None:
        parts.append(f"อุณหภูมิ {state['temperature']} องศา")
    return " ".join(parts) or "ยังไม่มีข้อมูลจากเซ็นเซอร์"


def _answer(ctx: Context, _text: str) -> str:
    gardens = [d for d in ctx.devices() if d.get("device_type") == "garden"]
    if not gardens:
        return "ยังไม่มีเซ็นเซอร์ต้นไม้เชื่อมต่ออยู่ค่ะ"
    return " ".join(f"ต้นไม้ {garden_detail(d.get('state', {}))}" for d in gardens)


SKILLS = (Skill(intent="garden", description="ถามเรื่องต้นไม้ สวน ความชื้นดิน รดน้ำ", handle=_answer),)
