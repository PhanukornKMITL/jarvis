"""Voice pipeline: mic → wake word → speech-to-text → intent → skill → speech."""

from __future__ import annotations

import asyncio
import platform
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

from .audio import BYTES_PER_SECOND, SPEECH_RMS, Microphone, rms_level
from .config import Config
from .dataset import Dataset
from .intent import UNCLEAR, classify
from .persona import apply_persona
from .skills import BY_INTENT, Context
from .skills.light import OFF_REPLY, ON_REPLY
from .stt import WhisperSTT, clean_transcript
from .tts import speak, synthesize_f5
from .wake import WAKE_PROMPT, WakeDetector, WhisperWake, split_wake

READY_REPLY = "พร้อมฟังค่ะ"
UNCLEAR_REPLY = "ฟังไม่ชัดค่ะ ลองพูดอีกทีนะคะ"
NOT_HEARD_REPLY = "ไม่ได้ยินคำถาม ลองเรียก Jarvis อีกครั้งนะคะ"
WARM_PHRASES = (READY_REPLY, ON_REPLY, OFF_REPLY, UNCLEAR_REPLY, NOT_HEARD_REPLY)

# Short polls while waiting for the wake word so "Jarvis" is noticed quickly.
WAKE_STEP_SECONDS = 2
OVERLAP_BYTES = 1 * BYTES_PER_SECOND
LISTEN_TIMEOUT_SECONDS = 6
# People pause after the name ("จาร์วิส … ปิดไฟ"); real recordings showed up to 1.25s.
AFTER_WAKE_GRACE_SECONDS = 1.5
LOG_PATH = Path(__file__).resolve().parent.parent / "work" / "voice_transcript.log"


def log_voice(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(line + "\n")


def answer(ctx: Context, text: str, alternatives: tuple[str, ...] = ()) -> tuple[str, str]:
    """Returns (intent, reply to speak)."""
    try:
        intent = classify(text, ctx.config.llm_endpoint, alternatives)
    except (OSError, ValueError, KeyError):
        return "error", "ติดต่อ Qwen ไม่ได้ค่ะ"
    if intent == UNCLEAR:
        return intent, UNCLEAR_REPLY
    try:
        return intent, BY_INTENT[intent].handle(ctx, text)
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError, ValueError, KeyError):
        return intent, "ติดต่อ JARVIS หรือ Qwen ไม่ได้ค่ะ"


def _transcribe(stt: WhisperSTT, pcm: bytes) -> str:
    # A whisper failure on one noisy clip must not end the voice session like a mic error does.
    try:
        return stt.transcribe(pcm)
    except RuntimeError as error:
        print(f"ข้ามช่วงเสียงนี้: {error}", file=sys.stderr, flush=True)
        return ""


