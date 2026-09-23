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
