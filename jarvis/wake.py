"""Wake-word detection.

A detector takes a short audio window and returns a WakeResult, or None when nothing
new was heard. Any detector with that shape (e.g. a trained "จาวิส" model) can replace
WhisperWake without touching the rest of the pipeline.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Protocol

from .stt import WhisperSTT, clean_transcript

WAKE_PROMPT = "จาร์วิส"
WAKE_WORDS = (
    "jarvis", "จาร์วิส", "จาวิส", "จาวีส", "เจอร์วิส", "เจวิส", "เตอร์วิส",
    "เจ้าวิส", "เธอวิส", "อาวิส", "เจ้าไว้",
    # Heard on the Mac headset when the final "ส" isn't crisp.
    "จาวิต", "จาวิท", "จาร์วิต", "จาร์วิท", "จับวิส", "จับวิต", "จาววิส", "ดาวิส", "ดาวิต", "ท่าวิส",
)
# Fuzzy prefix matching compares only against these, so loose variants like "อาวิส"
# can't make ordinary sentences starting with "อากาศ" wake JARVIS.
CORE_WAKE_WORDS = ("jarvis", "จาร์วิส", "จาวิส")
# Loose on purpose: a false wake only costs one extra transcription of the utterance.
FUZZY_THRESHOLD = 0.6
PREFIX_FUZZY_THRESHOLD = 0.75
# Normalized (tone marks removed) lead-ins allowed before the wake word, e.g. "เฮ้ จาวิส".
LEAD_INS = {"เฮ", "เฮย", "hey", "hi"}
# whisper.cpp is inconsistent about tone marks on a foreign name like "Jarvis".
TONE_MARKS = "่้๊๋"


def _kept_chars(text: str) -> list[tuple[int, str]]:
    return [
        (index, char) for index, char in enumerate(text.lower())
        if (char.isalnum() or "฀" <= char <= "๿") and char not in TONE_MARKS
    ]


def normalize(text: str) -> str:
    return "".join(char for _, char in _kept_chars(text))


def split_wake(text: str) -> str | None:
    """The command spoken after the wake word, or None if no wake word was heard."""
    kept = _kept_chars(text)
    normalized = "".join(char for _, char in kept)
    if not normalized:
        return None
    hits = [(normalized.find(word), len(word)) for word in map(normalize, WAKE_WORDS) if word in normalized]
    if hits:
        index, length = min(hits)
        return text[kept[index + length - 1][0] + 1 :].strip()
    # Fuzzy only at the start: matching inside a sentence woke on "อากาศ..." (≈ "อาวิส").
    words = text.split()
    if words and normalize(words[0]) in LEAD_INS:
        words = words[1:]
    # Too short to judge: "จับ" alone was ~67% similar to "จับวิส".
    if not words or len(normalize(words[0])) < 4:
        return None
    first = normalize(words[0])
    best = max(SequenceMatcher(None, normalize(word), first).ratio() for word in WAKE_WORDS)
    if best >= FUZZY_THRESHOLD:
        return " ".join(words[1:])
    # Whisper often glues the name to the command ("ท่าวิสปิฟาย"): compare the leading
    # characters of the first word instead, stricter and only against the core spellings.
    first_kept = _kept_chars(words[0])
    best_ratio, best_end = 0.0, 0
    for word in map(normalize, CORE_WAKE_WORDS):
        for end in range(max(1, len(word) - 1), min(len(first), len(word) + 1) + 1):
            ratio = SequenceMatcher(None, word, first[:end]).ratio()
            if ratio > best_ratio:
                best_ratio, best_end = ratio, end
    if best_ratio < PREFIX_FUZZY_THRESHOLD:
        return None
    rest_of_first = words[0][first_kept[best_end - 1][0] + 1 :]
    return " ".join([rest_of_first, *words[1:]]).strip()


@dataclass(frozen=True)
class WakeResult:
    heard: str
    """What the detector heard, for the log ("" for detectors that don't transcribe)."""
    command: str | None
    """Text after the wake word ("" if none yet); None means no wake word."""


class WakeDetector(Protocol):
    def detect(self, pcm: bytes) -> WakeResult | None: ...

    def reset(self) -> None: ...


class WhisperWake:
    """Transcribes each window with a fast model and looks for the wake word in the text."""

    def __init__(self, stt: WhisperSTT) -> None:
        self.stt = stt
        self._previous = ""

    def detect(self, pcm: bytes) -> WakeResult | None:
        try:
            text = self.stt.transcribe(pcm)
        except RuntimeError as error:
            print(f"ข้ามช่วงเสียงนี้: {error}", file=sys.stderr, flush=True)
            return None
        compact = normalize(text)
        # Overlapping windows often repeat the same words; report each phrase once.
        if not compact or compact == self._previous or not clean_transcript(text):
            return None
        self._previous = compact
        return WakeResult(heard=text, command=split_wake(text))

    def reset(self) -> None:
        self._previous = ""
