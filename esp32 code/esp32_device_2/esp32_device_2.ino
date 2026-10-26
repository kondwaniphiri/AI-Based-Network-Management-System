/*
 * ============================================================
 * IoT Network Testing System – ESP32 Device 2
 * ============================================================
 * Role     : HTTP only (pure TCP node)
 * Static IP: 192.168.8.102
 * Server   : 192.168.8.100
 *
 * This device sends data only over HTTP (TCP), acting as a stable
 * and reliable reference node.  Its traffic is used as a baseline
 * for comparison against the dual-protocol Device 1 and the
 * UDP-only Device 3 in Wireshark analysis.
 *
 * Protocol flags:
 *   ENABLE_HTTP true   – POST /data to Flask every 100–2000 ms (randomised)
 *   ENABLE_UDP  false  – UDP disabled; no datagrams sent
 *
 * Board   : ESP32 Dev Module (or any ESP32 board)
 * IDE     : Arduino IDE 2.x with arduino-esp32 board package
 * Libraries: WiFi, HTTPClient, WebServer, WiFiUDP (all built-in)
 * ============================================================
 */

#include <WiFi.h>
#include <esp_system.h>
#include <HTTPClient.h>
#include <WebServer.h>
#include <WiFiUDP.h>

// =======================
// <<< CHANGE PER DEVICE >>>
// Set these three values when flashing a new physical board.
// =======================
#define DEVICE_ID       "esp32_2"   // must match the server's device name
#define STATIC_IP_LAST  102         // unique host address; do not duplicate it

// ENABLE_HTTP true  = active;  false = skip all HTTP sends
// ENABLE_UDP  false = UDP disabled for this device
#define ENABLE_HTTP true
#define ENABLE_UDP  false

// =======================
// <<< WIFI SETTINGS >>>
// Change to match your network.
// =======================
#if __has_include("gateway_network.local.h")
#include "gateway_network.local.h"
#endif
#ifndef WIFI_SSID
#define WIFI_SSID  ""  // Configure locally; never commit real Wi-Fi credentials.
#define WIFI_PASS  ""
#endif

// =======================
// NETWORK CONFIG
// =======================
#ifndef SERVER_IP
#define SERVER_IP   "192.168.8.100"  // Flask server PC static IP
#endif
#ifndef LAB_NET_A
#define LAB_NET_A 192
#define LAB_NET_B 168
#define LAB_NET_C 8
#endif
#define SERVER_PORT 5000             // Flask HTTP port
#ifndef CONTROL_TOKEN
#define CONTROL_TOKEN ""  // Set a lab control token to enable POST /config.
#endif
#define UDP_PORT    12345            // UDP listener port (unused for this device)

// =======================
// GLOBALS
// =======================
WebServer server(80);   // HTTP server – answers GET /data from the Flask traffic generator
WiFiUDP udp;            // UDP socket (declared but unused since ENABLE_UDP is false)

// millis()-based non-blocking timing (no delay() used in the main loop)
unsigned long lastHttpSend = 0;
unsigned long lastUdpSend  = 0;

// Randomised send intervals re-rolled after every transmission. Changing the
// HTTP range changes offered traffic and therefore affects experiment results.
long httpInterval = 1000;  // HTTP interval: 100 – 2000 ms
long controlledUdpInterval = 0; // 0 retains the original random schedule
long udpInterval  = 100;   // UDP  interval: 50  –  200 ms (inactive for this device)

// =======================
// DATASET
// Fixed array of JSON strings sent sequentially, looping indefinitely.
//   Entry 0 – small  (~40 bytes)
//   Entry 1 – small+ (~55 bytes)
//   Entry 2 – medium (~90 bytes)
//   Entry 3 – large  (~150 bytes)
// =======================
const char* dataset[] = {
  "{\"device\":\"" DEVICE_ID "\",\"temp\":21,\"hum\":55}",
  "{\"device\":\"" DEVICE_ID "\",\"temp\":25,\"hum\":60,\"extra\":\"AAAAAAAAAAAAAA\"}",
  "{\"device\":\"" DEVICE_ID "\",\"temp\":30,\"hum\":65,\"extra\":\"BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB\"}",
  "{\"device\":\"" DEVICE_ID "\",\"temp\":28,\"hum\":70,\"log\":\"CCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC\"}"
};

// Independent sequence spaces prevent HTTP traffic appearing as UDP loss.
uint32_t httpSeq = 0, udpSeq = 0;
String bootId;

int dataIndex = 0;
int dataSize  = sizeof(dataset) / sizeof(dataset[0]);

