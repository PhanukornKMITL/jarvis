# Voice pipeline session notes — 2026-09-23

Working notes from a long debugging/tuning session on `jarvis/voice.py` +
`jarvis/intent.py`, done on a Windows dev machine. Final deployment target
is a **Mac M4** — see "Before moving to Mac" at the bottom before assuming
anything here still applies.

## What was broken, and what fixed it

1. **Wake word never matched close mishearings** ("จ้าวิส" vs "จาวิส")
   → `normalize()` now strips Thai tone marks before comparing.
2. **Wake word missed anything not in the fixed word list**
   → added a fuzzy-match fallback (`difflib.SequenceMatcher`, threshold 0.6)
   on top of the exact-match list.
3. **Whisper hallucinated long unrelated phrases** ("[เสียงดนตรี]", "จัดการเจ้า")
   → root cause: default decode was beam=5/best-of=5 + temperature fallback,
   which on short/silence-padded audio explodes into hundreds of decode
   passes. Switched to greedy decode (`-bs 1 -bo 1 -nf`) — also ~4x faster.
4. **Command capture could hang forever** if the mic's noise floor never
   dropped below the RMS gate (this mic's floor sits at 500–1900, way above
   the old 420 threshold) → timeout/finalize logic now runs unconditionally
   every poll, not just in the "quiet" branch.
5. **Repeat-loop hallucination** ("อืม อืม อืม...", "ฟัฟัฟัฟั...") slipping
   through as real speech → added `looks_like_babble()` (word-repeat ratio +
   regex for repeated short substrings, up to 12 chars, 4+ repeats).
6. **Full sentences got chopped mid-word** — a single fixed 2s window is too
   short for a natural Thai sentence → split into two window sizes:
   `WAKE_STEP_SECONDS = 2` (fast, while waiting for the wake word) and
   `COMMAND_STEP_SECONDS = 5` (wide, once actually capturing a command).
