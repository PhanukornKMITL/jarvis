"""Local-only wake-word and short voice status commands using whisper.cpp."""

from __future__ import annotations

import array
import asyncio
import math
import re
import shutil
from difflib import SequenceMatcher
import subprocess
import sys
import tempfile
import time
import wave
import platform
from datetime import datetime
from pathlib import Path

from .cli import request
from .intent import chat, classify

SAMPLE_RATE = 16_000
# Two different poll sizes: short while waiting for the wake word (fast
# reaction - a short word like "Jarvis" shouldn't need up to 5s to notice),
# wide once we're capturing an actual command/question (a full Thai sentence
# like "วันนี้แต่งชุดอะไรดี" easily runs past 2-3 seconds and gets chopped
# mid-word in a short window).
WAKE_STEP_SECONDS = 2
COMMAND_STEP_SECONDS = 5
OVERLAP_SECONDS = 1
WAKE_TIMEOUT_SECONDS = 10
COMMAND_SILENCE_SECONDS = 1.5
OVERLAP_BYTES = SAMPLE_RATE * OVERLAP_SECONDS * 2
LOG_PATH = Path(__file__).resolve().parent.parent / "work" / "voice_transcript.log"
WAKE_WORDS = (
    "jarvis", "จาร์วิส", "จาวิส", "จาวีส", "เจอร์วิส", "เจวิส", "เตอร์วิส",
    "เจ้าวิส", "เธอวิส", "อาวิส", "เจ้าไว้",
)


WAKE_PROMPT = "\u0e08\u0e32\u0e23\u0e4c\u0e27\u0e34\u0e2a"
# Biases the decoder toward the actual command vocabulary while listening for
# a command, the same trick WAKE_PROMPT uses for the wake word itself -
# without it, short commands like "\u0e40\u0e1b\u0e34\u0e14\u0e44\u0e1f" get misheard as unrelated
# fragments ("\u0e41\u0e04\u0e48\u0e44\u0e1f", "\u0e23\u0e39\u0e49\u0e2a\u0e36\u0e01") far too easily. Phrased the way people
# actually talk (with "\u0e2b\u0e19\u0e48\u0e2d\u0e22"/"\u0e43\u0e2b\u0e49\u0e2b\u0e19\u0e48\u0e2d\u0e22"), not just the bare verb+noun -
# whisper biases toward phrase *shapes* it's seen in the prompt, not just
# the words in isolation.
COMMAND_PROMPT = "\u0e40\u0e1b\u0e34\u0e14\u0e44\u0e1f\u0e43\u0e2b\u0e49\u0e2b\u0e19\u0e48\u0e2d\u0e22 \u0e1b\u0e34\u0e14\u0e44\u0e1f\u0e43\u0e2b\u0e49\u0e2b\u0e19\u0e48\u0e2d\u0e22 \u0e40\u0e0a\u0e47\u0e04\u0e2a\u0e16\u0e32\u0e19\u0e30\u0e2b\u0e19\u0e48\u0e2d\u0e22 \u0e44\u0e1f\u0e42\u0e15\u0e4a\u0e30\u0e40\u0e1b\u0e34\u0e14\u0e2d\u0e22\u0e39\u0e48\u0e44\u0e2b\u0e21"
# How close a mis-transcribed word needs to be to count as "close enough" to
# the wake word (1.0 = identical). Loose on purpose: this only gates whether
# we start listening for a command, the LLM in intent.py still decides what
# the command actually is, so a false trigger just costs one wasted prompt.
FUZZY_THRESHOLD = 0.6
# Consecutive ~COMMAND_STEP_SECONDS polls with nothing new before we treat
# the command as finished and answer. 1 is too eager here: the "พร้อมฟังค่ะ"
# reply itself takes a moment to play, eating into the very first window, so
# a single empty poll right after waking doesn't yet mean the user said
# nothing - give it a second poll before giving up.
NO_CONTENT_POLL_LIMIT = 2
# Thai tone marks (mai ek/tho/tri/chattawa): whisper.cpp is inconsistent about
# which one it attaches to a foreign name like "Jarvis", so drop them before
# comparing against WAKE_WORDS or the match breaks on tone alone.
TONE_MARKS = "\u0e48\u0e49\u0e4a\u0e4b"


