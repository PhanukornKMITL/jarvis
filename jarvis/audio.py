"""Microphone input through FFmpeg as 16 kHz mono 16-bit PCM."""

from __future__ import annotations

import array
import math
import platform
import select
import subprocess
import sys

from .stt import SAMPLE_RATE

BYTES_PER_SECOND = SAMPLE_RATE * 2
SPEECH_RMS = 420
CHUNK_SECONDS = 0.25
END_SILENCE_SECONDS = 0.8
MAX_UTTERANCE_SECONDS = 8


def rms_level(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm[: len(pcm) - len(pcm) % 2])
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


class Microphone:
    def __init__(self, ffmpeg: str, device: str) -> None:
        self.windows = platform.system() == "Windows"
        if self.windows and not device.startswith("audio="):
            device = f"audio={device}"
        self.device = device
        self.ffmpeg = ffmpeg
        self._open()

    def reopen(self) -> None:
        """Starts ffmpeg again: after a Bluetooth headset drops and reconnects, the old stream
        keeps delivering exact silence (rms 0 for an hour on 2026-09-26) instead of failing."""
        self.process.kill()
        self.process.wait()
        self._open()

    def _open(self) -> None:
        ffmpeg, device = self.ffmpeg, self.device
        self.process = subprocess.Popen(
            [
                ffmpeg, "-hide_banner", "-loglevel", "error",
                "-f", "dshow" if self.windows else "avfoundation", "-i", device,
                "-ac", "1", "-ar", str(SAMPLE_RATE),
                # Static filter for fan rumble; afftdn was dropped for suppressing real speech live.
                "-af", "highpass=f=150",
                "-f", "s16le", "-",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def read(self, size: int) -> bytes:
        """Exactly `size` bytes; RuntimeError if the microphone stream ended."""
        assert self.process.stdout is not None
        pcm = self.process.stdout.read(size)
        if len(pcm) != size:
            stderr = self.process.stderr
            error = stderr.read().decode(errors="replace").strip() if stderr else ""
            raise RuntimeError(error or "ไมโครโฟนหยุดทำงาน")
        return pcm

    def drain(self, seconds: float) -> None:
        """Drops audio recorded while JARVIS spoke so its reply isn't heard as a command.

        Capped at the speech length so the user's next words survive. On macOS only what
        is already buffered is dropped: the pipe holds ~2.5s (newer audio is lost while
        it is full), so a blocking drain after a long reply would eat live speech.
        """
        stdout = self.process.stdout
        if seconds <= 0 or stdout is None:
            return
        to_discard = int((seconds + 0.3) * BYTES_PER_SECOND)
        discarded = 0
        if not self.windows:
            while discarded < to_discard and select.select([stdout], [], [], 0)[0]:
                chunk = stdout.read1(min(65536, to_discard - discarded))
                if not chunk:
                    return
                discarded += len(chunk)
            return
        while discarded < to_discard:
            chunk = stdout.read(min(32_000, to_discard - discarded))
            if not chunk:
                return
            discarded += len(chunk)

    def capture(self, initial: bytes, wait_seconds: float) -> tuple[bytes, bool]:
        """Reads until the speaker pauses; returns the audio and whether speech was heard.

        Transcribing one whole utterance replaced fixed windows, which chopped Thai
        sentences mid-word and added seconds of latency.
        """
        chunk_bytes = int(CHUNK_SECONDS * SAMPLE_RATE) * 2
        audio = bytearray(initial)
        heard = bool(initial)
        quiet = waited = 0.0
        while len(audio) < MAX_UTTERANCE_SECONDS * BYTES_PER_SECOND:
            chunk = self.read(chunk_bytes)
            audio += chunk
            if rms_level(chunk) >= SPEECH_RMS:
                heard, quiet = True, 0.0
                continue
            quiet += CHUNK_SECONDS
            if heard and quiet >= END_SILENCE_SECONDS:
                break
            if not heard:
                waited += CHUNK_SECONDS
                if waited >= wait_seconds:
                    break
        return bytes(audio), heard

    def close(self) -> None:
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
