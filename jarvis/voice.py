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
import math
import os
import platform
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from difflib import SequenceMatcher
from datetime import datetime, timedelta
import urllib.request
from pathlib import Path

from .audio import BYTES_PER_SECOND, SPEECH_RMS, Microphone, rms_level
from .config import Config
from .dataset import Dataset
from .intent import INFO, UNCLEAR, route, rules_fired
from .persona import apply_persona
from .skills import BY_INTENT, Context
from .skills.chat import stream_from
from .skills.light import OFF_REPLIES, ON_REPLIES
from .stt import SAMPLE_RATE, WhisperSTT, clean_transcript
from . import memory, reminders, tools
from .skills import daily
from .skills.remind import describe
from .tts import Speaker, split_sentences, synthesize_f5
from .wake import WAKE_PROMPT, WakeDetector, WhisperWake, normalize, split_wake

READY_REPLY = "พร้อมฟังค่ะ"
UNCLEAR_REPLY = "ฟังไม่ชัดค่ะ ลองพูดอีกทีนะคะ"
NOT_HEARD_REPLY = "ไม่ได้ยินคำถาม ลองเรียก Jarvis อีกครั้งนะคะ"
ERROR_REPLY = "ติดต่อ JARVIS หรือโมเดลภาษาไม่ได้ค่ะ"
# "ขอเช็คแป๊บนึง" before a chat reply ("ขี้เกียจไปอาบน้ำอะ") sounded silly: nothing was checked.
# No "อืม": F5 reads it as a word, not the hum a person makes.
THINK_FILLERS = ("สักครู่นะคะ", "ขอคิดแป๊บนึงนะคะ", "ได้ค่ะ ขอคิดก่อนนะคะ")
LOOKUP_FILLERS = ("ขอเช็คแป๊บนึงนะคะ", "สักครู่นะคะ ขอดูข้อมูลก่อน", "ได้ค่ะ ขอเช็คก่อนนะคะ")
FILLERS = (*THINK_FILLERS, *LOOKUP_FILLERS)
FILLER_AFTER_SECONDS = 0.6
"""A filler is said only when the first sentence isn't ready by then, as a person would."""
STOPPED_REPLY = "ได้ค่ะ"
WARM_PHRASES = (READY_REPLY, *ON_REPLIES, *OFF_REPLIES, UNCLEAR_REPLY, NOT_HEARD_REPLY, *FILLERS, STOPPED_REPLY)
NAMED_WARM = 2 + len(ON_REPLIES) + len(OFF_REPLIES)
"""The first phrases are said with the owner's name, the rest (fillers) without."""
# Interrupting with only one of these drops the rest of the answer instead of resuming it.
# Matched against normalize()d text (tone marks removed) and must be the whole command,
# so a question like "พอจะมีร้านแนะนำไหม" is not taken as "พอ".
_STOP_WORDS = re.compile(r"(?:หยุด|พอแลว|พอ|เงียบ|ไมตองแลว|ไมตอง|ชางมัน|ยกเลิก|stop)(?:กอน|นะ|ครับ|คะ|เลย|ที|แลว|เถอะ|เถอ)*")