def normalize(text: str) -> str:
    return "".join(
        char for char in text.lower()
        if (char.isalnum() or "\u0e00" <= char <= "\u0e7f") and char not in TONE_MARKS
    )


def wake_suffix(text: str) -> str | None:
    normalized = normalize(text)
    if not normalized:
        return None
    matches = [(normalized.find(normalize(word)), normalize(word)) for word in WAKE_WORDS]
    matches = [(index, word) for index, word in matches if index >= 0]
    if matches:
        index, word = min(matches, key=lambda item: item[0])
        return normalized[index + len(word) :]
    # No exact hit: whisper still mangles a foreign name like "Jarvis" even
    # after tone-mark cleanup, so fall back to a phonetic closeness check.
    # A fuzzy hit gives up the command tail (we don't know where the word
    # "ends" inside a mis-transcription), so just open the command window.
    best = max(SequenceMatcher(None, normalize(word), normalized).ratio() for word in WAKE_WORDS)
    if best >= FUZZY_THRESHOLD:
        return ""
    return None


def is_non_speech_tag(text: str) -> bool:
    """Whisper marks noise/music it can't transcribe as e.g. "[เสียงดนตรี]"
    or "(inaudible)" instead of refusing outright; treat those as silence."""
    stripped = text.strip()
    if not stripped:
        return False
    return (stripped[0], stripped[-1]) in {("[", "]"), ("(", ")")}


def looks_like_babble(text: str) -> bool:
    """Whisper occasionally locks into a repeat loop on ambiguous/noisy audio
    (e.g. "อืม อืม อืม ..." or "ฟัฟัฟัฟั..."); treat that as non-speech too."""
    words = text.split()
    if len(words) >= 6:
        most_common = max(words.count(word) for word in set(words))
        if most_common / len(words) > 0.5:
            return True
    compact = "".join(text.split())
    return re.search(r"(.{1,12}?)\1{3,}", compact) is not None


def has_status_intent(text: str) -> bool:
    words = ("สถานะ", "เช็ค", "ตรวจ", "อุปกรณ์", "ออนไลน์", "ไฟ", "อุณหภูมิ", "status", "device", "online", "light", "temperature")
    return any(word in text for word in words)


PIPER_MODEL = Path(__file__).resolve().parent.parent / ".models" / "piper" / "th_TH-tsync2-medium.onnx"


def _has_latin_text(text: str) -> bool:
    return re.search(r"[A-Za-z]{2,}", text) is not None