class VoiceSession:
    def __init__(self, config: Config, ctx: Context, mic: Microphone, wake: WakeDetector,
                 wake_stt: WhisperSTT, command_stt: WhisperSTT) -> None:
        self.config = config
        self.ctx = ctx
        self.mic = mic
        self.wake = wake
        self.wake_stt = wake_stt
        self.command_stt = command_stt
        self.dataset = Dataset(config.dataset_dir)
        self.f5_port = config.f5_port if config.tts_engine == "f5" else None

    def render(self, text: str) -> str:
        return apply_persona(text, self.config.gender, self.config.profile.name)

    def say(self, text: str) -> None:
        self.mic.drain(speak(self.render(text), self.config.tts_voice, self.f5_port))

    def warm_up(self) -> None:
        """Pre-generates the fixed replies so they play instantly instead of after synthesis."""
        if not self.f5_port:
            return
        for text in WARM_PHRASES:
            try:
                synthesize_f5(self.render(text), self.f5_port, timeout=120)
            except OSError:
                return

    def run(self) -> None:
        overlap = b""
        idle_ticks = 0
        while True:
            window = overlap + self.mic.read(WAKE_STEP_SECONDS * BYTES_PER_SECOND)
            overlap = window[-OVERLAP_BYTES:]
            rms = rms_level(window)
            if rms < SPEECH_RMS:
                idle_ticks += 1
                if idle_ticks % 5 == 0:
                    print(f"[ระดับเสียงพื้นหลัง] rms={rms:.0f} (เกณฑ์ตอนนี้={SPEECH_RMS})", flush=True)
                continue
            idle_ticks = 0
            result = self.wake.detect(window)
            if result is None:
                continue
            if result.heard:
                log_voice(f"ได้ยิน: {result.heard} (rms={rms:.0f})")
            if result.command is None:
                continue
            log_voice("พบคำปลุก Jarvis")
            self.handle_wake(window, result.heard)
            overlap = b""
            self.wake.reset()

    def handle_wake(self, window: bytes, wake_heard: str) -> None:
        utterance, _ = self.mic.capture(window, 0)
        full_text = _transcribe(self.command_stt, utterance)
        command_text = split_wake(full_text)
        if command_text is None:
            log_voice(f"ฟังซ้ำแล้วไม่ใช่คำปลุก: {full_text}")
            return
        command_text = clean_transcript(command_text)
        # A second opinion from the wake model: small sometimes drops a short phrase after
        # the name, and on this headset base hears "ปิด" better (small turns it into "พิ").
        base_text = ""
        if self.command_stt is not self.wake_stt:
            base_text = clean_transcript(split_wake(_transcribe(self.wake_stt, utterance)) or "")
            command_text = command_text or base_text
        alternatives = (base_text,) if base_text and base_text != command_text else ()
        if not command_text:
            follow, heard = self.mic.capture(b"", AFTER_WAKE_GRACE_SECONDS)
            if not heard:
                self.say(READY_REPLY)
                follow, heard = self.mic.capture(b"", LISTEN_TIMEOUT_SECONDS)
            if heard:
                utterance += follow
                follow_text = _transcribe(self.command_stt, follow)
                after_wake = split_wake(follow_text)
                command_text = clean_transcript(follow_text if after_wake is None else after_wake)
        if not command_text:
            self.dataset.save(utterance, wake=wake_heard, transcript=full_text, command="", intent=None)
            self.say(NOT_HEARD_REPLY)
            return
        log_voice(f"คำสั่ง: {command_text}" + (f" (base: {alternatives[0]})" if alternatives else ""))
        intent, reply = answer(self.ctx, command_text, alternatives)
        log_voice(f"intent: {intent}")
        self.dataset.save(utterance, wake=wake_heard, transcript=full_text, command=command_text,
                          base=base_text, intent=intent, reply=self.render(reply))
        self.say(reply)


def listen(host: str, port: int, audio_device: str, config: Config) -> int:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        print("ต้องติดตั้ง FFmpeg ก่อน: brew install ffmpeg", file=sys.stderr)
        return 1
    if not shutil.which("whisper-cli"):
        print("ยังไม่พบ whisper-cli; รัน ./setup_local_voice.sh ก่อน", file=sys.stderr)
        return 1
    if not config.wake_model.is_file():
        print(f"ไม่พบโมเดลเสียง: {config.wake_model}; รัน ./setup_local_voice.sh ก่อน", file=sys.stderr)
        return 1
    wake_stt = WhisperSTT(config.wake_model, WAKE_PROMPT)
    command_stt = WhisperSTT(config.command_model, WAKE_PROMPT) if config.command_model.is_file() else wake_stt

    print("กำลังเปิดไมค์เพื่อฟังคำว่า Jarvis (ประมวลผลในเครื่อง; กด Ctrl+C เพื่อหยุด)", flush=True)
    mic = Microphone(ffmpeg, audio_device)
    if platform.system() == "Windows":
        print(f"ใช้ไมโครโฟน: {mic.device}; ดูชื่ออุปกรณ์ด้วยคำสั่ง ffmpeg -list_devices true -f dshow -i dummy", flush=True)
    else:
        print("ครั้งแรก macOS อาจขออนุญาตให้ Terminal ใช้ไมโครโฟน", flush=True)
    session = VoiceSession(config, Context(host, port, config), mic, WhisperWake(wake_stt), wake_stt, command_stt)
    threading.Thread(target=session.warm_up, daemon=True).start()
    try:
        session.run()
    except KeyboardInterrupt:
        print("หยุดฟังแล้ว", flush=True)
        return 0
    except (OSError, RuntimeError) as error:
        print(f"เสียงใช้งานไม่ได้: {error}", file=sys.stderr)
        print("ตรวจสิทธิ์ไมโครโฟนของ Terminal และชื่ออุปกรณ์เสียง แล้วลองใหม่", file=sys.stderr)
        return 1
    finally:
        mic.close()
    return 0