# Short polls while waiting for the wake word so "Jarvis" is noticed quickly.
WAKE_STEP_SECONDS = 2
DEAD_MIC_SECONDS = 30
"""All-zero audio this long means a stale device stream, not a quiet room (rms ≥ 3)."""
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
# Echo without hardware AEC: the mic hears JARVIS (on the WH-CH520: mic ≈ 0.26×playback
# median, 0.54 p90, correlation 0.80). The expected leak is subtracted from the mic level
# before deciding the user is talking; the ratio keeps adapting to mic and volume.
LEAK_GAIN_START = 0.6
LEAK_GAIN_RANGE = (0.2, 1.5)
LEAK_SAMPLES = 300
# Follow-ups without the wake word end after this many turns, so a TV can't chat forever.
MAX_FOLLOW_UPS = 5
HISTORY_TURNS = 6
# Share of a heard command found in JARVIS's last reply above which it is its own echo.
ECHO_OVERLAP = 0.6
ECHO_MIN_CHARS = 5
HISTORY_EXPIRES_SECONDS = 1800  # "ฝนตกไหม" then "จะไปกินข้าวข้างนอก" 10 minutes later is one conversation
LOG_PATH = Path(__file__).resolve().parent.parent / "work" / "voice_transcript.log"
MEMORY_PATH = LOG_PATH.with_name("conversation.json")
"""Recent turns and tool results, so a restart doesn't wipe the conversation: after one,
"เมื่อกี้ผมพูดว่าอะไร" had nothing to go on. Expired entries are dropped when saved."""
FACTS_KEEP_SECONDS = 3600
REMIND_CHECK_SECONDS = 15
ALARM_RINGS = 10
ALARM_LISTEN_SECONDS = 8
ALARM_SOUND = "/System/Library/Sounds/Glass.aiff"
ACK_SOUND = "/System/Library/Sounds/Pop.aiff"
MORNING_HOURS = (5, 11)
WORK_LISTEN_SECONDS = 30
_ALARM_SNOOZE = re.compile(rf"(?:นอน|เลื่อน|ขอ).{{0,8}}?อีก\s*{reminders._NUM}?\s*นาที|ขอนอนต่อ|ขอนอนอีก")


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


def _from_tools(ctx: Context, text: str, calls: tuple[tools.Call, ...]) -> Iterator[str]:
    for call, line in zip(calls, tools.run(ctx, list(calls))):
        ctx.facts[call.name] = (time.time(), line)
    said = []
    for sentence in stream_from(ctx, text):
        said.append(sentence)
        yield sentence
    reminder = tools.rain_reminder(ctx, text, " ".join(said))
    if reminder:
        yield reminder


Fillers = tuple[str, ...] | None
# Matched against normalize()d text, which drops tone marks (ไม่ → ไม, ใช่ → ใช).
_NO = re.compile(r"^(?:ไม|อยา)")
_YES = re.compile(r"^(?:ใช|ได|จำ|โอเค|เอา|ดี|ok)", re.IGNORECASE)


def _offer_first(ctx: Context, text: str) -> str | None:
    """"รับทราบค่ะ ให้ผมจำไว้ว่า...ไหมคะ" when a statement holds a fact worth remembering, or
    "ให้ผมตั้งเตือน...ไว้ไหมคะ" when it is a dated plan. Asked by code before any chat reply:
    left to chat, Gemma first promised "ผมจะจำไว้ครับ" or "ผมจะช่วยเตือน" with nothing saved."""
    if not ctx.config.memory_enabled:
        return None
    try:
        fact = memory.notice(text, ctx.config.llm_endpoint)
        at = reminders.parse_when(text) if fact else None
        if at and at > datetime.now():
            what = reminders.what_of(text, ctx.config.llm_endpoint) or fact.replace("ผู้ใช้", "", 1)
            reminder = reminders.new(what, at)
            ctx.offers[:] = [("reminder", reminder)]
            return f"รับทราบค่ะ ให้ผมตั้งเตือน{describe(reminder)} ไว้ไหมคะ"
    except (OSError, ValueError):
        return None
    if not fact:
        return None
    ctx.offers[:] = [("memory", fact)]
    return "รับทราบค่ะ ให้ผมจำไว้ว่า" + fact.replace("ผู้ใช้", "คุณ", 1) + "ไหมคะ"  # คะ last: the persona swaps it


def _offer_reply(ctx: Context, text: str) -> str | None:
    """The answer to a pending offer, or None when the owner moved on (the offer is dropped)."""
    kind, payload = ctx.offers.pop()
    short = normalize(text) if len(text) <= 20 else ""
    if short and _NO.search(short):
        return {"memory": "ได้ค่ะ ไม่จำค่ะ", "reminder": "ได้ค่ะ ไม่ตั้งเตือนค่ะ"}.get(kind, "ได้ค่ะ")
    if short and _YES.search(short):
        if kind == "light_off":
            ctx.action("desk_light", "turn_off")
            return "ปิดไฟแล้วค่ะ ฝันดีนะคะ"
        if kind == "reminder":
            reminders.add(payload)
            return "ตั้งเตือนไว้แล้วค่ะ"
        memory.add(payload)
        return "จำไว้แล้วค่ะ"
    return None