7. **Base model still garbled short commands** ("เปิดไฟให้หน่อย" → unusable)
   even with prompt biasing → command-phase transcription now uses
   `ggml-small.bin` specifically (falls back to the wake model if that file
   isn't present); wake-phase keeps `ggml-base.bin` for speed.
8. **JARVIS was inaudible** — Windows has no Thai SAPI voice installed here
   (only English David/Zira), so Thai text played back as silence/garbage.
   Fixed by adding real TTS: **Piper** (`th_TH-tsync2-medium`, fully local,
   female voice) as primary, **edge-tts** (`th-TH-NiwatNeural`, male, needs
   internet — calls Microsoft's cloud) as fallback/backup.
9. **JARVIS heard itself and re-processed its own replies** (audio echo
   through the speakers back into the mic) → `speak()` now returns exactly
   how long it was audible, and `flush_echo()` drains that much mic input
   afterward before resuming normal listening.
10. **Piper glitches on mixed Thai/English text** (Qwen's answers often
    include English terms) → `speak()` routes to edge-tts instead of Piper
    whenever the text contains 2+ consecutive Latin letters.
11. **Only 4 rigid intents, everything else got "ยังไม่เข้าใจคำสั่งค่ะ"** →
    added `chat()` in `intent.py`: free-form fallback through the same Qwen
    model when the transcript isn't a device command, so JARVIS can actually
    answer/advise instead of refusing everything off-script. System prompt
    also tells it the input came from imperfect speech-to-text, so it asks
    for clarification instead of confidently hallucinating on garbled input.

## Current architecture

- **STT**: whisper.cpp. `ggml-base.bin` for wake-word polling (2s windows,
  fast), `ggml-small.bin` for command capture (5s windows, more accurate).
  Both run greedy decode with a language-specific `--prompt` bias
  (`WAKE_PROMPT` = "จาร์วิส", `COMMAND_PROMPT` = natural phrasings of the
  device commands).
- **LLM**: Qwen2-7B-Instruct (Q4_K_M GGUF) via `llama-server` on port 8080.
  `classify()` → rigid JSON intent for device commands. `chat()` → free-form
  reply for anything else.
- **TTS**: Piper (local) → edge-tts (cloud) → Windows SAPI (English only,
  last resort), auto-selected by `speak()`.
- **Devices**: `jarvis.server` (port 8765) + `jarvis.fake_esp` (mock
  `desk_light`). No real ESP32 connected — its micro-USB cable is broken.

## Known limitations, accepted as-is

- STT is not 100% accurate. This is the real ceiling of free local models on
  CPU, not a bug — Qwen's classify()/chat() are tolerant enough of noisy
  transcripts that the system still mostly does the right thing.
- edge-tts needs internet and is an unofficial/reverse-engineered API — it
  can break if Microsoft changes something server-side, with no warning.
- No speaker verification — anyone's voice (or a loud-enough TV) can trigger
  it. Discussed as a future feature (see below), not built.
- Background services (`llama-server`, `jarvis.server`, `jarvis.fake_esp`)
  do not survive a session/terminal reset and must be restarted manually.

## Benchmarks (this Windows machine, CPU-only — see GPU note below)

| Model | Task | Result |
|---|---|---|
| `ggml-base.bin` | full Thai sentence | ~1.1s, 2 minor word errors |
| `ggml-small.bin` | full Thai sentence | ~3.8s, perfect |
| `ggml-large-v3-turbo-q5_0` | any | ~20s encode alone — **rejected**, too slow for continuous polling |
| Thonburian Whisper medium (Thai fine-tune) | full Thai sentence | ~10s, perfect but **rejected**, too slow |
| Qwen2-7B `classify()` | short command | ~1.5s |
| Qwen2-7B `chat()` | open question | ~5–10s |

## GPU discovery — important for the Mac move

This machine has an **NVIDIA RTX 3050 (6GB)** sitting unused — all the
whisper.cpp/llama.cpp builds used tonight are CPU-only (no CUDA compiled
in), which is why the benchmarks above are as slow as they are.

**Decided not to chase a CUDA build tonight**, because CUDA is NVIDIA-only
and the final deployment target is a **Mac M4** (Apple Silicon, no NVIDIA
GPU — uses Metal instead). Any CUDA-specific work here would not transfer.

## Before moving to Mac M4

- **It's a Mac mini — no built-in mic or speakers.** Needs an external mic
  and external audio out before any of this can run. Recommend a **wired
  USB mic**, not Bluetooth — tonight's Bluetooth headset testing hit a
  Windows-specific profile-switching issue (mic silently drops to
  playback-only mode), and a desktop unit that's meant to listen
  continuously is exactly the wrong place to risk that again. Speakers: fine
  via a display's HDMI audio or a wired speaker on the headphone jack.
- whisper.cpp and llama.cpp both have solid native Apple Silicon support
  (Metal GPU + Neural Engine), typically enabled by default in normal
  builds — this should perform meaningfully better than the CPU-only
  numbers above, possibly without any special setup.
- **Re-run the benchmark table above fresh on the M4** before trusting any
  of tonight's "too slow, rejected" verdicts (large-v3-turbo, Thonburian
  Whisper) — they may become viable.
- Also worth re-testing on M4: **F5-TTS Thai** (e.g. `biodatlab/thonburian-tts`,
  or the "JaiTTS" project) for expressive/emotion-controllable voice cloning
  (ElevenLabs-like) — rejected as a CPU option tonight (diffusion models are
  slow without a GPU, F5-TTS wants ~3GB VRAM) but M4's GPU may handle it.
- The README's existing macOS instructions (`./setup_local_voice.sh` +
  `say` for TTS) predate tonight's fixes — the tone-mark/fuzzy-match/babble-
  filter/dual-window/dual-model/chat-fallback logic in `voice.py` is
  platform-independent and should carry over as-is; only the TTS backend
  (`speak()`'s Piper/edge-tts routing) is Windows-specific right now and
  needs a macOS equivalent (or just keep using `say`/edge-tts there too).

## Ideas discussed, not yet built

- **Garden/plant sensor support**: `jarvis.server`'s device model already
  generalizes beyond `desk_light` — adding a soil-moisture/temperature
  sensor device just needs a new intent (e.g. `garden_status`) in
  `intent.py` + a new branch in `answer_intent()`/`summarize_status()`.
  Natural next step once the ESP32 cable situation is resolved.
- **Speaker verification** ("only listen to my voice"): two options
  discussed — real speaker-embedding verification (SpeechBrain/pyannote,
  needs an enrollment step) vs. a cheap RMS/proximity heuristic (not real
  verification, just prefers the loudest/closest source). Not implemented.
