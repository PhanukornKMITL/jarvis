# JARVIS — local device and voice dashboard

JARVIS ใช้ Python standard library สำหรับ launcher และ dashboard โดยเปิดเซิร์ฟเวอร์
อุปกรณ์จำลอง โมเดลภาษา และระบบเสียงจาก Terminal คำสั่งเสียงและปุ่มบนเว็บสั่งอุปกรณ์
ผ่านเซิร์ฟเวอร์เดียวกัน

## เริ่มใช้งาน

หากต้องการดู UI และ animation โดยไม่เปิดโมเดล ไมโครโฟน หรืออุปกรณ์จริง ให้รัน:

```sh
python3 -m jarvis.mock_dashboard
```

บน Windows ใช้ `py -m jarvis.mock_dashboard` แล้วเปิด `http://127.0.0.1:8766/`
ปุ่มในโหมดนี้เปลี่ยนเฉพาะข้อมูลจำลองในหน่วยความจำ กด Ctrl+C เพื่อหยุด

จาก root ของ repo รันคำสั่งเดียวใน Terminal:

```sh
python3 -m jarvis.run
```

เปิด `http://127.0.0.1:8766` เพื่อดูอุปกรณ์, สั่งเปิดปิดไฟ, ดูคำสั่งเสียง และจัดการ service
แดชบอร์ดเปิดได้ทันทีระหว่างรอ LLM โหลด กด Ctrl+C เพื่อหยุด process ที่ launcher เปิดเอง
ค่าเริ่มต้นรับการเชื่อมต่อเฉพาะเครื่องนี้ หากตั้ง `[dashboard] host` เป็น IP ของ LAN
ให้ใช้เฉพาะเครือข่ายส่วนตัวที่ไว้ใจ เพราะยังไม่มีระบบล็อกอิน

การรัน voice ต้องมี `llama-server`, `ffmpeg`, `whisper-cli` และไฟล์โมเดลตาม `config.toml`
หากยังไม่พร้อม แดชบอร์ดจะแสดงสถานะ `missing` หรือ `stopped` พร้อมเหตุผล
เอา `light` หรือ `garden` ออกจาก `[run] fake_devices` เมื่อใช้ฮาร์ดแวร์จริง

ข้อมูลส่วนตัวสำหรับ JARVIS เก็บใน `profile.toml` ที่ root ของ repo ไฟล์นี้ถูก Git ignore
และโหลดใหม่เมื่อเริ่ม voice ระบบใช้ข้อมูลนี้ตอบคำถามส่วนตัวและปรับคำแนะนำอาหาร

### โหมดประหยัด RAM

ปุ่ม "โหมดประหยัด RAM" บน dashboard (หรือ `python3 -m jarvis.cli optimize`, ใส่ `--dry-run`
เพื่อดูก่อน) สั่งปิดแอปใน `[optimize] quit_apps` แบบเดียวกับ Cmd+Q แอปที่มีงานค้างจะถามให้เซฟ
และไม่ปิด Terminal, Claude หรือ Finder เด็ดขาด dashboard แสดงระดับ RAM และ swap ให้ดูก่อน/หลัง

## รันแยกทีละตัว (ดีบัก)

```sh
python3 -m jarvis.server
python3 -m jarvis.fake_esp
python3 -m jarvis.fake_esp --kind garden
llama-server -m .models/gemma-4-E4B-it-Q4_K_M.gguf --port 8080 -c 4096 -ngl 99 --chat-template-kwargs '{"enable_thinking":false}'
python3 -m jarvis.cli voice
```

แต่ละคำสั่งต้องเปิดใน Terminal คนละแท็บ คำสั่ง CLI ใช้เช็กได้ดังนี้:

```sh
python3 -m jarvis.cli devices
python3 -m jarvis.cli status
python3 -m jarvis.cli light-on
python3 -m jarvis.cli light-off
```

หยุดอุปกรณ์จำลองด้วย Ctrl+C แล้วอุปกรณ์จะหายจากรายการที่เชื่อมต่อ

## Try local voice (macOS)

Everything runs on the Mac: whisper.cpp for Thai speech-to-text, Gemma through
`llama-server` for understanding and chat, and F5-TTS (or the local `say` voice) for
replies. Microphone audio never leaves the machine. Only the weather tool uses
the internet (it sends the configured coordinates to api.open-meteo.com).
Models download once into the ignored `.models/` folder.

```sh
./setup_local_voice.sh    # whisper.cpp + ggml-base.bin (wake word)
```

Command model: Thonburian Whisper medium, BiodatLab's Thai fine-tune. It heard
real headset commands far better than `ggml-small.bin` and takes about 1s per
command on an M4. Convert it from the official weights once (needs torch and
transformers in any Python 3.11+ environment):

```sh
# download https://huggingface.co/biodatlab/whisper-th-medium-combined into .models/thonburian-medium-hf
# plus whisper.cpp's models/convert-h5-to-ggml.py and a checkout of openai/whisper
python convert-h5-to-ggml.py .models/thonburian-medium-hf path/to/openai-whisper /tmp/thon
whisper-quantize /tmp/thon/ggml-model.bin .models/ggml-thonburian-medium-q5_0.bin q5_0
```

Local LLM (Gemma 4 E4B, ~5 GB) for intents and free-form questions. It replaced
Qwen2-7B after a side-by-side test on real prompts: same intent accuracy, more natural
Thai. Keep its thinking mode off (`[llm] args` in `config.toml`) or replies come back empty:

