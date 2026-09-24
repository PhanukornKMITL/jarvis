"""Speech-to-text: 16 kHz mono 16-bit PCM in, text out."""

from __future__ import annotations

import io
import json
import re
import shutil
import subprocess
import tempfile
import urllib.request
import uuid
import wave
from pathlib import Path

SAMPLE_RATE = 16_000


def _wav(pcm: bytes) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(SAMPLE_RATE)
        wav_file.writeframes(pcm)
    return buffer.getvalue()


class WhisperSTT:
    """whisper.cpp. With `port`, uses a whisper-server that keeps the model loaded (0.25s →
    0.10s for base, 0.94s → 0.67s for Thonburian); falls back to launching whisper-cli.
    Raises RuntimeError when whisper fails on a clip."""

    def __init__(self, model: Path, prompt: str | None = None, port: int | None = None) -> None:
        self.model = Path(model)
        self.prompt = prompt
        self.port = port
        self.binary = shutil.which("whisper-cli")

    def transcribe(self, pcm: bytes) -> str:
        if self.port:
            try:
                return self._transcribe_server(pcm)
            except OSError:
                pass  # server down or restarting: the CLI still works, just slower
        return self._transcribe_cli(pcm)

    def _transcribe_server(self, pcm: bytes) -> str:
        boundary = uuid.uuid4().hex
        fields = {"response_format": "json", "temperature": "0.0"}
        body = b"".join(
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            for name, value in fields.items()
        )
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="clip.wav"\r\n'
                 "Content-Type: audio/wav\r\n\r\n").encode() + _wav(pcm) + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/inference", data=body,
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        with urllib.request.urlopen(request, timeout=25) as response:
            result = json.loads(response.read())
        if "error" in result:
            raise RuntimeError(str(result["error"]))
        return " ".join(str(result.get("text", "")).split())

    def _transcribe_cli(self, pcm: bytes) -> str:
        if not self.binary:
            raise RuntimeError("whisper-cli not found")
        # Windows cannot reopen a NamedTemporaryFile while its handle is open.
        audio_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        audio_path = audio_file.name
        audio_file.close()
        try:
            with wave.open(audio_path, "wb") as wav_file:
                wav_file.setnchannels(1)
                wav_file.setsampwidth(2)
                wav_file.setframerate(SAMPLE_RATE)
                wav_file.writeframes(pcm)
            command = [
                self.binary, "-m", str(self.model), "-f", audio_path, "-l", "th", "-nt", "-np",
                # Greedy decode: beam search with temperature fallback was far slower and
                # hallucinated whole phrases on short, silence-padded chunks.
                "-bs", "1", "-bo", "1", "-nf",
            ]
            if self.prompt:
                command += ["--prompt", self.prompt]
            result = subprocess.run(
                command, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=25, check=False,
            )
        finally:
            Path(audio_path).unlink(missing_ok=True)
        if result.returncode:
            detail = (result.stderr or result.stdout).strip().splitlines()
            raise RuntimeError(detail[-1] if detail else "Whisper could not transcribe audio")
        # whisper-cli writes diagnostic lines to stderr; only stdout is speech text.
        return " ".join((result.stdout or "").split())


def is_non_speech_tag(text: str) -> bool:
    """Whisper marks noise/music it can't transcribe as e.g. "[เสียงดนตรี]"."""
    stripped = text.strip()
    if not stripped:
        return False
    return (stripped[0], stripped[-1]) in {("[", "]"), ("(", ")")}


def looks_like_babble(text: str) -> bool:
    """Whisper occasionally locks into a repeat loop ("อืม อืม อืม ...", "ฟัฟัฟัฟั...")."""
    words = text.split()
    if len(words) >= 6:
        most_common = max(words.count(word) for word in set(words))
        if most_common / len(words) > 0.5:
            return True
    compact = "".join(text.split())
    return re.search(r"(.{1,12}?)\1{3,}", compact) is not None


def collapse_repeats(text: str) -> str:
    words: list[str] = []
    for word in text.split():
        if not words or word != words[-1]:
            words.append(word)
    return " ".join(words)


def clean_transcript(text: str) -> str:
    """"" for noise or babble, otherwise the text with doubled words collapsed."""
    if is_non_speech_tag(text) or looks_like_babble(text):
        return ""
    return collapse_repeats(text)
