"""Voice pipeline: mic → wake word → speech-to-text → intent → skill → speech.

After the wake word JARVIS stays in a conversation: it says a short filler while a slow
answer is prepared, streams long answers sentence by sentence, stops talking when the
user speaks over it, and keeps listening for follow-ups without the wake word.
"""

from __future__ import annotations

import asyncio
import base64
import itertools
import json
import platform
import random
import re
import shutil
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from datetime import datetime
import urllib.request
from pathlib import Path

from .audio import BYTES_PER_SECOND, SPEECH_RMS, Microphone, rms_level
from .config import Config
from .dataset import Dataset
from .intent import UNCLEAR, classify
from .persona import apply_persona
from .skills import BY_INTENT, Context
from .skills.light import OFF_REPLY, ON_REPLY
from .stt import SAMPLE_RATE, WhisperSTT, clean_transcript
from .tts import Speaker, split_sentences, synthesize_f5
from .wake import WAKE_PROMPT, WakeDetector, WhisperWake, normalize, split_wake

READY_REPLY = "พร้อมฟังค่ะ"
UNCLEAR_REPLY = "ฟังไม่ชัดค่ะ ลองพูดอีกทีนะคะ"
NOT_HEARD_REPLY = "ไม่ได้ยินคำถาม ลองเรียก Jarvis อีกครั้งนะคะ"
ERROR_REPLY = "ติดต่อ JARVIS หรือ Qwen ไม่ได้ค่ะ"
FILLERS = ("อืม ขอคิดแป๊บนึงนะคะ", "อืม สักครู่นะคะ", "ได้ค่ะ ขอเช็คแป๊บนึงนะคะ")
RESUME_REPLY = "ขอตอบเรื่องเมื่อกี้ให้จบก่อนนะคะ"
STOPPED_REPLY = "ได้ค่ะ"
WARM_PHRASES = (READY_REPLY, ON_REPLY, OFF_REPLY, UNCLEAR_REPLY, NOT_HEARD_REPLY, *FILLERS, RESUME_REPLY, STOPPED_REPLY)
# Interrupting with only one of these drops the rest of the answer instead of resuming it.
# Matched against normalize()d text (tone marks removed) and must be the whole command,
# so a question like "พอจะมีร้านแนะนำไหม" is not taken as "พอ".
_STOP_WORDS = re.compile(r"(?:หยุด|พอแลว|พอ|เงียบ|ไมตองแลว|ไมตอง|ชางมัน|ยกเลิก|stop)(?:กอน|นะ|ครับ|คะ|เลย|ที|แลว|เถอะ|เถอ)*")

# Short polls while waiting for the wake word so "Jarvis" is noticed quickly.
WAKE_STEP_SECONDS = 2
OVERLAP_BYTES = 1 * BYTES_PER_SECOND
LISTEN_TIMEOUT_SECONDS = 6
# People pause after the name ("จาร์วิส … ปิดไฟ"); real recordings showed up to 1.25s.
AFTER_WAKE_GRACE_SECONDS = 1.5
# Barge-in: well above the wake threshold and sustained, so room noise doesn't cut JARVIS
# off. Headset leak of JARVIS's own voice into its mic measured weak (envelope r≈0.29).
BARGE_RMS = SPEECH_RMS * 3
BARGE_CHUNK_SECONDS = 0.1
BARGE_CHUNKS = 4
# Audio kept from before the loud part: the quiet start of "เปิด" was lost with less.
PREROLL_CHUNKS = 6
# Follow-ups without the wake word end after this many turns, so a TV can't chat forever.
MAX_FOLLOW_UPS = 5
HISTORY_TURNS = 6
# Share of a heard command found in JARVIS's last reply above which it is its own echo.
ECHO_OVERLAP = 0.6
ECHO_MIN_CHARS = 5
HISTORY_EXPIRES_SECONDS = 180
LOG_PATH = Path(__file__).resolve().parent.parent / "work" / "voice_transcript.log"


def log_voice(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(line + "\n")


def _guarded(parts: Iterator[str]) -> Iterator[str]:
    """Runs inside the speaker's thread; a failing skill still gets a spoken reply."""
    said = False
    try:
        for part in parts:
            said = True
            yield part
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError, ValueError, KeyError):
        if not said:
            yield ERROR_REPLY


def _deferred(skill, ctx: Context, text: str) -> Iterator[str]:
    yield from split_sentences(skill.handle(ctx, text))


