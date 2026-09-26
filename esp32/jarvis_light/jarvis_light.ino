// JARVIS light on an ESP32 DevKit: joins the Wi-Fi, registers with the JARVIS device server
// as "desk_light" and switches an LED on the JARVIS server's turn_on / turn_off actions.
// Same line-JSON protocol as jarvis/fake_esp.py. Wi-Fi and token go in secrets.h (git-ignored).
//
// Wiring for a plain LED: GPIO 23 → 220 Ω resistor → LED long leg (+), LED short leg (−) → GND.
// Set LED_PIN to 2 to use the blue LED on the DevKit itself instead.

#include <WiFi.h>
#include "secrets.h"

const int LED_PIN = 23;
const char *DEVICE_ID = "desk_light";
const uint16_t JARVIS_PORT = 8765;

WiFiClient jarvis;
bool power = false;

void sendLine(const String &json) {
  jarvis.print(json);
  jarvis.print('\n');
}

String stateJson() {
  return String("{\"power\":\"") + (power ? "on" : "off") + "\"}";
}

// The value of "key":"..." in a one-line JSON message; enough for this protocol.
String field(const String &line, const char *key) {
  String tag = String("\"") + key + "\":\"";
  int start = line.indexOf(tag);
  if (start < 0) return "";
  start += tag.length();
  int end = line.indexOf('"', start);
  return end < 0 ? "" : line.substring(start, end);
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Wi-Fi: joining %s\n", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  if (WiFi.status() == WL_CONNECTED) Serial.printf("Wi-Fi: %s\n", WiFi.localIP().toString().c_str());
}

void connectJarvis() {
  if (jarvis.connected()) return;
  Serial.printf("JARVIS: connecting to %s:%u\n", JARVIS_HOST, JARVIS_PORT);
  if (!jarvis.connect(JARVIS_HOST, JARVIS_PORT)) {
    Serial.println("JARVIS: not reachable, retrying");
    return;
  }
  sendLine(String("{\"type\":\"register\",\"token\":\"") + JARVIS_TOKEN + "\",\"device_id\":\"" + DEVICE_ID +
           "\",\"name\":\"Desk light (ESP32)\",\"device_type\":\"light\",\"state\":" + stateJson() + "}");
}

void handle(const String &line) {
  if (field(line, "type") != "action") {
    Serial.printf("JARVIS: %s\n", line.c_str());  // "registered", or "bad token"
    return;
  }
  String action = field(line, "action"), id = field(line, "action_id");
  bool ok = action == "turn_on" || action == "turn_off";
  if (ok) {
    power = action == "turn_on";
    digitalWrite(LED_PIN, power ? HIGH : LOW);
  }
  Serial.printf("action %s -> %s\n", action.c_str(), ok ? "ok" : "unsupported");
  sendLine(String("{\"type\":\"action_result\",\"action_id\":\"") + id + "\",\"ok\":" + (ok ? "true" : "false") +
           ",\"state\":" + stateJson() + "}");
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, LOW);
}

void loop() {
  connectWiFi();
  if (WiFi.status() == WL_CONNECTED) connectJarvis();
  while (jarvis.connected() && jarvis.available()) {
    String line = jarvis.readStringUntil('\n');
    line.trim();
    if (line.length()) handle(line);
  }
  delay(jarvis.connected() ? 10 : 2000);
}
