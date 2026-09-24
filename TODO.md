# TODO: Dashboard + เปิดระบบด้วยคำสั่งเดียว

เป้าหมาย: พิมพ์คำสั่งเดียว (`python3 -m jarvis.run`) แล้วทุก service ขึ้นมาเอง พร้อมหน้า
dashboard ที่ `http://127.0.0.1:8766` ซึ่ง:

- เห็นสถานะไฟ/อุปกรณ์แบบสด เช่นสั่งด้วยเสียงว่า "จาวิส ปิดไฟ" แล้วหน้าเว็บเปลี่ยนตามทันที
  เพื่อยืนยันว่า toggle จริง
- กดเปิด/ปิดไฟจากหน้าเว็บได้
- เห็นว่าแต่ละ service รันอยู่ไหม และกด start / stop / restart ได้
- เห็นคำสั่งเสียงล่าสุด: ได้ยินว่าอะไร, intent อะไร, JARVIS ตอบอะไร

---

## บริบทของโค้ดตอนนี้ (อ่านก่อนเริ่ม)

- Python 3.11+ **ใช้แค่ standard library** ตอนรันจริง ห้ามเพิ่ม dependency (ไม่มี venv,
  ใช้ `python3` ของ Homebrew) เครื่องหลักคือ macOS (Mac mini M4) แต่ต้องไม่ทำให้ Windows พัง
  โค้ดเดิมมี branch `platform.system() == "Windows"` อยู่ ให้รักษาไว้
- รันทุกคำสั่งจาก root ของ repo

### Service ที่ต้องรัน (ปัจจุบันต้องเปิดเองแท็บละตัว)

| ชื่อ | คำสั่ง | พอร์ต | วิธีเช็คว่าพร้อม |
|---|---|---|---|
| `llm` | `llama-server -m .models/qwen2-7b-instruct-q4_k_m.gguf --port 8080 -c 4096 -ngl 99` | 8080 | `GET http://127.0.0.1:8080/health` ได้ `{"status":"ok"}` (ใช้เวลาโหลด ~10–20s) |
| `server` | `python3 -m jarvis.server` | 8765 (TCP) | เปิด TCP connect ได้ |
| `fake_light` | `python3 -m jarvis.fake_esp` | – | ขึ้นใน status ของ server เป็น `desk_light` |
| `fake_garden` | `python3 -m jarvis.fake_esp --kind garden` | – | ขึ้นใน status เป็น `garden` |
| `voice` | `python3 -m jarvis.cli voice` | – | พิมพ์ `กำลังเปิดไมค์...` |

ลำดับที่ถูก: `server` → รอพอร์ต 8765 → `fake_light`, `fake_garden`
และ `llm` → รอ `/health` จากนั้น `voice` (voice ต้องใช้ทั้ง server และ llm)

### โปรโตคอลของ `jarvis.server` (TCP พอร์ต 8765)

เปิด connection ใหม่ทุกคำขอ ส่ง JSON หนึ่งบรรทัด รับ JSON หนึ่งบรรทัด
มี helper อยู่แล้วคือ `jarvis.cli.request(host, port, message)` (async ใช้กับ `asyncio.run`)

```json
→ {"type": "status"}
← {"devices": [{"device_id": "desk_light", "name": "Desk light (fake ESP)",
                "device_type": "light", "online": true, "state": {"power": "on"}},
               {"device_id": "garden", "device_type": "garden", "online": true,
                "state": {"soil_moisture": 28, "temperature": 31.5, "humidity": 68}}]}

→ {"type": "action", "target": "desk_light", "action": "turn_on"}   (หรือ "turn_off")
← {"ok": true, "device_id": "desk_light", "state": {"power": "on"}}
← {"ok": false, "error": "device 'x' is not connected"}
```

- อุปกรณ์ที่หลุดการเชื่อมต่อจะหายไปจาก status เลย (ไม่มีสถานะ offline ค้างไว้)
- ถ้า fake_esp ตัวใหม่ลงทะเบียนด้วย `device_id` เดิม server จะเตะตัวเก่าออก และตัวเก่าจะปิดตัวเอง

### ข้อมูลที่ dashboard ควรอ่าน

