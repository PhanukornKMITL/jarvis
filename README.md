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

## รันแยกทีละตัว (ดีบัก)

```sh
python3 -m jarvis.server
python3 -m jarvis.fake_esp
python3 -m jarvis.fake_esp --kind garden
llama-server -m .models/qwen2-7b-instruct-q4_k_m.gguf --port 8080 -c 4096 -ngl 99
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

Everything runs on the Mac: whisper.cpp for Thai speech-to-text, Qwen through
`llama-server` for understanding and chat, and the local `say` voice (Kanya) for
replies. Microphone audio never leaves the machine. Only the weather skill uses
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

Local LLM (Qwen2-7B-Instruct, ~4.7 GB) for intents and free-form questions:

```sh
curl -L -o .models/qwen2-7b-instruct-q4_k_m.gguf \
  https://huggingface.co/Qwen/Qwen2-7B-Instruct-GGUF/resolve/main/qwen2-7b-instruct-q4_k_m.gguf
llama-server -m .models/qwen2-7b-instruct-q4_k_m.gguf --port 8080 -c 4096 -ngl 99
```

Speak in one breath ("จาวิส ปิดไฟที", "เฮ้ จาวิส วันนี้ฝนจะตกไหม",
"จาวิส ต้นไม้เป็นไงบ้าง"), or say "จาวิส", wait for "พร้อมฟังค่ะ", then speak.
Anything else goes to Qwen as a question. macOS may ask Terminal for microphone
permission the first time. Press Ctrl+C to stop listening.

- `config.toml` picks the models, LLM endpoint, voice, weather coordinates and
  dataset folder, so a hardware upgrade is usually a config change.
- Light on/off is decided by rules, never by an LLM guess; when the transcript is
  ambiguous JARVIS asks again instead of switching the wrong way.
- Each skill lives in `jarvis/skills/`; add a module with `SKILLS` and list it in
  `jarvis/skills/__init__.py`, and its description reaches the Qwen prompt automatically.
- Every voice command's audio, transcripts and outcome are saved locally under
  `work/dataset/` for measuring and training later (`[dataset] enabled = false`
  turns this off).

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