def speak(text: str) -> float:
    """Speaks text aloud and returns how long it was actually audible (0.0 if
    nothing played). Callers use this to know how much mic input afterward is
    just this speech echoing back into the mic, not real user speech."""
    print(f"JARVIS: {text}", flush=True)
    if platform.system() == "Windows":
        # Piper's Thai voice can't handle mixed-language text - it produces
        # glitchy, repeating audio on English words/terms (which Qwen's
        # free-form chat() replies drop in fairly often, e.g. "Text to
        # Speech", "TTS"). edge-tts's multilingual voice handles mixed text
        # fine, so route there directly when English is present; otherwise
        # prefer Piper since it's fully offline, matching the project's
        # local-only design. edge-tts is also the fallback if either fails.
        synths = (_speak_edge_tts,) if _has_latin_text(text) else (_speak_piper, _speak_edge_tts)
        for synth in synths:
            played = synth(text)
            if played is not None:
                return played
        # Fallback: Windows SAPI has no Thai voice installed on this machine
        # (only English David/Zira), so Thai text comes out silent/garbled -
        # only reached if neither Piper nor edge-tts worked.
        ps = shutil.which("powershell") or shutil.which("pwsh")
        if ps:
            script = (
                "Add-Type -AssemblyName System.Speech; "
                "$s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
                "$s.Speak($args[0])"
            )
            start = time.monotonic()
            subprocess.run([ps, "-NoProfile", "-Command", script, text],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
            return time.monotonic() - start
        return 0.0
    say = shutil.which("say")
    if say:
        start = time.monotonic()
        subprocess.run([say, "-v", "Kanya", text], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
        return time.monotonic() - start
    return 0.0


def _speak_piper(text: str) -> float | None:
    """Synthesize with Piper (fully offline, no network) using the Thai
    tsync2 voice - preferred over edge-tts since it matches the project's
    local-only design. Returns seconds spent playing audio, or None if
    Piper/the model/ffplay aren't available (caller should fall back)."""
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
        start = time.monotonic()
        subprocess.run(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )
        return time.monotonic() - start
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        Path(audio_path).unlink(missing_ok=True)


def _speak_edge_tts(text: str, voice: str = "th-TH-NiwatNeural") -> float | None:
    """Synthesize with edge-tts (a real Thai neural voice, unlike the
    English-only SAPI voices installed on this machine) and play it back
    with ffplay. Needs internet (calls Microsoft's edge-tts service).
    Returns seconds actually spent playing audio, or None if edge-tts wasn't
    available or failed (caller should fall back) - deliberately NOT
    counting the synthesis/network round-trip before playback starts, or a
    slow network call would make flush_echo() eat the user's next reply."""
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
        start = time.monotonic()
        subprocess.run(
            [ffplay, "-nodisp", "-autoexit", "-loglevel", "quiet", audio_path],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False,
        )
        return time.monotonic() - start
    except (subprocess.TimeoutExpired, OSError):
        return None
    finally:
        Path(audio_path).unlink(missing_ok=True)


def log_voice(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"{datetime.now().isoformat(timespec='seconds')} {message}"
    print(line, flush=True)
    with LOG_PATH.open("a", encoding="utf-8") as log_file:
        log_file.write(line + "\n")


def rms_level(pcm: bytes) -> float:
    samples = array.array("h")
    samples.frombytes(pcm)
    if sys.byteorder != "little":
        samples.byteswap()
    if not samples:
        return 0
    return math.sqrt(sum(sample * sample for sample in samples) / len(samples))


def transcribe(whisper: str, model: str, pcm: bytes, prompt: str | None = None) -> str:
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
            whisper, "-m", model, "-f", audio_path, "-l", "th", "-nt", "-np",
            # Greedy decode (no beam search, no temperature-fallback retries):
            # on short, silence-padded chunks the default beam=5/best-of=5
            # search burns ~5-25x more compute AND is what was hallucinating
            # whole unrelated phrases ("[เสียงดนตรี]", "จัดการเจ้า") on
            # background noise. Greedy is faster and closer to real time.
            "-bs", "1", "-bo", "1", "-nf",
        ]
        if prompt:
            command += ["--prompt", prompt]
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=25,
            check=False,
        )
    finally:
        Path(audio_path).unlink(missing_ok=True)
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        raise RuntimeError(detail[-1] if detail else "Whisper could not transcribe audio")
    # whisper-cli writes diagnostic lines to stderr; only stdout is speech text.
    return " ".join((result.stdout or "").split())


def summarize_status(devices: list[dict]) -> str:
    if not devices:
        return "ตอนนี้ยังไม่มีอุปกรณ์เชื่อมต่ออยู่"
    summaries: list[str] = []
    for device in devices:
        device_name = "ไฟโต๊ะ" if device.get("device_id") == "desk_light" else device.get("name", "อุปกรณ์")
        online = "ออนไลน์" if device.get("online") else "ออฟไลน์"
        state = device.get("state", {})
        power = state.get("power")
        if power == "on":
            detail = "เปิดอยู่"
        elif power == "off":
            detail = "ปิดอยู่"
        else:
            detail = ", ".join(f"{key} {value}" for key, value in state.items()) or "ยังไม่มีข้อมูลสถานะ"
        summaries.append(f"{device_name} {online} {detail}")
    return "ค่ะ ".join(summaries)


def answer_status(host: str, port: int) -> float:
    try:
        response = asyncio.run(request(host, port, {"type": "status"}))
        return speak(summarize_status(response.get("devices", [])))
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError):
        return speak("ติดต่อ JARVIS server ไม่ได้")