- `work/dataset/index.jsonl` เก็บคำสั่งเสียง 1 บรรทัดต่อ 1 คำสั่ง ฟิลด์:
  `time, audio, wake, transcript, command, base, intent, reply`
  (โฟลเดอร์มาจาก `load_config().dataset_dir` ถ้าเป็น `None` แปลว่าผู้ใช้ปิดการเก็บไว้)
- `work/voice_transcript.log` เป็น log ข้อความของ voice
- ค่าตั้งค่าอยู่ที่ `config.toml` อ่านผ่าน `jarvis/config.py` (`load_config()` คืน dataclass `Config`)

### ข้อควรระวัง (ห้ามทำพัง)

- **สิทธิ์ไมค์ของ macOS:** `voice` ต้องเป็น child process ของโปรแกรมที่รันจาก Terminal
  (ถ้า launcher รันจาก Terminal ก็ใช้ได้ ห้ามแยกไปรันผ่าน launchd ในงานนี้)
- **หยุด voice ด้วย SIGINT ไม่ใช่ kill:** voice มีตัวจัดการ `KeyboardInterrupt` ที่ปิด ffmpeg
  ให้เรียบร้อย ให้ส่ง SIGINT → รอ 3s → `terminate()` → รอ 2s → `kill()`
  (บน Windows ใช้ `terminate()` ได้เลย)
- ถ้ามี service รันอยู่แล้วตอนเริ่ม (เช่นผู้ใช้เปิด llama-server ไว้เองในแท็บอื่น) **ห้ามเปิดซ้ำ**
  ให้แสดงสถานะเป็น `external` และไม่ stop มัน
  - service ที่มีพอร์ต: เช็คว่ามีใครฟังพอร์ตนั้นอยู่
  - `voice`: เช็คด้วย `pgrep -f "jarvis.cli voice"` (macOS/Linux) **ถ้ามี voice สองตัวพร้อมกัน
    JARVIS จะตอบซ้ำสองรอบ**
- ตั้ง `PYTHONUNBUFFERED=1` ให้ child process จะได้เห็น log ทันที

---

## TODO

### 1. Config

- [ ] เพิ่มใน `Config` / `config.toml` (ทุกค่าต้องมี default):
  - `[llm] model = ".models/qwen2-7b-instruct-q4_k_m.gguf"`
  - `[run] fake_devices = ["light", "garden"]` เอาออกเมื่อมี ESP32 จริง
  - `[run] autostart_voice = true`
  - `[dashboard] port = 8766`, `host = "127.0.0.1"`

### 2. Supervisor: `jarvis/run.py` (`python3 -m jarvis.run`)

- [ ] คลาส `Service`: `name`, `label` (ภาษาไทยไว้แสดงบนหน้าเว็บ), `command`, `port | None`,
      `ready_check`, `process`, `log` (`collections.deque(maxlen=300)`), `started_at`, `exit_code`
- [ ] สถานะ: `starting | running | stopped | crashed | external | missing`
  (`missing` = ไม่มีไฟล์ที่ต้องใช้ เช่นไม่มี `llama-server` หรือไม่มีไฟล์โมเดล ให้แสดงเหตุผลด้วย)
- [ ] thread อ่าน stdout+stderr ของแต่ละ process ทีละบรรทัด เก็บเข้า `log`
      และพิมพ์ออก console โดยมี prefix เช่น `[voice] ...`
- [ ] เปิด service ตามลำดับข้างบนใน background thread
      ให้หน้า dashboard เปิดได้ทันทีโดยไม่ต้องรอ LLM โหลด
- [ ] `start(name)`, `stop(name)`, `restart(name)` ใช้ได้ตอนรันอยู่
  (dashboard เรียกใช้) ห้าม stop service ที่เป็น `external`
- [ ] Ctrl+C ที่ launcher ต้องหยุดทุก service ที่มันเปิดเอง ในลำดับกลับกัน:
      voice → fakes → server → llm
- [ ] ถ้า process ตายเอง ให้เป็น `crashed` พร้อม exit code **ห้าม restart เองอัตโนมัติในรอบนี้**
- [ ] พิมพ์ URL ของ dashboard ตอนเริ่ม

### 3. HTTP API ของ dashboard: `jarvis/dashboard.py`

