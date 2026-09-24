"""Text-to-speech. speak() blocks while talking and returns how long audio played."""

from __future__ import annotations

import json
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PIPER_MODEL = Path(__file__).resolve().parent.parent / ".models" / "piper" / "th_TH-tsync2-medium.onnx"


def speak(text: str, voice: str = "Kanya", f5_port: int | None = None) -> float:
    """`voice` is the macOS `say` voice; with `f5_port` the F5-TTS server is tried first."""
    print(f"JARVIS: {text}", flush=True)
    if f5_port:
        played = _speak_f5(text, f5_port)
        if played is not None:
            return played
    if platform.system() == "Windows":
        return _speak_windows(text)
    say = shutil.which("say")
    if not say:
        return 0.0
    start = time.monotonic()
    subprocess.run([say, "-v", voice, text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    return time.monotonic() - start


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


def _speak_f5(text: str, port: int) -> float | None:
    """Plays each sentence while the next one is generated; None if the server is unreachable."""
    player = _player()
    sentences = split_sentences(text)
    if not player or not sentences:
        return None
    start = time.monotonic()
    playing: subprocess.Popen | None = None
    files: list[Path] = []
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(synthesize_f5, sentences[0], port)
            for index in range(len(sentences)):
                try:
                    audio = pending.result()
                except OSError:
                    if index == 0:
                        return None  # nothing played yet: let the caller fall back to `say`
                    break
                if index + 1 < len(sentences):
                    pending = pool.submit(synthesize_f5, sentences[index + 1], port)
                handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
                handle.write(audio)
                handle.close()
                files.append(Path(handle.name))
                if playing:
                    playing.wait()
                playing = subprocess.Popen([*player, handle.name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if playing:
                playing.wait()
    finally:
        for path in files:
            path.unlink(missing_ok=True)
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
