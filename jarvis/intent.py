"""Turn a transcript into a small, safe JARVIS intent via llama.cpp."""

from __future__ import annotations

import json
import urllib.request


def classify(transcript: str, endpoint: str = "http://127.0.0.1:8080") -> dict:
    prompt = (
        "You classify Thai smart-home commands. Return JSON only, no markdown. "
        "Allowed intents: status, light_on, light_off, unknown. "
        f"Transcript: {transcript}\nJSON:"
    )
    body = json.dumps({
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
        "max_tokens": 32,
    }).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        result = json.loads(response.read())
    content = result["choices"][0]["message"]["content"]
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < start:
        return {"intent": "unknown"}
    parsed = json.loads(content[start : end + 1])
    if parsed.get("intent") not in {"status", "light_on", "light_off", "unknown"}:
        return {"intent": "unknown"}
    return parsed


def chat(transcript: str, endpoint: str = "http://127.0.0.1:8080") -> str:
    """Free-form fallback for anything that isn't a device command: same
    local Qwen model as classify(), no JSON constraint, so it can actually
    answer questions or give advice instead of a canned "unknown" reply."""
    body = json.dumps({
        "messages": [
            {
                "role": "system",
                "content": (
                    "คุณคือ JARVIS ผู้ช่วยเสียงพูดภาษาไทย ตอบสั้น กระชับ เป็นกันเอง "
                    "ไม่เกิน 2-3 ประโยค เพราะคำตอบจะถูกพูดออกลำโพง "
                    "ข้อความที่ได้รับมาจากการแปลงเสียงพูดเป็นตัวอักษรด้วยโปรแกรมที่ไม่แม่นยำ "
                    "อาจมีคำผิดหรือฟังไม่ครบ ถ้าข้อความดูไม่สมเหตุสมผลหรือไม่แน่ใจว่าหมายถึงอะไร "
                    "ให้ถามกลับสั้นๆ เพื่อความชัดเจน อย่าเดาหรือแต่งเรื่องขึ้นมาตอบ"
                ),
            },
            {"role": "user", "content": transcript},
        ],
        "temperature": 0.7,
        "max_tokens": 120,
    }).encode()
    request = urllib.request.Request(
        endpoint.rstrip("/") + "/v1/chat/completions",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        result = json.loads(response.read())
    return result["choices"][0]["message"]["content"].strip()
