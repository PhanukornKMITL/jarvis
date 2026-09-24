"""Text-to-speech. Speaker.speak() blocks while talking, can be stopped mid-sentence
(barge-in), and accepts sentences as they stream in from the LLM."""

from __future__ import annotations

import itertools
import json
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
from collections.abc import Iterable, Iterator
from pathlib import Path

PIPER_MODEL = Path(__file__).resolve().parent.parent / ".models" / "piper" / "th_TH-tsync2-medium.onnx"
_POLL_SECONDS = 0.05


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

    def speak(self, parts: Iterable[str], stop: threading.Event | None = None) -> float:
        """Speaks each part in order; returns seconds spent. Setting `stop` cuts it off."""
        stop = stop or threading.Event()
        parts = iter(parts)
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

        def produce() -> None:
            try:
                for text in parts:  # may pull from a live LLM stream
                    if stop.is_set():
                        return
                    try:
                        audio = synthesize_f5(text, self.f5_port)
                    except OSError:
                        failed.append(text)
                        return
                    ready.put((text, audio))
            finally:
                ready.put(None)

        producer = threading.Thread(target=produce, daemon=True)
        producer.start()
        while (item := ready.get()) is not None:
            if stop.is_set():
                continue
            text, audio = item
            print(f"JARVIS: {text}", flush=True)
            handle = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
            try:
                handle.write(audio)
                handle.close()
                _run_until_stopped([*player, handle.name], stop)
            finally:
                Path(handle.name).unlink(missing_ok=True)
        producer.join()
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
                return
            print(f"JARVIS: {text}", flush=True)
            _run_until_stopped([say, "-v", self.voice, text], stop)


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