```sh
curl -L -o .models/gemma-4-E4B-it-Q4_K_M.gguf \
  https://huggingface.co/unsloth/gemma-4-E4B-it-GGUF/resolve/main/gemma-4-E4B-it-Q4_K_M.gguf
llama-server -m .models/gemma-4-E4B-it-Q4_K_M.gguf --port 8080 -c 4096 -ngl 99 --chat-template-kwargs '{"enable_thinking":false}'
```

Speak in one breath ("จาวิส ปิดไฟที", "เฮ้ จาวิส วันนี้ฝนจะตกไหม",
"จาวิส ต้นไม้เป็นไงบ้าง"), or say "จาวิส", wait for "พร้อมฟังค่ะ", then speak.
Anything else goes to the LLM, which looks up weather, the garden, devices or the time
when the question needs real data. macOS may ask Terminal for microphone
permission the first time. Press Ctrl+C to stop listening.

- `config.toml` picks the models, LLM endpoint, voice, weather coordinates and
  dataset folder, so a hardware upgrade is usually a config change.
- Light on/off is decided by rules, never by an LLM guess; when the transcript is
  ambiguous JARVIS asks again instead of switching the wrong way.
- Actions (switching the light) are skills in `jarvis/skills/`, run only by their rules.
- Read-only information is a tool in `jarvis/tools.py`: the LLM picks the tools a question
  needs (in a short prompt of its own), code turns the numbers into words (ฝนปรอยๆ,
  ดินค่อนข้างแห้ง) and computes facts like when the rain stops, and the LLM answers from
  them. Add a `Tool` to `TOOLS` and the LLM can use it; a wrong pick only gives a wrong
  answer, never a wrong action.
- Every voice command's audio, transcripts and outcome are saved locally under
  `work/dataset/` for measuring and training later (`[dataset] enabled = false`
  turns this off).

### Natural voice with F5-TTS (optional)

`[tts] engine = "f5"` clones a voice from a 2–8 s reference clip with F5-TTS Thai
(VIZINTZOR/F5-TTS-THAI, CC-BY-4.0). No per-voice training: the model conditions on
the clip every time, so a shorter clip is faster (2.8 s ≈ 38% faster than 6.8 s).
It runs as the `tts` service in its own venv because it needs torch; everything
else stays stdlib-only. If it is not running, JARVIS falls back to `say`.

```sh
python3.13 -m venv .venv-tts
.venv-tts/bin/pip install f5-tts-th soundfile torchcodec
# .models/f5/reference.wav  - 2-8 s of clean speech (24 kHz mono)
# .models/f5/reference.txt  - its exact transcript
```

The model (~1.3 GB) downloads into `.models/f5/` on first start. `[tts.f5] step`
trades quality for speed (16 ≈ real time on an M4). Fixed replies are
pre-generated at startup and cached in `work/tts_cache/`; long replies are spoken
sentence by sentence while the next one is generated. Keep reference clips in the
git-ignored `.models/`: a voice clip is someone's voice and must not be committed.

`[persona] gender` sets ครับ/ค่ะ and ผม/ฉัน for every reply; match it to the voice.

### Conversation

After the wake word JARVIS stays in a conversation:

- slow answers (chat, tool lookups) start with a cached filler ("อืม สักครู่นะครับ");
- chat replies stream from the LLM and are spoken sentence by sentence (first audio in ~1 s);
- speaking over JARVIS stops it and your words become the next command (`[voice] barge_in`;
  meant for a headset, where JARVIS barely hears itself);
- for `[voice] follow_up_seconds` after a reply you can ask again without "จาวิส", and
  chat remembers the last few turns (up to 5 follow-ups, history expires after 3 minutes).

## Try local voice on Windows

Install FFmpeg and whisper.cpp so that `ffmpeg` and `whisper-cli` are available
in PowerShell. Download `ggml-large-v3-turbo-q5_0.bin` into `.models` (or pass
another path with `--model`). Then list microphone names:

The included setup script checks the tools and downloads the model automatically:

```powershell
.\setup_windows.ps1
```

```powershell
ffmpeg -list_devices true -f dshow -i dummy
```

Start voice mode with the exact microphone name shown by FFmpeg:

```powershell
py -m jarvis.cli voice --audio-device "Microphone (USB Audio Device)"
```

Windows uses DirectShow for recording and PowerShell's local SpeechSynthesizer
for spoken replies. Run `py -m jarvis.run` for the other services. If your microphone
name differs from the default, set `[run] autostart_voice = false` and run the
voice command above separately.

## What this proves

- A device can initiate a connection and identify itself to JARVIS.
- JARVIS can report connected devices and their latest reported state.
- JARVIS can route a command to a device process and receive the resulting state.
- A disconnected device no longer appears as connected.

The server binds to `127.0.0.1` by default, so this demo is only reachable from
the same Mac. Later, the server can bind to the Mac mini's LAN address and an
ESP32 can connect over the home Wi-Fi. SQLite persistence and Wi-Fi access-point
setup remain later steps.

## งานถัดไป

ดู `TODO.md` สำหรับการวัดผลจาก dataset, คำปลุกเฉพาะ, หลายอุปกรณ์ และฮาร์ดแวร์จริง
