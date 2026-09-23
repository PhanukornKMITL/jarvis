"""Local-only wake-word and short voice status commands using whisper.cpp."""

from __future__ import annotations

import array
import asyncio
import math
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

from .cli import request

SAMPLE_RATE = 16_000
STEP_SECONDS = 2
OVERLAP_SECONDS = 1
WAKE_TIMEOUT_SECONDS = 10
OVERLAP_BYTES = SAMPLE_RATE * OVERLAP_SECONDS * 2
WAKE_WORDS = ("jarvis", "จาร์วิส", "จาวิส", "จาวีส")


def normalize(text: str) -> str:
    return "".join(char for char in text.lower() if char.isalnum() or "\u0e00" <= char <= "\u0e7f")


def wake_suffix(text: str) -> str | None:
    normalized = normalize(text)
    matches = [(normalized.find(normalize(word)), normalize(word)) for word in WAKE_WORDS]
    matches = [(index, word) for index, word in matches if index >= 0]
    if not matches:
        return None
    index, word = min(matches, key=lambda item: item[0])
    return normalized[index + len(word) :]


def has_status_intent(text: str) -> bool:
    words = ("สถานะ", "เช็ค", "ตรวจ", "อุปกรณ์", "ออนไลน์", "ไฟ", "อุณหภูมิ", "status", "device", "online", "light", "temperature")
    return any(word in text for word in words)


def speak(text: str) -> None:
    print(f"JARVIS: {text}", flush=True)
    say = shutil.which("say")
    if say:
        subprocess.run([say, "-v", "Kanya", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def rms_level(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def transcribe(whisper: str, model: str, pcm: bytes) -> str:
    with tempfile.NamedTemporaryFile(suffix=".wav") as audio_file:
        with wave.open(audio_file.name, "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm)
        result = subprocess.run(
            [whisper, "-m", model, "-f", audio_file.name, "-l", "th", "-nt", "-np"],
            capture_output=True,
            text=True,
            timeout=25,
            check=False,
        )
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "Whisper could not transcribe audio")
    return " ".join((result.stdout + " " + result.stderr).split())


def summarize_status(devices: list[dict]) -> str:
    if not devices:
        return "ตอนนี้ยังไม่มีอุปกรณ์เชื่อมต่ออยู่"
    summaries: list[str] = []
    for device in devices:
        device_name = "ไฟโต๊ะ" if device.get("device_id") == "desk_light" else device.get("name", "อุปกรณ์")
        online = "ออนไลน์" if device.get("online") else "ออฟไลน์"
        state = device.get("state", {})
        power = state.get("power")
        if power == "on":
            detail = "เปิดอยู่"
        elif power == "off":
            detail = "ปิดอยู่"
        else:
            detail = ", ".join(f"{key} {value}" for key, value in state.items()) or "ยังไม่มีข้อมูลสถานะ"
        summaries.append(f"{device_name} {online} {detail}")
    return "ค่ะ ".join(summaries)


def answer_status(host: str, port: int) -> None:
    try:
        response = asyncio.run(request(host, port, {"type": "status"}))
        speak(summarize_status(response.get("devices", [])))
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
        speak("ติดต่อ JARVIS server ไม่ได้")


def listen(host: str, port: int, model: str, audio_device: str) -> int:
    ffmpeg = shutil.which("ffmpeg")
    whisper = shutil.which("whisper-cli")
    if not ffmpeg:
        print("ต้องติดตั้ง FFmpeg ก่อน: brew install ffmpeg", file=sys.stderr)
        return 1
    if not whisper:
        print("ยังไม่พบ whisper-cli; รัน ./setup_local_voice.sh ก่อน", file=sys.stderr)
        return 1
    if not Path(model).is_file():
        print(f"ไม่พบโมเดลเสียง: {model}; รัน ./setup_local_voice.sh ก่อน", file=sys.stderr)
        return 1

    bytes_per_step = SAMPLE_RATE * STEP_SECONDS * 2
    overlap = b""
    print("กำลังเปิดไมค์เพื่อฟังคำว่า Jarvis (ประมวลผลในเครื่อง; กด Ctrl+C เพื่อหยุด)", flush=True)
    print("ครั้งแรก macOS อาจขออนุญาตให้ Terminal ใช้ไมโครโฟน", flush=True)
    command = [
        ffmpeg,
        "-hide_banner", "-loglevel", "error",
        "-f", "avfoundation", "-i", audio_device,
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        "-f", "s16le", "-",
    ]
    recorder = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    state = "wake"
    deadline = 0.0
    previous_text = ""
    try:
        assert recorder.stdout is not None
        while True:
            pcm = recorder.stdout.read(bytes_per_step)
            if len(pcm) != bytes_per_step:
                error = recorder.stderr.read().decode(errors="replace").strip() if recorder.stderr else ""
                raise RuntimeError(error or "ไมโครโฟนหยุดทำงาน")
            combined = overlap + pcm
            overlap = combined[-OVERLAP_BYTES:]
            if rms_level(combined) < 420:
                if state == "command" and time.monotonic() > deadline:
                    speak("ไม่ได้ยินคำถาม ลองเรียก Jarvis อีกครั้งนะคะ")
                    state = "wake"
                continue
            text = transcribe(whisper, model, combined)
            compact = normalize(text)
            if not compact or compact == previous_text:
                continue
            previous_text = compact
            tail = wake_suffix(text)
            if state == "wake" and tail is not None:
                if has_status_intent(tail):
                    answer_status(host, port)
                    continue
                state = "command"
                deadline = time.monotonic() + WAKE_TIMEOUT_SECONDS
                speak("พร้อมฟังค่ะ")
                continue
            if state == "command":
                deadline = time.monotonic() + WAKE_TIMEOUT_SECONDS
                if has_status_intent(text):
                    answer_status(host, port)
                    state = "wake"
    except KeyboardInterrupt:
        print("หยุดฟังแล้ว", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"เสียงใช้งานไม่ได้: {error}", file=sys.stderr)
        print("ตรวจสิทธิ์ไมโครโฟนของ Terminal และชื่ออุปกรณ์เสียง แล้วลองใหม่", file=sys.stderr)
        return 1
    finally:
        recorder.terminate()
        try:
            recorder.wait(timeout=2)
        except subprocess.TimeoutExpired:
            recorder.kill()
    return 0
