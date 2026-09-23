"""Keeps real voice commands (audio + transcripts + outcome) for measuring and training later."""

from __future__ import annotations

import json
import wave
from datetime import datetime
from pathlib import Path

from .stt import SAMPLE_RATE


class Dataset:
    def __init__(self, directory: Path | None) -> None:
        self.directory = directory

    def save(self, pcm: bytes, **fields: object) -> None:
        if not self.directory:
            return
        audio_dir = self.directory / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        audio_path = audio_dir / f"{now.strftime('%Y%m%d-%H%M%S-%f')}.wav"
        with wave.open(str(audio_path), "wb") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(SAMPLE_RATE)
            wav_file.writeframes(pcm)
        entry = {"time": now.isoformat(timespec="seconds"), "audio": str(audio_path.relative_to(self.directory)), **fields}
        with (self.directory / "index.jsonl").open("a", encoding="utf-8") as index:
            index.write(json.dumps(entry, ensure_ascii=False) + "\n")