def answer(ctx: Context, text: str, alternatives: tuple[str, ...] = ()) -> tuple[str, str | Iterator[str]]:
    """Returns (intent, reply). Slow skills return a lazy iterator so they run while the
    filler plays; everything else returns the finished text."""
    try:
        intent = classify(text, ctx.config.llm_endpoint, alternatives)
    except (OSError, ValueError, KeyError):
        return "error", "ติดต่อ Qwen ไม่ได้ค่ะ"
    if intent == UNCLEAR:
        return intent, UNCLEAR_REPLY
    skill = BY_INTENT[intent]
    if skill.stream:
        return intent, _guarded(skill.stream(ctx, text))
    if skill.slow:
        return intent, _guarded(_deferred(skill, ctx, text))
    try:
        return intent, skill.handle(ctx, text)
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError, ValueError, KeyError):
        return intent, ERROR_REPLY


def is_echo(heard: str, said: str) -> bool:
    """True when most of what was heard is JARVIS's own last reply coming back through the mic."""
    heard, said = normalize(heard), normalize(said)
    # Short replies ("โอเค") share scattered letters with almost any sentence: never echo.
    if len(heard) < ECHO_MIN_CHARS or not said:
        return False
    # One contiguous stretch, not scattered letters: summing all matching blocks flagged
    # the owner's "โอเค" as echo of a garden reply that merely contained อ, เ and ค.
    longest = SequenceMatcher(None, heard, said, autojunk=False).find_longest_match(0, len(heard), 0, len(said))
    return longest.size / len(heard) >= ECHO_OVERLAP


