"""Text-to-speech. Speaker.speak() blocks while talking, can be stopped mid-sentence
(barge-in), and accepts sentences as they stream in from the LLM."""

from __future__ import annotations

import array
import io
import itertools
import json
import math
import platform
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import wave
from collections.abc import Iterable, Iterator
from pathlib import Path

PIPER_MODEL = Path(__file__).resolve().parent.parent / ".models" / "piper" / "th_TH-tsync2-medium.onnx"
_POLL_SECONDS = 0.05
ENVELOPE_SECONDS = 0.1
# Level assumed while `say` plays (its audio isn't available to measure).
SAY_LEVEL = 3000.0


def wav_envelope(audio: bytes) -> list[float]:
    """RMS of a 16-bit WAV in ENVELOPE_SECONDS steps (same scale as the mic's rms_level)."""
    with wave.open(io.BytesIO(audio)) as wav_file:
        step = int(wav_file.getframerate() * ENVELOPE_SECONDS) * wav_file.getnchannels()
        samples = array.array("h", wav_file.readframes(wav_file.getnframes()))
    return [math.sqrt(sum(x * x for x in samples[i:i + step]) / max(len(samples[i:i + step]), 1))
            for i in range(0, len(samples), step)]


_DIGITS = ("ศูนย์", "หนึ่ง", "สอง", "สาม", "สี่", "ห้า", "หก", "เจ็ด", "แปด", "เก้า")
_PLACES = ("", "สิบ", "ร้อย", "พัน", "หมื่น", "แสน")
_NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?")


