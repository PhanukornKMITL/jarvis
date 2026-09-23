"""Text-to-speech. speak() blocks while talking and returns how long audio played."""

from __future__ import annotations

import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PIPER_MODEL = Path(__file__).resolve().parent.parent / ".models" / "piper" / "th_TH-tsync2-medium.onnx"


def speak(text: str, voice: str = "Kanya") -> float:
    """`voice` is the macOS `say` voice; Windows picks its own engines."""
    print(f"JARVIS: {text}", flush=True)
    if platform.system() == "Windows":
        return _speak_windows(text)
    say = shutil.which("say")
    if not say:
        return 0.0
    start = time.monotonic()
    subprocess.run([say, "-v", voice, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return time.monotonic() - start


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