def speaker_score(pcm: bytes, port: int) -> float | None:
    """Similarity of `pcm` to the enrolled owner (via the tts service); None if unavailable."""
    request = urllib.request.Request(
        f"http://127.0.0.1:{port}/verify",
        data=json.dumps({"pcm": base64.b64encode(pcm).decode()}).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return json.loads(response.read()).get("score")
    except (OSError, ValueError):
        return None


def _transcribe(stt: WhisperSTT, pcm: bytes) -> str:
    # A whisper failure on one noisy clip must not end the voice session like a mic error does.
    try:
        return stt.transcribe(pcm)
    except RuntimeError as error:
        print(f"ข้ามช่วงเสียงนี้: {error}", file=sys.stderr, flush=True)
        return ""


@dataclass
class Heard:
    pcm: bytes
    full: str
    command: str
    base: str
    alternatives: tuple[str, ...]


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
        self.speaker = Speaker(config.tts_voice, self.f5_port)
        self.last_turn_at = 0.0
        self._spoken: list[str] = []
        self._carry = b""
        self.last_score: float | None = None

    def is_owner(self, pcm: bytes, what: str, in_conversation: bool = False) -> bool:
        """Speaker check; allows everything when disabled or when the service is unavailable.

        Inside a conversation the owner was already verified at the wake word, and short
        casual follow-ups score lower (0.24 for a real one), so a looser threshold applies."""
        self.last_score = None
        if not self.config.speaker_check:
            return True
        score = speaker_score(pcm, self.config.f5_port)
        self.last_score = score
        threshold = self.config.speaker_follow_up_threshold if in_conversation else self.config.speaker_threshold
        if score is None or score >= threshold:
            return True
        log_voice(f"ไม่ใช่เสียงเจ้าของ ไม่รับ{what} (score={score:.2f} < {threshold:.2f})")
        # Kept (marked) so thresholds can be tuned from real rejections later.
        self.dataset.save(pcm, rejected=what, speaker_score=score, intent=None)
        return False

    def _clearly_owner(self) -> bool:
        """The last speaker check passed the strict threshold. Text overlap alone can't tell
        echo from a user repeating JARVIS's words ("อาหารประเภทไหน" in an answer to
        "คุณชอบอาหารประเภทไหนครับ"), so the voice decides and text only breaks the tie."""
        score = self.last_score
        return score is not None and score >= self.config.speaker_threshold

    def capture(self, initial: bytes, wait_seconds: float) -> tuple[bytes, bool]:
        """Microphone.capture, starting with speech the barge-in watcher already heard."""
        carry, self._carry = self._carry, b""
        if carry and not initial:
            return self.mic.capture(carry, 0)
        return self.mic.capture(initial, wait_seconds)

    # --- speaking -------------------------------------------------------------------

    def render(self, text: str, with_name: bool = True) -> str:
        return apply_persona(text, self.config.gender, self.config.profile.name if with_name else "")

    def _rendered(self, reply: str | Iterable[str]) -> Iterator[str]:
        """Applies the persona per sentence (the name only once) and records what was said."""
        name = self.config.profile.name
        if isinstance(reply, str):
            with_name = not name or name not in reply
            reply = split_sentences(reply) or [reply]
        else:
            with_name = True
        for index, part in enumerate(reply):
            text = self.render(part, with_name=with_name and index == 0)
            self._spoken.append(text)
            yield text

    def say(self, reply: str | Iterable[str], filler: bool = False, rendered: bool = False,
            prefix: str | None = None) -> tuple[bytes | None, list[str]]:
        """Speaks a reply. Returns (the user's audio if they spoke over JARVIS, the parts of
        the reply that were not said). `rendered` parts already have the persona applied."""
        parts: Iterator[str] = iter(reply) if rendered else self._rendered(reply)
        lead = [self.render(random.choice(FILLERS), with_name=False)] if filler else []
        if prefix:
            lead.append(self.render(prefix, with_name=False))
        parts = itertools.chain(lead, parts)
        if not self.config.barge_in:
            self.mic.drain(self.speaker.speak(parts))
            return None, []
        # Audio buffered while JARVIS was thinking must not be mistaken for barge-in.
        self.mic.drain(30)
        self._carry = b""
        stop, done = threading.Event(), threading.Event()

        def talk() -> None:
            try:
                self.speaker.speak(parts, stop)
            finally:
                done.set()

        talker = threading.Thread(target=talk, daemon=True)
        talker.start()
        barge = self._watch_for_barge_in(done, stop)
        talker.join()
        if barge is None:
            return None, []
        skip = {self.render(text, with_name=False) for text in (*FILLERS, RESUME_REPLY)}
        unspoken = [text for text in itertools.chain(self.speaker.unspoken, parts) if text not in skip]
        return barge, unspoken

    def _watch_for_barge_in(self, done: threading.Event, stop: threading.Event) -> bytes | None:
        chunk_bytes = int(BARGE_CHUNK_SECONDS * SAMPLE_RATE) * 2
        recent: deque[bytes] = deque(maxlen=BARGE_CHUNKS + PREROLL_CHUNKS)
        loud = gap = 0
        while not done.is_set():
            chunk = self.mic.read(chunk_bytes)
            recent.append(chunk)
            if rms_level(chunk) >= BARGE_RMS:
                loud, gap = loud + 1, 0
            elif loud:
                gap += 1
                if gap > 1:
                    loud = gap = 0
            if loud >= BARGE_CHUNKS:
                audio = b"".join(recent)
                # Strict threshold here: JARVIS's own echo scored up to 0.28, above the follow-up one.
                if not self.is_owner(audio, "การพูดแทรก"):
                    recent.clear()
                    loud = gap = 0  # e.g. JARVIS's own voice leaking into the mic: keep talking
                    continue
                stop.set()
                log_voice("ถูกพูดแทรก หยุดพูด")
                return audio
        if loud:
            # The user started talking just as JARVIS finished: hand that start to the next
            # capture, or its first syllable is lost ("เปิดไฟ" was heard as "ไฟ").
            self._carry = b"".join(recent)
        return None

    def warm_up(self) -> None:
        """Pre-generates the fixed replies so they play instantly instead of after synthesis."""
        if not self.f5_port:
            return
        texts = [self.render(t) for t in WARM_PHRASES[:5]] + [self.render(t, with_name=False) for t in WARM_PHRASES[5:]]
        for text in texts:
            try:
                synthesize_f5(text, self.f5_port, timeout=120)
            except OSError:
                return

    # --- listening ------------------------------------------------------------------

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

    def hear(self, pcm: bytes, need_wake: bool) -> Heard | None:
        """Transcribes an utterance; None if `need_wake` and the name isn't in it after all."""
        full = _transcribe(self.command_stt, pcm)
        after = split_wake(full)
        if need_wake and after is None:
            return None
        command = clean_transcript(full if after is None else after)
        # A second opinion from the wake model: on this headset it sometimes hears "ปิด"
        # better, and the light rules refuse to act when the two disagree.
        base = ""
        if self.command_stt is not self.wake_stt:
            base_full = _transcribe(self.wake_stt, pcm)
            base_after = split_wake(base_full)
            base = clean_transcript(base_after if base_after is not None else ("" if need_wake else base_full))
            command = command or base
        alternatives = (base,) if base and base != command else ()
        return Heard(pcm, full, command, base, alternatives)

    def handle_wake(self, window: bytes, wake_heard: str) -> None:
        utterance, _ = self.mic.capture(window, 0)
        if not self.is_owner(utterance, "คำสั่ง"):
            return
        heard = self.hear(utterance, need_wake=True)
        if heard is None:
            log_voice("ฟังซ้ำแล้วไม่ใช่คำปลุก")
            return
        if not heard.command:
            follow, got = self.mic.capture(b"", AFTER_WAKE_GRACE_SECONDS)
            if not got:
                barge, _ = self.say(READY_REPLY)
                follow, got = self.capture(barge or b"", 0 if barge else LISTEN_TIMEOUT_SECONDS)
            if got:
                heard = self.hear(follow, need_wake=False) or heard
                heard.pcm = utterance + follow
        if not heard.command:
            self.dataset.save(heard.pcm, wake=wake_heard, transcript=heard.full, command="", intent=None)
            self.say(NOT_HEARD_REPLY)
            return
        self.converse(heard, wake_heard)

    def converse(self, heard: Heard, wake_heard: str) -> None:
        """Answers, then keeps listening for follow-ups without the wake word. A question
        asked over an answer is queued: JARVIS finishes the old answer first, then answers
        it (unless the interruption was "หยุด"/"พอแล้ว", which drops the rest)."""
        if time.monotonic() - self.last_turn_at > HISTORY_EXPIRES_SECONDS:
            self.ctx.history.clear()
        pending: deque[Heard] = deque([heard])
        leftover: list[str] = []
        follow_ups = 0
        while True:
            while pending or leftover:
                if leftover:
                    barge, leftover = self.say(leftover, rendered=True, prefix=RESUME_REPLY)
                else:
                    barge, leftover = self.respond(pending.popleft(), wake_heard)
                    wake_heard = ""
                if barge is None:
                    continue
                pcm, _ = self.capture(barge, 0)
                interruption = self.hear(pcm, need_wake=False) if self.is_owner(pcm, "การพูดแทรก") else None
                if interruption is None or not interruption.command:
                    continue  # nothing usable was said: resume the answer
                if is_echo(interruption.command, " ".join(self._spoken)) and not self._clearly_owner():
                    log_voice(f"พูดแทรกเป็นเสียงตัวเอง พูดต่อ: {interruption.command}")
                    continue
                if _STOP_WORDS.fullmatch(normalize(interruption.command)):
                    log_voice(f"สั่งหยุด: {interruption.command}")
                    leftover = []
                    self.say(STOPPED_REPLY)
                    continue
                log_voice(f"จำคำถามใหม่ไว้ ตอบเรื่องเดิมให้จบก่อน: {interruption.command}")
                pending.append(interruption)
            if follow_ups >= MAX_FOLLOW_UPS:
                return
            pcm, got = self.capture(b"", self.config.follow_up_seconds)
            if not got:
                log_voice("จบบทสนทนา กลับไปรอคำว่า Jarvis")
                return
            if not self.is_owner(pcm, "คำถามต่อ", in_conversation=True):
                return
            next_heard = self.hear(pcm, need_wake=False)
            if next_heard is None or not next_heard.command:
                return
            last_said = self.ctx.history[-1][1] if self.ctx.history else ""
            if is_echo(next_heard.command, last_said) and not self._clearly_owner():
                # JARVIS heard itself ("ต้นไม้ความชืด" right after its garden reply): answering it loops.
                log_voice(f"ได้ยินเสียงตัวเอง ไม่ตอบ: {next_heard.command}")
                return
            pending.append(next_heard)
            follow_ups += 1
            log_voice("คุยต่อโดยไม่ต้องเรียกชื่อ")

    def respond(self, heard: Heard, wake_heard: str) -> tuple[bytes | None, list[str]]:
        log_voice(f"คำสั่ง: {heard.command}" + (f" (base: {heard.alternatives[0]})" if heard.alternatives else ""))
        intent, reply = answer(self.ctx, heard.command, heard.alternatives)
        log_voice(f"intent: {intent}")
        skill = BY_INTENT.get(intent)
        self._spoken = []
        barge, unspoken = self.say(reply, filler=bool(skill and skill.slow))
        said = " ".join(self._spoken)
        self.dataset.save(heard.pcm, wake=wake_heard, transcript=heard.full, command=heard.command,
                          base=heard.base, intent=intent, reply=said, interrupted=barge is not None,
                          speaker_score=self.last_score)
        self.ctx.history.append((heard.command, said))
        del self.ctx.history[:-HISTORY_TURNS]
        self.last_turn_at = time.monotonic()
        return barge, unspoken


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
    wake_stt = WhisperSTT(config.wake_model, WAKE_PROMPT, config.wake_port or None)
    command_stt = (WhisperSTT(config.command_model, WAKE_PROMPT, config.command_port or None)
                   if config.command_model.is_file() else wake_stt)

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