def answer_intent(host: str, port: int, text: str) -> float:
    try:
        intent = classify(text).get("intent", "unknown")
        log_voice(f"intent: {intent}")
        if intent == "status":
            return answer_status(host, port)
        elif intent in {"light_on", "light_off"}:
            action = "turn_on" if intent == "light_on" else "turn_off"
            response = asyncio.run(request(host, port, {
                "type": "action", "target": "desk_light", "action": action,
            }))
            if response.get("ok"):
                return speak("เปิดไฟให้แล้วค่ะ" if intent == "light_on" else "ปิดไฟให้แล้วค่ะ")
            return speak("สั่งไฟไม่สำเร็จค่ะ")
        else:
            return speak(chat(text))
    except (ConnectionRefusedError, asyncio.TimeoutError, OSError, ValueError, KeyError):
        return speak("ติดต่อ JARVIS หรือ Qwen ไม่ได้ค่ะ")


def listen(host: str, port: int, model: str, audio_device: str) -> int:
    ffmpeg = shutil.which("ffmpeg")
    whisper = shutil.which("whisper-cli")
    if not ffmpeg:
        print("ต้องติดตั้ง FFmpeg ก่อน: brew install ffmpeg", file=sys.stderr)
        return 1
    if not whisper:
        print("ยังไม่พบ whisper-cli; รัน ./setup_local_voice.sh ก่อน", file=sys.stderr)
        return 1
    if not Path(model).is_file():
        print(f"ไม่พบโมเดลเสียง: {model}; รัน ./setup_local_voice.sh ก่อน", file=sys.stderr)
        return 1
    # The wake-word loop needs to stay fast (small polls, tight budget), but
    # once we're actually capturing a command there's room for a slower,
    # more accurate model - small nails short commands ("เปิดไฟให้หน่อย")
    # that base still garbles even with prompt biasing. Falls back to the
    # wake model if ggml-small.bin isn't sitting next to it.
    small_model = Path(model).parent / "ggml-small.bin"
    command_model = str(small_model) if small_model.is_file() else model

    overlap = b""
    system = platform.system()
    input_format = "dshow" if system == "Windows" else "avfoundation"
    if system == "Windows" and not audio_device.startswith("audio="):
        audio_device = f"audio={audio_device}"
    print("กำลังเปิดไมค์เพื่อฟังคำว่า Jarvis (ประมวลผลในเครื่อง; กด Ctrl+C เพื่อหยุด)", flush=True)
    if system == "Windows":
        print(f"ใช้ไมโครโฟน: {audio_device}; ดูชื่ออุปกรณ์ด้วยคำสั่ง ffmpeg -list_devices true -f dshow -i dummy", flush=True)
    else:
        print("ครั้งแรก macOS อาจขออนุญาตให้ Terminal ใช้ไมโครโฟน", flush=True)
    command = [
        ffmpeg, "-hide_banner", "-loglevel", "error",
        "-f", input_format, "-i", audio_device,
        "-ac", "1", "-ar", str(SAMPLE_RATE),
        # Cuts steady low-frequency fan rumble below typical speech. A plain
        # static filter (unlike afftdn, which was pulled: it adapts its noise
        # profile very differently on a live stream than on a file and was
        # seen suppressing real speech on a clean mic).
        "-af", "highpass=f=150",
        "-f", "s16le", "-",
    ]
    recorder = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    state = "wake"
    command_deadline = 0.0
    command_texts: list[str] = []
    # Counts consecutive ~STEP_SECONDS polls with nothing new to add to the
    # command. This mic's noise floor sits well above the RMS gate (see the
    # "[ระดับเสียงพื้นหลัง]" debug line), so the RMS-based silence check below
    # rarely fires; this poll-based counter is what actually lets us respond
    # promptly instead of always waiting out the full WAKE_TIMEOUT_SECONDS.
    no_content_polls = 0
    previous_text = ""
    idle_ticks = 0

    def flush_echo(seconds: float) -> None:
        # speak() plays out loud through the PC speakers, and this mic picks
        # that right back up - without this, JARVIS's own replies get heard
        # and transcribed as if the user said them (e.g. an error message
        # coming back around as the next "command"). The recorder keeps
        # buffering audio while speak() blocks, so drain out roughly that
        # much afterward and drop any overlap tail that's tainted by it.
        nonlocal overlap
        if seconds <= 0 or recorder.stdout is None:
            return
        to_discard = int(SAMPLE_RATE * (seconds + 0.3) * 2)
        discarded = 0
        while discarded < to_discard:
            chunk = recorder.stdout.read(min(32_000, to_discard - discarded))
            if not chunk:
                break
            discarded += len(chunk)
        overlap = b""

    def finalize_command() -> None:
        nonlocal state, command_texts, no_content_polls
        if command_texts:
            utterance = " ".join(command_texts)
            log_voice(f"คำสั่งครบ: {utterance}")
            flush_echo(answer_intent(host, port, utterance))
        else:
            flush_echo(speak("ไม่ได้ยินคำถาม ลองเรียก Jarvis อีกครั้งนะคะ"))
        state = "wake"
        command_texts = []
        no_content_polls = 0

    try:
        assert recorder.stdout is not None
        while True:
            step_seconds = WAKE_STEP_SECONDS if state == "wake" else COMMAND_STEP_SECONDS
            bytes_per_step = SAMPLE_RATE * step_seconds * 2
            pcm = recorder.stdout.read(bytes_per_step)
            if len(pcm) != bytes_per_step:
                error = recorder.stderr.read().decode(errors="replace").strip() if recorder.stderr else ""
                raise RuntimeError(error or "ไมโครโฟนหยุดทำงาน")
            combined = overlap + pcm
            overlap = combined[-OVERLAP_BYTES:]
            # Enforce the command deadline unconditionally: relying only on
            # the "quiet" branch below meant a noisy/hallucinating mic that
            # never dropped under the RMS gate could trap us in "command"
            # state forever, since the timeout was never even checked.
            if state == "command" and time.monotonic() > command_deadline:
                finalize_command()
                continue
            rms = rms_level(combined)
            if rms < 420:
                idle_ticks += 1
                if idle_ticks % 5 == 0:
                    # Console-only (not written to the log file): lets you see
                    # the mic's actual noise floor to tell whether 420 is the
                    # right cutoff for this room/mic.
                    print(f"[ระดับเสียงพื้นหลัง] rms={rms:.0f} (เกณฑ์ตอนนี้=420)", flush=True)
                if state == "command":
                    no_content_polls += 1
                    if command_texts and no_content_polls >= NO_CONTENT_POLL_LIMIT:
                        finalize_command()
                continue
            idle_ticks = 0
            try:
                if state == "wake":
                    text = transcribe(whisper, model, combined, WAKE_PROMPT)
                else:
                    text = transcribe(whisper, command_model, combined, COMMAND_PROMPT)
            except RuntimeError as error:
                # Short/noisy microphone frames can occasionally make Whisper
                # fail; keep listening instead of killing the voice session.
                print(f"ข้ามช่วงเสียงนี้: {error}", file=sys.stderr, flush=True)
                continue
            compact = normalize(text)
            if not compact or compact == previous_text:
                if state == "command":
                    no_content_polls += 1
                    if command_texts and no_content_polls >= NO_CONTENT_POLL_LIMIT:
                        finalize_command()
                continue
            previous_text = compact
            if is_non_speech_tag(text) or looks_like_babble(text):
                if state == "command":
                    no_content_polls += 1
                    if command_texts and no_content_polls >= NO_CONTENT_POLL_LIMIT:
                        finalize_command()
                continue
            log_voice(f"ได้ยิน: {text} (rms={rms:.0f})")
            tail = wake_suffix(text)
            if state == "wake" and tail is not None:
                log_voice("พบคำปลุก Jarvis")
                if has_status_intent(tail):
                    flush_echo(answer_status(host, port))
                    continue
                state = "command"
                command_deadline = time.monotonic() + WAKE_TIMEOUT_SECONDS
                no_content_polls = 0
                command_texts = [tail] if tail else []
                flush_echo(speak("พร้อมฟังค่ะ"))
                continue
            if state == "command":
                if text not in command_texts:
                    command_texts.append(text)
                no_content_polls = 0
    except KeyboardInterrupt:
        print("หยุดฟังแล้ว", flush=True)
        return 0
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(f"เสียงใช้งานไม่ได้: {error}", file=sys.stderr)
        print("ตรวจสิทธิ์ไมโครโฟนของ Terminal และชื่ออุปกรณ์เสียง แล้วลองใหม่", file=sys.stderr)
        return 1
    finally:
        recorder.terminate()
        try:
            recorder.wait(timeout=2)
        except subprocess.TimeoutExpired:
            recorder.kill()
    return 0
