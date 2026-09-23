# JARVIS prototype — network device discovery

This first runnable step uses only the Mac and Python's standard library. JARVIS
runs as a server process. A second process acts like an ESP32: it connects over
TCP, announces its device ID and current state, then waits for actions. The CLI
can list the connected device, read its state, and turn its simulated light on
or off.

## Run it

Open three Terminal tabs from this directory and run:

```sh
python3 -m jarvis.server
```

Open `http://127.0.0.1:8766` for the live device dashboard. To accept ESP32
connections and view the dashboard from other devices on your home network,
bind the server to the LAN interface (for example `python -m jarvis.server
--host 0.0.0.0`) and use the computer's LAN IP from the ESP32. This prototype
does not include authentication, so keep it on a trusted private network.

## Control a real ESP32 LED

The ready-to-upload Arduino sketch is at `esp32/jarvis_led/jarvis_led.ino`.
Wire GPIO 25 to an LED anode through a 220–330 ohm resistor, and wire the LED
cathode to GND. In the sketch, set `WIFI_SSID`, `WIFI_PASSWORD`, and
`JARVIS_HOST` to the LAN IP of the computer running JARVIS. Start Core for LAN
connections, then upload the sketch:

```sh
python -m jarvis.server --host 0.0.0.0
```

The ESP32 registers as `living_room_led` and appears in the dashboard. If
Windows Firewall asks, allow Python on private networks; otherwise add an
inbound TCP rule for port 8765.

```sh
python3 -m jarvis.fake_esp
```

```sh
python3 -m jarvis.cli devices
python3 -m jarvis.cli status
python3 -m jarvis.cli light-on
python3 -m jarvis.cli light-off
```

Stop the fake ESP with Ctrl+C. It disappears from the connected-device list.

## Try local voice (macOS)

Local voice recognition is an optional step. It uses whisper.cpp on the Mac for
Thai transcription and the Mac's local `say` voice for spoken replies; it does
not send microphone audio to Apple or another cloud service. The model downloads
once during setup and stays in the ignored `.models/` folder.

```sh
./setup_local_voice.sh
python3 -m jarvis.cli voice
```

Keep the JARVIS server and fake ESP running in their own Terminal tabs. Say
“Jarvis”, wait for “พร้อมฟังค่ะ”, then ask for device status. macOS may ask
Terminal for microphone permission the first time. Press Ctrl+C to stop listening.

The setup downloads the 142 MiB multilingual base model once. Recognition speed
and accuracy depend on the microphone and Mac model. This first voice step
answers device-status questions only.

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
for spoken replies. Keep the JARVIS server and fake ESP running first.

## What this proves

- A device can initiate a connection and identify itself to JARVIS.
- JARVIS can report connected devices and their latest reported state.
- JARVIS can route a command to a device process and receive the resulting state.
- A disconnected device no longer appears as connected.

The server binds to `127.0.0.1` by default, so this demo is only reachable from
the same Mac. Later, the server can bind to the Mac mini's LAN address and an
ESP32 can connect over the home Wi-Fi. SQLite persistence and Wi-Fi access-point
setup remain later steps.

## Next increments

1. Add a temperature fake device and a natural-language-independent status
   command model.
2. Improve local wake-word and short-utterance detection based on real mic tests.
3. Add SQLite persistence and clearer online/offline timeouts.
4. Move the server onto the Mac mini's home network, then replace the fake
   device with an ESP32 when hardware is available.
