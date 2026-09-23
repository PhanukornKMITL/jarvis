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

## What this proves

- A device can initiate a connection and identify itself to JARVIS.
- JARVIS can report connected devices and their latest reported state.
- JARVIS can route a command to a device process and receive the resulting state.
- A disconnected device no longer appears as connected.

The server binds to `127.0.0.1` by default, so this demo is only reachable from
the same Mac. That is deliberate for the first local prototype. Later, the
server can bind to the Mac mini's LAN address and an ESP32 can connect over the
home Wi-Fi. This prototype does not yet include voice, wake-word detection,
SQLite persistence, or Wi-Fi access-point setup.

## Next increments

1. Add a temperature fake device and a natural-language-independent status
   command model.
2. Add microphone capture, local wake-word detection for “Jarvis”, and short
   voice queries for device status.
3. Add SQLite persistence and clearer online/offline timeouts.
4. Move the server onto the Mac mini's home network, then replace the fake
   device with an ESP32 when hardware is available.