def thai_integer(n: int) -> str:
    if n == 0:
        return "ศูนย์"
    if n >= 1_000_000:
        rest = n % 1_000_000
        return thai_integer(n // 1_000_000) + "ล้าน" + (thai_integer(rest) if rest else "")
    digits = str(n)
    words = ""
    for index, char in enumerate(digits):
        digit, place = int(char), len(digits) - index - 1
        if digit == 0:
            continue
        if place == 1:
            words += {1: "", 2: "ยี่"}.get(digit, _DIGITS[digit]) + "สิบ"
        elif place == 0 and digit == 1 and len(digits) > 1:
            words += "เอ็ด"
        else:
            words += _DIGITS[digit] + _PLACES[place]
    return words


# Dotted Thai abbreviations F5-TTS reads letter by letter. Longest first so "ม.ค." wins over "ม.".
_ABBREVIATIONS = sorted({
    "พ.ศ.": "พุทธศักราช", "ค.ศ.": "คริสต์ศักราช",
    "ม.ค.": "มกราคม", "ก.พ.": "กุมภาพันธ์", "มี.ค.": "มีนาคม", "เม.ย.": "เมษายน",
    "พ.ค.": "พฤษภาคม", "มิ.ย.": "มิถุนายน", "ก.ค.": "กรกฎาคม", "ส.ค.": "สิงหาคม",
    "ก.ย.": "กันยายน", "ต.ค.": "ตุลาคม", "พ.ย.": "พฤศจิกายน", "ธ.ค.": "ธันวาคม",
    "ก.ม.": "กิโลเมตร", "กม.": "กิโลเมตร", "ซม.": "เซนติเมตร", "มม.": "มิลลิเมตร",
    "กก.": "กิโลกรัม", "ชม.": "ชั่วโมง", "นศ.": "นักศึกษา", "รพ.": "โรงพยาบาล",
    "ร.พ.": "โรงพยาบาล", "ร.ร.": "โรงเรียน", "จ.": "จังหวัด", "อ.": "อำเภอ", "ต.": "ตำบล",
    "ถ.": "ถนน", "ดร.": "ด็อกเตอร์", "น.": "นาฬิกา", "ฯลฯ": "และอื่นๆ",
}.items(), key=lambda item: -len(item[0]))
# Only at the start of a word: otherwise "ผลิต." (a sentence-final full stop) became "ผลิ ตำบล".
_ABBREVIATION_PATTERNS = [(re.compile(r"(?<![ก-๙])" + re.escape(short)), full) for short, full in _ABBREVIATIONS]
# "21.10 น." / "08:30 น." is a time, not the decimal 21.10.
_CLOCK = re.compile(r"(\d{1,2})[.:](\d{2})(?:\s*น\.)?(?=\s|$|[^\d])")


def expand_abbreviations(text: str) -> str:
    def clock(match: re.Match) -> str:
        hour, minute = int(match.group(1)), int(match.group(2))
        if hour > 24 or minute > 59 or not (match.group(0).endswith("น.") or ":" in match.group(0)):
            return match.group(0)
        return f"{hour} นาฬิกา" + (f" {minute} นาที" if minute else "")

    text = _CLOCK.sub(clock, text)
    for pattern, full in _ABBREVIATION_PATTERNS:
        text = pattern.sub(f" {full} ", text)
    # F5's reader takes "รร" as the vowel of กรรม, so "ควรรดน้ำ" came out as "ควร.อดน้ำ".
    # Nothing is spelled "ควรร…" inside one word, so a gap after ควร is always right.
    text = re.sub(r"ควร(?=ร)", "ควร ", text)
    return text.replace("ฯ", "")  # ไปยาลน้อย (กรุงเทพฯ) is silent


def spell_numbers(text: str) -> str:
    """Writes numbers as Thai words for TTS: F5-TTS Thai read "31.5" as "สามร้อยสิบห้า"."""
    def spell(match: re.Match) -> str:
        whole, _, fraction = match.group().replace(",", "").partition(".")
        words = thai_integer(int(whole))
        if fraction:
            words += "จุด" + "".join(_DIGITS[int(d)] for d in fraction)
        return f" {words} "

    text = _NUMBER.sub(spell, text).replace("%", " เปอร์เซ็นต์")
    return " ".join(text.split())


def split_sentences(text: str, min_chars: int = 20) -> list[str]:
    """Thai has no full stops, so split after ครับ/ค่ะ/นะคะ and punctuation; merge tiny pieces."""
    pieces = [p for p in re.split(r"(?<=[.!?])\s+|(?<=ครับ)\s+|(?<=ค่ะ)\s+|(?<=คะ)\s+", text.strip()) if p]
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < min_chars:
            merged[-1] = f"{merged[-1]} {piece}"
        else:
            merged.append(piece)
    return merged


def synthesize_f5(text: str, port: int, timeout: float = 60) -> bytes:
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/synthesize",
        data=json.dumps({"text": text}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _player() -> list[str] | None:
    if shutil.which("afplay"):
        return ["afplay"]
    ffplay = shutil.which("ffplay")
    return [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet"] if ffplay else None


def _run_until_stopped(command: list[str], stop: threading.Event) -> None:
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    while process.poll() is None:
        if stop.wait(_POLL_SECONDS):
            process.terminate()
            process.wait()
            return


class Speaker:
    """`voice` is the macOS `say` voice; with `f5_port` the F5-TTS server is tried first."""

    def __init__(self, voice: str = "Kanya", f5_port: int | None = None) -> None:
        self.voice = voice
        self.f5_port = f5_port
        self.unspoken: list[str] = []
        """After a stopped speak(): the cut-off part and the parts already pulled but not played,
        in order. Parts still inside the caller's iterator were never taken."""
        self._playing: tuple[float, list[float] | None] | None = None

    def playback_level(self, now: float, before: float = 0.2, after: float = 0.1) -> float:
        """How loud JARVIS is playing around `now` (0 when silent). A window, because the mic
        hears the speaker slightly late and syllable timing jitters."""
        playing = self._playing
        if playing is None:
            return 0.0
        start, envelope = playing
        if envelope is None:
            return SAY_LEVEL
        first = max(0, int((now - start - before) / ENVELOPE_SECONDS))
        last = int((now - start + after) / ENVELOPE_SECONDS) + 1
        window = envelope[first:last]
        return max(window) if window else 0.0

    def speak(self, parts: Iterable[str], stop: threading.Event | None = None) -> float:
        """Speaks each part in order; returns seconds spent. Setting `stop` cuts it off."""
        stop = stop or threading.Event()
        parts = (spell_numbers(expand_abbreviations(part)) for part in parts)
        self.unspoken = []
        start = time.monotonic()
        if self.f5_port and _player():
            unspoken = self._speak_f5(parts, stop)
            if unspoken is None:
                return time.monotonic() - start
            parts = itertools.chain(unspoken, parts)  # F5 went down: finish with `say`
        self._speak_basic(parts, stop)
        return time.monotonic() - start

    def _speak_f5(self, parts: Iterator[str], stop: threading.Event) -> list[str] | None:
        """Synthesizes the next part while the current one plays. Returns the parts that
        could not be synthesized (for the fallback), or None when everything was handled."""
        player = _player()
        assert player is not None and self.f5_port is not None
        ready: queue.Queue[tuple[str, bytes] | None] = queue.Queue(maxsize=2)
        failed: list[str] = []

        pulled_after_stop: list[str] = []

        def produce() -> None:
            try:
                for text in parts:  # may pull from a live LLM stream
                    if stop.is_set():
                        pulled_after_stop.append(text)
                        return
                    try:
                        audio = synthesize_f5(text, self.f5_port)
                    except OSError:
                        failed.append(text)
                        return
                    if stop.is_set():
                        pulled_after_stop.append(text)
                        return
                    ready.put((text, audio))
            finally:
                ready.put(None)

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        while (item := ready.get()) is not None:
            text, audio = item
            if stop.is_set():
                self.unspoken.append(text)
                continue
            print(f"JARVIS: {text}", flush=True)
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            try:
                handle.write(audio)
                handle.close()
                self._playing = (time.monotonic(), wav_envelope(audio))
                _run_until_stopped([*player, handle.name], stop)
            finally:
                self._playing = None
                Path(handle.name).unlink(missing_ok=True)
            if stop.is_set():
                self.unspoken.append(text)  # cut off mid-sentence: resume from its start
        producer.join()
        self.unspoken += pulled_after_stop
        return failed or None

    def _speak_basic(self, parts: Iterable[str], stop: threading.Event) -> None:
        if platform.system() == "Windows":
            text = " ".join(parts)
            if text:
                print(f"JARVIS: {text}", flush=True)
                _speak_windows(text)
            return
        say = shutil.which("say")
        for text in parts:
            if stop.is_set() or not say:
                self.unspoken.append(text)
                return
            print(f"JARVIS: {text}", flush=True)
            self._playing = (time.monotonic(), None)
            try:
                _run_until_stopped([say, "-v", self.voice, text], stop)
            finally:
                self._playing = None
            if stop.is_set():
                self.unspoken.append(text)
                return


def speak(text: str, voice: str = "Kanya", f5_port: int | None = None) -> float:
    return Speaker(voice, f5_port).speak(split_sentences(text) or [text])


def _has_latin_text(text: str) -> bool:
    return re.search(r"[A-Za-z]{2,}", text) is not None


def _speak_windows(text: str) -> float:
    # Piper's Thai voice glitches on English words, so mixed text goes to edge-tts.
    synths = (_speak_edge_tts,) if _has_latin_text(text) else (_speak_piper, _speak_edge_tts)
    for synth in synths:
        played = synth(text)
        if played is not None:
            return played
    # Last resort: that machine's SAPI voices are English-only, so Thai may be garbled.
    ps = shutil.which("powershell") or shutil.which("pwsh")
    if not ps:
        return 0.0
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Speak($args[0])"
    )
    start = time.monotonic()
    subprocess.run([ps, "-NoProfile", "-Command", script, text],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return time.monotonic() - start


def _play(ffplay: str, audio_path: str) -> float:
    start = time.monotonic()
    subprocess.run(
        [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
    )
    return time.monotonic() - start


def _speak_piper(text: str) -> float | None:
    """Offline Thai TTS via Piper; None if Piper, its model, or ffplay is unavailable."""
    ffplay = shutil.which("ffplay")
    if not ffplay or not PIPER_MODEL.is_file():
        return None
    audio_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    audio_path = audio_file.name
    audio_file.close()
    try:
        result = subprocess.run(
            [sys.executable, "-m", "piper", "-m", str(PIPER_MODEL), "-f", audio_path],
            input=text, capture_output=True, text=True, encoding="utf-8",
            timeout=20, check=False,
        )
        if result.returncode != 0 or Path(audio_path).stat().st_size == 0:
            return None
        return _play(ffplay, audio_path)
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        Path(audio_path).unlink(missing_ok=True)


def _speak_edge_tts(text: str, voice: str = "th-TH-NiwatNeural") -> float | None:
    """Cloud Thai TTS via edge-tts; returns playback time only, excluding the network call."""
    edge_tts = shutil.which("edge-tts")
    ffplay = shutil.which("ffplay")
    if not edge_tts or not ffplay:
        return None
    audio_file = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    audio_path = audio_file.name
    audio_file.close()
    try:
        result = subprocess.run(
            [edge_tts, "--voice", voice, "--text", text, "--write-media", audio_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=20, check=False,
        )
        if result.returncode != 0 or Path(audio_path).stat().st_size == 0:
            return None
        return _play(ffplay, audio_path)
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        Path(audio_path).unlink(missing_ok=True)