ใช้ `http.server.ThreadingHTTPServer` รันใน thread ของ supervisor

- [ ] `GET /` ส่งไฟล์ `jarvis/dashboard.html`
- [ ] `GET /api/state` →
  ```json
  {"services": [{"name","label","status","reason","pid","uptime_s","exit_code"}],
   "devices": [...จาก server...] ,  "devices_error": null | "ติดต่อ server ไม่ได้",
   "recent": [...20 บรรทัดล่าสุดของ index.jsonl ใหม่สุดก่อน...]}
  ```
  ถ้าติดต่อ server ไม่ได้ ให้คืน `devices: []` พร้อม `devices_error` ห้ามคืน error 500
- [ ] `GET /api/logs?name=voice` → 200 บรรทัดล่าสุดของ service นั้น
- [ ] `POST /api/device` body `{"device_id": "desk_light", "action": "turn_on"}`
      ส่งต่อไป server แล้วคืนคำตอบของ server
- [ ] `POST /api/service` body `{"name": "voice", "op": "start|stop|restart"}`
- [ ] **ความปลอดภัย** (server ไม่มีระบบล็อกอิน):
  - bind `127.0.0.1` เป็นค่าเริ่มต้น ถ้าตั้ง host อื่นให้พิมพ์คำเตือน
  - `POST` ต้องมี `Content-Type: application/json` ถ้าไม่มีให้ตอบ 415
    กันเว็บอื่นในเบราว์เซอร์แอบยิง form มาสั่งไฟ (CSRF)
  - เช็ค header `Host` ว่าเป็น `127.0.0.1:<port>` หรือ `localhost:<port>` (กัน DNS rebinding)
    ถ้าตั้ง host เป็น LAN ให้ยอมรับ IP นั้นด้วย
  - อ่าน body ไม่เกิน 4 KB, `action` ยอมรับแค่ `turn_on` / `turn_off`,
    `op` ยอมรับแค่ `start` / `stop` / `restart`

### 4. หน้าเว็บ: `jarvis/dashboard.html`

ไฟล์เดียว CSS/JS อยู่ในไฟล์ ไม่ใช้ CDN (ต้องใช้ได้แบบออฟไลน์)

- [ ] ดึง `/api/state` ทุก 1 วินาที ถ้าดึงไม่ได้ให้ขึ้นแถบ "ติดต่อ dashboard ไม่ได้"
- [ ] การ์ดอุปกรณ์
  - ไฟ: รูปหลอดไฟ/สีที่เห็นชัดว่าเปิดหรือปิด ปุ่มสลับ (ส่ง `turn_on` / `turn_off` ตามสถานะ
    ตอนนั้น) และระหว่างรอคำตอบให้ปุ่มกดไม่ได้
  - ต้นไม้: ความชื้นดินเป็นแถบ % (ต่ำกว่า 30% ให้เตือนว่า "ควรรดน้ำ"), อุณหภูมิ, ความชื้นอากาศ
  - อุปกรณ์ประเภทอื่น: แสดง `state` เป็น key/value
  - เมื่อสถานะเปลี่ยน ให้การ์ดกะพริบสั้นๆ จะได้เห็นว่าคำสั่งเสียงไปถึงจริง
- [ ] ตาราง service: จุดสี + สถานะ + uptime + ปุ่ม start/stop/restart
      (ซ่อนปุ่มถ้าเป็น `external`) กดดู log ได้ (ขยายเป็นกล่อง log ที่ดึง `/api/logs` ทุก 2s)
- [ ] คำสั่งเสียงล่าสุด: เวลา, ข้อความที่ได้ยิน (`command`), `intent`, `reply`
      ถ้า intent เป็น `unclear` หรือ `error` ให้เป็นสีเตือน
- [ ] รองรับ light/dark ตาม `prefers-color-scheme` ใช้บนมือถือได้ (กว้าง 375px ไม่ต้องเลื่อนแนวนอน)
- [ ] ข้อความบนหน้าเป็นภาษาไทย

### 5. เอกสาร

- [ ] README: เปลี่ยนวิธีเริ่มเป็น `python3 -m jarvis.run` แล้วเปิด `http://127.0.0.1:8766`
      และเก็บวิธีรันแยกทีละตัวไว้ในหัวข้อ "รันแยกทีละตัว" (ไว้ใช้ดีบัก)