def answer(ctx: Context, text: str, alternatives: tuple[str, ...] = ()) -> tuple[str, str | Iterator[str], Fillers]:
    """Returns (what was decided, reply, fillers to use if it is slow). Slow replies are lazy
    iterators, so tools and the LLM run while a filler plays; the rest is finished text."""
    if ctx.offers:
        reply = _offer_reply(ctx, text)
        if reply:
            return "offer_reply", reply, None
    if not memory.is_question(text) and not rules_fired(text, alternatives):
        # A statement ("วันเสาร์ผมจะไปเชียงใหม่ตอน 7 โมงเช้า") first: it went to the weather
        # tool and the plan was never offered as a reminder.
        offer = _offer_first(ctx, text)
        if offer:
            return "offer", offer, None
    try:
        decided = route(text, ctx.config, alternatives)
    except (OSError, ValueError, KeyError):
        return "error", "ติดต่อโมเดลภาษาไม่ได้ค่ะ", None
    if decided.intent == UNCLEAR:
        return UNCLEAR, UNCLEAR_REPLY, None
    if decided.intent == INFO:
        label = INFO + ":" + "+".join(call.label() for call in decided.calls)
        return label, _guarded(_from_tools(ctx, text, decided.calls)), LOOKUP_FILLERS
    skill = BY_INTENT[decided.intent]
    fillers = THINK_FILLERS if skill.slow else None
    if skill.stream:
        return skill.intent, _guarded(skill.stream(ctx, text)), fillers
    if skill.slow:
        return skill.intent, _guarded(_deferred(skill, ctx, text)), fillers
    try:
        return skill.intent, skill.handle(ctx, text), None
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError, ValueError, KeyError):
        return skill.intent, ERROR_REPLY, None


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
        self._reminded_at = 0.0
        self._load_memory()
        self._spoken: list[str] = []
        self._carry = b""
        self.last_score: float | None = None
        self.leak_gain = LEAK_GAIN_START
        self._leak_ratios: deque[float] = deque(maxlen=LEAK_SAMPLES)

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
        # Kept (marked) so thresholds can be tuned from real rejections later. Not for
        # barge-in: those are almost all JARVIS's own voice leaking into the mic.
        if what != "การพูดแทรก":
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
            # The name about half the time, as people do (VISION.md), not after every reply.
            with_name = (not name or name not in reply) and random.random() < 0.5
            reply = split_sentences(reply) or [reply]
        else:
            # Streamed chat: Qwen already knows the name from the profile and often says it in a
            # later sentence, which gave "ได้ยินครับ คุณแบงค์ คุณแบงค์ ผม…" when it was added here too.
            with_name = False
        for index, part in enumerate(reply):
            text = self.render(part, with_name=with_name and index == 0)
            self._spoken.append(text)
            yield text

    def _after_pause(self, parts: Iterator[str], fillers: tuple[str, ...]) -> Iterator[str]:
        """Says one of `fillers` only if the first part takes longer than FILLER_AFTER_SECONDS."""
        first: list = []

        def pull() -> None:
            try:
                first.append(next(parts))
            except StopIteration:
                pass
            except BaseException as error:  # re-raised below, in the speaker's thread
                first.append(error)

        puller = threading.Thread(target=pull, daemon=True)
        puller.start()
        puller.join(FILLER_AFTER_SECONDS)
        if puller.is_alive():
            yield self.render(random.choice(fillers), with_name=False)
            puller.join()
        for item in first:
            if isinstance(item, BaseException):
                raise item
            yield item
        yield from parts

    def say(self, reply: str | Iterable[str], fillers: Fillers = None, rendered: bool = False,
            prefix: str | None = None) -> tuple[bytes | None, list[str]]:
        """Speaks a reply. Returns (the user's audio if they spoke over JARVIS, the parts of
        the reply that were not said). `rendered` parts already have the persona applied."""
        parts: Iterator[str] = iter(reply) if rendered else self._rendered(reply)
        if fillers:
            parts = self._after_pause(parts, fillers)
        if prefix:
            parts = itertools.chain([self.render(prefix, with_name=False)], parts)
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
        skip = {self.render(text, with_name=False) for text in FILLERS}
        unspoken = [text for text in itertools.chain(self.speaker.unspoken, parts) if text not in skip]
        return barge, unspoken

    def _watch_for_barge_in(self, done: threading.Event, stop: threading.Event) -> bytes | None:
        chunk_bytes = int(BARGE_CHUNK_SECONDS * SAMPLE_RATE) * 2
        recent: deque[bytes] = deque(maxlen=BARGE_CHUNKS + PREROLL_CHUNKS)
        loud = gap = 0
        while not done.is_set():
            chunk = self.mic.read(chunk_bytes)
            recent.append(chunk)
            if self._user_level(rms_level(chunk), loud) >= BARGE_RMS:
                loud, gap = loud + 1, 0
            elif loud:
                gap += 1
                if gap > 1:
                    loud = gap = 0
            if loud >= BARGE_CHUNKS:
                audio = b"".join(recent)
                # Follow-up threshold: the owner's ~1 s interruption, mixed with JARVIS, scored
                # 0.26-0.28. Echo is already subtracted before this point, and text that repeats
                # JARVIS's words is still dropped unless the score clears speaker_threshold.
                if not self.is_owner(audio, "การพูดแทรก", in_conversation=True):
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

    def _user_level(self, mic_rms: float, loud: int) -> float:
        """Mic level left after removing JARVIS's expected echo (energies subtract)."""
        playing = self.speaker.playback_level(time.monotonic())
        echo = self.leak_gain * playing
        user = math.sqrt(max(0.0, mic_rms * mic_rms - echo * echo))
        ratio = mic_rms / playing if playing > 500 else None
        if ratio is not None and not loud and ratio <= LEAK_GAIN_RANGE[1]:
            # Ratios in the echo range teach the gain (talking over JARVIS usually pushes the
            # ratio above it); gating on the subtracted level instead could never raise a
            # gain that started too low.
            self._leak_ratios.append(ratio)
            if len(self._leak_ratios) >= 20:
                ordered = sorted(self._leak_ratios)
                low, high = LEAK_GAIN_RANGE
                self.leak_gain = min(high, max(low, ordered[int(len(ordered) * 0.9)] * 1.2))
        return user

    def warm_up(self) -> None:
        """Pre-generates the fixed replies so they play instantly instead of after synthesis."""
        if not self.f5_port:
            return
        texts = [self.render(t, with_name=w) for t in WARM_PHRASES[:NAMED_WARM] for w in (True, False)] + \
                [self.render(t, with_name=False) for t in WARM_PHRASES[NAMED_WARM:]]
        for text in texts:
            try:
                synthesize_f5(text, self.f5_port, timeout=120)
            except OSError:
                return

    # --- listening ------------------------------------------------------------------

    def run(self) -> None:
        overlap = b""
        idle_ticks = silent_ticks = 0
        while True:
            window = overlap + self.mic.read(WAKE_STEP_SECONDS * BYTES_PER_SECOND)
            overlap = window[-OVERLAP_BYTES:]
            if self._remind():
                overlap = b""
                continue
            rms = rms_level(window)
            silent_ticks = silent_ticks + 1 if not any(window) else 0
            if silent_ticks * WAKE_STEP_SECONDS >= DEAD_MIC_SECONDS:
                log_voice("ไมค์เงียบสนิท (หูฟังอาจหลุดแล้วต่อใหม่) เปิดไมค์ใหม่")
                self.mic.reopen()
                silent_ticks, overlap = 0, b""
                continue
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

    def _remind(self) -> bool:
        """Speaks reminders that are due; checked every REMIND_CHECK_SECONDS while idle."""
        if time.monotonic() - self._reminded_at < REMIND_CHECK_SECONDS:
            return False
        self._reminded_at = time.monotonic()
        try:
            due = reminders.due()
        except (OSError, ValueError):
            return False
        for r in due:
            if r.alarm:
                self._ring(r)
                continue
            early = r.when > datetime.now() + timedelta(minutes=1)
            text = (f"เตือนความจำค่ะ อีก {r.lead} นาที มีเรื่อง{r.what}" if early
                    else f"เตือนความจำค่ะ ถึงเวลา{r.what}แล้วค่ะ")
            log_voice(f"เตือน: {r.what} ({r.at})")
            self.ctx.fired[:] = [r.id]
            self.say(text)
            self.ctx.history.append(("", self.render(text)))
            self.last_turn_at = time.monotonic()
        return bool(due)

    def _ring(self, alarm: reminders.Reminder) -> None:
        """An alarm rings and asks until answered: "ขอนอนอีก 10 นาที" snoozes, anything else from
        the owner ("ตื่นแล้ว", "หยุด") ends it with a good-morning summary."""
        log_voice(f"ปลุก ({alarm.at})")
        self.ctx.fired[:] = [alarm.id]
        for _ in range(ALARM_RINGS):
            subprocess.run(["afplay", ALARM_SOUND], check=False)
            now = datetime.now()
            self.say(f"ตื่นได้แล้วค่ะ ตอนนี้ {now.hour} นาฬิกา{f' {now.minute} นาที' if now.minute else ''}ค่ะ")
            pcm, got = self.capture(b"", ALARM_LISTEN_SECONDS)
            if not got or not self.is_owner(pcm, "ตอบปลุก", in_conversation=True):
                continue
            heard = self.hear(pcm, need_wake=False)
            if heard is None or not heard.command:
                continue
            snooze = _ALARM_SNOOZE.search(heard.command)
            if snooze:
                minutes = reminders._n(snooze.group(1)) if snooze.group(1) else 10
                reminders.snooze(alarm.id, minutes)
                self.say(f"ได้ค่ะ อีก {minutes} นาทีผมปลุกใหม่นะคะ")
                return
            self.say(self._good_morning())
            return
        log_voice("ปลุกแล้วไม่มีคนตอบ")

    def _good_morning(self) -> str:
        """Greeting after an alarm: the day's weather and what is set for today."""
        parts = ["อรุณสวัสดิ์ค่ะ"]
        facts = tools.BY_NAME["get_weather"].run(self.ctx)
        summary = facts.get("day_summary", {}) if isinstance(facts, dict) else {}
        if summary:
            spans = []
            for part in ("เช้า", "บ่าย", "เย็นถึงค่ำ"):
                if part in summary:
                    chance, _, strength = summary[part].partition(" แรงสุด")
                    spans.append(f"ช่วง{part}ไม่น่ามีฝน" if chance == "ไม่น่าตก" else f"ช่วง{part}{chance.replace('ตก', 'มี')}{strength}")
            parts.append(f"วันนี้อากาศ{facts.get('feels', '')} " + " ".join(spans))
        today = [r for r in reminders.upcoming() if r.when.date() == datetime.now().date() and not r.alarm]
        if today:
            parts.append("วันนี้มี " + " ".join(describe(r) for r in today[:3]))
        return " ".join(parts) + "ค่ะ"

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

    def _load_memory(self) -> None:
        if not self.config.remember_across_restarts:
            MEMORY_PATH.unlink(missing_ok=True)  # turned off: don't keep what was said
            return
        try:
            saved = json.loads(MEMORY_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        age = time.time() - saved.get("saved_at", 0)
        if age < HISTORY_EXPIRES_SECONDS:
            self.ctx.history[:] = [tuple(turn) for turn in saved.get("history", [])][-HISTORY_TURNS:]
            self.last_turn_at = time.monotonic() - age
        now = time.time()
        self.ctx.facts.update({name: (at, line) for name, (at, line) in saved.get("facts", {}).items()
                               if now - at < FACTS_KEEP_SECONDS})

    def _save_memory(self) -> None:
        if not self.config.remember_across_restarts:
            return
        now = time.time()
        memory = {"saved_at": now, "history": self.ctx.history,
                  "facts": {name: fact for name, fact in self.ctx.facts.items() if now - fact[0] < FACTS_KEEP_SECONDS}}
        try:
            MEMORY_PATH.write_text(json.dumps(memory, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def converse(self, heard: Heard, wake_heard: str) -> None:
        """Answers, then keeps listening for follow-ups without the wake word. Speaking over
        an answer drops the rest of it and answers the new question instead ("หยุด"/"พอแล้ว"
        alone just stops)."""
        if time.monotonic() - self.last_turn_at > HISTORY_EXPIRES_SECONDS:
            self.ctx.history.clear()  # facts expire on their own (chat.FACTS_EXPIRE_SECONDS)
        pending: deque[Heard] = deque([heard])
        follow_ups = 0
        while True:
            while pending:
                barge, _ = self.respond(pending.popleft(), wake_heard)
                wake_heard = ""
                if barge is None:
                    continue
                pcm, _ = self.capture(barge, 0)
                interruption = (self.hear(pcm, need_wake=False)
                                if self.is_owner(pcm, "การพูดแทรก", in_conversation=True) else None)
                if interruption is None or not interruption.command:
                    continue  # nothing usable was said
                if is_echo(interruption.command, " ".join(self._spoken)) and not self._clearly_owner():
                    log_voice(f"พูดแทรกเป็นเสียงตัวเอง พูดต่อ: {interruption.command}")
                    continue
                if _STOP_WORDS.fullmatch(normalize(interruption.command)):
                    log_voice(f"สั่งหยุด: {interruption.command}")
                    self.say(STOPPED_REPLY)
                    continue
                log_voice(f"เปลี่ยนไปตอบคำถามใหม่: {interruption.command}")
                pending.append(interruption)
            working = self.ctx.mode.get("work_until", 0) > time.monotonic()
            if follow_ups >= MAX_FOLLOW_UPS and not working:
                return
            pcm, got = self.capture(b"", WORK_LISTEN_SECONDS if working else self.config.follow_up_seconds)
            if not got and working:
                continue  # work mode: keep listening until it times out or is ended
            if not got:
                log_voice("จบบทสนทนา กลับไปรอคำว่า Jarvis")
                return
            if working:
                self.ctx.mode["work_until"] = time.monotonic() + daily.WORK_MODE_SECONDS
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
        started = time.monotonic()
        intent, reply, fillers = answer(self.ctx, heard.command, heard.alternatives)
        log_voice(f"intent: {intent} ({time.monotonic() - started:.2f}s)")
        self._spoken = []
        prefix = None
        today = datetime.now()
        if MORNING_HOURS[0] <= today.hour < MORNING_HOURS[1] and self.ctx.mode.get("greeted") != today.toordinal():
            # The first talk of the morning starts with the day's weather and plans (VISION.md).
            self.ctx.mode["greeted"] = today.toordinal()
            try:
                prefix = self._good_morning()
            except (OSError, ValueError, KeyError):
                prefix = "อรุณสวัสดิ์ค่ะ"
        if intent.startswith("light_"):
            subprocess.Popen(["afplay", ACK_SOUND])  # a click as the light switches
        barge, unspoken = self.say(reply, fillers=fillers, prefix=prefix)
        said = " ".join(self._spoken)
        self.dataset.save(heard.pcm, wake=wake_heard, transcript=heard.full, command=heard.command,
                          base=heard.base, intent=intent, reply=said, interrupted=barge is not None,
                          speaker_score=self.last_score)
        self.ctx.history.append((heard.command, said))
        del self.ctx.history[:-HISTORY_TURNS]
        self.last_turn_at = time.monotonic()
        self._save_memory()
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
    if config.keep_awake and shutil.which("caffeinate"):
        # -i: no idle sleep, -s: none on power either; -w: ends when this process does.
        subprocess.Popen(["caffeinate", "-is", "-w", str(os.getpid())])
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