String measuredPayload(bool isUdp) {
  String body = dataset[dataIndex];
  body.remove(body.length() - 1);
  uint32_t seq = isUdp ? udpSeq++ : httpSeq++;
  return body + ",\"boot_id\":\"" + bootId + "\",\"seq\":" + String(seq) + "}";
}


// =======================
// HTTP RESPONSE (GET)
// Handles GET /data requests sent by the Flask traffic_generator thread.
// Returns a variable-length plain-text body (50–500 random 'X' chars)
// to simulate download traffic of varying sizes.
// =======================
void handleData() {
  String response = "ESP32 RESPONSE ";

  int size = random(50, 500);
  for (int i = 0; i < size; i++) {
    response += "X";
  }

  server.send(200, "text/plain", response);
}

// =======================
// SEND HTTP
// =======================
void sendHTTP() {
  HTTPClient http;

  String url = "http://" + String(SERVER_IP) + ":" + String(SERVER_PORT) + "/data";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");

  String payload = measuredPayload(false);

  int code = http.POST(payload);

  Serial.print("[HTTP] Sent → ");
  Serial.print(payload);
  Serial.print(" | Code: ");
  Serial.println(code);

  http.end();
}

// =======================
// SEND UDP  (not used on this device – ENABLE_UDP is false)
// Included for code consistency across all device files.
// =======================
void sendUDP() {
  udp.beginPacket(SERVER_IP, UDP_PORT);
  String payload = measuredPayload(true);
  udp.write((const uint8_t*)payload.c_str(), payload.length());
  udp.endPacket();
  Serial.print("[UDP]  Sent \u2192 ");
  Serial.println(payload);
}

// =======================
// SETUP
// =======================
void handleConfig() {
  if (String(CONTROL_TOKEN).length() == 0 ||
      server.header("X-Control-Token") != String(CONTROL_TOKEN)) {
    server.send(403, "text/plain", "Control disabled or invalid token");
    return;
  }
  String value = server.arg("udp_interval_ms");
  for (unsigned int i = 0; i < value.length(); i++) {
    if (!isDigit(value[i])) { server.send(400, "text/plain", "Integer required"); return; }
  }
  long interval = value.toInt();
  if (interval < 50 || interval > 10000) {
    server.send(400, "text/plain", "Interval must be 50..10000 ms");
    return;
  }
  controlledUdpInterval = interval;
  udpInterval = interval;
  server.send(200, "application/json", "{\"status\":\"ok\"}");
}

void setup() {
  Serial.begin(115200);
  bootId = String(esp_random(), HEX) + String(esp_random(), HEX);

  IPAddress local_IP(LAB_NET_A, LAB_NET_B, LAB_NET_C, STATIC_IP_LAST);
  IPAddress gateway(LAB_NET_A, LAB_NET_B, LAB_NET_C, 1);
  IPAddress subnet(255, 255, 255, 0);

  // WiFi.config() before WiFi.begin() is required for static IP assignment
  WiFi.config(local_IP, gateway, subnet);
  WiFi.begin(WIFI_SSID, WIFI_PASS);

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println("\nConnected!");
  Serial.println(WiFi.localIP());

  server.on("/data", handleData);  // register GET /data handler
  const char* controlHeaders[] = {"X-Control-Token"};
  server.collectHeaders(controlHeaders, 1);
  server.on("/config", HTTP_POST, handleConfig);
  server.begin();

  udp.begin(0);   // open UDP socket (source port assigned by OS)

  randomSeed(analogRead(0));  // seed PRNG with floating ADC noise
}

// =======================
// LOOP
// millis()-based non-blocking loop.
// server.handleClient() is called every iteration so GET /data requests
// are never missed even during send operations.
// =======================
void loop() {
  server.handleClient();

  if (WiFi.status() != WL_CONNECTED) {
    WiFi.reconnect();
    return;
  }

  unsigned long now = millis();

  // HTTP TRAFFIC – stable TCP traffic (100–2000 ms interval)
  if (ENABLE_HTTP && now - lastHttpSend >= (unsigned long)httpInterval) {
    sendHTTP();
    lastHttpSend = now;
    httpInterval = random(1100, 3000);
  }

  // UDP TRAFFIC – disabled (ENABLE_UDP false); block never executes
  if (ENABLE_UDP && now - lastUdpSend >= (unsigned long)udpInterval) {
    sendUDP();
    lastUdpSend = now;
    udpInterval = controlledUdpInterval > 0 ? controlledUdpInterval : random(1050, 1200);
  }

  // Advance dataset index after any send in this pass
  if ((now - lastHttpSend < 10) || (now - lastUdpSend < 10)) {
    dataIndex = (dataIndex + 1) % dataSize;
  }
}