- [ ] README ตอนนี้อ้างถึงไฟล์ `esp32/jarvis_led/jarvis_led.ino` และ dashboard พอร์ต 8766
      ของ `jarvis.server` ซึ่ง**ไม่มีอยู่ใน repo** ถ้ามีโค้ดค้างในเครื่อง Windows ให้ push มาก่อน
      ถ้าไม่มี ให้แก้ README ให้ตรงกับของจริง

### 6. ทดสอบก่อนถือว่าเสร็จ (acceptance)

- [ ] `python3 -m jarvis.run` ครั้งเดียว: ทุก service เป็น `running` (llm ใช้เวลาหน่อย)
      แล้ว `python3 -m jarvis.cli devices` เห็น `desk_light` และ `garden`
- [ ] กดปุ่มไฟบนหน้าเว็บแล้ว `python3 -m jarvis.cli status` เปลี่ยนตาม
- [ ] สั่ง `python3 -m jarvis.cli light-on` จาก Terminal หน้าเว็บเปลี่ยนภายใน ~1 วินาที
- [ ] พูด "จาวิส ปิดไฟที" ไฟบนหน้าเว็บดับ และคำสั่งขึ้นในรายการคำสั่งล่าสุด
- [ ] stop / start `voice` จากหน้าเว็บ ไมค์กลับมาทำงานได้ (มีบรรทัด `กำลังเปิดไมค์...` ใน log)
- [ ] ถ้าเปิด `llama-server` ไว้เองก่อน สถานะ `llm` เป็น `external` และ Ctrl+C ไม่ปิดมัน
- [ ] Ctrl+C ที่ launcher แล้วไม่มี process ค้าง (`pgrep -fl "jarvis\.|llama-server|ffmpeg"` ว่าง)
- [ ] `curl -X POST -d 'device_id=desk_light&action=turn_on' http://127.0.0.1:8766/api/device`
      (ไม่มี JSON content-type) ต้องได้ 415 และไฟไม่เปลี่ยน
- [ ] ปิด server ระหว่างเปิดหน้าเว็บอยู่ หน้าไม่พัง ขึ้นว่าติดต่อ server ไม่ได้

---

## งานถัดไป (ยังไม่ต้องทำในรอบนี้)

1. **สคริปต์วัดผลจาก dataset:** เอาเสียงใน `work/dataset/audio/` มารันกับโมเดลหรือค่าตั้งค่า
   ต่างๆ แล้วบอก % ถูก จะได้ตัดสินเรื่องเปลี่ยนโมเดล ถอด base หรือปรับเวลารอเงียบ ด้วยตัวเลข
2. **คำปลุก:** เก็บเสียง "จาวิส" จริงให้ได้ 50–100 ครั้งแล้วเทรนโมเดลคำปลุกเฉพาะ
   (ตอนนี้ base พลาดบ้าง และ "จริงสิ" ปลุกได้)
3. **หลายอุปกรณ์:** "เปิดไฟทั้งหมด", "เปิดไฟหมายเลข 3" ให้ skill ไฟรับ `target` ที่ Qwen
   ดึงออกมาจากประโยค แต่ทิศทางเปิด/ปิดยังต้องตัดสินด้วยกฎใน `jarvis/skills/light.py` เหมือนเดิม
   ห้ามให้ LLM เดา
4. **ฮาร์ดแวร์จริง:** ESP32 LED และเชื่อมเซ็นเซอร์ต้นไม้จริง (ต้องส่ง `soil_moisture` ใน state
   แบบเดียวกับ `fake_esp --kind garden`)
5. **เสียงพูด:** Kanya (Enhanced) ซึ่งไม่ต้องแก้โค้ด แค่ตั้ง `[tts] voice` หรือ F5-TTS Thai
   (ต้องวัดความเร็วบน M4 ก่อน และอัดเสียงประโยคที่ใช้ประจำเก็บไว้ล่วงหน้า)
6. สกิลบอกเวลาและวันที่, ให้ chat จำบทสนทนาก่อนหน้า, ระบบล็อกอินก่อนเปิดให้เข้าผ่าน LAN
